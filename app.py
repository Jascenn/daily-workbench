#!/usr/bin/env python3
"""Tiny local sync server for the workbench.

Serves the folder statically and exposes a shared JSON store at /data.json:
  GET  /data.json  -> current canonical store (used by the workbench UI)
  POST /data.json  -> atomically replace the store (called by the workbench on save)
  GET  /sections.json -> 派生快照：资讯 / 写作 / 复盘 三个「功能类型」区的数据
                         （只读，由 scripts/sync/sections.py 生成，页面不写回）
  GET  /social.json -> 自媒体只读快照：账号、逐条内容、草稿能力与数据边界
                       （只读，由 scripts/sync/social-sync.py 生成，页面只写本地今日计划）
  GET  /web-presence.json -> 网站健康、博客 RSS 与流量接入状态
                             （只读，由 scripts/sync/web-monitor.py 每 5 分钟刷新）
  GET  /api/status -> {"served": true}  (lets the UI detect online mode)
  POST /api/sync   -> run scripts/sync-now.sh and return its output
                      (lets the UI's 「同步一次」 button trigger a real Notion pull;
                       the browser can't execute shell scripts itself)

The local sync scripts read and write data.json through the authenticated API.
Bound to 127.0.0.1 only; not exposed to the network.

Write safety (added 2026-09-29):
  * every overwrite first rotates a timestamped copy into backups/ (keep last 20)
  * a write is REJECTED (409) if it would shrink earnings or projects by more
    than 50% while the existing side holds more than 10 records — this catches
    an accidental "blank the whole file" save. Pass ?force=1 to override.

/api/sync runs a FIXED local script path with no user input interpolated,
so there is no command-injection surface. It still requires the bearer token.

Static serving (tightened 2026-09-30):
  the fallback is an ALLOWLIST of exactly one path (/workbench.html). It used to be
  an unconditional SimpleHTTPRequestHandler fallback, which meant the token gate on
  /data.json was decorative: the same records were downloadable via
  /backups/data.json.<ts>.bak (full copies, rotated by the write guard) and
  /notion-sync.md. See STATIC_ALLOW below.
"""
import http.server
import email.utils
import hashlib
import datetime
import json
import glob
import html.parser
import ipaddress
import math
import os
import secrets
import signal
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse, parse_qs, urljoin

DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(DIR, "scripts", "sync"))
import writing_store  # noqa: E402

DATA = os.path.join(DIR, "data.json")
# 派生快照（资讯/写作/复盘）。**只读**：页面永远不写它，只由 scripts/sync/sections.py 生成。
# 与 data.json 分开存的原因见 scripts/sync/sections.py 文件头：data.json 是 Notion 同步产物、
# 有写入护栏，混进去会被「同步一次」冲掉。
SECTIONS = os.path.join(DIR, "sections.json")
REVIEW_DIR = os.path.join(writing_store.OBSIDIAN_ROOT, "调试经验")
SOCIAL = os.path.join(DIR, "social.json")
WEB_PRESENCE = os.path.join(DIR, "web-presence.json")
JEV_SETTINGS = os.path.expanduser("~/.config/daily-workbench/jev-settings.json")


def _jev_settings_read():
    try:
        with open(JEV_SETTINGS, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _jev_settings_write(d):
    os.makedirs(os.path.dirname(JEV_SETTINGS), exist_ok=True)
    tmp = JEV_SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, JEV_SETTINGS)


def _jev_call(text):
    """调 TypeSafe Jev 对一段文本做质量判断。

    只读 ``~/.config/jev-jarvis/env``，使用直连；不再硬编码某个本机代理端口，
    避免代理未启动或端口变化时把可用的网络链路一起拖垮。
    """
    cfg = {}
    env_path = os.path.expanduser("~/.config/jev-jarvis/env")
    try:
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    except OSError:
        return False, {"error": "未找到 ~/.config/jev-jarvis/env"}
    key = cfg.get("TYPESAFE_API_KEY", "")
    if not key:
        return False, {"error": "未配置 TYPESAFE_API_KEY"}
    base = (cfg.get("TYPESAFE_BASE_URL") or "https://api.typesafe.ai").rstrip("/")
    model = cfg.get("TYPESAFE_MODEL") or "jev-latest"
    payload = {
        "model": model,
        "state": "以下是一段要发布的内容，请从「对读者是否有实际用处」的角度给出评分与理由。",
        "questions": {
            "quality": {
                "type": "score",
                "instructions": "内容质量 0-10 分：有用、有观点、说人话。",
                # score 的 criteria 是 list（0-9 档文字说明），与 jev-jarvis
                # 内置 RISK_LEVELS 同构 —— 上限 10 档，从无价值到非常好。
                "criteria": [
                    "完全没有用", "几乎没有信息量", "有点用但很空", "信息量少",
                    "一般，缺观点", "有用但平淡", "有用，有一点观点",
                    "较好，有用有观点", "很好，有用有观点说人话", "优秀，看完就想收藏",
                ],
            }
        },
        # 附带待评文本：TypeSafe 的 state 是判断上下文，直接拼接
    }
    body = dict(payload)
    body["state"] = payload["state"] + "\n\n" + text[:2000]
    req = urllib.request.Request(
        base + "/v1/systemone",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return True, json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None)
        detail = ""
        if code is not None:
            try:
                detail = exc.read().decode("utf-8")[:160]  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
        return False, {"error": "HTTP %s %s" % (code or "?", detail or str(exc)[:120])}
# 订阅真源独立化：只读写工作台自己的 rss-store.json，运行时完全不碰 Obsidian
# 插件文件（2026-10-31 插件更新丢过一次 data.json）。全新环境需要种子数据时，
# 显式执行一次 python3 scripts/sync/sections.py --import-qiaomu。
RSS_DATA = os.path.join(DIR, "rss-store.json")
# 订阅兜底代理：本机 Surge 混合端口。部分源（如 lingyi.me）直连超时只有代理能通，
# 插件侧不一定走代理，服务端代抓时用它兜底；可用 WB_FEED_PROXY 覆盖。
FEED_PROXY = os.environ.get("WB_FEED_PROXY") or "http://127.0.0.1:7890"
SECTIONS_SCRIPT = os.path.join(DIR, "scripts", "sync", "sections.py")
PORT = 8787
TOKEN_FILE = os.path.expanduser("~/.config/daily-workbench/sync-token")
SYNC_SCRIPT = os.path.join(DIR, "scripts", "sync-now.sh")
SYNC_TIMEOUT = 300
SYNC_LOG = os.path.join(DIR, "sync-log.json")
SYNC_LOG_KEEP = 20

BACKUP_DIR = os.path.join(DIR, "backups")
KEEP_BACKUPS = 20
GUARD_MIN_EXISTING = 10
GUARD_MIN_RATIO = 0.5
MAX_BODY_BYTES = 20 * 1024 * 1024
CLIENT_BODY_BYTES = 64 * 1024
MAX_WRITING_REQUEST_BYTES = writing_store.MAX_CHAPTER_BYTES + 64 * 1024
WRITING_BACKUP_DIR = os.path.join(BACKUP_DIR, "obsidian-writing")
NTN = "/usr/local/bin/ntn"
DS_FOCUS = "238b66ad-8b35-81bc-8fb4-000b255bc3da"  # 今日番茄追踪表
DS_PROJECT = "264b66ad-8b35-806b-bb64-000bb903657c"  # 项目管理
DS_TASKS = "238b66ad-8b35-81f2-9a82-000b56e129f1"  # 待办数据库
DS_RECORDS = "f75bd828-b11d-42c6-9af8-a81debb43da7"  # 日常记录 - 日历视图
# ntn 走 macOS 系统代理（当前为 Surge）。若再注入 HTTP(S)_PROXY，
# 会和系统代理叠加，实测表现为间歇性 TLS 失败和
# “Failed to execute public API request”。这里主动清掉重复的代理环境变量。
NOTION_SUBPROCESS_ENV = dict(os.environ)
for _notion_proxy_key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy"):
    NOTION_SUBPROCESS_ENV.pop(_notion_proxy_key, None)
# launchd 默认 PATH 只有 /usr/bin:/bin:/usr/sbin:/sbin。ntn 的公开 API
# 调用还需要从 /usr/local/bin 查找配套命令；保留系统 PATH，只补安装目录。
NOTION_SUBPROCESS_ENV["PATH"] = (
    "/usr/local/bin:/opt/homebrew/bin:"
    + os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
)

REQUIRED_KEYS = ("projects", "earnings")

# RSS 订阅源管理的安全边界：
#  * 只写工作台自己的 rss-store.json，且写前先备份到项目 backups/
#  * 探查只发 HTTP GET、只解析 RSS/HTML 的 <link> 标签，不执行任何远程内容
#  * 收录/暂停/永久删除都要求与 /data.json 相同的 bearer token
#  * 日常移除订阅源使用 paused；永久删除必须显式传 purge=true
import urllib.request
import xml.etree.ElementTree as ET
import html as _html
import re as _re


def _rss_read():
    try:
        with open(RSS_DATA, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def _rss_write(d):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    bak = os.path.join(BACKUP_DIR, "rss-store.%s.bak" % stamp)
    try:
        shutil.copy2(RSS_DATA, bak)
    except FileNotFoundError:
        pass
    tmp = RSS_DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RSS_DATA)


def _regen_sections():
    try:
        subprocess.run([sys.executable, SECTIONS_SCRIPT, "--write"],
                       cwd=DIR, capture_output=True, text=True, timeout=60)
    except Exception:
        pass


