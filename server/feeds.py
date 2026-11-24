"""
[INPUT]: 依赖 rss-store.json、scripts/sync/sections.py 与可选本机代理 FEED_PROXY
[OUTPUT]: 导出 _rss_read、_rss_write、_regen_sections、_fetch_text、_probe_rss、_feed_*、_rss_parse_entries、_refresh_stale_feeds
[POS]: server/ RSS 订阅源管理与抓取解析子模块，由 app.py 路由 /api/feeds/* 调用
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""
import datetime
import email.utils
import glob
import hashlib
import html as _html
import json
import os
import re as _re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

from server.runtime import cfg as _cfg

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RSS_DATA = os.path.join(DIR, "rss-store.json")
BACKUP_DIR = os.path.join(DIR, "backups")
KEEP_RSS_BACKUPS = 10
FEED_PROXY = os.environ.get("WB_FEED_PROXY") or "http://127.0.0.1:7890"
SECTIONS_SCRIPT = os.path.join(DIR, "scripts", "sync", "sections.py")

def _rss_read():
    try:
        with open(_cfg("RSS_DATA", RSS_DATA), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def _rss_write(d):
    rss_data = _cfg("RSS_DATA", RSS_DATA)
    bak_dir = _cfg("BACKUP_DIR", BACKUP_DIR)
    keep_n = _cfg("KEEP_RSS_BACKUPS", KEEP_RSS_BACKUPS)
    os.makedirs(bak_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    bak = os.path.join(bak_dir, "rss-store.%s.bak" % stamp)
    try:
        shutil.copy2(rss_data, bak)
    except FileNotFoundError:
        pass
    try:
        kept = sorted(glob.glob(os.path.join(bak_dir, "rss-store.*.bak")))
        for stale in kept[:-keep_n]:
            os.remove(stale)
    except OSError:
        pass
    # 剔除旧版 Qiaomu Obsidian 插件遗留的 UI 缓存键（全库零引用，单字段即占 ~836 KB）
    payload = {k: v for k, v in d.items() if k != "channelStates"} if isinstance(d, dict) and "channelStates" in d else d
    tmp = rss_data + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, rss_data)


def _regen_sections():
    try:
        subprocess.run([sys.executable, _cfg("SECTIONS_SCRIPT", SECTIONS_SCRIPT), "--write"],
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
            {"http": _cfg("FEED_PROXY", FEED_PROXY), "https": _cfg("FEED_PROXY", FEED_PROXY)}))
        with opener.open(req, timeout=15) as r:
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

