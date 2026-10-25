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

The WorkBuddy agent also reads/writes data.json directly for Notion sync.
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
import json
import glob
import os
import secrets
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlparse, parse_qs

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(DIR, "data.json")
# 派生快照（资讯/写作/复盘）。**只读**：页面永远不写它，只由 scripts/sync/sections.py 生成。
# 与 data.json 分开存的原因见 scripts/sync/sections.py 文件头：data.json 是 Notion 同步产物、
# 有写入护栏，混进去会被「同步一次」冲掉。
SECTIONS = os.path.join(DIR, "sections.json")
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
    """调 TypeSafe Jev 对一段文本做质量判断。只读 ~/.config/jev-jarvis/env，
    走本机 7890 代理。返回 (ok, data_or_error)。"""
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
    proxy = urllib.request.ProxyHandler({"https": "http://127.0.0.1:7890"})
    try:
        resp = urllib.request.build_opener(proxy).open(req, timeout=30)
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
RSS_DATA = os.path.expanduser(
    os.environ.get("WB_RSS_DATA",
    "~/Documents/Obsidian/.obsidian/plugins/qiaomu-ai-rss/data.json").expanduser())
SECTIONS_SCRIPT = os.path.join(DIR, "scripts", "sync", "sections.py")
PORT = 8787
TOKEN_FILE = os.path.expanduser("~/.config/daily-workbench/sync-token")
SYNC_SCRIPT = os.path.join(DIR, "scripts", "sync-now.sh")
SYNC_TIMEOUT = 180
SYNC_LOG = os.path.join(DIR, "sync-log.json")
SYNC_LOG_KEEP = 20

BACKUP_DIR = os.path.join(DIR, "backups")
KEEP_BACKUPS = 20
GUARD_MIN_EXISTING = 10
GUARD_MIN_RATIO = 0.5
MAX_BODY_BYTES = 20 * 1024 * 1024

REQUIRED_KEYS = ("projects", "earnings")

# RSS 订阅源管理的安全边界：
#  * 只写 qiaomu-ai-rss 的 data.json，且写前先备份到项目 backups/
#  * 探查只发 HTTP GET、只解析 RSS/HTML 的 <link> 标签，不执行任何远程内容
#  * 收录/删除都要求与 /data.json 相同的 bearer token
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
    bak = os.path.join(BACKUP_DIR, "qiaomu-rss-data.%s.bak"
                       % time.strftime("%Y%m%d-%H%M%S"))
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
    req = urllib.request.Request(url, headers={"User-Agent": "WorkBuddy/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read(limit).decode("utf-8", "replace")


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


def _feed_add(d, url, title, group):
    subs = d.setdefault("subscriptions", [])
    if any(s.get("url") == url for s in subs):
        raise ValueError("这个 RSS 地址已在列表里")
    sid = "local:" + __import__("hashlib").sha256(url.encode()).hexdigest()
    name = title or url.split("//")[-1].split("/")[0]
    subs.insert(0, {"id": sid, "url": url, "name": name,
                    "group": group or "", "entries": []})
    return name

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


def _build_sync_steps(code, out, state):
    """把一次同步拆成 Linux 启动日志式的步骤行（✓/✗ + 一句话结果）。
    步骤顺序与 sync-now.sh 一致；失败时只列到挂掉的那一步 —— 后面的没跑。"""
    import re as _re

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
        return steps

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
        return steps

    n = rss_subs()
    steps.append({"name": "资讯快照", "ok": fail_step != "sections",
                  "detail": ("订阅源 %d 个 · 已重建" % n) if n else "已重建"})
    if fail_step == "sections":
        return steps

    steps.append({"name": "自媒体快照", "ok": fail_step != "social",
                  "detail": "已刷新" if fail_step != "social" else "失败，保留上一份快照"})
    if fail_step == "social":
        return steps

    w = web_line()
    steps.append({"name": "网站监控", "ok": True,
                  "detail": w or "已刷新（快照缺失）"})
    steps.append({"name": "离线兜底页", "ok": ok,
                  "detail": "已重建" if ok else "失败"})
    return steps



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
            # 若上一次同步还在跑（最长 SYNC_TIMEOUT=180s），新请求会直接拿到 409
            # 「已有一次同步正在进行」。测试/脚本能靠这个字段判断"现在是不是能发起同步"，
            # 否则会把 409 误读成"同步功能坏了"（2026-09-30 实测：一个被中途杀掉的
            # 测试进程留下服务端同步仍在跑，随后 4 条断言全红，全是误报）。
            # 页面本身不依赖它，加它不改变探活语义。
            self._send(200, {"served": True, "port": PORT, "syncing": _sync_lock.locked()})
            return
        if p == "/api/feeds":
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            d = _rss_read()
            feeds = []
            if isinstance(d, dict):
                for s in (d.get("subscriptions") or []):
                    feeds.append({"name": s.get("name") or "(未命名)",
                                  "url": s.get("url") or "",
                                  "group": s.get("group") or ""})
            self._send(200, {"feeds": feeds})
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
        if p == "/api/notes/push":
            # 把「记一笔」的本地草稿（source:"local"）真正写进 Notion「副业收入」库。
            # 复用 notion-push-drafts.py --confirm：它只碰 source=="local" 的记录，
            # Notion 拉来的只读条目绝不动；写入成功后本地自动转为「已入 Notion」。
            if not self._authorized():
                self._send(401, {"error": "unauthorized"})
                return
            script = os.path.join(DIR, "scripts", "notion-push-drafts.py")
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
                started = time.time()
                try:
                    proc = subprocess.run(
                        ["bash", SYNC_SCRIPT],
                        cwd=DIR, capture_output=True, text=True,
                        timeout=SYNC_TIMEOUT,
                    )
                except subprocess.TimeoutExpired:
                    self._send(504, {"error": "同步超时（%ds）" % SYNC_TIMEOUT})
                    return
                out = ((proc.stdout or "") + (proc.stderr or "")).strip()
                state = _read_current() or {}
                ok = proc.returncode == 0
                steps = _build_sync_steps(proc.returncode, out, state)
                self._send(200 if ok else 500, {
                    "ok": proc.returncode == 0,
                    "code": proc.returncode,
                    "elapsed": round(time.time() - started, 1),
                    "output": out[-4000:],
                    "earnings": len(state.get("earnings", [])),
                    "projects": len(state.get("projects", [])),
                    "lastNotionSync": state.get("lastNotionSync"),
                })
                _sync_log_append({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "ok": ok,
                    "code": proc.returncode,
                    "elapsed": round(time.time() - started, 1),
                    "earnings": len(state.get("earnings", [])),
                    "projects": len(state.get("projects", [])),
                    "error": "" if ok else out.splitlines()[0][:160] if out else "",
                    "trigger": trigger if trigger in ("manual", "auto") else "manual",
                    "steps": steps,
                })
            finally:
                _sync_lock.release()
            return
        if p in ("/api/feeds/probe", "/api/feeds/add", "/api/feeds/delete"):
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
                self._send(200, {"hits": _probe_rss(url)})
                return
            d = _rss_read()
            if not isinstance(d, dict):
                self._send(404, {"error": "RSS data file not found",
                                 "hint": RSS_DATA})
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
            # /api/feeds/delete
            url = str(obj.get("url") or "").strip()
            subs = d.get("subscriptions") or []
            before = len(subs)
            d["subscriptions"] = [s for s in subs if s.get("url") != url]
            if len(d["subscriptions"]) == before:
                self._send(404, {"error": "没有找到这个订阅源"})
                return
            _rss_write(d)
            _regen_sections()
            self._send(200, {"ok": True})
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