def _fetch_text(url, limit=1_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": "DailyWorkbench/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read(limit).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        if isinstance(e, urllib.error.HTTPError):
            raise
        # 直连失败（超时/拒连/DNS 不通）时走本机代理重试一次
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": FEED_PROXY, "https": FEED_PROXY}))
        with opener.open(req, timeout=15) as r:
            return r.read(limit).decode("utf-8", "replace")


MAX_ARTICLE_BYTES = 4 * 1024 * 1024
MAX_ARTICLE_CHARS = 80_000
ARTICLE_TIMEOUT = 15
_ARTICLE_SKIP_TAGS = {
    "script", "style", "noscript", "svg", "nav", "footer", "aside", "form",
    "button", "input", "select", "textarea", "iframe", "canvas", "video", "audio",
}
_ARTICLE_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
_ARTICLE_BLOCK_TAGS = {"p", "h2", "h3", "blockquote", "li", "pre"}
_ARTICLE_CANDIDATE_RE = _re.compile(
    r"(?:^|[\s_-])(?:article|entry|post|markdown|prose|story)(?:$|[\s_-])",
    _re.I,
)
_ARTICLE_CONTENT_RE = _re.compile(
    r"(?:^|[\s_-])(?:article|entry|post|markdown|prose|story)"
    r"(?:[\s_-][a-z0-9]+)*[\s_-](?:body|content|text|detail)(?:$|[\s_-])",
    _re.I,
)
# 部分站点会把「追踪报道、相关推荐、点赞分享」等组件放进正文容器。
# 这里只按明确的组件标识跳过，文章自己的普通 ul/li 仍然保留。
_ARTICLE_SKIP_COMPONENT_RE = _re.compile(
    r"(?:^|\s)(?:"
    r"appinn-track|appinn-article-like|"
    r"related-posts?|related-articles?|"
    r"breadcrumbs?|post-navigation|post-nav|"
    r"author-card|author-box|"
    r"mylike|share|social-share"
    r")(?:\s|$)",
    _re.I,
)


def _validate_article_url(url):
    """只允许访问公网 HTTP(S)，阻止本机、私网、链路本地与元数据地址。"""
    raw = str(url or "").strip()
    if not raw:
        raise ValueError("缺少文章地址")
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("只支持 http / https 文章地址")
    if parsed.username or parsed.password:
        raise ValueError("文章地址不能包含用户名或密码")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise ValueError("文章地址缺少域名")
    if host == "localhost" or host.endswith(".local"):
        raise ValueError("不允许访问本机域名")
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise ValueError("文章地址端口不合法") from exc
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("文章域名无法解析") from exc
    if not infos:
        raise ValueError("文章域名没有可用地址")
    for info in infos:
        ip_text = str((info[4] or [""])[0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError as exc:
            raise ValueError("文章域名解析结果不合法") from exc
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ValueError("不允许访问内网或保留地址")
    return raw


class _SafeArticleRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_article_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _ArticleHTMLParser(html.parser.HTMLParser):
    """从第三方 HTML 中提取结构化正文块，不把远程标签原样带进页面 DOM。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._tag_stack = []
        self._skip_depth = 0
        self._candidate_stack = []
        self._candidates = []
        self._fallback_blocks = []
        self._active_blocks = []
        self._title_tag = []
        self._h1 = []
        self._meta = {}
        self._in_title = False
        self._in_h1 = False
        self._figure = None
        self._image_caption = None
        self._details_stack = []
        self._details_seq = 0
        self._inline_styles = []
        self._pre_depth = 0
        self._list_stack = []

    @staticmethod
    def _clean(text):
        return _re.sub(r"\s+", " ", str(text or "")).strip()

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = {str(k).lower(): str(v or "") for k, v in attrs}
        marker = " ".join((attrs.get("id", ""), attrs.get("class", ""))).strip()
        strength = 0
        if tag in ("article", "main"):
            strength = 1
        if marker and _ARTICLE_CANDIDATE_RE.search(marker):
            strength = max(strength, 2)
        if marker and _ARTICLE_CONTENT_RE.search(marker):
            # WordPress 等站点常用 article > .post-single-content 的两层结构。
            # 外层还包含相关文章、作者卡和侧栏；明确的内容容器应优先于 article。
            strength = 4
        is_candidate = strength > 0
        is_skip = (
            tag in _ARTICLE_SKIP_TAGS
            or bool(marker and _ARTICLE_SKIP_COMPONENT_RE.search(marker))
        )
        candidate = None
        if is_candidate:
            candidate = {
                "id": len(self._candidates),
                "tag": tag,
                "marker": marker,
                "strength": strength,
                "blocks": [],
            }
            self._candidates.append(candidate)
            self._candidate_stack.append(candidate)
        self._tag_stack.append({
            "tag": tag,
            "candidate_id": candidate["id"] if candidate else None,
            "skip": is_skip,
        })
        if tag in ("meta", "link", "time"):
            key = (attrs.get("property") or attrs.get("name") or "").lower()
            if key and attrs.get("content"):
                self._meta[key] = self._clean(attrs["content"])
            if tag == "time" and attrs.get("datetime"):
                self._meta.setdefault("article:published_time", self._clean(attrs["datetime"]))
        if tag in _ARTICLE_VOID_TAGS:
            if tag == "img" and self._skip_depth == 0 and self._candidate_stack:
                src = next((
                    attrs.get(key) for key in (
                        "data-src", "data-original", "data-lazy-src", "src")
                    if attrs.get(key)
                ), "")
                image = self._safe_image(src, attrs.get("alt", ""))
                if image:
                    if self._figure is not None:
                        self._figure["images"].append(image)
                    else:
                        capture_ids = tuple(c["id"] for c in self._candidate_stack)
                        self._append_record(dict(image, type="image"), capture_ids)
            elif tag == "hr" and self._skip_depth == 0 and self._candidate_stack:
                self._record_block("hr", "")
            return
        if tag == "title":
            self._in_title = True
        if tag == "h1":
            self._in_h1 = True
        if is_skip:
            self._skip_depth += 1
            return
        if tag == "figure" and self._candidate_stack:
            self._figure = {
                "images": [],
                "caption": [],
                "capture_ids": tuple(c["id"] for c in self._candidate_stack),
            }
        elif tag == "details" and self._skip_depth == 0:
            # 博客常用 <details> 折叠长附录；summary 是标题，内部块要归组
            self._details_seq += 1
            self._details_stack.append({"id": self._details_seq, "summary": []})
        elif tag == "summary" and self._details_stack:
            self._details_stack[-1]["collect"] = True
        elif tag == "figcaption" and self._figure is not None:
            self._image_caption = self._figure
        elif tag in ("ul", "ol"):
            self._list_stack.append({"tag": tag, "index": 0})
            if self._active_blocks and self._active_blocks[-1]["tag"] == "li":
                self._active_blocks[-1]["list_marker"] = (
                    "%d." % self._list_stack[-1]["index"] if tag == "ol" else "•")
        elif tag == "a":
            self._inline_styles.append({
                "type": "link",
                "href": self._safe_link(attrs.get("href", "")),
            })
        elif tag in ("strong", "b"):
            self._inline_styles.append({"type": "strong"})
        elif tag in ("em", "i"):
            self._inline_styles.append({"type": "em"})
        elif tag == "code" and self._pre_depth == 0:
            self._inline_styles.append({"type": "code"})
        elif tag == "code" and self._pre_depth > 0:
            if self._active_blocks and self._active_blocks[-1]["tag"] == "pre":
                self._active_blocks[-1]["language_source"] = attrs.get("class", "")
        if tag == "pre":
            self._pre_depth += 1
        if tag in _ARTICLE_BLOCK_TAGS and self._skip_depth == 0:
            marker = ""
            if tag == "li":
                current = self._list_stack[-1] if self._list_stack else {"tag": "ul", "index": 0}
                current["index"] += 1
                marker = ("%d." % current["index"]) if current["tag"] == "ol" else "•"
            block = {
                "tag": tag,
                "parts": [],
                "segments": [],
                "capture_ids": tuple(c["id"] for c in self._candidate_stack),
                "ordered": bool(self._list_stack and self._list_stack[-1]["tag"] == "ol"),
                "marker": marker,
                "language": "",
                "language_source": "",
            }
            if tag == "pre":
                block["language_source"] = attrs.get("class", "")
            self._active_blocks.append(block)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag == "h1":
            self._in_h1 = False
        if tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)
        if tag == "a":
            if self._inline_styles and self._inline_styles[-1]["type"] == "link":
                self._inline_styles.pop()
        elif tag in ("strong", "b"):
            if self._inline_styles and self._inline_styles[-1]["type"] == "strong":
                self._inline_styles.pop()
        elif tag in ("em", "i"):
            if self._inline_styles and self._inline_styles[-1]["type"] == "em":
                self._inline_styles.pop()
        elif tag == "code" and self._pre_depth == 0:
            if self._inline_styles and self._inline_styles[-1]["type"] == "code":
                self._inline_styles.pop()
        if (
            tag in _ARTICLE_BLOCK_TAGS
            and self._active_blocks
            and self._active_blocks[-1]["tag"] == tag
        ):
            block = self._active_blocks.pop()
            if tag == "pre":
                text = "".join(block["parts"]).strip("\n")
                block["segments"] = [{"type": "text", "text": text}]
                if self._active_blocks and self._active_blocks[-1]["tag"] == "pre":
                    self._active_blocks[-1]["language"] = self._pre_language(
                        self._active_blocks[-1].get("language_source", ""))
            else:
                text = self._clean("".join(block["parts"]))
            if text:
                record = {
                    "type": "quote" if tag == "blockquote" else tag,
                    "text": text,
                    "segments": self._clean_segments(block["segments"], text),
                }
                if tag == "li":
                    record["ordered"] = block["ordered"]
                    record["marker"] = block["marker"]
                if tag == "pre":
                    record["language"] = block["language"]
                self._append_record(record, block["capture_ids"])
        if tag == "figcaption" and self._image_caption is not None:
            self._image_caption = None
        if tag == "summary" and self._details_stack:
            self._details_stack[-1]["collect"] = False
        if tag == "details" and self._details_stack:
            self._details_stack.pop()
        if tag == "figure" and self._figure is not None:
            figure = self._figure
            self._figure = None
            caption = self._clean("".join(figure["caption"]))
            for index, image in enumerate(figure["images"]):
                image["caption"] = caption if index == 0 else ""
                self._append_record(dict(image, type="image"), figure["capture_ids"])
        if (
            tag in _ARTICLE_BLOCK_TAGS
            and self._active_blocks
            and self._active_blocks[-1]["tag"] == "li"
            and self._active_blocks[-1].get("list_marker")
        ):
            self._active_blocks[-1]["marker"] = self._active_blocks[-1].pop("list_marker")
        if tag in ("code", "pre"):
            if self._active_blocks and self._active_blocks[-1]["tag"] == "pre":
                self._active_blocks[-1]["language"] = self._pre_language(
                    self._active_blocks[-1].get("language_source", ""))
        if tag in ("ul", "ol") and self._list_stack:
            self._list_stack.pop()
        while self._tag_stack:
            entry = self._tag_stack.pop()
            if entry.get("skip"):
                self._skip_depth = max(0, self._skip_depth - 1)
            if entry.get("candidate_id") is not None:
                self._candidate_stack.pop()
            if entry.get("tag") == tag:
                break

    def handle_data(self, data):
        if self._in_title:
            self._title_tag.append(data)
        if self._in_h1:
            self._h1.append(data)
        if self._details_stack and self._details_stack[-1].get("collect"):
            self._details_stack[-1]["summary"].append(data)
        if self._image_caption is not None:
            self._image_caption["caption"].append(data)
        if self._active_blocks and self._skip_depth == 0:
            self._active_blocks[-1]["parts"].append(data)
            if data:
                style = self._inline_styles[-1] if self._inline_styles else None
                segment_type = style["type"] if style else "text"
                href = style.get("href", "") if style else ""
                if segment_type == "link" and not href:
                    segment_type = "text"
                segments = self._active_blocks[-1]["segments"]
                if (
                    segments
                    and segments[-1]["type"] == segment_type
                    and segments[-1].get("href", "") == href
                ):
                    segments[-1]["text"] += data
                else:
                    segment = {"type": segment_type, "text": data}
                    if href:
                        segment["href"] = href
                    segments.append(segment)

    def _append_record(self, record, capture_ids):
        if self._details_stack:
            top = self._details_stack[-1]
            record["fold"] = top["id"]
            record["fold_summary"] = self._clean("".join(top["summary"]))
        for candidate_id in capture_ids:
            self._candidates[candidate_id]["blocks"].append(record)
        if not capture_ids:
            self._fallback_blocks.append(record)

    def _record_block(self, kind, text):
        record = {"type": kind, "text": text, "segments": []}
        capture_ids = tuple(c["id"] for c in self._candidate_stack)
        self._append_record(record, capture_ids)

    def _safe_link(self, value):
        raw = _html.unescape(str(value or "")).strip()
        if not raw or raw.startswith("#"):
            return ""
        if raw.startswith("mailto:"):
            return raw if _re.match(r"^mailto:[^@\s]+@[^@\s]+", raw, _re.I) else ""
        if raw.startswith("//"):
            raw = "https:" + raw
        absolute = urljoin(getattr(self, "_source_url", ""), raw)
        parsed = urlparse(absolute)
        return absolute if parsed.scheme.lower() in ("http", "https") else ""

    def _safe_image(self, value, alt):
        raw = _html.unescape(str(value or "")).strip()
        if not raw:
            return None
        if raw.startswith("//"):
            raw = "https:" + raw
        absolute = urljoin(getattr(self, "_source_url", ""), raw)
        parsed = urlparse(absolute)
        if parsed.scheme.lower() not in ("http", "https"):
            return None
        return {"src": absolute, "alt": self._clean(alt), "caption": ""}

    @staticmethod
    def _pre_language(marker):
        match = _re.search(r"(?:^|\s)language-([a-z0-9_+-]+)", marker or "", _re.I)
        return match.group(1).lower() if match else ""

    def _clean_segments(self, segments, fallback):
        cleaned = []
        for segment in segments:
            text = segment.get("text", "")
            if not text:
                continue
            if self._pre_depth:
                text = text.replace("\r", "")
            item = {"type": segment.get("type", "text"), "text": text}
            if item["type"] == "link" and segment.get("href"):
                item["href"] = segment["href"]
            elif item["type"] == "link":
                item["type"] = "text"
            cleaned.append(item)
        return cleaned or ([{"type": "text", "text": fallback}] if fallback else [])

    def result(self, source_url):
        def score(candidate):
            blocks = candidate["blocks"]
            return (
                sum(len(b["text"]) for b in blocks if b["type"] in ("p", "quote", "li")),
                sum(1 for b in blocks if b["type"] in ("h2", "h3")),
                len(blocks),
            )

        nonempty = [c for c in self._candidates if c["blocks"]]
        strong = [c for c in nonempty if c["strength"] >= 4]
        if strong:
            blocks = max(strong, key=score)["blocks"]
        elif nonempty:
            blocks = max(nonempty, key=lambda c: (c["strength"], score(c)))["blocks"]
        else:
            blocks = self._fallback_blocks
        cleaned = []
        seen = set()
        chars = 0
        budget_exhausted = False

        def process(block, sink):
            nonlocal chars
            kind = block.get("type") if block.get("type") in (
                "p", "h2", "h3", "quote", "li", "pre", "image", "hr") else "p"
            text = (
                str(block.get("text") or "").strip("\n")
                if kind == "pre"
                else self._clean(block.get("text"))
            )
            if kind == "hr":
                sink.append({"type": "hr", "text": "", "segments": []})
                return True
            if kind == "image":
                image = {
                    "type": "image",
                    "text": "",
                    "segments": [],
                    "src": block["src"],
                    "alt": self._clean(block.get("alt")),
                    "caption": self._clean(block.get("caption")),
                }
                sink.append(image)
                return True
            minimum = 1 if kind == "pre" else (2 if kind in ("h2", "h3", "li") else 8)
            if len(text) < minimum:
                return True
            key = (kind, text)
            if key in seen:
                return True
            seen.add(key)
            if chars + len(text) > MAX_ARTICLE_CHARS:
                remaining = MAX_ARTICLE_CHARS - chars
                if remaining < 20:
                    return False
                text = text[:remaining]
                block = dict(block, segments=[{"type": "text", "text": text}])
            record = {"type": kind, "text": text, "segments": block.get("segments", [])}
            if kind == "li":
                record["ordered"] = bool(block.get("ordered"))
                record["marker"] = str(block.get("marker") or "•")
            if kind == "pre":
                record["language"] = str(block.get("language") or "")
            sink.append(record)
            chars += len(text)
            return len(cleaned) < 160

        for block in blocks:
            fold_id = block.get("fold")
            if fold_id is None:
                if not process(block, cleaned):
                    budget_exhausted = True
                    break
                continue
            # 连续同一折叠块的记录归为一组，前端按 <details> 渲染
            if (
                cleaned
                and cleaned[-1].get("type") == "details"
                and cleaned[-1].get("_fold") == fold_id
            ):
                sink = cleaned[-1]["blocks"]
            else:
                sink = []
                cleaned.append({
                    "type": "details",
                    "text": str(block.get("fold_summary") or "展开查看"),
                    "blocks": sink,
                    "_fold": fold_id,
                })
            if not process(block, sink):
                budget_exhausted = True
                break
        for record in cleaned:
            record.pop("_fold", None)
        cleaned[:] = [r for r in cleaned if not (r.get("type") == "details" and not r["blocks"])]
        if not cleaned:
            raise ValueError("没有从页面里识别出可读正文")
        title = (
            self._meta.get("og:title")
            or self._meta.get("twitter:title")
            or self._clean("".join(self._h1))
            or self._clean("".join(self._title_tag))
            or urlparse(source_url).hostname
            or "未命名文章"
        )
        site = (
            self._meta.get("og:site_name")
            or urlparse(source_url).hostname
            or ""
        )
        if site:
            for separator in (" - ", " | ", " — ", " – ", " _ "):
                suffix = separator + site
                if title.endswith(suffix):
                    title = title[:-len(suffix)].rstrip()
                    break
        return {
            "ok": True,
            "url": source_url,
            "title": title,
            "site": site,
            "publishedAt": self._meta.get("article:published_time") or "",
            "description": self._meta.get("og:description") or self._meta.get("description") or "",
            "blocks": cleaned,
            "chars": chars,
        }


def _extract_article(source, source_url):
    parser = _ArticleHTMLParser()
    parser._source_url = source_url
    parser.feed(source)
    parser.close()
    return parser.result(source_url)


def _decode_article_body(raw, content_type):
    match = _re.search(r"charset\s*=\s*[\"']?([^;\"'\s]+)", content_type or "", _re.I)
    encodings = [match.group(1)] if match else []
    encodings.extend(["utf-8", "gb18030", "big5"])
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _fetch_article(url):
    _validate_article_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "DailyWorkbench/1.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    })
    opener = urllib.request.build_opener(_SafeArticleRedirectHandler())
    with opener.open(req, timeout=ARTICLE_TIMEOUT) as resp:
        final_url = resp.geturl()
        _validate_article_url(final_url)
        ctype = resp.headers.get("Content-Type", "")
        if ctype and "html" not in ctype.lower():
            raise ValueError("目标地址不是 HTML 文章页")
        raw = resp.read(MAX_ARTICLE_BYTES + 1)
        if len(raw) > MAX_ARTICLE_BYTES:
            raise ValueError("文章页面超过 4 MB，已停止读取")
    return _extract_article(_decode_article_body(raw, ctype), final_url)


def _probe_rss(url):
    """返回候选 [{url,title}]；url 本身是 RSS 时直接命中。"""
    hits = []
    try:
        text = _fetch_text(url)
    except Exception:
        return hits
    # ① url 本身就是 RSS（xml 根是 rss/feed/rdf）
    try:
        root = ET.fromstring(text)
        tag = root.tag.rsplit("}", 1)[-1].lower()
        if tag in ("rss", "feed", "rdf"):
            ch = root.find(".//channel") or root
            t = ch.findtext(".//title") or (ch.findtext(
                "{http://www.w3.org/2005/Atom}title") if tag == "feed" else None)
            return [{"url": url, "title": (t or "").strip() or url}]
    except ET.ParseError:
        pass
    # ② HTML：<link rel=alternate type=...rss+xml...>
    for m in _re.finditer(
            r"<link[^>]+type=[\"'][^\"']*(?:rss|atom)[^\"']*"
            r"[\"'][^>]*>", text, _re.I):
        tag = m.group(0)
        hm = _re.search(r"href=[\"']([^\"']+)[\"']", tag, _re.I)
        if not hm:
            continue
        href = _html.unescape(hm.group(1))
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            from urllib.parse import urljoin
            href = urljoin(url, href)
        elif not href.startswith("http"):
            continue
        if not any(h["url"] == href for h in hits):
            tm = _re.search(r"title=[\"']([^\"']+)[\"']", tag, _re.I)
            hits.append({"url": href,
                         "title": _html.unescape(tm.group(1)) if tm else ""})
        if len(hits) >= 5:
            break
    # ③ 命中候选后逐个验证是否真 RSS（最多 5 个，每个 10s 超时内）
    verified = []
    for h in hits:
        try:
            t2 = _fetch_text(h["url"], 200_000)
            root = ET.fromstring(t2)
            tag = root.tag.rsplit("}", 1)[-1].lower()
            if tag in ("rss", "feed", "rdf"):
                ch = root.find(".//channel") or root
                t = ch.findtext(".//title") or (ch.findtext(
                    "{http://www.w3.org/2005/Atom}title")
                    if tag == "feed" else None)
                verified.append({"url": h["url"],
                                 "title": (t or h["title"] or "").strip()})
        except Exception:
            continue
    return verified


def _feed_group_id(d, group):
    """返回分组 ID；分组名不存在时创建它。"""
    groups = d.setdefault("subscriptionGroups", [])
    for g in groups:
        if (g.get("name") or "").strip() == group:
            return g.get("id") or ""
    gid = str(uuid.uuid4())
    groups.append({"id": gid, "name": group,
                   "order": max((int(g.get("order") or 0) for g in groups), default=-1) + 1})
    return gid


def _feed_reindex_meta(d):
    """让 sourceMeta.order 与 subscriptions 的真实顺序一致。"""
    meta = d.setdefault("sourceMeta", {})
    for i, s in enumerate(d.get("subscriptions") or []):
        sid = s.get("id")
        if not sid:
            continue
        cur = meta.get(sid)
        if not isinstance(cur, dict):
            cur = {}
        cur["order"] = i
        cur.setdefault("name", "")
        meta[sid] = cur


def _feed_url_key(url):
    """URL 去重键：忽略协议/域名大小写、末尾斜杠与 fragment。"""
    p = urlparse(str(url or "").strip())
    path = (p.path or "/").rstrip("/") or "/"
    key = "%s://%s%s" % (p.scheme.lower(), p.netloc.lower(), path)
    if p.query:
        key += "?" + p.query
    return key


def _feed_add(d, url, title, group):
    subs = d.setdefault("subscriptions", [])
    key = _feed_url_key(url)
    dup = next((s for s in subs if _feed_url_key(s.get("url")) == key), None)
    if dup:
        raise ValueError("这个 RSS 地址已在列表里：%s" % (dup.get("name") or url))
    sid = "local:" + __import__("hashlib").sha256(url.encode()).hexdigest()
    name = title or url.split("//")[-1].split("/")[0]
    subs.insert(0, {"id": sid, "url": url, "name": name,
                    "group": group or "", "entries": []})
    # 插件以 sourceMeta[sourceId].groupId 作为真实分组依据。
    # 只写 subscription.group 会让工作台和 Obsidian 各显示一套分组。
    if group:
        gid = _feed_group_id(d, group)
        d.setdefault("sourceMeta", {})[sid] = {
            "groupId": gid, "name": "", "order": 0}
    _feed_reindex_meta(d)
    return name


def _feed_pause(d, url, paused):
    for s in (d.get("subscriptions") or []):
        if s.get("url") == url:
            s["paused"] = bool(paused)
            return s
    return None


def _feed_delete(d, url):
    """永久删除订阅源及其历史；调用方必须先取得明确的 purge 确认。"""
    subs = d.get("subscriptions") or []
    target = next((s for s in subs if s.get("url") == url), None)
    if target is None:
        return None
    entry_ids = {e.get("id") for e in (target.get("entries") or []) if e.get("id")}
    d["subscriptions"] = [s for s in subs if s.get("url") != url]
    sid = target.get("id")
    if sid:
        (d.get("sourceMeta") or {}).pop(sid, None)
    if entry_ids:
        d["readIds"] = [x for x in (d.get("readIds") or []) if x not in entry_ids]
        for key in ("readAt", "favorites", "readLater", "savedArticles"):
            val = d.get(key)
            if isinstance(val, dict):
                for eid in entry_ids:
                    val.pop(eid, None)
            elif isinstance(val, list):
                d[key] = [x for x in val if x not in entry_ids]
    _feed_reindex_meta(d)
    return {"name": target.get("name") or url, "entries": len(entry_ids)}


def _strip_html(text, cap=2000):
    """RSS description 里常带 HTML，插件 summary 存纯文本。"""
    text = _re.sub(r"<[^>]+>", " ", text or "")
    text = _html.unescape(text)
    return _re.sub(r"\s+", " ", text).strip()[:cap]


def _parse_feed_time(raw):
    """RFC822（RSS）和 ISO8601（Atom）都接，解析不出返回 None。"""
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _rss_parse_entries(xml_text, sub):
    """把 RSS/Atom 文本解析成订阅条目结构（与 Qiaomu 导入数据同构）。"""
    root = ET.fromstring(xml_text)
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag not in ("rss", "feed", "rdf"):
        raise ValueError("返回内容不是 RSS/Atom")
    atom = "{http://www.w3.org/2005/Atom}"
    nodes = root.findall(".//item") or root.findall(".//" + atom + "entry")
    sid = sub.get("id") or ""
    name = sub.get("name") or ""
    out = []
    for node in nodes:
        title = (node.findtext("title") or node.findtext(atom + "title") or "").strip()
        link = ""
        for ln in node.findall("link") + node.findall(atom + "link"):
            link = (ln.text or "").strip() or (ln.get("href") or "").strip()
            if link:
                break
        if not title and not link:
            continue
        raw_pub = (node.findtext("pubDate") or node.findtext(atom + "published")
                   or node.findtext(atom + "updated") or "")
        pub_dt = _parse_feed_time(raw_pub)
        desc = (node.findtext("description") or node.findtext(atom + "content")
                or node.findtext(atom + "summary") or "")
        image = ""
        for enc in node.findall("enclosure"):
            if str(enc.get("type") or "").startswith("image"):
                image = enc.get("url") or ""
                break
        author = (node.findtext("author") or node.findtext(atom + "author")
                  or node.findtext(atom + "name") or "").strip()
        out.append({
            "id": "local-" + hashlib.sha256(
                ("%s|%s|%s" % (sid, link, title)).encode()).hexdigest(),
            "sourceId": sid,
            "origin": "local",
            "sourceName": name,
            "title": title,
            "link": link,
            "author": author,
            "published": email.utils.format_datetime(pub_dt) if pub_dt else "",
            "publishedTs": int(pub_dt.timestamp() * 1000) if pub_dt else None,
            "summary": _strip_html(desc),
            "image": image,
            "audio": None,
        })
    return out


def _feed_fetch_apply(d, sub):
    """代抓单个源并合并进插件数据；返回 (fetched, added)。失败抛异常。"""
    xml_text = _fetch_text(sub.get("url") or "", 2_000_000)
    fresh = _rss_parse_entries(xml_text, sub)
    entries = sub.get("entries") or []
    have = {(e.get("link") or e.get("id")) for e in entries}
    added = [e for e in fresh if (e.get("link") or e.get("id")) not in have]
    if added:
        sub["entries"] = (added + entries)[:120]
    now_ms = int(time.time() * 1000)
    sub["lastAttemptAt"] = now_ms
    sub["updatedAt"] = now_ms
    sub["error"] = ""
    return len(fresh), len(added)


def _feed_refresh(d, url):
    sub = next((s for s in (d.get("subscriptions") or [])
                if _feed_url_key(s.get("url")) == _feed_url_key(url)), None)
    if sub is None:
        raise ValueError("没有找到这个订阅源")
    fetched, added = _feed_fetch_apply(d, sub)
    return {"name": sub.get("name") or url, "fetched": fetched, "added": added}


def _refresh_stale_feeds():
    """同步前兜底：插件从没抓到过（lastAttemptAt 空 / 0 条）或上次抓取报错的源，
    由服务端代抓一次（直连失败自动走代理）。任何单源失败都不影响同步。"""
    d = _rss_read()
    if not isinstance(d, dict):
        return
    changed = False
    tried = 0
    six_hours = 6 * 3600 * 1000
    now_ms = int(time.time() * 1000)
    for sub in (d.get("subscriptions") or []):
        if sub.get("paused"):
            continue
        needs = ((not sub.get("lastAttemptAt")) and not sub.get("entries")) \
            or bool(sub.get("error")) \
            or (sub.get("lastErrorAt") and now_ms - int(sub["lastErrorAt"]) < six_hours
                and not sub.get("entries"))
        if not needs:
            continue
        if tried >= 5:  # 每次同步最多代抓 5 个源，避免网络超时拖慢整体同步
            break
        tried += 1
        changed = True
        try:
            _feed_fetch_apply(d, sub)
        except Exception as e:  # noqa: BLE001
            sub["lastAttemptAt"] = now_ms
            sub["lastErrorAt"] = now_ms
            sub["errorCount"] = int(sub.get("errorCount") or 0) + 1
            sub["error"] = str(e)[:300]
    if changed:
        _rss_write(d)

ALLOWED_HOSTS = {"127.0.0.1:8787", "localhost:8787"}

# 静态兜底的白名单。**只列 workbench.html 一个**。
#
# 为什么必须收窄（2026-09-30 实测）：原来末尾是无条件 `super().do_GET()`，
# 于是 /data.json 那条路由虽然要令牌，**同一份数据的几十个副本却不用**：
#     GET /backups/data.json.bak-20260930-000558  → 200，87110 字节
#     GET /notion-sync.md                         → 200，56255 字节
# 令牌只锁了正门、侧门开着 —— 而 backups/ 目录是写入护栏自己旋转出来的，
# 里面是完整的收入/支出记录。收窄成白名单之后，只有应用本体可读。
#
# ⚠️ workbench.local.html **绝不能**加进来：它把整份 data.json 与 sections.json
# 内嵌在文件内（正是它"离线可用"的原因），等于一份可直接下载的完整数据副本。
# 它该用 file:// 打开 —— make-local.py 的产物本来就是为这个场景做的。
#
# 页面自身 CSS/JS 全内联；这里额外放行两个固定品牌图片，不开目录访问。
STATIC_ALLOW = {"/workbench.html", "/assets/lingyi-logo.png", "/assets/favicon.png"}

# 同一时刻只允许一次同步，避免连点造成并发跑脚本。
_sync_lock = threading.Lock()
_data_write_lock = threading.Lock()
_writing_write_lock = threading.Lock()


def _rotate_backup():
    """Copy the current store into backups/ before it is overwritten."""
    if not os.path.exists(DATA):
        return None
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        dst = os.path.join(BACKUP_DIR, "data.json.%s.bak" % time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(DATA, dst)
    except OSError:
        return None
    try:
        kept = sorted(glob.glob(os.path.join(BACKUP_DIR, "data.json.*.bak")))
        for stale in kept[:-KEEP_BACKUPS]:
            os.remove(stale)
    except OSError:
        pass
    return dst


def _read_current():
    try:
        with open(DATA, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


_CLIENT_SELECT_PROPS = {
    "customerType": "客户类型",
    "status": "客户状态",
    "relationSource": "关系来源",
}
_CLIENT_DATE_PROPS = {
    "firstContact": "首次接触",
    "lastContact": "上次联系",
    "nextFollowUp": "下次跟进",
}
_CLIENT_TEXT_PROPS = {
    "note": "备注",
    "wechatId": "微信ID",
    "coordinate": "坐标",
    "todo": "待办",
    "benefits": "已送福利",
    "address": "快递地址",
    "channel": "渠道",
    "knownChannel": "认识渠道",
}
_CLIENT_WRITABLE = set(_CLIENT_SELECT_PROPS) | set(_CLIENT_DATE_PROPS) | set(_CLIENT_TEXT_PROPS) | {"name"}
_CLIENT_LOCAL_KEYS = {key: key for key in _CLIENT_WRITABLE}

_PROJECT_WRITABLE = {
    "name", "status", "priority", "projectType", "tags",
    "start", "due", "doneDate", "sprint", "sprintStart", "sprintEnd",
    "estHours", "actHours", "note", "relatedLink",
    "parentNotionId", "relatedTaskIds", "sourceRecordIds", "ownerIds",
}
_PROJECT_LOCAL_KEYS = {
    "name": "name",
    "status": "status",
    "priority": "priority",
    "projectType": "projectType",
    "tags": "tags",
    "start": "start",
    "due": "due",
    "doneDate": "doneDate",
    "sprint": "sprint",
    "sprintStart": "sprintStart",
    "sprintEnd": "sprintEnd",
    "estHours": "estHours",
    "actHours": "actHours",
    "note": "note",
    "relatedLink": "relatedLink",
    "parentNotionId": "parentNotionId",
    "relatedTaskIds": "relatedTaskIds",
    "sourceRecordIds": "sourceRecordIds",
    "ownerIds": "ownerIds",
}
_PROJECT_SCHEMA_CACHE = {"at": 0.0, "schema": None}
_PROJECT_RELATION_CACHE = {}
_PROJECT_RELATION_CACHE_TTL = 300


def _rich_text_value(value):
    text = "" if value is None else str(value).strip()
    if not text:
        return []
    if len(text) > 8000:
        raise ValueError("单个文本字段最多 8000 字")
    return [{"type": "text", "text": {"content": text[i:i + 2000]}}
            for i in range(0, len(text), 2000)]


def _ntn_api_call(cmd, body=None, timeout=45, attempts=3):
    """执行 ntn 公开 API 调用，并重试代理侧的瞬时失败。

    Surge/上游偶发返回 ``Failed to execute public API request``。该错误在
    同一请求稍后重试即可恢复；认证、参数和权限错误不重试，避免掩盖真问题。
    Notion 的 PATCH 是设置属性而不是追加记录，重复发送相同属性结果一致。
    """
    last_error = None
    for attempt in range(max(1, int(attempts))):
        try:
            proc = subprocess.run(
                cmd,
                input=body,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=NOTION_SUBPROCESS_ENV,
            )
        except subprocess.TimeoutExpired:
            # 超时不自动重试：一次已经等了完整超时，再等会让页面长期无响应。
            raise
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode == 0 and out:
            return out
        message = err or out or "ntn API 请求失败"
        last_error = RuntimeError(message[-1200:])
        transient = "failed to execute public api request" in message.lower()
        if not transient or attempt + 1 >= attempts:
            raise last_error
        time.sleep(1.0 * (attempt + 1))
    raise last_error or RuntimeError("ntn API 请求失败")


def _build_client_properties(fields):
    """把白名单字段转换成 Notion page properties。

    页面只能改客户库里的资料字段，不允许借这个接口改收益、项目或任意其它页面。
    """
    if not isinstance(fields, dict):
        raise ValueError("fields 必须是对象")
    unknown = sorted(set(fields) - _CLIENT_WRITABLE)
    if unknown:
        raise ValueError("不支持回写的字段：" + ", ".join(unknown))
    props = {}
    if "name" in fields:
        name = str(fields.get("name") or "").strip()
        if not name:
            raise ValueError("客户名称不能为空")
        if len(name) > 200:
            raise ValueError("客户名称最多 200 字")
        props["Name"] = {"title": _rich_text_value(name)}
    for key, prop_name in _CLIENT_SELECT_PROPS.items():
        if key not in fields:
            continue
        value = str(fields.get(key) or "").strip()
        if len(value) > 100:
            raise ValueError("%s 选项过长" % prop_name)
        props[prop_name] = {"select": {"name": value} if value else None}
    for key, prop_name in _CLIENT_DATE_PROPS.items():
        if key not in fields:
            continue
        value = str(fields.get(key) or "").strip()
        if value:
            try:
                datetime.date.fromisoformat(value)
            except ValueError:
                raise ValueError("%s 必须是 YYYY-MM-DD" % prop_name)
        props[prop_name] = {"date": {"start": value} if value else None}
    for key, prop_name in _CLIENT_TEXT_PROPS.items():
        if key in fields:
            props[prop_name] = {"rich_text": _rich_text_value(fields.get(key))}
    if not props:
        raise ValueError("没有可写入的字段")
    return props


def _notion_update_page(page_id, properties):
    """经 ntn CLI 更新 Notion 页面；令牌仍由 ntn 自己从系统钥匙串读取。"""
    cmd = [NTN, "api", "/v1/pages/%s" % page_id, "-X", "PATCH"]
    out = _ntn_api_call(
        cmd,
        json.dumps({"properties": properties}, ensure_ascii=False),
        timeout=45,
    )
    return json.loads(out)


def _project_schema_from_current():
    """用最近一次 Notion 同步保存的选项构造一套最小 schema。

    回写时不必再额外 GET 一次数据库 schema；同步脚本已经把真实选项写进
    ``notionMeta.projectSchema``。属性和类型是工作台白名单的一部分，选项则
    永远以最近同步结果为准。
    """
    data = _read_current() or {}
    meta = data.get("notionMeta") or {}
    options = meta.get("projectSchema") or {}
    required = ("status", "priority", "type", "sprint", "tags")
    if not all(isinstance(options.get(key), list) for key in required):
        return None

    def option_prop(typ, key):
        return {
            "type": typ,
            typ: {"options": [{"name": str(name)} for name in options.get(key, [])
                               if str(name or "").strip()]},
        }

    def typed_prop(typ):
        return {"type": typ, typ: {}}

    return {"properties": {
        "项目名称": typed_prop("title"),
        "状态": option_prop("status", "status"),
        "优先级": option_prop("select", "priority"),
        "类型": option_prop("select", "type"),
        "冲刺": option_prop("select", "sprint"),
        "标签": option_prop("multi_select", "tags"),
        "开始日期": typed_prop("date"),
        "截止日期": typed_prop("date"),
        "完成日期": typed_prop("date"),
        "冲刺开始日期": typed_prop("date"),
        "冲刺结束日期": typed_prop("date"),
        "预计工时": typed_prop("number"),
        "实际工时": typed_prop("number"),
        "备注": typed_prop("rich_text"),
        "相关链接": typed_prop("url"),
        "上级 项目": typed_prop("relation"),
        "关联任务": typed_prop("relation"),
        "来源记录": typed_prop("relation"),
        "负责人": typed_prop("people"),
    }}


def _notion_project_schema():
    """读取项目管理 schema：优先用同步快照，缺失时再现场拉取并短时缓存。"""
    now = time.time()
    cached = _PROJECT_SCHEMA_CACHE.get("schema")
    if cached and now - float(_PROJECT_SCHEMA_CACHE.get("at") or 0) < 300:
        return cached
    schema = _project_schema_from_current()
    if schema:
        _PROJECT_SCHEMA_CACHE["at"] = now
        _PROJECT_SCHEMA_CACHE["schema"] = schema
        return schema
    cmd = [NTN, "api", "/v1/data_sources/%s" % DS_PROJECT, "-X", "GET"]
    out = _ntn_api_call(cmd, timeout=45)
    try:
        schema = json.loads(out)
    except ValueError:
        raise RuntimeError("项目管理 schema 返回的不是 JSON")
    if not isinstance(schema.get("properties"), dict):
        raise RuntimeError("项目管理 schema 缺少 properties")
    _PROJECT_SCHEMA_CACHE["at"] = now
    _PROJECT_SCHEMA_CACHE["schema"] = schema
    return schema


def _project_relation_kind(kind):
    """返回关系选择器允许查询的 Notion 数据源配置。"""
    cfg = {
        "task": {"data_source": DS_TASKS, "title": "任务名称", "label": "关联任务"},
        "record": {"data_source": DS_RECORDS, "title": "记录标题", "label": "来源记录"},
    }.get(str(kind or "").strip().lower())
    if not cfg:
        raise ValueError("关系类型必须是 task 或 record")
    return cfg


def _relation_option_from_page(page):
    """把 Notion 页面压缩成关系选择器需要的安全摘要。"""
    if not isinstance(page, dict):
        return None
    page_id = str(page.get("id") or "").strip().lower()
    if not page_id:
        return None
    title = ""
    status = ""
    date = ""
    for prop in (page.get("properties") or {}).values():
        if not isinstance(prop, dict):
            continue
        typ = prop.get("type")
        if typ == "title" and not title:
            title = "".join(str(x.get("plain_text") or "")
                            for x in (prop.get("title") or []))
        elif typ == "status" and not status:
            status = str((prop.get("status") or {}).get("name") or "")
        elif typ == "select" and not status:
            status = str((prop.get("select") or {}).get("name") or "")
        elif typ == "date" and not date:
            date = str((prop.get("date") or {}).get("start") or "")
    return {
        "id": page_id,
        "title": title.strip() or "未命名页面",
        "status": status,
        "date": date,
    }


def _project_relation_lookup(kind, ids):
    """按页面 ID 读取关系标签，并缓存五分钟避免重复请求。"""
    cfg = _project_relation_kind(kind)
    now = time.time()
    out = []
    for raw_id in ids or []:
        page_id = str(raw_id or "").strip().lower()
        if not _re.fullmatch(
                r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}",
                page_id):
            raise ValueError("%s 含无效页面 ID" % cfg["label"])
        cache_key = (kind, page_id)
        cached = _PROJECT_RELATION_CACHE.get(cache_key)
        if cached and now - float(cached.get("at") or 0) < _PROJECT_RELATION_CACHE_TTL:
            out.append(cached["item"])
            continue
        cmd = [NTN, "api", "/v1/pages/%s" % page_id, "-X", "GET"]
        page = json.loads(_ntn_api_call(cmd, timeout=45))
        item = _relation_option_from_page(page) or {
            "id": page_id, "title": "未命名页面", "status": "", "date": "",
        }
        _PROJECT_RELATION_CACHE[cache_key] = {"at": now, "item": item}
        out.append(item)
    return out


def _project_relation_search(kind, query="", limit=20):
    """按标题搜索 Notion 关系数据源；返回摘要而不是整页原始属性。"""
    cfg = _project_relation_kind(kind)
    q = str(query or "").strip()
    if len(q) > 100:
        raise ValueError("搜索词最多 100 字")
    try:
        size = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        size = 20
    body = {
        "page_size": size,
        "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
    }
    if q:
        body["filter"] = {
            "property": cfg["title"],
            "title": {"contains": q},
        }
    cmd = [NTN, "api", "/v1/data_sources/%s/query" % cfg["data_source"],
           "-X", "POST"]
    page = json.loads(_ntn_api_call(
        cmd, json.dumps(body, ensure_ascii=False), timeout=45))
    return [item for item in (
        _relation_option_from_page(row) for row in (page.get("results") or [])
    ) if item]


def _project_option_names(schema, prop_name, expected_type):
    prop = (schema.get("properties") or {}).get(prop_name)
    if not isinstance(prop, dict) or prop.get("type") != expected_type:
        raise ValueError("Notion 项目库缺少可写属性：%s" % prop_name)
    section = prop.get(expected_type) or {}
    return [str(o.get("name") or "") for o in (section.get("options") or [])
            if o.get("name")]


def _project_relation_ids(value, label):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("%s 必须是页面 ID 数组" % label)
    out = []
    for item in value:
        page_id = str(item or "").strip().lower()
        if not _re.fullmatch(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", page_id):
            raise ValueError("%s 含无效页面 ID" % label)
        out.append(page_id)
    return out


def _build_project_properties(fields, schema=None):
    """把项目白名单字段转换成 Notion page properties。

    只允许写 Notion 中真实存在且可编辑的属性。完成百分比、子任务数量、
    子级项目等 rollup/派生值永远不接受前端写入。
    """
    if not isinstance(fields, dict):
        raise ValueError("fields 必须是对象")
    unknown = sorted(set(fields) - _PROJECT_WRITABLE)
    if unknown:
        raise ValueError("不支持回写的字段：" + ", ".join(unknown))
    schema = schema or _notion_project_schema()
    props = schema.get("properties") or {}
    out = {}

    def require(name, typ):
        prop = props.get(name)
        if not isinstance(prop, dict) or prop.get("type") != typ:
            raise ValueError("Notion 项目库缺少可写属性：%s" % name)
        return prop

    if "name" in fields:
        require("项目名称", "title")
        name = str(fields.get("name") or "").strip()
        if not name:
            raise ValueError("项目名称不能为空")
        if len(name) > 200:
            raise ValueError("项目名称最多 200 字")
        out["项目名称"] = {"title": _rich_text_value(name)}

    select_fields = {
        "status": ("状态", "status"),
        "priority": ("优先级", "select"),
        "projectType": ("类型", "select"),
        "sprint": ("冲刺", "select"),
    }
    for key, (prop_name, typ) in select_fields.items():
        if key not in fields:
            continue
        value = str(fields.get(key) or "").strip()
        require(prop_name, typ)
        allowed = _project_option_names(schema, prop_name, typ)
        if value and value not in allowed:
            raise ValueError("%s 不是 Notion 中的有效选项：%s" % (prop_name, value))
        out[prop_name] = {typ: {"name": value} if value else None}

    if "tags" in fields:
        require("标签", "multi_select")
        raw = fields.get("tags")
        if raw is None:
            values = []
        elif isinstance(raw, list):
            values = [str(x or "").strip() for x in raw]
        else:
            raise ValueError("标签必须是数组")
        values = [x for x in values if x]
        if len(values) > 20:
            raise ValueError("标签最多 20 个")
        if any(len(x) > 100 for x in values):
            raise ValueError("单个标签最多 100 字")
        out["标签"] = {"multi_select": [{"name": x} for x in values]}

    date_fields = {
        "start": "开始日期",
        "due": "截止日期",
        "doneDate": "完成日期",
        "sprintStart": "冲刺开始日期",
        "sprintEnd": "冲刺结束日期",
    }
    for key, prop_name in date_fields.items():
        if key not in fields:
            continue
        require(prop_name, "date")
        value = str(fields.get(key) or "").strip()
        if value:
            try:
                datetime.date.fromisoformat(value)
            except ValueError:
                raise ValueError("%s 必须是 YYYY-MM-DD" % prop_name)
        out[prop_name] = {"date": {"start": value} if value else None}

    for key, prop_name in (("estHours", "预计工时"), ("actHours", "实际工时")):
        if key not in fields:
            continue
        require(prop_name, "number")
        raw = fields.get(key)
        if raw in (None, ""):
            value = None
        else:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError("%s 必须是数字" % prop_name)
            if not math.isfinite(value) or value < 0 or value > 100000:
                raise ValueError("%s 必须在 0–100000 之间" % prop_name)
        out[prop_name] = {"number": value}

    if "note" in fields:
        require("备注", "rich_text")
        out["备注"] = {"rich_text": _rich_text_value(fields.get("note"))}

    if "relatedLink" in fields:
        require("相关链接", "url")
        value = str(fields.get("relatedLink") or "").strip()
        if value and not _re.match(r"^https?://", value, _re.I):
            raise ValueError("相关链接必须是 http(s) URL")
        out["相关链接"] = {"url": value or None}

    if "parentNotionId" in fields:
        require("上级 项目", "relation")
        parent = str(fields.get("parentNotionId") or "").strip().lower()
        parent_ids = _project_relation_ids([parent] if parent else [], "上级项目")
        out["上级 项目"] = {"relation": [{"id": x} for x in parent_ids]}

    for key, prop_name in (("relatedTaskIds", "关联任务"),
                           ("sourceRecordIds", "来源记录"),
                           ("ownerIds", "负责人")):
        if key not in fields:
            continue
        expected = "people" if key == "ownerIds" else "relation"
        require(prop_name, expected)
        ids = _project_relation_ids(fields.get(key), prop_name)
        out[prop_name] = {expected: [{"id": x} for x in ids]}

    if not out:
        raise ValueError("没有可写入的字段")
    return out


def _build_focus_properties(session):
    """把一条已完成的本地专注记录映射到「今日番茄追踪表」白名单字段。

    只接受工作台自己生成的结构，不接受前端任意 Notion 属性。放弃/未完成的
    计时不会回写，避免把中断记录伪装成完成番茄。
    """
    if not isinstance(session, dict):
        raise ValueError("专注记录格式不正确")
    if session.get("status") != "completed":
        raise ValueError("只有已完成的专注记录可以回写")
    project = str(session.get("projectName") or "自由专注").strip()[:200]
    date = str(session.get("date") or "").strip()
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise ValueError("专注日期必须是 YYYY-MM-DD")
    try:
        minutes = int(round(float(session.get("elapsedSeconds") or 0) / 60))
    except (TypeError, ValueError):
        minutes = 0
    if minutes <= 0 or minutes > 240:
        raise ValueError("专注时长必须在 1–240 分钟之间")
    title = ("%s · %d 分钟专注" % (project, minutes))[:200]
    note = str(session.get("noteLine") or "").strip()
    if not note:
        note = "%s · %d 分钟 · 工作台专注记录" % (project, minutes)
    return {
        "任务名称": {"title": _rich_text_value(title)},
        "备注": {"rich_text": _rich_text_value(note[:2000])},
        "完成": {"checkbox": True},
        "实际番茄": {"number": 1},
        "干扰记录": {"rich_text": _rich_text_value("无")},
        "所属项目": {"rich_text": _rich_text_value(project)},
        "日期": {"date": {"start": date}},
        "状态": {"status": {"name": "完成"}},
        "预估番茄": {"number": 1},
    }


def _notion_create_focus_page(properties):
    """在 Notion「今日番茄追踪表」创建一条页面；凭据仍由 ntn 钥匙串读取。"""
    cmd = [NTN, "api", "/v1/pages", "-X", "POST"]
    payload = {
        "parent": {"type": "data_source_id", "data_source_id": DS_FOCUS},
        "properties": properties,
    }
    out = _ntn_api_call(
        cmd,
        json.dumps(payload, ensure_ascii=False),
        timeout=45,
    )
    return json.loads(out)


def _write_focus_update(session_id, notion_page):
    """Notion 创建成功后把页面标识写回对应本地专注记录。"""
    with _data_write_lock:
        data = _read_current()
        if not data:
            raise RuntimeError("data.json 不可读")
        session = next((s for s in (data.get("focusSessions") or [])
                        if str(s.get("id") or "") == session_id), None)
        if not session:
            raise LookupError("本地专注记录不存在")
        now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        session["notionPageId"] = notion_page.get("id")
        session["notionUrl"] = notion_page.get("url") or ""
        session["notionPushedAt"] = now
        meta = data.get("notionMeta")
        if isinstance(meta, dict):
            meta["focusDs"] = DS_FOCUS
            meta["focusDsName"] = "今日番茄追踪表"
            meta["lastFocusWritebackAt"] = now
        _rotate_backup()
        tmp = "%s.tmp.%d" % (DATA, threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA)
        return session


def _client_money_note(client):
    profit = client.get("profit")
    if isinstance(profit, (int, float)) and profit != 0:
        return "利润 ¥%s" % format(round(profit, 2), ",.2f").rstrip("0").rstrip(".")
    income = client.get("income")
    if isinstance(income, (int, float)) and income != 0:
        return "收入 ¥%s" % format(round(income, 2), ",.2f").rstrip("0").rstrip(".")
    return ""


def _write_client_update(page_id, fields):
    """Notion 写入成功后同步本地 data.json，页面不用等下一次全量同步。"""
    with _data_write_lock:
        data = _read_current()
        if not data:
            raise RuntimeError("data.json 不可读")
        client = next((c for c in (data.get("clients") or [])
                       if str(c.get("notionId") or "") == page_id), None)
        if not client:
            raise LookupError("本地客户缓存里找不到这个页面")
        for src, dst in _CLIENT_LOCAL_KEYS.items():
            if src == "note" or src not in fields:
                continue
            client[dst] = "" if fields.get(src) is None else str(fields.get(src))
        if "note" in fields:
            memo = "" if fields.get("note") is None else str(fields.get("note")).strip()
            client["memo"] = memo
            client["note"] = memo or _client_money_note(client)
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        client["updatedAt"] = now
        client["writable"] = True
        meta = data.get("notionMeta")
        if isinstance(meta, dict):
            meta["lastWritebackAt"] = now
        _rotate_backup()
        tmp = "%s.tmp.%d" % (DATA, threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA)
        return client


def _write_project_update(page_id, fields):
    """Notion 写入成功后同步本地项目卡，避免页面等下一次全量同步。"""
    with _data_write_lock:
        data = _read_current()
        if not data:
            raise RuntimeError("data.json 不可读")
        projects = data.get("projects") or []
        project = next((p for p in projects
                        if str(p.get("notionId") or "").lower() == page_id), None)
        if not project:
            raise LookupError("本地项目缓存里找不到这个页面")
        for src, dst in _PROJECT_LOCAL_KEYS.items():
            if src not in fields or src == "parentNotionId":
                continue
            value = fields.get(src)
            if src == "tags":
                project["tags"] = [str(x) for x in (value or []) if str(x or "").strip()]
            elif src in ("estHours", "actHours"):
                project[dst] = None if value in (None, "") else float(value)
            elif src == "doneDate":
                project[dst] = str(value or "").strip() or None
            elif src in ("ownerIds", "relatedTaskIds", "sourceRecordIds"):
                project[dst] = [str(x) for x in (value or []) if str(x or "").strip()]
            else:
                project[dst] = "" if value is None else str(value).strip()
        if "parentNotionId" in fields:
            parent_nid = str(fields.get("parentNotionId") or "").strip().lower()
            parent = next((p for p in projects
                           if str(p.get("notionId") or "").lower() == parent_nid), None) if parent_nid else None
            if parent_nid and not parent:
                raise LookupError("上级项目不在当前本地缓存里")
            project["parent"] = parent.get("id") if parent else None
            project["parentNotionId"] = parent_nid or None
        project["category"] = ((project.get("tags") or [None])[0]
                               or project.get("projectType") or "")
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        project["updatedAt"] = now
        project["writable"] = True
        meta = data.get("notionMeta")
        if isinstance(meta, dict):
            meta["lastProjectWritebackAt"] = now
        _rotate_backup()
        tmp = "%s.tmp.%d" % (DATA, threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA)
        return project


def _sync_log_append(entry):
    """每次同步结束追加一条记录，只留最近 SYNC_LOG_KEEP 条。
    日志是本地诊断信息，不属于需备份的用户数据，写入失败不影响同步结果本身。"""
    try:
        logs = []
        if os.path.exists(SYNC_LOG):
            try:
                with open(SYNC_LOG, encoding="utf-8") as f:
                    logs = json.load(f)
                if not isinstance(logs, list):
                    logs = []
            except Exception:
                logs = []
        logs.append(entry)
        tmp = SYNC_LOG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(logs[-SYNC_LOG_KEEP:], f, ensure_ascii=False, indent=2)
        os.replace(tmp, SYNC_LOG)
    except OSError:
        pass


def _run_sync_process(timeout=SYNC_TIMEOUT):
    """运行同步脚本，并在超时后清理整个进程组。

    同步脚本会再拉起 Python 同步任务；只杀掉 bash 本身会让 Notion 拉取等
    后代进程继续占用网络和文件。独立进程组保证超时不会留下后台残骸。
    """
    proc = subprocess.Popen(
        ["bash", SYNC_SCRIPT],
        cwd=DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        raise subprocess.TimeoutExpired(
            proc.args,
            timeout,
            output=stdout,
            stderr=stderr,
        )
    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode,
        stdout,
        stderr,
    )


def _sync_timeout_entry(started, trigger):
    """构造同步超时记录；独立函数便于验证超时一定会在历史里留痕。"""
    state = _read_current() or {}
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ok": False,
        "code": None,
        "elapsed": round(time.time() - started, 1),
        "earnings": len(state.get("earnings", [])),
        "projects": len(state.get("projects", [])),
        "error": "同步超时（%ds），已终止全部子进程" % SYNC_TIMEOUT,
        "trigger": trigger if trigger in ("manual", "auto") else "manual",
        "steps": [],
    }


def _parse_sync_step_timings(out):
    """解析 sync-now.sh 的分阶段耗时标记。

    标记只用于本机诊断，格式固定为 ``__WB_STEP__\\t步骤名\\t秒``。
    同名标记以最后一次为准；旧脚本没有标记时返回空字典，不伪造耗时。
    """
    import re as _re

    timings = {}
    for match in _re.finditer(
            r"__WB_STEP__\t([^\t\r\n]+)\t([0-9]+(?:\.[0-9]+)?)", out or ""):
        name, raw = match.groups()
        try:
            elapsed = round(float(raw), 1)
        except ValueError:
            continue
        if math.isfinite(elapsed) and elapsed >= 0:
            timings[name] = elapsed
    return timings


def _strip_sync_step_timings(out):
    """从给用户看的脚本输出里移除机器标记，避免失败提示出现内部协议行。"""
    return "\n".join(
        line for line in (out or "").splitlines()
        if not line.startswith("__WB_STEP__\t")
    ).strip()


def _build_sync_steps(code, out, state):
    """把一次同步拆成 Linux 启动日志式的步骤行（✓/✗ + 一句话结果）。
    步骤顺序与 sync-now.sh 一致；失败时只列到挂掉的那一步 —— 后面的没跑。"""
    import re as _re

    timings = _parse_sync_step_timings(out)

    def finish(rows):
        for row in rows:
            if row["name"] in timings:
                row["elapsed"] = timings[row["name"]]
        return rows

    def rss_subs():
        try:
            d = _rss_read()
            subs = d.get("subscriptions") if isinstance(d, dict) else None
            return len(subs) if isinstance(subs, list) else None
        except Exception:
            return None

    def web_line():
        try:
            with open(WEB_PRESENCE, encoding="utf-8") as f:
                wp = json.load(f)
            sites = wp.get("sites") if isinstance(wp, dict) else None
            if not isinstance(sites, list) or not sites:
                return None
            bad = [s for s in sites if isinstance(s, dict)
                   and s.get("state") not in ("ok", "slow", "checking")]
            total = len(sites)
            slow = [s for s in sites if isinstance(s, dict) and s.get("state") == "slow"]
            line = "%d 个站点正常" % (total - len(bad)) if not bad \
                else "%d 个站点中 %d 个异常" % (total, len(bad))
            if slow:
                line += " · %d 个偏慢（%s）" % (
                    len(slow), "、".join(str(s.get("name") or s.get("id") or "?") for s in slow))
            return line
        except Exception:
            return None

    fail_at = {2: "ntn", 3: "ntn", 4: "notion", 6: "sections", 7: "social", 5: "local"}
    fail_step = fail_at.get(code) if code else None
    ok = code == 0
    steps = []
    steps.append({"name": "前置检查", "ok": fail_step not in ("ntn",),
                  "detail": "ntn 存在 · Surge 通路 401" if fail_step != "ntn" else "失败，见错误信息"})
    if fail_step == "ntn":
        return finish(steps)

    diff = ""
    m = _re.search(r"原 \((\d+), (\d+)\) → 新 \((\d+), (\d+)\)", out)
    if m:
        oe, op, ne, np_ = map(int, m.groups())
        parts = []
        if ne != oe:
            parts.append("收益 %+d 条" % (ne - oe))
        if np_ != op:
            parts.append("项目 %+d 条" % (np_ - op))
        diff = (" · 变化：" + " · ".join(parts)) if parts else " · 数据无变化"
    drafts = len([e for e in (state.get("earnings") or [])
                  if isinstance(e, dict) and e.get("source") == "local"]) \
        if isinstance(state, dict) else None
    draft_txt = " · 本地草稿 %d 篇待整理" % drafts if drafts else ""
    ne_count = len(state.get("earnings") or []) if isinstance(state, dict) else 0
    np_count = len(state.get("projects") or []) if isinstance(state, dict) else 0
    steps.append({"name": "Notion 拉取", "ok": fail_step != "notion",
                  "detail": "收益 %d 条 / 项目 %d 条%s%s" % (
                      ne_count, np_count, diff, draft_txt)
                  if fail_step != "notion" else "失败，data.json 未修改"})
    if fail_step == "notion":
        return finish(steps)

    n = rss_subs()
    steps.append({"name": "资讯快照", "ok": fail_step != "sections",
                  "detail": ("订阅源 %d 个 · 已重建" % n) if n else "已重建"})
    if fail_step == "sections":
        return finish(steps)

    steps.append({"name": "自媒体快照", "ok": fail_step != "social",
                  "detail": "已刷新" if fail_step != "social" else "失败，保留上一份快照"})
    if fail_step == "social":
        return finish(steps)

    w = web_line()
    steps.append({"name": "网站监控", "ok": True,
                  "detail": w or "已刷新（快照缺失）"})
    steps.append({"name": "离线兜底页", "ok": ok,
                  "detail": "已重建" if ok else "失败"})
    return finish(steps)



def _load_token():
    """Read the sync token; generate one on first run (0600)."""
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            t = f.read().strip()
            if t:
                return t
    except FileNotFoundError:
        pass
    t = secrets.token_hex(16)
    os.makedirs(os.path.dirname(TOKEN_FILE), mode=0o700, exist_ok=True)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(t + "\n")
    return t


TOKEN = _load_token()


class Handler(http.server.SimpleHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        # workbench.html 自身就是应用入口，没有带哈希的资源文件名可供换缓存。
        # 若沿用 SimpleHTTPRequestHandler 默认的 Last-Modified 协商，Safari 可能继续
        # 展示内存或往返缓存中的旧页面，看起来像代码没有生效。入口 HTML 明确禁止
        # 存储，品牌图片等静态资源仍可正常缓存。
        if urlparse(self.path).path == "/workbench.html":
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def _authorized(self):
        qs = parse_qs(urlparse(self.path).query)
        supplied = (qs.get("token") or [""])[0]
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            supplied = auth[7:].strip()
        return bool(supplied) and secrets.compare_digest(supplied, TOKEN)

    def _force(self):
        qs = parse_qs(urlparse(self.path).query)
        return (qs.get("force") or [""])[0] == "1"

    def _host_ok(self):
        return self.headers.get("Host", "") in ALLOWED_HOSTS

    def do_GET(self):
        if not self._host_ok():
            self._send(403, {"error": "bad host"})
            return
        p = urlparse(self.path).path
        if p in ("/data.json", "/api/data"):
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                with open(DATA, encoding="utf-8") as f:
                    self._send(200, f.read())
            except FileNotFoundError:
                self._send(404, {"error": "no data"})
            return
        if p == "/api/status":
            # `syncing` 是给自动化用的：/api/sync 用的是**非阻塞**锁，
            # 若上一次同步还在跑（最长 SYNC_TIMEOUT=300s），新请求会直接拿到 409
            # 「已有一次同步正在进行」。测试/脚本能靠这个字段判断"现在是不是能发起同步"，
            # 否则会把 409 误读成"同步功能坏了"（2026-09-30 实测：一个被中途杀掉的
            # 测试进程留下服务端同步仍在跑，随后 4 条断言全红，全是误报）。
            # 页面本身不依赖它，加它不改变探活语义。
            self._send(200, {"served": True, "port": PORT, "syncing": _sync_lock.locked()})
            return
        if p == "/api/projects/relations":
            # 项目回写关系选择器：只读 Notion 页面标题与状态，不返回页面正文。
            # ids 用于回显项目已有关系；q 用于按标题搜索，避免一次传输整库。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            qs = parse_qs(urlparse(self.path).query)
            kind = (qs.get("kind") or [""])[0]
            query = (qs.get("q") or [""])[0]
            raw_ids = (qs.get("ids") or [""])[0]
            ids = [x for x in raw_ids.split(",") if x.strip()]
            if len(ids) > 30:
                self._send(400, {"error": "一次最多回显 30 条关系"})
                return
            try:
                items = (_project_relation_lookup(kind, ids) if ids
                         else _project_relation_search(kind, query))
                self._send(200, {"kind": kind, "items": items})
            except ValueError as e:
                self._send(400, {"error": str(e)})
            except subprocess.TimeoutExpired:
                self._send(504, {"error": "读取 Notion 关系选项超时（45s）"})
            except Exception as e:
                self._send(502, {"error": str(e)[:1200]})
            return
        if p == "/api/feeds":
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            d = _rss_read()
            feeds = []
            groups = []
            if isinstance(d, dict):
                meta = d.get("sourceMeta") or {}
                group_names = {
                    g.get("id"): g.get("name") or ""
                    for g in (d.get("subscriptionGroups") or [])
                    if g.get("id")
                }
                for s in (d.get("subscriptions") or []):
                    sid = s.get("id") or ""
                    sm = meta.get(sid) if isinstance(meta.get(sid), dict) else {}
                    gid = sm.get("groupId") or ""
                    feeds.append({"name": s.get("name") or "(未命名)",
                                  "url": s.get("url") or "",
                                  "group": s.get("group") or group_names.get(gid) or "",
                                  "groupId": gid,
                                  "paused": bool(s.get("paused")),
                                  "error": str(s.get("error") or ""),
                                  "errorCount": int(s.get("errorCount") or 0),
                                  "lastAttemptAt": s.get("lastAttemptAt") or 0,
                                  "lastErrorAt": s.get("lastErrorAt") or 0,
                                  "updatedAt": s.get("updatedAt") or 0,
                                  "count": len(s.get("entries") or [])})
                groups = [{"id": g.get("id") or "", "name": g.get("name") or ""}
                          for g in (d.get("subscriptionGroups") or [])]
            self._send(200, {"feeds": feeds, "groups": groups})
            return
        if p == "/api/article":
            # 资讯站内阅读：只返回标题与清洗后的正文块，不把第三方 HTML / JS
            # 直接交给前端。目标 URL 仍走公网地址校验，避免本地服务被拿来做 SSRF。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            qs = parse_qs(urlparse(self.path).query)
            target = (qs.get("url") or [""])[0].strip()
            try:
                _validate_article_url(target)
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
                return
            try:
                self._send(200, _fetch_article(target))
            except ValueError as exc:
                self._send(422, {"error": str(exc)})
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                self._send(502, {"error": "抓取文章失败：%s" % str(exc)[:240]})
            except Exception as exc:  # noqa: BLE001
                self._send(502, {"error": "抓取文章失败：%s" % str(exc)[:240]})
            return
        if p == "/api/writing/chapter":
            # 工作台只拿稳定 ID，服务端每次重新从 Obsidian projects/ 发现文件。
            # 前端即使被篡改，也无法传任意路径读取本机文件。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            qs = parse_qs(urlparse(self.path).query)
            book_id = (qs.get("bookId") or [""])[0].strip()
            chapter_id = (qs.get("chapterId") or [""])[0].strip()
            if not _re.fullmatch(r"book_[0-9a-f]{20}", book_id) or \
                    not _re.fullmatch(r"chapter_[0-9a-f]{20}", chapter_id):
                self._send(400, {"error": "bookId 或 chapterId 格式不正确"})
                return
            try:
                payload = writing_store.read_chapter(
                    book_id, chapter_id)
                self._send(200, payload)
            except writing_store.WritingNotFound as exc:
                self._send(404, {"error": str(exc)})
            except ValueError as exc:
                code = 413 if "2 MB" in str(exc) else 422
                self._send(code, {"error": str(exc)})
            except OSError as exc:
                self._send(500, {"error": "读取章节失败：%s" % str(exc)[:240]})
            return
        if p == "/api/writing/document":
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            qs = parse_qs(urlparse(self.path).query)
            document_id = (qs.get("documentId") or [""])[0].strip()
            if not _re.fullmatch(r"doc_[0-9a-f]{20}", document_id):
                self._send(400, {"error": "documentId 格式不正确"})
                return
            try:
                payload = writing_store.read_document(document_id)
                self._send(200, payload)
            except writing_store.WritingNotFound as exc:
                self._send(404, {"error": str(exc)})
            except ValueError as exc:
                code = 413 if "2 MB" in str(exc) else 422
                self._send(code, {"error": str(exc)})
            except OSError as exc:
                self._send(500, {"error": "读取稿件失败：%s" % str(exc)[:240]})
            return
        if p == "/api/writing/document/asset":
            # 独立稿件里的相对图片也走稳定 ID 定位；不接收本机绝对路径。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            qs = parse_qs(urlparse(self.path).query)
            document_id = (qs.get("documentId") or [""])[0].strip()
            asset_path = (qs.get("path") or [""])[0].strip()
            if not _re.fullmatch(r"doc_[0-9a-f]{20}", document_id):
                self._send(400, {"error": "documentId 格式不正确"})
                return
            try:
                ctype, data = writing_store.read_document_asset(
                    document_id, asset_path)
                self._send(200, {
                    "contentType": ctype,
                    "data": __import__("base64").b64encode(data).decode("ascii"),
                })
            except writing_store.WritingNotFound as exc:
                self._send(404, {"error": str(exc)})
            except ValueError as exc:
                self._send(404 if "不存在" in str(exc) else 400, {"error": str(exc)})
            except OSError as exc:
                self._send(500, {"error": "读取图片失败：%s" % str(exc)[:240]})
            return
        if p == "/api/writing/asset":
            # 只读章节同目录或子目录里的图片；章节 ID 仍由服务端重新解析，
            # 前端无法通过 asset 路径读取任意本机文件。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            qs = parse_qs(urlparse(self.path).query)
            book_id = (qs.get("bookId") or [""])[0].strip()
            chapter_id = (qs.get("chapterId") or [""])[0].strip()
            asset_path = (qs.get("path") or [""])[0].strip()
            if not _re.fullmatch(r"book_[0-9a-f]{20}", book_id) or \
                    not _re.fullmatch(r"chapter_[0-9a-f]{20}", chapter_id):
                self._send(400, {"error": "bookId 或 chapterId 格式不正确"})
                return
            try:
                ctype, data = writing_store.read_asset(
                    book_id, chapter_id, asset_path)
                self._send(200, {
                    "contentType": ctype,
                    "data": __import__("base64").b64encode(data).decode("ascii"),
                })
            except writing_store.WritingNotFound as exc:
                self._send(404, {"error": str(exc)})
            except ValueError as exc:
                self._send(404 if "不存在" in str(exc) else 400, {"error": str(exc)})
            except OSError as exc:
                self._send(500, {"error": "读取图片失败：%s" % str(exc)[:240]})
            return
        if p == "/api/writing/trash":
            # 回收站清单：与删除同一套令牌；只读名称/类型/大小，不含路径。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                self._send(200, {"items": writing_store.list_trash()})
            except OSError as exc:
                self._send(500, {"error": "读取回收站失败：%s" % str(exc)[:240]})
            return
        if p == "/api/review/note":
            # 复盘站内预览：按稳定 ID 重新发现文件，正文只读。不接收路径，
            # 返回体也不带路径 —— 与 sections.json 的 noteId 同一套 root + kind。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            qs = parse_qs(urlparse(self.path).query)
            note_id = (qs.get("noteId") or [""])[0].strip()
            if not _re.fullmatch(r"note_[0-9a-f]{20}", note_id):
                self._send(400, {"error": "noteId 格式不正确"})
                return
            try:
                found = None
                for f in glob.glob(os.path.join(REVIEW_DIR, "调试经验-*.md")):
                    if os.path.basename(f) == "调试经验 MOC.md":
                        continue
                    try:
                        if writing_store.stable_id(
                                "note", f, writing_store.OBSIDIAN_ROOT) == note_id:
                            found = f
                            break
                    except ValueError:
                        continue
                if not found:
                    self._send(404, {"error": "复盘笔记不存在"})
                    return
                if os.path.islink(found) or not os.path.isfile(found):
                    self._send(400, {"error": "复盘笔记不是普通 Markdown 文件"})
                    return
                with open(found, "rb") as handle:
                    raw = handle.read()
                if len(raw) > writing_store.MAX_CHAPTER_BYTES:
                    self._send(413, {"error": "复盘笔记超过 2 MB，工作台暂不打开"})
                    return
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    self._send(422, {"error": "复盘笔记不是 UTF-8 Markdown"})
                    return
                self._send(200, {
                    "ok": True,
                    "noteId": note_id,
                    "title": os.path.basename(found)[:-3],
                    "content": content,
                    "chars": len(content),
                    "modifiedAt": datetime.datetime.fromtimestamp(
                        os.path.getmtime(found)).isoformat(timespec="seconds"),
                })
            except OSError as exc:
                self._send(500, {"error": "读取复盘失败：%s" % str(exc)[:240]})
            return
        if p == "/api/jev":
            # Jev 增强插件：GET 读开关状态 + 缓存的分析结果；POST 切开关 /
            # 触发分析。状态存 ~/.config/daily-workbench/jev-settings.json，刷新不丢。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            cfg = _jev_settings_read()
            self._send(200, {
                "enabled": bool(cfg.get("enabled")),
                "lastAnalysis": cfg.get("lastAnalysis"),
                "analyzedAt": cfg.get("analyzedAt"),
            })
            return
        if p == "/api/sync/log":
            # 同步历史（最近 20 条，服务端落盘 sync-log.json）。与 data.json 同一把
            # 令牌：失败记录里带了脚本输出的首行，不希望没有令牌的进程随意翻看。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                with open(SYNC_LOG, encoding="utf-8") as f:
                    logs = json.load(f)
                if not isinstance(logs, list):
                    logs = []
            except FileNotFoundError:
                logs = []
            except Exception:
                logs = []
            self._send(200, {"logs": logs})
            return
        if p == "/api/weekly":
            # 周报：GET 读最近一份已生成的周报（不重新算）。
            # POST 的生成分支在 do_POST 里。与 /api/sync/log 同一把令牌：
            # 周报含个人收益与账号数据。
            # 与 /api/sync/log 同一把令牌：周报含个人收益与账号数据。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            reports_dir = os.path.join(DIR, "reports")
            latest = None
            if os.path.isdir(reports_dir):
                candidates = sorted(
                    f for f in os.listdir(reports_dir)
                    if f.startswith("weekly-") and f.endswith(".md")
                )
                if candidates:
                    latest = os.path.join(reports_dir, candidates[-1])
            if not latest:
                self._send(404, {"error": "还没有周报",
                                 "hint": "点「生成本周周报」先生成一份"})
                return
            try:
                with open(latest, encoding="utf-8") as f:
                    content = f.read()
            except FileNotFoundError:
                self._send(404, {"error": "周报文件不存在"})
                return
            self._send(200, {"ok": True, "path": os.path.basename(latest),
                             "content": content})
            return
        if p == "/sections.json":
            # 与 /data.json 同一把令牌。放在 /api/status 之后：那条是探活用的、**故意不要令牌**
            # （页面靠它判断在线/离线），别把它也圈进来。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                with open(SECTIONS, encoding="utf-8") as f:
                    self._send(200, f.read())
            except FileNotFoundError:
                # 明确报"还没生成 + 怎么生成"，而不是笼统 404：
                # 这个文件是派生的，第一次跑、或换了机器都会缺。
                self._send(404, {"error": "no sections",
                                 "hint": "先跑 python3 scripts/sync/sections.py --write 生成派生快照"})
            return
        if p == "/social.json":
            # 与 data.json / sections.json 同一把令牌。里面没有密钥，但含个人账号与
            # 逐条内容表现，不能重新打开静态侧门。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                with open(SOCIAL, encoding="utf-8") as f:
                    self._send(200, f.read())
            except FileNotFoundError:
                self._send(404, {"error": "no social snapshot",
                                 "hint": "先跑 python3 scripts/sync/social-sync.py 生成自媒体快照"})
            return
        if p == "/web-presence.json":
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                with open(WEB_PRESENCE, encoding="utf-8") as f:
                    self._send(200, f.read())
            except FileNotFoundError:
                self._send(404, {"error": "no website snapshot",
                                 "hint": "先跑 python3 scripts/sync/web-monitor.py 生成网站监控快照"})
            return
        if p == "/mpdt-export.json" or p == "/mpdt-detail.json":
            # mpdt 导出已并入 social.json，这两个中间产物不再单独对外提供，
            # 防止绕过令牌直接读公众号原始统计（.gitignore 已排除，正常不存在）。
            self._send(404, {"error": "not found"})
            return
        if p == "/":
            # 裸访问 / 时直接带上令牌重定向到工作台。
            # 为什么：/data.json 与 /api/sync 都要求 token，裸开 workbench.html 会让页面
            # 静默退回浏览器缓存、点「同步一次」报 401（2026-09-29 实际踩过）。
            # 安全性说明：请求已通过 _host_ok() 校验（只接受 127.0.0.1:8787 / localhost:8787），
            # 且服务只绑回环。跨源脚本拿不到重定向的 Location（响应是不透明的），
            # 而同用户的本地进程本来就能读 600 权限的令牌文件，故暴露面不变。
            self.send_response(302)
            self.send_header("Location", "/workbench.html?token=" + TOKEN)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # 静态兜底收窄成白名单，其余一律 404（原因见 STATIC_ALLOW 上方的注释）。
        # 注意这里**不能**退回 `super().do_GET()`：那就是把所有项目文件重新敞开，
        # 包括 backups/ 里的全量数据副本 —— 这正是本次修掉的漏洞。
        if p in STATIC_ALLOW:
            return super().do_GET()
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            self._send(403, {"error": "bad host"})
            return
        p = urlparse(self.path).path
        if p == "/api/writing/save":
            # 正文写回 Obsidian 的唯一入口。baseSha256 不匹配时返回 409，
            # 由页面让用户选择重新加载，避免静默覆盖外部编辑。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                self._send(400, {"error": "bad length"})
                return
            if length <= 0 or length > MAX_WRITING_REQUEST_BYTES:
                self._send(413, {
                    "error": "bad body size",
                    "limit": MAX_WRITING_REQUEST_BYTES,
                })
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._send(400, {"error": "invalid json: %s" % str(exc)[:160]})
                return
            if not isinstance(body, dict):
                self._send(400, {"error": "invalid body"})
                return
            book_id = str(body.get("bookId") or "").strip()
            chapter_id = str(body.get("chapterId") or "").strip()
            if not _re.fullmatch(r"book_[0-9a-f]{20}", book_id) or \
                    not _re.fullmatch(r"chapter_[0-9a-f]{20}", chapter_id):
                self._send(400, {"error": "bookId 或 chapterId 格式不正确"})
                return
            try:
                with _writing_write_lock:
                    payload = writing_store.write_chapter(
                        book_id,
                        chapter_id,
                        body.get("content"),
                        str(body.get("baseSha256") or "").strip(),
                        WRITING_BACKUP_DIR,
                    )
                _regen_sections()
                self._send(200, {"ok": True, **payload})
            except writing_store.WritingConflict as exc:
                self._send(409, {
                    "error": str(exc),
                    "conflict": True,
                    "hint": "重新加载最新正文，确认后再保存；不要直接覆盖。",
                })
            except writing_store.WritingNotFound as exc:
                self._send(404, {"error": str(exc)})
            except ValueError as exc:
                code = 413 if "2 MB" in str(exc) else 400
                self._send(code, {"error": str(exc)})
            except OSError as exc:
                self._send(500, {"error": "保存章节失败：%s" % str(exc)[:240]})
            return
        if p == "/api/writing/document/save":
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                self._send(400, {"error": "bad length"})
                return
            if length <= 0 or length > MAX_WRITING_REQUEST_BYTES:
                self._send(413, {
                    "error": "bad body size",
                    "limit": MAX_WRITING_REQUEST_BYTES,
                })
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._send(400, {"error": "invalid json: %s" % str(exc)[:160]})
                return
            if not isinstance(body, dict):
                self._send(400, {"error": "invalid body"})
                return
            document_id = str(body.get("documentId") or "").strip()
            if not _re.fullmatch(r"doc_[0-9a-f]{20}", document_id):
                self._send(400, {"error": "documentId 格式不正确"})
                return
            try:
                with _writing_write_lock:
                    payload = writing_store.write_document(
                        document_id,
                        body.get("content"),
                        str(body.get("baseSha256") or "").strip(),
                        WRITING_BACKUP_DIR,
                    )
                _regen_sections()
                self._send(200, {"ok": True, **payload})
            except writing_store.WritingConflict as exc:
                self._send(409, {
                    "error": str(exc),
                    "conflict": True,
                    "hint": "重新加载最新正文，确认后再保存；不要直接覆盖。",
                })
            except writing_store.WritingNotFound as exc:
                self._send(404, {"error": str(exc)})
            except ValueError as exc:
                code = 413 if "2 MB" in str(exc) else 400
                self._send(code, {"error": str(exc)})
            except OSError as exc:
                self._send(500, {"error": "保存稿件失败：%s" % str(exc)[:240]})
            return
        if p == "/api/writing/create":
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                self._send(400, {"error": "bad length"})
                return
            if length <= 0 or length > 64 * 1024:
                self._send(413, {"error": "bad body size"})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._send(400, {"error": "invalid json: %s" % str(exc)[:160]})
                return
            if not isinstance(body, dict):
                self._send(400, {"error": "invalid body"})
                return
            kind = str(body.get("kind") or "document").strip()
            title = str(body.get("title") or "").strip()
            try:
                with _writing_write_lock:
                    if kind == "novel":
                        payload = writing_store.create_novel(title)
                    elif kind == "document":
                        payload = writing_store.create_document(
                            str(body.get("contentType") or "").strip(), title)
                    else:
                        raise ValueError("kind 必须是 novel 或 document")
                _regen_sections()
                self._send(200, {"ok": True, **payload})
            except FileExistsError:
                self._send(409, {"error": "同名内容已存在"})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except OSError as exc:
                self._send(500, {"error": "创建内容失败：%s" % str(exc)[:240]})
            return
        if p == "/api/writing/delete":
            # 删除不是物理删除：文件整体移入 Obsidian 回收站，可在 Obsidian 内恢复。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                self._send(400, {"error": "bad length"})
                return
            if length <= 0 or length > 4 * 1024:
                self._send(413, {"error": "bad body size"})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._send(400, {"error": "invalid json: %s" % str(exc)[:160]})
                return
            if not isinstance(body, dict):
                self._send(400, {"error": "invalid body"})
                return
            kind = str(body.get("kind") or "").strip()
            try:
                with _writing_write_lock:
                    if kind == "document":
                        payload = writing_store.delete_document(
                            str(body.get("documentId") or "").strip())
                    elif kind == "novel":
                        payload = writing_store.delete_novel(
                            str(body.get("bookId") or "").strip())
                    else:
                        raise ValueError("kind 必须是 document 或 novel")
                _regen_sections()
                self._send(200, {"ok": True, **payload})
            except writing_store.WritingNotFound as exc:
                self._send(404, {"error": str(exc)})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except OSError as exc:
                self._send(500, {"error": "删除失败：%s" % str(exc)[:240]})
            return
        if p == "/api/writing/restore":
            # 恢复 = 从回收站移回原位置；目标重名时加后缀，绝不覆盖现有文件。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                self._send(400, {"error": "bad length"})
                return
            if length <= 0 or length > 4 * 1024:
                self._send(413, {"error": "bad body size"})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                self._send(400, {"error": "invalid json: %s" % str(exc)[:160]})
                return
            if not isinstance(body, dict):
                self._send(400, {"error": "invalid body"})
                return
            try:
                with _writing_write_lock:
                    payload = writing_store.restore_from_trash(
                        str(body.get("name") or "").strip())
                _regen_sections()
                self._send(200, {"ok": True, **payload})
            except writing_store.WritingNotFound as exc:
                self._send(404, {"error": str(exc)})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except OSError as exc:
                self._send(500, {"error": "恢复失败：%s" % str(exc)[:240]})
            return
        if p == "/api/weekly":
            # 周报生成：跑 scripts/sync/weekly-report.py 重新生成本周 Markdown，
            # 落盘 reports/ 后返回最新一份全文。脚本只读本地快照、只写 reports/。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            script = os.path.join(DIR, "scripts", "sync", "weekly-report.py")
            if not os.path.exists(script):
                self._send(500, {"error": "weekly-report script missing", "path": script})
                return
            try:
                proc = subprocess.run(
                    [sys.executable, script],
                    cwd=DIR, capture_output=True, text=True, timeout=30,
                )
            except subprocess.TimeoutExpired:
                self._send(504, {"error": "周报生成超时（30s）"})
                return
            if proc.returncode != 0:
                out = ((proc.stdout or "") + (proc.stderr or "")).strip()
                self._send(500, {"error": out[-1000:] or "周报生成失败"})
                return
            reports_dir = os.path.join(DIR, "reports")
            candidates = sorted(
                f for f in os.listdir(reports_dir)
                if f.startswith("weekly-") and f.endswith(".md")
            ) if os.path.isdir(reports_dir) else []
            if not candidates:
                self._send(500, {"error": "脚本执行成功但没有产出周报文件"})
                return
            latest = os.path.join(reports_dir, candidates[-1])
            try:
                with open(latest, encoding="utf-8") as f:
                    content = f.read()
            except FileNotFoundError:
                self._send(404, {"error": "周报文件不存在"})
                return
            self._send(200, {"ok": True, "path": os.path.basename(latest),
                             "content": content})
            return
        if p in ("/data.json", "/api/data"):
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                self._send(400, {"error": "bad length"})
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send(413, {"error": "bad body size", "limit": MAX_BODY_BYTES})
                return
            raw = self.rfile.read(length)
            try:
                obj = json.loads(raw.decode("utf-8"))
            except Exception as e:
                self._send(400, {"error": str(e)})
                return
            if not isinstance(obj, dict) or not all(k in obj for k in REQUIRED_KEYS):
                self._send(400, {"error": "invalid schema"})
                return
            # Guard: refuse a write that would wipe out most of the data.
            prev = _read_current()
            if prev is not None and not self._force():
                for key in REQUIRED_KEYS:
                    before = prev.get(key)
                    after = obj.get(key)
                    before = len(before) if isinstance(before, list) else 0
                    after = len(after) if isinstance(after, list) else 0
                    if before > GUARD_MIN_EXISTING and after < before * GUARD_MIN_RATIO:
                        self._send(409, {
                            "error": "refused: record count would drop sharply",
                            "field": key, "before": before, "after": after,
                            "hint": "append ?force=1 to the request URL to override",
                        })
                        return
            # 写入互斥：并发保存曾共用同一个 data.json.tmp，第一个 os.replace 把
            # 临时文件消费掉后第二个直接 FileNotFoundError（浏览器侧表现为
            # 「保存失败：连不上同步服务」）。读-备-写整体持锁，临时文件再加
            # 随机后缀双保险。
            with _data_write_lock:
                _rotate_backup()
                tmp = "%s.tmp.%d" % (DATA, threading.get_ident())
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False, indent=2)
                os.replace(tmp, DATA)
            self._send(200, {"ok": True, "earnings": len(obj.get("earnings", [])),
                             "projects": len(obj.get("projects", []))})
            return
        if p == "/api/focus/push":
            # 单条专注记录回写：必须显式点击、只写已完成记录、同一本地 ID 只创建一次。
            # 与同步/客户回写共用 _sync_lock，避免回写期间发生全量拉取覆盖。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                self._send(400, {"error": "bad length"})
                return
            if length <= 0 or length > CLIENT_BODY_BYTES:
                self._send(413, {"error": "bad body size", "limit": CLIENT_BODY_BYTES})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as e:
                self._send(400, {"error": str(e)})
                return
            session_id = str((body or {}).get("sessionId") or "").strip()
            if not _re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id):
                self._send(400, {"error": "sessionId 格式不正确"})
                return
            prev = _read_current() or {}
            session = next((s for s in (prev.get("focusSessions") or [])
                            if str(s.get("id") or "") == session_id), None)
            if not session:
                self._send(404, {"error": "专注记录不存在，或尚未保存到 data.json"})
                return
            if session.get("notionPageId"):
                self._send(200, {
                    "ok": True,
                    "alreadyPushed": True,
                    "session": session,
                    "notionPageId": session.get("notionPageId"),
                    "notionUrl": session.get("notionUrl") or "",
                })
                return
            try:
                properties = _build_focus_properties(session)
            except ValueError as e:
                self._send(400, {"error": str(e)})
                return
            if not _sync_lock.acquire(blocking=False):
                self._send(409, {"error": "同步正在进行，请稍后再回写"})
                return
            try:
                notion_page = _notion_create_focus_page(properties)
                try:
                    updated = _write_focus_update(session_id, notion_page)
                    local_updated = True
                except Exception:
                    updated = dict(session)
                    updated["notionPageId"] = notion_page.get("id")
                    updated["notionUrl"] = notion_page.get("url") or ""
                    local_updated = False
                self._send(200, {
                    "ok": True,
                    "session": updated,
                    "localUpdated": local_updated,
                    "notionPageId": notion_page.get("id"),
                    "notionUrl": notion_page.get("url") or "",
                })
            except subprocess.TimeoutExpired:
                self._send(504, {"error": "Notion 回写超时（45s）"})
            except Exception as e:
                self._send(502, {"error": str(e)[:1200]})
            finally:
                _sync_lock.release()
            return
        if p == "/api/projects/update":
            # 项目回写：只允许修改 data.json 中已经存在的 Notion 项目页。
            # 可写字段与类型由 Notion schema 现场校验，rollup / 公式不可写。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                self._send(400, {"error": "bad length"})
                return
            if length <= 0 or length > CLIENT_BODY_BYTES:
                self._send(413, {"error": "bad body size", "limit": CLIENT_BODY_BYTES})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as e:
                self._send(400, {"error": str(e)})
                return
            page_id = str((body or {}).get("pageId") or "").strip().lower()
            fields = (body or {}).get("fields")
            if not _re.fullmatch(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", page_id):
                self._send(400, {"error": "pageId 格式不正确"})
                return
            prev = _read_current() or {}
            projects = prev.get("projects") or []
            project = next((p for p in projects
                            if str(p.get("notionId") or "").lower() == page_id), None)
            if not project:
                self._send(404, {"error": "项目不存在，或尚未同步到本地"})
                return
            if isinstance(fields, dict) and fields.get("parentNotionId"):
                parent_id = str(fields.get("parentNotionId") or "").strip().lower()
                if parent_id == page_id:
                    self._send(400, {"error": "项目不能把自己设为上级项目"})
                    return
                if not any(str(p.get("notionId") or "").lower() == parent_id for p in projects):
                    self._send(400, {"error": "上级项目不在当前同步结果中"})
                    return
            try:
                properties = _build_project_properties(fields)
            except subprocess.TimeoutExpired:
                self._send(504, {"error": "读取 Notion 项目 schema 超时（45s）"})
                return
            except ValueError as e:
                self._send(400, {"error": str(e)})
                return
            except Exception as e:
                self._send(502, {"error": str(e)[:1200]})
                return
            if not _sync_lock.acquire(blocking=False):
                self._send(409, {"error": "同步正在进行，请稍后再回写"})
                return
            try:
                notion_page = _notion_update_page(page_id, properties)
                try:
                    updated = _write_project_update(page_id, fields)
                    local_updated = True
                except Exception:
                    updated = dict(project)
                    for key, local_key in _PROJECT_LOCAL_KEYS.items():
                        if key in fields and key != "parentNotionId":
                            updated[local_key] = fields.get(key)
                    local_updated = False
                self._send(200, {
                    "ok": True,
                    "pageId": page_id,
                    "project": updated,
                    "localUpdated": local_updated,
                    "notionPageId": notion_page.get("id") or page_id,
                })
            except subprocess.TimeoutExpired:
                self._send(504, {"error": "Notion 项目回写超时（45s）"})
            except Exception as e:
                self._send(502, {"error": str(e)[:1200]})
            finally:
                _sync_lock.release()
            return
        if p == "/api/clients/update":
            # 客户资料回写：只允许改 data.json 里已经存在的客户页，且字段走白名单。
            # 与同步共用 _sync_lock，避免一边 PATCH Notion、一边全量拉取造成本地覆盖。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                self._send(400, {"error": "bad length"})
                return
            if length <= 0 or length > CLIENT_BODY_BYTES:
                self._send(413, {"error": "bad body size", "limit": CLIENT_BODY_BYTES})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as e:
                self._send(400, {"error": str(e)})
                return
            page_id = str((body or {}).get("pageId") or "").strip().lower()
            fields = (body or {}).get("fields")
            if not _re.fullmatch(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", page_id):
                self._send(400, {"error": "pageId 格式不正确"})
                return
            prev = _read_current()
            client = next((c for c in ((prev or {}).get("clients") or [])
                           if str(c.get("notionId") or "").lower() == page_id), None)
            if not client:
                self._send(404, {"error": "客户不存在，或尚未同步到本地"})
                return
            try:
                properties = _build_client_properties(fields)
            except ValueError as e:
                self._send(400, {"error": str(e)})
                return
            if not _sync_lock.acquire(blocking=False):
                self._send(409, {"error": "同步正在进行，请稍后再回写"})
                return
            try:
                notion_page = _notion_update_page(page_id, properties)
                try:
                    updated = _write_client_update(page_id, fields)
                    local_updated = True
                except Exception:
                    updated = dict(client)
                    updated.update({k: ("" if fields.get(k) is None else str(fields.get(k)))
                                    for k in fields})
                    local_updated = False
                self._send(200, {
                    "ok": True,
                    "pageId": page_id,
                    "client": updated,
                    "localUpdated": local_updated,
                    "notionPageId": notion_page.get("id") or page_id,
                })
            except subprocess.TimeoutExpired:
                self._send(504, {"error": "Notion 回写超时（45s）"})
            except Exception as e:
                self._send(502, {"error": str(e)[:1200]})
            finally:
                _sync_lock.release()
            return
        if p == "/api/notes/push":
            # 把「记一笔」的本地草稿（source:"local"）真正写进 Notion「副业收入」库。
            # 复用 notion-push-drafts.py --confirm：它只碰 source=="local" 的记录，
            # Notion 拉来的只读条目绝不动；写入成功后本地自动转为「已入 Notion」。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            script = os.path.join(DIR, "scripts", "sync", "notion-push-drafts.py")
            if not os.path.exists(script):
                self._send(500, {"error": "push script missing", "path": script})
                return
            try:
                proc = subprocess.run(
                    [sys.executable, script, "--confirm"],
                    cwd=DIR, capture_output=True, text=True, timeout=300,
                )
            except subprocess.TimeoutExpired:
                self._send(504, {"error": "推送超时（300s）"})
                return
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            self._send(200 if proc.returncode == 0 else 500, {
                "ok": proc.returncode == 0,
                "code": proc.returncode,
                "output": out[-4000:],
            })
            return
        if p == "/api/sync":
            qs = parse_qs(urlparse(self.path).query)
            trigger = qs.get("src", ["manual"])[0]
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            if not os.path.exists(SYNC_SCRIPT):
                self._send(500, {"error": "sync script missing", "path": SYNC_SCRIPT})
                return
            if not _sync_lock.acquire(blocking=False):
                self._send(409, {"error": "已有一次同步正在进行，请稍候"})
                return
            try:
                # 同步前兜底：服务端代抓插件拿不到的源（直连失败走代理），失败不影响同步
                try:
                    _refresh_stale_feeds()
                except Exception:  # noqa: BLE001
                    pass
                started = time.time()
                try:
                    proc = _run_sync_process()
                except subprocess.TimeoutExpired:
                    _sync_log_append(_sync_timeout_entry(started, trigger))
                    self._send(504, {"error": "同步超时（%ds）" % SYNC_TIMEOUT})
                    return
                out = ((proc.stdout or "") + (proc.stderr or "")).strip()
                visible_out = _strip_sync_step_timings(out)
                state = _read_current() or {}
                ok = proc.returncode == 0
                steps = _build_sync_steps(proc.returncode, out, state)
                # 先落日志再发响应：浏览器超时断开时 _send 会抛 BrokenPipeError，
                # 若把它放在后面，真实已完成的同步会从历史里消失（2026-10-29 实测）。
                _sync_log_append({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "ok": ok,
                    "code": proc.returncode,
                    "elapsed": round(time.time() - started, 1),
                    "earnings": len(state.get("earnings", [])),
                    "projects": len(state.get("projects", [])),
                    "error": "" if ok else visible_out.splitlines()[0][:160] if visible_out else "",
                    "trigger": trigger if trigger in ("manual", "auto") else "manual",
                    "steps": steps,
                })
                self._send(200 if ok else 500, {
                    "ok": proc.returncode == 0,
                    "code": proc.returncode,
                    "elapsed": round(time.time() - started, 1),
                    "output": visible_out[-4000:],
                    "earnings": len(state.get("earnings", [])),
                    "projects": len(state.get("projects", [])),
                    "lastNotionSync": state.get("lastNotionSync"),
                })
            finally:
                _sync_lock.release()
            return
        if p in ("/api/feeds/probe", "/api/feeds/add", "/api/feeds/refresh",
                 "/api/feeds/pause", "/api/feeds/delete"):
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                self._send(400, {"error": "bad length"})
                return
            if length <= 0 or length > 200_000:
                self._send(413, {"error": "bad body size"})
                return
            try:
                obj = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception as e:
                self._send(400, {"error": str(e)})
                return
            if not isinstance(obj, dict):
                self._send(400, {"error": "invalid body"})
                return
            if p == "/api/feeds/probe":
                url = str(obj.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    self._send(400, {"error": "url must start with http(s)://"})
                    return
                hits = _probe_rss(url)
                current = _rss_read() or {}
                by_key = {
                    _feed_url_key(s.get("url")): s.get("name") or ""
                    for s in (current.get("subscriptions") or [])
                }
                for h in hits:
                    h["existing"] = _feed_url_key(h.get("url")) in by_key
                    h["existingName"] = by_key.get(_feed_url_key(h.get("url")), "")
                self._send(200, {"hits": hits})
                return
            d = _rss_read()
            if not isinstance(d, dict):
                self._send(404, {"error": "RSS data file not found",
                                 "hint": RSS_DATA})
                return
            if p == "/api/feeds/refresh":
                url = str(obj.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    self._send(400, {"error": "url must start with http(s)://"})
                    return
                try:
                    result = _feed_refresh(d, url)
                except ValueError as e:
                    self._send(400, {"error": str(e)})
                    return
                except Exception as e:  # noqa: BLE001
                    # 抓取失败也要落 lastAttemptAt/error，别让源一直停在「未尝试」
                    for s in (d.get("subscriptions") or []):
                        if _feed_url_key(s.get("url")) == _feed_url_key(url):
                            s["lastAttemptAt"] = int(time.time() * 1000)
                            s["lastErrorAt"] = int(time.time() * 1000)
                            s["errorCount"] = int(s.get("errorCount") or 0) + 1
                            s["error"] = str(e)[:300]
                            break
                    _rss_write(d)
                    self._send(502, {"ok": False, "error": str(e)[:300]})
                    return
                _rss_write(d)
                _regen_sections()
                self._send(200, {"ok": True, **result})
                return
            if p == "/api/feeds/add":
                url = str(obj.get("url") or "").strip()
                if not url.startswith(("http://", "https://")):
                    self._send(400, {"error": "url must start with http(s)://"})
                    return
                try:
                    name = _feed_add(d, url, str(obj.get("title") or "").strip(),
                                     str(obj.get("group") or "").strip())
                except ValueError as e:
                    self._send(409, {"error": str(e)})
                    return
                _rss_write(d)
                _regen_sections()
                self._send(200, {"ok": True, "name": name})
                return
            if p == "/api/feeds/pause":
                url = str(obj.get("url") or "").strip()
                if "paused" not in obj:
                    self._send(400, {"error": "missing paused"})
                    return
                feed = _feed_pause(d, url, bool(obj.get("paused")))
                if feed is None:
                    self._send(404, {"error": "没有找到这个订阅源"})
                    return
                _rss_write(d)
                _regen_sections()
                self._send(200, {"ok": True, "paused": bool(feed.get("paused")),
                                 "name": feed.get("name") or url})
                return
            # /api/feeds/delete
            url = str(obj.get("url") or "").strip()
            if obj.get("purge") is not True:
                self._send(400, {"error": "永久删除需要明确传入 purge=true；日常停用请用 /api/feeds/pause"})
                return
            removed = _feed_delete(d, url)
            if removed is None:
                self._send(404, {"error": "没有找到这个订阅源"})
                return
            _rss_write(d)
            _regen_sections()
            self._send(200, {"ok": True, **removed})
            return
        if p in ("/api/jev/toggle", "/api/jev/analyze"):
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                length = 0
            obj = {}
            if length:
                try:
                    obj = json.loads(self.rfile.read(length).decode("utf-8"))
                except Exception:
                    obj = {}
            if not isinstance(obj, dict):
                obj = {}
            cfg = _jev_settings_read()
            if p == "/api/jev/toggle":
                cfg["enabled"] = bool(obj.get("enabled"))
                _jev_settings_write(cfg)
                self._send(200, {"ok": True, "enabled": cfg["enabled"]})
                return
            # /api/jev/analyze：把 social.json 的内容信号喂给 Jev 评质量，
            # 结果缓存进 jev-settings.json（每次分析覆盖上一次）。
            if not cfg.get("enabled"):
                self._send(409, {"error": "Jev 增强未开启",
                                 "hint": "先在更多菜单里打开 Jev 增强"})
                return
            try:
                with open(SOCIAL, encoding="utf-8") as f:
                    social = json.load(f)
            except (OSError, ValueError):
                self._send(404, {"error": "没有 social.json 快照"})
                return
            posts = social.get("posts") or []
            if not posts:
                self._send(404, {"error": "快照里没有逐条内容"})
                return
            results = []
            for post in posts[:8]:  # 最多 8 条，控制探测成本
                title = str(post.get("title") or "")[:120]
                # 结合真实表现数据：质量分要与阅读/互动对照，才回答「写得好但没人看」还是「内容本身弱」。
                m = post.get("metrics") or {}
                perf_bits = []
                if isinstance(m.get("reads"), (int, float)):
                    perf_bits.append(f"阅读 {int(m['reads'])}")
                if isinstance(m.get("likes"), (int, float)) and m["likes"]:
                    perf_bits.append(f"赞 {int(m['likes'])}")
                if isinstance(m.get("comments"), (int, float)) and m["comments"]:
                    perf_bits.append(f"评论 {int(m['comments'])}")
                if isinstance(m.get("shares"), (int, float)) and m["shares"]:
                    perf_bits.append(f"分享 {int(m['shares'])}")
                perf = "；真实表现：" + "、".join(perf_bits) if perf_bits else ""
                text = title + perf
                ok, data = _jev_call(text)
                if not ok:
                    results.append({"title": title, "error": data.get("error")})
                    break
                ans = ((data.get("answers") or {}).get("quality") or {})
                results.append({
                    "title": title, "url": post.get("url"),
                    "platform": post.get("platform"),
                    "score": ans.get("score"),
                    "confidence": ans.get("confidence"),
                    "reason": ans.get("reason") or ans.get("explanation"),
                    "usage": data.get("usage"),
                    "metrics": {k: m.get(k) for k in ("reads", "likes", "comments", "shares") if m.get(k) is not None},
                })
            ok_any = any("error" not in r for r in results)
            if not ok_any:
                self._send(502, {"error": results[0].get("error") or "Jev 调用失败"})
                return
            cfg["lastAnalysis"] = results
            cfg["analyzedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
            _jev_settings_write(cfg)
            self._send(200, {"ok": True, "results": results,
                             "analyzedAt": cfg["analyzedAt"]})
            return
        self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    os.chdir(DIR)
    # 必须用 ThreadingHTTPServer，不能退回单线程 TCPServer：
    # /api/sync 会阻塞等 sync-now.sh 跑完，而该脚本最后要 POST 回 /data.json。
    # 单线程时那个回写请求永远排不上队 → 30s 超时自锁（2026-09-29 实测踩到）。
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler) as httpd:
        print(f"workbench sync server on http://127.0.0.1:{PORT}")
        httpd.serve_forever()
