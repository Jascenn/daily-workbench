#!/usr/bin/env python3
"""Tiny local sync server for the workbench.

Serves the folder statically and exposes a shared JSON store at /data.json:
  GET  /data.json  -> current canonical store (used by the workbench UI)
  POST /data.json  -> atomically replace the store (called by the workbench on save)
  GET  /sections.json -> 派生快照：资讯 / 写作 / 复盘 三个「功能类型」区的数据
                         （只读，由 scripts/sync/sections.py 生成，页面不写回）
  GET  /social.json -> 自媒体只读快照：账号、逐条内容、草稿能力与数据边界
  GET  /radar.json  -> 选题雷达：热点条目 + Jev 相关度/可写价值 + 来源可用性
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
from http.cookies import SimpleCookie
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
import re
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse, parse_qs, urljoin

DIR = os.path.dirname(os.path.abspath(__file__))
if DIR not in sys.path:
    sys.path.insert(0, DIR)
sys.path.insert(0, os.path.join(DIR, "scripts", "sync"))
import writing_store  # noqa: E402

DATA = os.path.join(DIR, "data.json")
# 派生快照（资讯/写作/复盘）。**只读**：页面永远不写它，只由 scripts/sync/sections.py 生成。
# 与 data.json 分开存的原因见 scripts/sync/sections.py 文件头：data.json 是 Notion 同步产物、
# 有写入护栏，混进去会被「同步一次」冲掉。
SECTIONS = os.path.join(DIR, "sections.json")
REVIEW_DIR = os.path.join(writing_store.OBSIDIAN_ROOT, "调试经验")
SOCIAL = os.path.join(DIR, "social.json")
RADAR = os.path.join(DIR, "radar.json")
# 知识星球日报：只读派生快照，由 scripts/sync/zsxq-daily-sync.py 生成，页面不写回。
ZSXQ_DAILY = os.path.join(DIR, "zsxq-daily.json")
WEB_PRESENCE = os.path.join(DIR, "web-presence.json")
# 微信只读快照（scripts/sync/wechat-sync.py 生成）。与 social.json 同理：不含密钥，
# 但含真实对话对象与消息预览，必须和 data.json 共用令牌门。
WECHAT = os.path.join(DIR, "wechat.json")
JEV_SETTINGS = os.path.expanduser("~/.config/daily-workbench/jev-settings.json")


from server.runtime import bind_app_globals  # noqa: E402
from server.sync import (  # noqa: E402
    _jev_settings_read, _jev_settings_write, _jev_call,
    _sync_log_append, _run_sync_process, _sync_timeout_entry,
    _parse_sync_step_timings, _strip_sync_step_timings,
    _parse_sync_step_failures, _sync_error_line, _build_sync_steps,
)


RSS_DATA = os.path.join(DIR, "rss-store.json")
# 订阅兜底代理：本机 Surge 混合端口。部分源（如 lingyi.me）直连超时只有代理能通，
# 插件侧不一定走代理，服务端代抓时用它兜底；可用 WB_FEED_PROXY 覆盖。
FEED_PROXY = os.environ.get("WB_FEED_PROXY") or "http://127.0.0.1:7890"
SECTIONS_SCRIPT = os.path.join(DIR, "scripts", "sync", "sections.py")
PORT = 8787
VERSION_FILE = os.path.join(DIR, "VERSION")
try:
    with open(VERSION_FILE, encoding="utf-8") as _vf:
        APP_VERSION = _vf.read().strip() or "1.1.0"
except OSError:
    APP_VERSION = "1.1.0"
TOKEN_FILE = os.path.expanduser("~/.config/daily-workbench/sync-token")
SYNC_SCRIPT = os.path.join(DIR, "scripts", "sync-now.sh")
SYNC_TIMEOUT = 300
SYNC_LOG = os.path.join(DIR, "sync-log.json")
SYNC_LOG_KEEP = 20

BACKUP_DIR = os.path.join(DIR, "backups")
KEEP_BACKUPS = 20
KEEP_RSS_BACKUPS = 10
SESSION_COOKIE = "wb_session"
GUARD_MIN_EXISTING = 10
GUARD_MIN_RATIO = 0.5
MAX_BODY_BYTES = 20 * 1024 * 1024
CLIENT_BODY_BYTES = 64 * 1024
MAX_WRITING_REQUEST_BYTES = writing_store.MAX_CHAPTER_BYTES + 64 * 1024
WRITING_BACKUP_DIR = os.path.join(BACKUP_DIR, "obsidian-writing")
NTN = "/usr/local/bin/ntn"
DS_FOCUS = "00000000-0000-4000-8000-000000000001"  # 今日番茄追踪表
DS_PROJECT = "00000000-0000-4000-8000-000000000002"  # 项目管理
DS_TASKS = "00000000-0000-4000-8000-000000000003"  # 待办数据库
DS_RECORDS = "00000000-0000-4000-8000-000000000004"  # 日常记录 - 日历视图
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


from server.article import (  # noqa: E402
    MAX_ARTICLE_BYTES, MAX_ARTICLE_CHARS, ARTICLE_TIMEOUT,
    _validate_article_url, _SafeArticleRedirectHandler, _ArticleHTMLParser,
    _extract_article, _decode_article_body, _fetch_article,
)
from server.feeds import (  # noqa: E402
    _rss_read, _rss_write, _regen_sections, _fetch_text, _probe_rss,
    _feed_group_id, _feed_reindex_meta, _feed_url_key, _feed_add,
    _feed_pause, _feed_delete, _strip_html, _parse_feed_time,
    _rss_parse_entries, _feed_fetch_apply, _feed_refresh, _refresh_stale_feeds,
)


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
# /zsxq-daily.html 是独立日报页：HTML 本身不含真实数据，数据只走 /zsxq-daily.json（令牌门）。
STATIC_ALLOW = {"/workbench.html", "/zsxq-daily.html", "/assets/lingyi-logo.png", "/assets/favicon.png"}

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


from server.notion import (  # noqa: E402
    _CLIENT_SELECT_PROPS, _CLIENT_DATE_PROPS, _CLIENT_TEXT_PROPS,
    _CLIENT_WRITABLE, _CLIENT_LOCAL_KEYS,
    _PROJECT_WRITABLE, _PROJECT_LOCAL_KEYS, _PROJECT_SCHEMA_CACHE, _PROJECT_RELATION_CACHE,
    _rich_text_value, _ntn_api_call, _build_client_properties, _notion_update_page,
    _project_schema_from_current, _notion_project_schema, _project_relation_kind,
    _relation_option_from_page, _project_relation_lookup, _project_relation_search,
    _project_option_names, _project_relation_ids, _build_project_properties,
    _build_focus_properties, _notion_create_focus_page, _write_focus_update,
    _client_money_note, _write_client_update, _write_project_update,
)


def _data_version(path=DATA):
    """Return a cheap opaque version for conditional data reads."""
    try:
        st = os.stat(path)
        return f"{st.st_mtime_ns:x}-{st.st_size:x}"
    except FileNotFoundError:
        return "missing"


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


# 知识星球同步、详情、标签、AI 草稿续写与两步发布已迁至 server/zsxq.py
import server.zsxq as _zsxq_mod  # noqa: E402
bind_app_globals(globals())
_zsxq_mod.bind_app_globals(globals())
from server.zsxq import (  # noqa: E402
    _find_cli, _node_bin_dir, _cli_env,
    ZSXQ_CLI, _ZSXQ_DETAIL_CACHE, _ZSXQ_DETAIL_TTL,
    ZSXQ_PUBLISH_CONFIG, ZSXQ_PUBLISH_LOG, ZSXQ_HISTORICAL_SEEDS,
    _ZSXQ_HASHTAG_CACHE, _ZSXQ_TAGOPT_CACHE, _ZSXQ_SYNC_LOCK,
    ZSXQ_OLLAMA_URL, ZSXQ_DRAFT_MODEL, ZSXQ_DRAFT_TIMEOUT, ZSXQ_DRAFT_MAX, ZSXQ_STYLE,
    zsxq_clean_text, zsxq_publish_config, zsxq_publish_targets, zsxq_compose,
    zsxq_publish_digest, zsxq_recent_publishes, zsxq_publish_history, zsxq_publish,
    zsxq_group_hashtags, sort_tag_options, zsxq_group_tag_options, zsxq_tag_line,
    merge_zsxq_snapshot, zsxq_only_sync, zsxq_draft_prompt, zsxq_clean_draft,
    zsxq_generate_draft, zsxq_preview_warnings, zsxq_annotation, zsxq_set_annotation,
    zsxq_log_publish, zsxq_topic_detail,
)


TOKEN = _load_token()


class Handler(http.server.SimpleHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for key, value in (headers or {}).items():
                self.send_header(key, str(value))
            self.end_headers()
            if data:
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            # 浏览器切页或取消预加载时断开连接属正常现象，静默忽略以免污染日志
            pass

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def end_headers(self):
        # 全局防 MIME 嗅探与 Referer 泄漏（避免点击外链时把本机地址带出去）
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # workbench.html / zsxq-daily.html 自身就是应用入口，没有带哈希的资源文件名可供换缓存。
        # 若沿用 SimpleHTTPRequestHandler 默认的 Last-Modified 协商，Safari 可能继续
        # 展示内存或往返缓存中的旧页面，看起来像代码没有生效。入口 HTML 明确禁止
        # 存储，品牌图片等静态资源仍可正常缓存。
        parsed = urlparse(self.path)
        if parsed.path in ("/workbench.html", "/zsxq-daily.html"):
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            # 禁止任意外部脚本域加载与跨站 iframe 嵌入，允许单文件内联、站内阅读远程图片与霞鹜文楷 CDN 字体
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "font-src 'self' data: https://cdn.jsdelivr.net; "
                "img-src 'self' data: https: http:; "
                "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
            )
            # 入口页若通过合法 ?token= 引导进入，自动种下 HttpOnly SameSite=Strict Cookie，
            # 让前端可立刻清洗地址栏里的 ?token=，降低地址栏/历史记录暴露面。
            qs_tok = (parse_qs(parsed.query).get("token") or [""])[0]
            if qs_tok and secrets.compare_digest(qs_tok, TOKEN):
                self.send_header(
                    "Set-Cookie",
                    f"{SESSION_COOKIE}={TOKEN}; HttpOnly; SameSite=Strict; Path=/",
                )
        super().end_headers()

    def _cookie_token(self):
        raw = self.headers.get("Cookie", "")
        if not raw:
            return ""
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return ""
        morsel = jar.get(SESSION_COOKIE)
        return morsel.value if morsel else ""

    def _authorized(self):
        qs = parse_qs(urlparse(self.path).query)
        supplied = (qs.get("token") or [""])[0]
        cookie_tok = self._cookie_token()
        if cookie_tok:
            supplied = cookie_tok
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
            version = _data_version()
            if self.headers.get("If-None-Match", "").strip('"') == version:
                self._send(304, b"", headers={"ETag": f'"{version}"', "X-Data-Version": version,
                                                "Cache-Control": "no-cache"})
                return
            try:
                with open(DATA, encoding="utf-8") as f:
                    self._send(200, f.read(), headers={"ETag": f'"{version}"',
                                                        "X-Data-Version": version,
                                                        "Cache-Control": "no-cache"})
            except FileNotFoundError:
                self._send(404, {"error": "no data"})
            return
        if p == "/api/data-meta":
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            version = _data_version()
            if version == "missing":
                self._send(404, {"error": "no data"})
                return
            try:
                updated_at = datetime.datetime.fromtimestamp(os.stat(DATA).st_mtime, datetime.timezone.utc).isoformat()
            except FileNotFoundError:
                self._send(404, {"error": "no data"})
                return
            self._send(200, {"version": version, "updatedAt": updated_at},
                       headers={"Cache-Control": "no-cache"})
            return
        if p == "/api/status":
            # `syncing` 是给自动化用的：/api/sync 用的是**非阻塞**锁，
            # 若上一次同步还在跑（最长 SYNC_TIMEOUT=300s），新请求会直接拿到 409
            # 「已有一次同步正在进行」。测试/脚本能靠这个字段判断"现在是不是能发起同步"，
            # 否则会把 409 误读成"同步功能坏了"（2026-09-30 实测：一个被中途杀掉的
            # 测试进程留下服务端同步仍在跑，随后 4 条断言全红，全是误报）。
            # 页面本身不依赖它，加它不改变探活语义。
            self._send(200, {"served": True, "port": PORT, "version": APP_VERSION, "syncing": _sync_lock.locked()})
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
        if p == "/api/zsxq/tags":
            # 常用标签候选（按使用次数排序），发帖面板用来出快捷选项。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            cfg = zsxq_publish_config()
            group_id = (parse_qs(urlparse(self.path).query).get("group") or [cfg.get("groupId") or ""])[0]
            if not group_id:
                self._send(400, {"error": "没有配置星球"})
                return
            items = zsxq_group_tag_options(str(group_id))
            self._send(200, {"ok": True, "group": group_id, "count": len(items),
                             "items": items[:60]})
            return
        if p == "/api/zsxq/publish-history":
            # 发布记录：日志本来就在写，这里只是读出来给页面看
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            self._send(200, {"ok": True, "rows": zsxq_publish_history(20)})
            return
        if p == "/api/zsxq/publish-config":
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            cfg = zsxq_publish_config()
            self._send(200, {
                "ok": True,
                "groupId": cfg.get("groupId") or "",
                "groupName": cfg.get("groupName") or "",
                "targets": zsxq_publish_targets(cfg),
                "enabled": bool(zsxq_publish_targets(cfg)),
                "aiModes": ["aigc", "personal_perspective", "none"],
            })
            return
        if p == "/api/zsxq/topic":
            # 只读：把一条星球主题的全文取回给页面展开看。不写、不发、不改。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            topic_id = (parse_qs(urlparse(self.path).query).get("id") or [""])[0].strip()
            if not topic_id.isdigit():
                self._send(400, {"error": "id 必须是数字的主帖 ID"})
                return
            result = zsxq_topic_detail(topic_id)
            self._send(200 if result.get("ok") else 502, result)
            return
        if p == "/radar.json":
            # 选题雷达：只读派生快照，和 social.json 共用一把令牌。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                with open(RADAR, encoding="utf-8") as f:
                    self._send(200, f.read())
            except FileNotFoundError:
                self._send(404, {"error": "no radar snapshot",
                                 "hint": "先跑 python3 scripts/sync/radar-sync.py 生成选题雷达"})
            return
        if p == "/zsxq-daily.json":
            # 知识星球日报：只读派生快照，和 social.json / radar.json 共用令牌。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                with open(ZSXQ_DAILY, encoding="utf-8") as f:
                    self._send(200, f.read())
            except FileNotFoundError:
                self._send(404, {"error": "no zsxq daily snapshot",
                                 "hint": "先跑 python3 scripts/sync/zsxq-daily-sync.py 生成知识星球日报"})
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
        if p == "/wechat.json":
            # 与 data.json 共用令牌：里面是真实联系人名字和消息预览，不能裸奔。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            try:
                with open(WECHAT, encoding="utf-8") as f:
                    self._send(200, f.read())
            except FileNotFoundError:
                self._send(404, {"error": "no wechat snapshot",
                                 "hint": "先跑 python3 scripts/sync/wechat-sync.py 生成微信快照"})
            return
        if p == "/api/wechat/capabilities":
            # 预留契约：当前只读。状态/跟进/草稿先存在浏览器本地，
            # 未来若接 Notion 或微信写入，必须由这里显式打开能力，不允许页面偷偷直连。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            snapshot = {}
            try:
                with open(WECHAT, encoding="utf-8") as f:
                    snapshot = json.load(f)
            except Exception:
                snapshot = {}
            summary = snapshot.get("summary") if isinstance(snapshot, dict) else {}
            self._send(200, {
                "ok": True,
                "version": 1,
                "mode": "reserved-readonly",
                "readOnly": True,
                "writes": {"wechat": False, "notion": False},
                "mcp": snapshot.get("mcp", {}),
                "coverage": {
                    "generatedAt": snapshot.get("generatedAt"),
                    "available": snapshot.get("available"),
                    "lookbackDays": summary.get("businessLookbackDays"),
                    "opportunities": summary.get("opportunities", 0),
                    "consultations": summary.get("consultations", 0),
                    "scanned": summary.get("scanned", 0),
                },
                "reservedEndpoints": {
                    "action": "/api/wechat/action",
                    "replyDraft": "/api/wechat/reply-draft",
                },
            })
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
        from server.routes_post import handle_post
        return handle_post(self)

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
