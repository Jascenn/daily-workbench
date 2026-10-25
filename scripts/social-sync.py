#!/usr/bin/env python3
"""Build the read-only social snapshot consumed by the content screen.

This first version only imports evidence that already exists on this Mac:

* a historical SuperX PDF snapshot for the configured X handle;
* a 41-row ZSXQ content export;
* the presence (not runtime health) of local draft-publishing skills.

Missing platform metrics stay missing. In particular, this script must never turn
"not connected" into a numeric zero or infer that today's post was not published.
"""
from __future__ import annotations

import datetime as dt
import csv
import email.utils
import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote, urlparse
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "social.json"

# ===== 个人化配置：开源默认值是示例，实际使用时改这里或用环境变量覆盖 =====
import os
KNOWLEDGE_VAULT = Path(os.environ.get("WB_OBSIDIAN_VAULT", "~/Documents/Obsidian").expanduser())
X_HANDLE = os.environ.get("WB_X_HANDLE", "@your_handle")
WECHAT_ACCOUNT = os.environ.get("WB_WECHAT_ACCOUNT", "示例公众号")
ZSXQ_GROUPS = [g.strip() for g in os.environ.get("WB_ZSXQ_GROUPS", "示例星球A,示例星球B").split(",") if g.strip()]
# 可选的历史快照文件（无则自动降级为「历史快照不可用」状态，不报错）
X_PDF = Path(os.environ.get("WB_X_PDF", "")) if os.environ.get("WB_X_PDF") else None
ZSXQ_XLSX = Path(os.environ.get("WB_ZSXQ_XLSX", "")) if os.environ.get("WB_ZSXQ_XLSX") else None
ZSXQ_CSV = Path(os.environ.get("WB_ZSXQ_CSV", "")) if os.environ.get("WB_ZSXQ_CSV") else None

XREACH_SESSION = Path.home() / ".config" / "xfetch" / "session.json"
OPENCLAW_ENV = Path.home() / ".openclaw" / ".env"
WECHAT_PUSH = Path.home() / ".openclaw" / "workspace" / "articles" / "mp_push.py"
XHS_PIPELINE = Path.home() / ".openclaw" / "workspace" / "skills" / "xiaohongshu-skills" / "scripts" / "fill_draft_defaults.py"
MCPORTER_CONFIG = Path.home() / ".mcporter" / "mcporter.json"
ZSXQ_TOKEN = Path.home() / ".config" / "daily-workbench" / "zsxq-token"
MPDT_BIN = Path.home() / ".venvs" / "mpdt" / "bin" / "mpdt"
MPDT_PROFILE = Path.home() / ".mpdt" / "chrome-profile"

XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def zsxq_live_snapshot() -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    """Read the star group through the official web API with a browser cookie.

    The token is the zsxq_access_token cookie copied from wx.zsxq.com; it is
    read-only traffic (group/topic listing) and never posts anything.  A
    missing or expired token degrades to None so the caller can keep serving
    the historical export instead of inventing numbers.
    """
    token = ZSXQ_TOKEN.read_text(encoding="utf-8").strip() if ZSXQ_TOKEN.exists() else ""
    if not token:
        return None, []

    def fetch(url: str) -> dict[str, object]:
        # 知识星球对无浏览器特征的请求偶发 1059 拦截，带 UA 并重试一次可消除绝大多数偶发失败。
        headers = {"Cookie": f"zsxq_access_token={token}",
                   "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"}
        request = urllib.request.Request(url, headers=headers)
        proxy = os.environ.get("ZSXQ_PROXY", "http://127.0.0.1:7890")
        handler = urllib.request.ProxyHandler({} if proxy == "direct" else {"http": proxy, "https": proxy})
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.build_opener(handler).open(request, timeout=20) as response:
                    value = json.loads(response.read().decode("utf-8", errors="replace"))
                    return value if isinstance(value, dict) else {}
            except (OSError, ValueError) as error:
                last_error = error
                time.sleep(1 + attempt * 2)
        raise last_error if last_error else OSError("zsxq fetch failed")

    try:
        groups = fetch("https://api.zsxq.com/v2/groups")
    except (OSError, ValueError):
        return None, []
    if groups.get("succeeded") is not True:
        return None, []
    # token 是账号级凭据，会列出加入的所有星球；只聚合自己创建/管理的星球，绝不误抓别人的。
    group_list = [g for g in (groups.get("resp_data") or {}).get("groups") or [] if isinstance(g, dict)]
    mine = [g for g in group_list if str(g.get("name") or "").strip() in ZSXQ_GROUPS]
    if not mine:
        return {"platform": "知识星球", "account": ZSXQ_GROUP_NAME, "profileUrl": "https://wx.zsxq.com/",
                "dashboardUrl": "https://wx.zsxq.com/", "asOf": None,
                "sourceKind": "知识星球 Web API（登录态只读）", "freshness": "partial", "metrics": {},
                "note": "登录态有效，但星球列表里一个都没匹配上（" + "、".join(ZSXQ_GROUPS) + "）；请确认星球名。"}, []
    posts: list[dict[str, object]] = []
    per_group: dict[str, int] = {}
    failed_groups: list[str] = []
    for group in mine:
        group_name = str(group.get("name") or "").strip()
        topics: dict[str, object] = {}
        if per_group:  # 星球之间留间隔，降低触发 1059 风控的概率
            time.sleep(2)
        for attempt in range(3):
            try:
                topics = fetch(f"https://api.zsxq.com/v2/groups/{group.get('group_id')}/topics?scope=all&count=30")
                break
            except (OSError, ValueError):
                time.sleep(3 + attempt * 4)
        per_group[group_name] = 0
        if topics.get("succeeded") is not True:
            failed_groups.append(group_name)
        for topic in (topics.get("resp_data") or {}).get("topics") or []:
            if not isinstance(topic, dict):
                continue
            talk = topic.get("talk") or {}
            article = talk.get("article") or {}
            title = str(article.get("title") or talk.get("title") or topic.get("title") or "").strip()
            if not title:
                text = str((talk.get("text") or ""))[:60].strip()
                if not text:
                    continue
                title = text
            published = parse_date(topic.get("create_time"))
            likes = topic.get("likes_count")
            comments = topic.get("comments_count")
            readers = topic.get("reading_count")
            ident = hashlib.sha1(str(topic.get("topic_id") or title).encode("utf-8")).hexdigest()[:12]
            posts.append({
                "id": "zsxq-live-" + ident,
                "platform": "知识星球",
                "publishedAt": published.isoformat(timespec="seconds") if published else None,
                "title": title,
                "url": f"https://wx.zsxq.com/topic/{topic.get('topic_id')}" if topic.get("topic_id") else None,
                "metrics": {
                    "reads": readers if isinstance(readers, (int, float)) else None,
                    "likes": likes if isinstance(likes, (int, float)) else None,
                    "comments": comments if isinstance(comments, (int, float)) else None,
                },
            })
            per_group[group_name] += 1
    posts.sort(key=lambda x: x.get("publishedAt") or "", reverse=True)
    newest = max((p["publishedAt"] for p in posts if p.get("publishedAt")), default=None)
    account: dict[str, object] = {
        "platform": "知识星球", "account": "、".join(ZSXQ_GROUPS),
        "profileUrl": "https://wx.zsxq.com/", "dashboardUrl": "https://wx.zsxq.com/",
        "asOf": str(newest)[:10] if newest else None,
        "sourceKind": "知识星球 Web API（登录态只读）", "freshness": "fresh" if posts else "partial",
        "metrics": {"posts": len(posts), "groups": len(mine), "perGroup": per_group},
        "note": ("已聚合 %d 个星球的最近话题（%s）；只读拉取，不发帖不修改。" % (
            len(mine), " · ".join(f"{name} {count} 条" for name, count in per_group.items()))) +
                (f" 本轮 {len(failed_groups)} 个星球被限流未拉到（{'、'.join(failed_groups)}），对应计数为 0 不是没更新。" if failed_groups else ""),
    }
    if not posts:
        account["freshness"] = "partial"
        account["note"] = "登录态有效但没有拉到话题明细，统计字段保持为空。"
    return account, posts

def now_iso() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def safe_http_url(value: object) -> str:
    text = str(value or "").strip()
    try:
        p = urlparse(text)
    except ValueError:
        return ""
    return text if p.scheme in {"http", "https"} and p.netloc else ""


def column_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "")
    n = 0
    for ch in (letters.group(0) if letters else "A"):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_simple_xlsx(path: Path) -> list[dict[str, object]]:
    """Read the first worksheet with stdlib only; enough for this exported table."""
    if not path.exists():
        return []
    with zipfile.ZipFile(path) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{XML_NS}si"):
                shared.append("".join((node.text or "") for node in si.iter(f"{XML_NS}t")))

        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
        first_sheet = workbook.find(f"{XML_NS}sheets/{XML_NS}sheet")
        if first_sheet is None:
            return []
        rel_id = first_sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
        target = rel_map.get(rel_id, "worksheets/sheet1.xml")
        sheet_name = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
        sheet = ET.fromstring(zf.read(sheet_name))

        matrix: list[list[object]] = []
        for row in sheet.findall(f".//{XML_NS}sheetData/{XML_NS}row"):
            values: dict[int, object] = {}
            for cell in row.findall(f"{XML_NS}c"):
                idx = column_index(cell.attrib.get("r", "A1"))
                kind = cell.attrib.get("t", "")
                raw = cell.find(f"{XML_NS}v")
                if kind == "inlineStr":
                    inline = cell.find(f"{XML_NS}is")
                    value = "".join((n.text or "") for n in inline.iter(f"{XML_NS}t")) if inline is not None else ""
                else:
                    value = raw.text if raw is not None else ""
                    if kind == "s" and str(value).isdigit():
                        pos = int(value)
                        value = shared[pos] if pos < len(shared) else ""
                    elif kind == "n":
                        try:
                            number = float(value)
                            value = int(number) if number.is_integer() else number
                        except (TypeError, ValueError):
                            pass
                values[idx] = value
            if values:
                width = max(values) + 1
                matrix.append([values.get(i, "") for i in range(width)])
        if not matrix:
            return []
        headers = [str(x).strip() for x in matrix[0]]
        return [dict(zip(headers, row + [""] * (len(headers) - len(row)))) for row in matrix[1:]]


def parse_date(value: object) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            return email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None


def file_time(path: Path) -> str | None:
    if not path.exists():
        return None
    return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def env_flags(path: Path, names: set[str]) -> dict[str, bool]:
    found = {name: False for name in names}
    if not path.exists():
        return found
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in found:
                found[key.strip()] = bool(value.strip())
    except OSError:
        pass
    return found


def env_values(path: Path, names: set[str]) -> dict[str, str]:
    found = {name: "" for name in names}
    if not path.exists():
        return found
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in found:
                found[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return found


def tool_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidates = [
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
        Path.home() / ".local" / "bin" / name,
    ]
    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        candidates.extend(sorted(nvm_root.glob(f"*/bin/{name}"), reverse=True))
    return next((str(path) for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


def xreach_ready() -> bool:
    if not tool_binary("xreach") or not XREACH_SESSION.exists():
        return False
    try:
        session = json.loads(XREACH_SESSION.read_text(encoding="utf-8"))
        return bool(session.get("authToken") and session.get("ct0"))
    except (OSError, ValueError):
        return False


def run_json(command: list[str], timeout: int = 45) -> object | None:
    try:
        env = os.environ.copy()
        if command:
            # Keep the symlink's bin directory. NVM CLIs use `#!/usr/bin/env node`,
            # so resolving the symlink into node_modules would hide the sibling
            # `node` executable from launchd's minimal PATH.
            bindir = str(Path(command[0]).parent)
            env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        result = subprocess.run(command, check=True, text=True, capture_output=True, timeout=timeout, env=env)
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def wechat_api_snapshot() -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]], dict[str, str]]:
    """Read official-account metrics without publishing or changing the account.

    Credentials can be valid while the official API still rejects the current
    outbound IP.  Keep that state distinct from missing credentials so the UI
    can show the one concrete next step instead of a vague empty card.
    """
    dashboard = "https://mp.weixin.qq.com/"
    account: dict[str, object] = {
        "platform": "公众号", "account": WECHAT_ACCOUNT, "profileUrl": "", "dashboardUrl": dashboard,
        "asOf": None, "sourceKind": "公众号官方 API", "freshness": "missing", "connectionState": "missing",
        "metrics": {}, "note": "尚未读取到公众号官方统计。",
    }
    status = {"state": "missing", "label": "官方数据未接入", "detail": "需要可用的 APPID、Secret 与 API 白名单。"}
    creds = env_values(OPENCLAW_ENV, {"WECHAT_APPID", "WECHAT_SECRET"})
    if not all(creds.values()):
        return account, [], [], status

    def fetch_json(url: str, payload: dict[str, object] | None = None, timeout: int = 25) -> dict[str, object]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        # 家里宽带是动态 IP，直连出口会漂移（223.66.x.x 段内跳），公众号白名单没法长期绑定。
        # 快切方案：公众号 API 固定走 Surge 出口（当前 103.151.173.208），
        # 白名单填这一个 IP 即可。用 WECHAT_EGRESS=direct 可切回直连兜底。
        egress = os.environ.get("WECHAT_EGRESS", "").strip().lower()
        handler = urllib.request.ProxyHandler({}) if egress == "direct" else urllib.request.ProxyHandler({
            "http": os.environ.get("WECHAT_PROXY", "http://127.0.0.1:7890"),
            "https": os.environ.get("WECHAT_PROXY", "http://127.0.0.1:7890"),
        })
        opener = urllib.request.build_opener(handler)
        try:
            with opener.open(request, timeout=timeout) as response:
                value = json.loads(response.read().decode("utf-8", errors="replace"))
                return value if isinstance(value, dict) else {}
        except (OSError, ValueError, urllib.error.URLError):
            return {}

    token_url = "https://api.weixin.qq.com/cgi-bin/token?" + urllib.parse.urlencode({
        "grant_type": "client_credential", "appid": creds["WECHAT_APPID"], "secret": creds["WECHAT_SECRET"],
    })
    auth = fetch_json(token_url)
    access_token = str(auth.get("access_token") or "")
    if not access_token:
        code = int(auth.get("errcode") or 0)
        if code == 40164:
            reason = "APPID 与 Secret 已配置，但当前出口 IP 不在公众号 API 白名单。"
            account.update({"connectionState": "blocked", "blockReason": reason, "note": reason})
            status = {"state": "blocked", "label": "凭据有效，白名单阻塞", "detail": reason + " 放行后同步会自动回填文章与关注数据。"}
        else:
            reason = f"公众号官方 API 鉴权失败（错误 {code or '未知'}）。"
            account.update({"connectionState": "error", "blockReason": reason, "note": reason})
            status = {"state": "partial", "label": "官方 API 鉴权失败", "detail": reason}
        return account, [], [], status

    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()

    def api_post(path: str, payload: dict[str, object]) -> dict[str, object]:
        return fetch_json(f"https://api.weixin.qq.com{path}?access_token={access_token}", payload)

    basic_info = fetch_json(f"https://api.weixin.qq.com/cgi-bin/account/getaccountbasicinfo?access_token={access_token}")
    published = api_post("/cgi-bin/freepublish/batchget", {"offset": 0, "count": 20, "no_content": 1})
    material = api_post("/cgi-bin/material/get_materialcount", {})
    material_items = api_post("/cgi-bin/material/batchget_material", {"type": "news", "offset": 0, "count": 20})
    article_total = api_post("/datacube/getarticletotal", {"begin_date": yesterday, "end_date": yesterday})
    user_cumulate = api_post("/datacube/getusercumulate", {"begin_date": yesterday, "end_date": yesterday})
    user_summary = api_post("/datacube/getusersummary", {"begin_date": yesterday, "end_date": yesterday})

    stats_by_title: dict[str, dict[str, object]] = {}
    for row in article_total.get("list") or []:
        if not isinstance(row, dict):
            continue
        details = [x for x in (row.get("details") or []) if isinstance(x, dict)]
        stats_by_title[str(row.get("title") or "").strip()] = details[-1] if details else row

    posts: list[dict[str, object]] = []
    published_available = not published.get("errcode")
    content_items = published.get("item") or [] if published_available else material_items.get("item") or []
    for item in content_items:
        if not isinstance(item, dict):
            continue
        content = item.get("content") or {}
        articles = content.get("news_item") or [] if isinstance(content, dict) else []
        updated = item.get("update_time")
        try:
            published_at = dt.datetime.fromtimestamp(int(updated), dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
        except (TypeError, ValueError, OSError, OverflowError):
            published_at = None
        for article in articles:
            if not isinstance(article, dict):
                continue
            title = str(article.get("title") or "未命名文章").strip()
            link = safe_http_url(article.get("url"))
            detail = stats_by_title.get(title, {})
            ident = hashlib.sha1((str(item.get("article_id") or item.get("media_id") or "") + title).encode("utf-8")).hexdigest()[:12]
            posts.append({
                "id": "wechat-" + ident, "platform": "公众号", "publishedAt": published_at,
                "title": title, "url": link,
                "contentState": "published" if published_available else "material",
                "metrics": {
                    "reads": detail.get("int_page_read_count"), "likes": detail.get("like_count"),
                    "comments": None, "shares": detail.get("share_count"), "collects": detail.get("add_to_fav_count"),
                },
            })

    cumulate_rows = [x for x in (user_cumulate.get("list") or []) if isinstance(x, dict)]
    summary_rows = [x for x in (user_summary.get("list") or []) if isinstance(x, dict)]
    article_rows = [x for x in (article_total.get("list") or []) if isinstance(x, dict)]
    latest_cumulate = cumulate_rows[-1] if cumulate_rows else {}
    total_reads = sum(int((stats_by_title.get(str(row.get("title") or "").strip()) or {}).get("int_page_read_count") or 0) for row in article_rows)
    total_shares = sum(int((stats_by_title.get(str(row.get("title") or "").strip()) or {}).get("share_count") or 0) for row in article_rows)
    metrics = {
        "followers": latest_cumulate.get("cumulate_user"),
        "newFollowers": sum(int(row.get("new_user") or 0) - int(row.get("cancel_user") or 0) for row in summary_rows) if summary_rows else None,
        "publishedArticles": published.get("total_count") if not published.get("errcode") else None,
        "materialArticles": material.get("news_count") if not material.get("errcode") else None,
        "yesterdayReads": total_reads if article_rows else None,
        "yesterdayShares": total_shares if article_rows else None,
        "samplePosts": len(posts),
    }
    permission_errors = [
        str(value.get("errcode")) for value in (published, material, material_items, article_total, user_cumulate, user_summary)
        if isinstance(value, dict) and value.get("errcode")
    ]
    core_connected = any(metrics[key] is not None for key in (
        "followers", "newFollowers", "publishedArticles", "yesterdayReads", "yesterdayShares"
    ))
    connected = core_connected or metrics["materialArticles"] is not None
    limited = connected and bool(permission_errors) and not core_connected
    latest_post_date = max((str(post.get("publishedAt") or "")[:10] for post in posts), default="")
    account_type = {1: "订阅号", 2: "服务号", 3: "小程序"}.get(basic_info.get("account_type"), "未知类型")
    verify_info = basic_info.get("wx_verify_info") or {}
    verified = bool(verify_info.get("qualification_verify")) if isinstance(verify_info, dict) else False
    permission_reason = ""
    if permission_errors and account_type == "订阅号" and not verified:
        permission_reason = "当前账号为未认证订阅号；微信数据统计接口需要已完成微信认证的公众号。"
    elif permission_errors:
        permission_reason = "当前账号没有文章统计、用户分析或已发布内容接口权限。"
    account.update({
        "asOf": yesterday if core_connected else (latest_post_date or None),
        "sourceKind": "公众号官方 API · " + ("已发布内容与昨日统计" if published_available else "永久图文素材"),
        "freshness": "fresh" if connected and not permission_errors else "partial",
        "connectionState": "limited" if limited else ("connected" if connected else "limited"),
        "accountType": account_type,
        "verified": verified,
        "permissionReason": permission_reason,
        "metrics": metrics,
        "note": "官方 API 只读同步；未提供的指标保持未核验。" +
                (f" 已读取 {len(posts)} 篇永久图文素材；" if posts and not published_available else "") +
                ((permission_reason + " ") if permission_reason else "") +
                (f"受限接口返回：{', '.join(permission_errors)}。" if permission_errors else ""),
    })
    analysis: list[dict[str, object]] = []
    scored = [p for p in posts if isinstance((p.get("metrics") or {}).get("reads"), (int, float))]
    if len(scored) >= 3:
        values = sorted(int(p["metrics"]["reads"]) for p in scored)
        median = values[len(values) // 2]
        for post in sorted(scored, key=lambda p: int(p["metrics"]["reads"]), reverse=True)[:2]:
            analysis.append({"postId": post["id"], "label": "公众号昨日阅读较高", "tone": "ok",
                             "reason": f"昨日阅读 {post['metrics']['reads']}，高于当前可比文章中位数 {median}；仍需结合分享与关注净增判断。"})
    status = {
        "state": "partial" if permission_errors or not connected else "available",
        "label": ("已连接，账号未认证" if permission_errors and not verified else "已连接，统计权限不足") if permission_errors else ("官方数据已接通" if connected else "凭据有效，统计权限待开通"),
        "detail": account["note"] if permission_errors or not connected else "只读同步已发布文章、昨日阅读/分享与关注变化；不会推草稿或发布。",
    }
    return account, posts, analysis, status


def xreach_command(*args: str) -> list[str]:
    """Build a token-safe xreach command and reuse the local HTTP proxy if present."""
    command = [tool_binary("xreach") or "xreach"]
    proxy = os.environ.get("XREACH_PROXY", "").strip()
    if not proxy:
        try:
            with socket.create_connection(("127.0.0.1", 7890), timeout=0.25):
                proxy = "http://127.0.0.1:7890"
        except OSError:
            pass
    if proxy:
        command.extend(["--proxy", proxy])
    command.extend(["--timeout", "30000", "--json", *args])
    return command


def mpdt_snapshot() -> tuple[dict[str, object] | None, list[dict[str, object]], dict[str, str]]:
    """Read official-account stats from the local mpdt SQLite via its exporter.

    Each sync first refreshes the local database through mpdt fetch (Chrome
    CDP).  The dedicated debug Chrome is auto-started when not running, so
    sync works unattended; when login is missing the fetch fails and the
    wrapper degrades to the last good snapshot with an explicit state.
    """
    if not MPDT_BIN.exists():
        return None, [], {"state": "missing", "label": "mpdt 未安装", "detail": "本地库工具 ~/.venvs/mpdt 不存在，公众号统计走官方 API。"}

    cdp_ok = False
    try:
        with socket.create_connection(("127.0.0.1", 9222), timeout=1):
            cdp_ok = True
    except OSError:
        pass
    if not cdp_ok and MPDT_PROFILE.exists():
        # Chrome 136+ 禁止在默认用户目录开调试端口；这里固定用 mpdt 专用 profile 拉起。
        subprocess.Popen(["open", "-na", "Google Chrome", "--args",
                          f"--user-data-dir={MPDT_PROFILE}", "--remote-debugging-port=9222",
                          "--remote-allow-origins=*", "--no-first-run"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(10):
            time.sleep(1)
            try:
                with socket.create_connection(("127.0.0.1", 9222), timeout=1):
                    cdp_ok = True
                    break
            except OSError:
                continue
    fetch_rc = -1
    fetch_note = ""
    if cdp_ok:
        fetched = subprocess.run([str(MPDT_BIN), "fetch"], capture_output=True, text=True, timeout=180)
        fetch_rc, fetch_note = fetched.returncode, (fetched.stdout or fetched.stderr or "").strip().splitlines()[-1:] and (fetched.stdout or fetched.stderr or "").strip().splitlines()[-1] or ""
        # datacube 文章级分析只在发表后 7 天窗口内出数；回填一轮，老文章静默跳过属预期。
        subprocess.run([str(MPDT_BIN), "fetch", "--backfill", "7"], capture_output=True, text=True, timeout=180)
        # 完读率/渠道构成走单篇渠道页（30 天窗口）；没有窗口内文章时入库 0 篇属预期。
        detail_run = subprocess.run([str(MPDT_BIN), "detail", "--recent", "7"], capture_output=True, text=True, timeout=300)
        detail_note = (detail_run.stdout or detail_run.stderr or "").strip().splitlines()[-1:] or [""]
    result = subprocess.run([str(MPDT_BIN), "--json", "export", "--source", "publish_list",
                             "--format", "json", "--out", str(ROOT / "mpdt-export.json")],
                            capture_output=True, text=True, timeout=60)
    result_detail = subprocess.run([str(MPDT_BIN), "--json", "export", "--source", "article_detail",
                                    "--format", "json", "--out", str(ROOT / "mpdt-detail.json")],
                                   capture_output=True, text=True, timeout=60)
    payload_path = ROOT / "mpdt-export.json"
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8")) if payload_path.exists() else {}
    except (OSError, ValueError):
        payload = {}
    articles = payload.get("records") or (payload.get("data") or {}).get("rows") or []
    if not isinstance(articles, list):
        articles = []
    detail_payload_path = ROOT / "mpdt-detail.json"
    try:
        detail_payload = json.loads(detail_payload_path.read_text(encoding="utf-8")) if detail_payload_path.exists() else {}
    except (OSError, ValueError):
        detail_payload = {}
    detail_rows = detail_payload.get("records") or []
    if not isinstance(detail_rows, list):
        detail_rows = []
    detail_by_key: dict[tuple[str, int], dict[str, object]] = {}
    for row in detail_rows:
        if isinstance(row, dict):
            detail_by_key[(str(row.get("appmsgid")), int(row.get("itemidx") or 1))] = row
    if result.returncode != 0 or not payload:
        detail = (result.stderr or result.stdout or "").strip().splitlines()[-1:] or ["mpdt 导出失败"]
        state = {"state": "partial", "label": "mpdt 未取到数据", "detail": str(detail[0])[:200] + "（Chrome 需以 --remote-debugging-port=9222 启动并登录公众号后台；数据 T+1 更新）"}
        return None, [], state
    posts: list[dict[str, object]] = []
    for row in articles:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        published = parse_date(row.get("publish_date") or row.get("publish_time") or row.get("pub_time"))
        ident = hashlib.sha1((row.get("url") or title).encode("utf-8")).hexdigest()[:12]
        extra = detail_by_key.get((str(row.get("appmsgid")), int(row.get("itemidx") or 1)), {})
        posts.append({
            "id": "wechat-mpdt-" + ident, "platform": "公众号", "publishedAt": published.isoformat(timespec="seconds") if published else None,
            "title": title, "url": safe_http_url(row.get("url")),
            "contentState": "published",
            "metrics": {
                "reads": row.get("read_num"),
                "likes": (row.get("like_num") or 0) + (row.get("old_like_num") or 0) or None,
                "comments": row.get("comment_num"), "shares": row.get("share_num"),
                "readUsers": extra.get("read_uv"),
                "completionRate": extra.get("completion_rate"),
                "avgReadSeconds": extra.get("avg_read_sec"),
            },
        })
    if not posts:
        return None, [], {"state": "partial", "label": "mpdt 本地库为空", "detail": "还没抓到文章数据：Chrome 以 --remote-debugging-port=9222 启动并登录 mp.weixin.qq.com 后跑一次 mpdt fetch。"}
    reads = [int(p["metrics"]["reads"]) for p in posts if isinstance(p["metrics"].get("reads"), (int, float))]
    completion = [p["metrics"]["completionRate"] for p in posts if isinstance(p["metrics"].get("completionRate"), (int, float))]
    newest = max((str(p.get("publishedAt") or "") for p in posts), default="")
    account: dict[str, object] = {
        "platform": "公众号", "account": WECHAT_ACCOUNT, "profileUrl": "", "dashboardUrl": "https://mp.weixin.qq.com/",
        "asOf": newest[:10] if newest else None,
        "sourceKind": "mpdt 登录态抓取（Chrome CDP）", "freshness": "fresh",
        "metrics": {"samplePosts": len(posts), "avgReads": sum(reads) // len(reads) if reads else None,
                    "detailPosts": len(detail_rows),
                    "avgCompletion": (sum(completion) / len(completion)) if completion else None},
        "note": "数据来自本机 mpdt 本地库（每次同步自动抓取）；当天数据微信侧 T+1 才可得。" +
                (f" 完读率/渠道构成仅覆盖最近 7 天发表的文章（30 天窗口口径），当前有 {len(detail_rows)} 篇明细。" if detail_rows else " 完读率/渠道构成只对发表后 30 天内的文章有效；当前没有窗口内文章，下次发文后自动出现。"),
    }
    fetch_part = f"本轮 fetch {'成功' if fetch_rc == 0 else f'失败({fetch_rc})：{fetch_note[:80]}'}；" if cdp_ok else "调试 Chrome 未在线，用上次快照；"
    detail_part = f"完读率明细「{detail_note[0][:60]}」；" if cdp_ok and detail_note and detail_note[0] else ""
    return account, posts, {"state": "available" if fetch_rc == 0 else "partial", "label": "mpdt 已接通" if fetch_rc == 0 else "mpdt 用上次快照", "detail": f"{fetch_part}{detail_part}本地库 {len(posts)} 篇文章统计；只读抓取，不影响公众号。"}


def xreach_live_snapshot() -> tuple[dict[str, object] | None, list[dict[str, object]], list[dict[str, object]]]:
    """Read public X profile/timeline after the user has explicitly authenticated xreach."""
    if not xreach_ready():
        return None, [], []
    profile = run_json(xreach_command("user", X_HANDLE))
    timeline = run_json(xreach_command(
        "tweets", X_HANDLE, "-n", "30", "--all", "--max-pages", "5"
    ), timeout=90)
    if not isinstance(profile, dict) or not isinstance(timeline, dict):
        return None, [], []
    tweets = timeline.get("tweets") or timeline.get("items") or []
    if not isinstance(tweets, list):
        tweets = []
    posts: list[dict[str, object]] = []
    for tweet in tweets:
        if not isinstance(tweet, dict) or tweet.get("isRetweet"):
            continue
        ident = str(tweet.get("id") or "").strip()
        text = re.sub(r"\s+", " ", str(tweet.get("text") or "")).strip()
        if not ident or not text:
            continue
        # The timeline payload currently omits viewCount, while the public
        # single-tweet endpoint exposes it. Enrich only when it is missing and
        # keep all absent values as null instead of inventing a zero.
        if not isinstance(tweet.get("viewCount"), (int, float)):
            detail = run_json(xreach_command("tweet", ident), timeout=35)
            if isinstance(detail, dict):
                for key in ("viewCount", "likeCount", "replyCount", "retweetCount", "quoteCount", "bookmarkCount"):
                    if key in detail:
                        tweet[key] = detail[key]
        posts.append({
            "id": "x-" + ident,
            "platform": "X",
            "publishedAt": tweet.get("createdAt"),
            "title": text[:96] + ("…" if len(text) > 96 else ""),
            "url": f"https://x.com/{X_HANDLE}/status/{ident}",
            "metrics": {
                "reads": tweet.get("viewCount"),
                "likes": tweet.get("likeCount"),
                "comments": tweet.get("replyCount"),
                "shares": (int(tweet.get("retweetCount") or 0) + int(tweet.get("quoteCount") or 0)),
            },
        })
    dated = [(parse_date(p.get("publishedAt")), p) for p in posts]
    now = dt.datetime.now(dt.timezone.utc)
    mature = [p for when, p in dated if when and (now - when.astimezone(dt.timezone.utc)).days >= 2
              and isinstance(p["metrics"].get("reads"), (int, float))]
    views = sorted(int(p["metrics"]["reads"]) for p in mature)
    median = views[len(views) // 2] if views else None
    analysis: list[dict[str, object]] = []
    if median is not None:
        best = sorted(mature, key=lambda p: (p["metrics"]["reads"], p["metrics"].get("likes") or 0), reverse=True)[:3]
        low = sorted(mature, key=lambda p: (p["metrics"]["reads"], -(p["metrics"].get("likes") or 0)))[:2]
        for p in best:
            analysis.append({"postId": p["id"], "label": "X 表现较好", "tone": "ok",
                             "reason": f"公开浏览 {p['metrics']['reads']}，高于当前成熟样本中位数 {median}；赞 {p['metrics'].get('likes') or 0}。"})
        for p in low:
            analysis.append({"postId": p["id"], "label": "X 需要复盘", "tone": "warn",
                             "reason": f"公开浏览 {p['metrics']['reads']}，低于当前成熟样本中位数 {median}；缺少点击与关注转化，暂不直接归因内容质量。"})
    recent_public_views = sum(int(p["metrics"]["reads"] or 0) for p in posts
                              if isinstance(p["metrics"].get("reads"), (int, float)))
    newest = max((parse_date(p.get("publishedAt")) for p in posts if parse_date(p.get("publishedAt"))), default=None)
    account = {
        "platform": "X", "account": X_HANDLE, "profileUrl": f"https://x.com/{X_HANDLE}",
        "dashboardUrl": "https://analytics.x.com/", "asOf": newest.date().isoformat() if newest else dt.date.today().isoformat(),
        "sourceKind": "公开账号与最近推文", "freshness": "fresh" if posts else "stale",
        "metrics": {
            "posts": profile.get("tweetCount"), "following": profile.get("followingCount"),
            "followers": profile.get("followersCount"), "recentPublicViews": recent_public_views,
            "samplePosts": len(posts),
        },
        "note": "只读公开指标；不包含 X Analytics 的点击、关注转化或私有曝光明细。",
    }
    return account, posts, analysis


def metric_int(value: object) -> int:
    text = str(value or "").replace(",", "").strip()
    try:
        return int(float(text)) if text else 0
    except ValueError:
        return 0


def xhs_note_time(ident: object) -> dt.datetime | None:
    """XHS public note IDs encode their creation timestamp in the first 8 hex chars."""
    text = str(ident or "").strip()
    if len(text) < 8 or not re.fullmatch(r"[0-9a-fA-F]{8}.*", text):
        return None
    try:
        stamp = int(text[:8], 16)
        when = dt.datetime.fromtimestamp(stamp, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    if not (dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc) <= when <= dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)):
        return None
    return when.astimezone(dt.timezone(dt.timedelta(hours=8)))


def xhs_live_snapshot() -> tuple[dict[str, object] | None, list[dict[str, object]], list[dict[str, object]]]:
    """Read the authenticated user's own public XHS profile without any write action."""
    mcporter = tool_binary("mcporter")
    if not mcporter:
        return None, [], []
    profile = run_json([
        mcporter, "call", 'xiaohongshu.get_my_profile(tab: "note")',
        "--timeout", "120000", "--output", "json",
    ], timeout=135)
    if not isinstance(profile, dict):
        return None, [], []
    basic = profile.get("userBasicInfo") or {}
    feeds = profile.get("feeds") or []
    interactions = profile.get("interactions") or []
    if not isinstance(basic, dict) or not isinstance(feeds, list):
        return None, [], []

    counts = {}
    if isinstance(interactions, list):
        for row in interactions:
            if isinstance(row, dict):
                counts[str(row.get("type") or "")] = metric_int(row.get("count"))

    posts: list[dict[str, object]] = []
    user_id = ""
    for feed in feeds:
        if not isinstance(feed, dict):
            continue
        ident = str(feed.get("id") or "").strip()
        card = feed.get("noteCard") or {}
        if not ident or not isinstance(card, dict):
            continue
        info = card.get("interactInfo") or {}
        if not isinstance(info, dict):
            info = {}
        user = card.get("user") or {}
        if not user_id and isinstance(user, dict):
            user_id = str(user.get("userId") or "").strip()
        when = xhs_note_time(ident)
        posts.append({
            "id": "xhs-" + ident,
            "platform": "小红书",
            "publishedAt": when.isoformat(timespec="seconds") if when else None,
            "title": re.sub(r"\s+", " ", str(card.get("displayTitle") or "未命名笔记")).strip(),
            "url": f"https://www.xiaohongshu.com/explore/{ident}",
            "metrics": {
                "reads": None,
                "likes": metric_int(info.get("likedCount")),
                "comments": metric_int(info.get("commentCount")),
                "shares": metric_int(info.get("sharedCount")),
                "collects": metric_int(info.get("collectedCount")),
            },
        })

    scored = []
    for post in posts:
        metrics = post["metrics"]
        score = sum(int(metrics.get(k) or 0) for k in ("likes", "comments", "shares", "collects"))
        scored.append((score, post))
    values = sorted(score for score, _ in scored)
    median = values[len(values) // 2] if values else None
    analysis: list[dict[str, object]] = []
    if median is not None and scored:
        best = sorted(scored, key=lambda item: item[0], reverse=True)[:3]
        low = sorted(scored, key=lambda item: item[0])[:2]
        for score, post in best:
            metrics = post["metrics"]
            analysis.append({
                "postId": post["id"], "label": "小红书互动较好", "tone": "ok",
                "reason": f"主页公开互动 {score}（赞 {metrics['likes']} / 藏 {metrics['collects']} / 评 {metrics['comments']} / 分享 {metrics['shares']}），高于当前 {len(scored)} 篇样本中位数 {median}。",
            })
        for score, post in low:
            metrics = post["metrics"]
            analysis.append({
                "postId": post["id"], "label": "小红书需要复盘", "tone": "warn",
                "reason": f"主页公开互动 {score}，低于当前样本中位数 {median}；未接曝光与关注转化，暂不直接归因内容质量。",
            })

    newest = max((xhs_note_time(feed.get("id")) for feed in feeds if isinstance(feed, dict) and xhs_note_time(feed.get("id"))), default=None)
    account = {
        "platform": "小红书", "account": str(basic.get("nickname") or WECHAT_ACCOUNT),
        "profileUrl": f"https://www.xiaohongshu.com/user/profile/{user_id}" if user_id else "https://www.xiaohongshu.com/",
        "dashboardUrl": "https://creator.xiaohongshu.com/",
        "asOf": newest.date().isoformat() if newest else dt.date.today().isoformat(),
        "sourceKind": "本人主页与公开笔记列表", "freshness": "fresh",
        "metrics": {
            "posts": len(posts), "following": counts.get("follows"), "followers": counts.get("fans"),
            "interactions": counts.get("interaction"), "samplePosts": len(posts),
        },
        "note": "只读本人主页与公开互动；当前接口不提供曝光、点击或关注转化，缺失值不会写成 0。",
    }
    return account, posts, analysis


def pdf_text(path: Path) -> str:
    if not path.exists():
        return ""
    # launchd gives this service a minimal PATH, so Homebrew tools are not
    # discoverable there even though they work in an interactive shell.
    binary = shutil.which("pdftotext")
    if not binary:
        for candidate in ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext"):
            if Path(candidate).is_file():
                binary = candidate
                break
    if not binary:
        return ""
    try:
        return subprocess.run(
            [binary, "-layout", str(path), "-"],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def x_snapshot() -> dict[str, object]:
    text = pdf_text(X_PDF)
    tweets = re.search(r"With\s+(\d+)\s+tweets", text)
    counts = re.search(r"(\d+)\s+following\s+(\d+)\s+followers", text)
    return {
        "platform": "X",
        "account": X_HANDLE,
        "profileUrl": f"https://x.com/{X_HANDLE}",
        "dashboardUrl": "https://analytics.x.com/",
        "asOf": "2025-09-16" if text else None,
        "sourceKind": "第三方历史快照",
        "freshness": "stale" if text else "missing",
        "metrics": {
            "posts": int(tweets.group(1)) if tweets else None,
            "following": int(counts.group(1)) if counts else None,
            "followers": int(counts.group(2)) if counts else None,
            "last30DayImpressions": 274 if "Last 30 days engagement" in text and re.search(r"\b274\b", text) else None,
        },
        "note": "报告把转发卡片上原作者的浏览与互动也展示出来，不能当作本账号原创内容表现。",
    }


def zsxq_snapshot() -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    rows = read_simple_xlsx(ZSXQ_XLSX)
    posts: list[dict[str, object]] = []
    for row in rows:
        published = parse_date(row.get("发布时间"))
        link = safe_http_url(row.get("主题链接"))
        title = str(row.get("主题") or "").strip()
        if not title:
            continue
        ident = hashlib.sha1((link or (str(published) + title)).encode("utf-8")).hexdigest()[:12]
        posts.append({
            "id": "zsxq-" + ident,
            "platform": "知识星球",
            "publishedAt": published.isoformat(timespec="seconds") if published else None,
            "title": title,
            "url": link,
            "metrics": {
                "reads": int(row.get("阅读数") or 0),
                "likes": int(row.get("点赞数") or 0),
                "comments": int(row.get("评论数") or 0),
            },
        })
    posts.sort(key=lambda x: x.get("publishedAt") or "", reverse=True)
    dated = [parse_date(p.get("publishedAt")) for p in posts]
    dated = [d for d in dated if d]
    newest = max(dated) if dated else None
    oldest = min(dated) if dated else None
    mature_cutoff = newest - dt.timedelta(days=4) if newest else None
    mature = [p for p in posts if parse_date(p.get("publishedAt")) and parse_date(p.get("publishedAt")) <= mature_cutoff]
    reads = sorted(int(p["metrics"]["reads"]) for p in mature)
    median = reads[len(reads) // 2] if reads else None
    best = sorted(mature, key=lambda p: (p["metrics"]["reads"], p["metrics"]["likes"], p["metrics"]["comments"]), reverse=True)[:3]
    low = sorted(mature, key=lambda p: (p["metrics"]["reads"], -(p["metrics"]["likes"] + p["metrics"]["comments"])))[:2]
    recent = [p for p in posts if mature_cutoff and parse_date(p.get("publishedAt")) > mature_cutoff][:2]
    analysis: list[dict[str, object]] = []
    for p in best:
        analysis.append({"postId": p["id"], "label": "表现较好", "tone": "ok",
                         "reason": f"阅读 {p['metrics']['reads']}，高于成熟样本中位数 {median or '—'}；互动 {p['metrics']['likes'] + p['metrics']['comments']}。"})
    for p in low:
        analysis.append({"postId": p["id"], "label": "需要复盘", "tone": "warn",
                         "reason": f"成熟样本中阅读 {p['metrics']['reads']}，低于中位数 {median or '—'}，且互动偏低；当前导出没有曝光、完读和来源，不能直接归因内容质量。"})
    for p in recent:
        analysis.append({"postId": p["id"], "label": "样本未成熟", "tone": "acc",
                         "reason": "发布距快照不足 4 天，低阅读可能只是累计时间短，先不判好坏。"})

    # The detailed export above has reads and direct links, but only covers 41
    # posts.  A second local export contains the full 2022-2025 posting history.
    # Use it for cadence and total-post context without pretending that its
    # 2025-12-31 endpoint is current in 2026.
    history: list[dict[str, str]] = []
    if ZSXQ_CSV.exists():
        try:
            with ZSXQ_CSV.open(encoding="utf-8-sig", newline="") as fh:
                history = list(csv.DictReader(fh))
        except (OSError, csv.Error):
            history = []
    history_dates = [parse_date(row.get("发布时间")) for row in history]
    history_dates = [value for value in history_dates if value]
    history_newest = max(history_dates) if history_dates else newest
    history_oldest = min(history_dates) if history_dates else oldest
    recent_window = history_newest - dt.timedelta(days=29) if history_newest else None
    recent_count = sum(1 for value in history_dates if recent_window and value >= recent_window)

    def history_int(row: dict[str, str], key: str) -> int:
        try:
            return int(float(row.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    summary = {
        "platform": "知识星球",
        "account": "示例知识星球主",
        "profileUrl": "https://wx.zsxq.com/",
        "dashboardUrl": "https://wx.zsxq.com/",
        "asOf": history_newest.date().isoformat() if history_newest else None,
        "sourceKind": "历史全量 + 近 41 条明细导出",
        "freshness": "stale" if posts else "missing",
        "exportAgeDays": (dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
                          - history_newest.date()).days if history_newest else None,
        "metrics": {
            "posts": len(history) or len(posts),
            "detailedPosts": len(posts),
            "readsSample": sum(int(p["metrics"]["reads"]) for p in posts),
            "likes": sum(history_int(row, "赞") for row in history) if history else sum(int(p["metrics"]["likes"]) for p in posts),
            "comments": sum(history_int(row, "评论") for row in history) if history else sum(int(p["metrics"]["comments"]) for p in posts),
            "postsInLast30ExportDays": recent_count if history else None,
            "medianMatureReads": median,
        },
        "range": {
            "start": history_oldest.date().isoformat() if history_oldest else None,
            "end": history_newest.date().isoformat() if history_newest else None,
        },
        "note": "1454 条历史导出用于看长期产量与互动；阅读分析只基于另一个 41 条明细样本。数据截至 2025-12-31，不冒充今天。",
    }
    return summary, posts, analysis


def automation(x_live: bool = False, xhs_live: bool = False) -> list[dict[str, object]]:
    env = env_flags(OPENCLAW_ENV, {"WECHAT_APPID", "WECHAT_SECRET"})
    wechat_ready = WECHAT_PUSH.exists() and all(env.values())
    x_draft_ready = importlib.util.find_spec("patchright") is not None
    try:
        mcporter = json.loads(MCPORTER_CONFIG.read_text(encoding="utf-8")) if MCPORTER_CONFIG.exists() else {}
    except (OSError, ValueError):
        mcporter = {}
    xhs_data_ready = "xiaohongshu" in (mcporter.get("mcpServers") or {})
    xreach_binary = tool_binary("xreach")
    return [
        {
            "platform": "公众号",
            "state": "available" if wechat_ready else "missing",
            "label": "草稿凭据已就绪" if wechat_ready else "草稿凭据未完成",
            "detail": "APPID 与 Secret 已改为从本机环境读取；本轮未取 token、未推草稿。仍需后台确认原创、赞赏、合集与最终发布。" if wechat_ready else "需要配置 WECHAT_APPID / WECHAT_SECRET，并保留人工发布确认。",
            "dashboardUrl": "https://mp.weixin.qq.com/",
        },
        {
            "platform": "小红书",
            "state": "available" if xhs_live else "partial" if XHS_PIPELINE.exists() or xhs_data_ready else "missing",
            "label": "账号数据已接通" if xhs_live else "数据通道已配置，本次读取失败" if xhs_data_ready else "草稿管线在，数据通道待登录" if XHS_PIPELINE.exists() else "未找到草稿入口",
            "detail": "同步时只读本人主页与公开笔记互动；不会自动点赞、评论或发布。草稿发布仍需人工确认。" if xhs_live else "小红书 MCP 已配置，但本次未取回主页数据；请检查服务与登录态。" if xhs_data_ready else "本地草稿脚本已存在；小红书 MCP 尚未登录，暂不能回收笔记指标。成功仍以官方草稿箱可见为准。",
            "dashboardUrl": "https://creator.xiaohongshu.com/",
        },
        {
            "platform": "知识星球",
            "state": "available" if ZSXQ_TOKEN.exists() else "partial",
            "label": "登录态实时只读已接通" if ZSXQ_TOKEN.exists() else "历史快照 + 可选实时通道",
            "detail": ("同步时按星球聚合最近话题（阅读/赞/评论实时），发布核验仍以星球后台为准；token 过期时自动退回历史导出并在卡片标注。"
                       if ZSXQ_TOKEN.exists() else
                       "把 wx.zsxq.com 的 zsxq_access_token Cookie 存入 ~/.config/daily-workbench/zsxq-token 即可实时拉取；当前用历史导出。"),
            "dashboardUrl": "https://wx.zsxq.com/",
        },
        {
            "platform": "X 数据",
            "state": "available" if x_live else "partial" if xreach_binary else "missing",
            "label": "公开数据已接通" if x_live else "授权就绪，本次读取失败" if xreach_ready() else "读取工具已装，等待账号授权" if xreach_binary else "读取工具未安装",
            "detail": "同步时会只读抓取公开账号资料与最近 5 页时间线，并补齐原创内容的公开浏览、赞、评论与转发；不读取私信，也不发布内容。" if x_live else "授权已保存，但本次没有取回公开数据；请检查 X 网络连接。" if xreach_ready() else "需要一次 X 登录授权，完成后每日同步会自动加入公开浏览、赞、评论与转发指标。",
            "dashboardUrl": "https://analytics.x.com/",
        },
        {
            "platform": "X 草稿",
            "state": "review" if x_draft_ready else "missing",
            "label": "长文草稿入口待验证" if x_draft_ready else "缺少 patchright 运行依赖",
            "detail": "现有入口只保存长文草稿，不会自动发布；授权后仍需做一次真实草稿箱可见验证。" if x_draft_ready else "脚本文件存在，但当前 Python 环境无法启动浏览器，因此不能算已接通。",
            "dashboardUrl": "https://x.com/compose/post",
        },
    ]


def knowledge_snapshot() -> dict[str, object]:
    """Pick social-content candidates from the local Obsidian vault.

    This is deliberately a small, read-only index. It does not copy note bodies
    into the dashboard and never writes back to the vault. Scores only decide
    which existing notes deserve a short link on the social screen.
    """
    if not KNOWLEDGE_VAULT.is_dir():
        return {
            "available": False,
            "vault": KNOWLEDGE_VAULT.name,
            "totalNotes": 0,
            "matchedNotes": 0,
            "items": [],
            "note": "本地知识库目录不存在；没有生成任何占位素材。",
        }

    weights = {
        "小红书": 12, "自媒体": 12, "公众号": 11, "twitter": 10,
        "内容": 5, "写作": 5, "seo": 5, "热点": 7, "增长": 5,
        "发布": 4, "营销": 4, "选题": 7, "流量": 4,
    }
    excluded = {".obsidian", ".smart-env", "_Archive", "Backups", "Attachments"}
    candidates: list[dict[str, object]] = []
    total = 0
    for path in KNOWLEDGE_VAULT.rglob("*.md"):
        try:
            rel = path.relative_to(KNOWLEDGE_VAULT)
        except ValueError:
            continue
        if any(part in excluded for part in rel.parts):
            continue
        total += 1
        try:
            raw = path.read_text(encoding="utf-8", errors="ignore")[:16000]
            stat = path.stat()
        except OSError:
            continue
        path_text = str(rel).lower()
        body_text = raw.lower()
        matched: list[str] = []
        score = 0
        for keyword, weight in weights.items():
            needle = keyword.lower()
            if needle in path_text:
                score += weight
                matched.append(keyword)
            elif needle in body_text:
                score += max(1, weight // 3)
                matched.append(keyword)
        if score < 5:
            continue

        heading = next((m.group(1).strip() for line in raw.splitlines()
                        if (m := re.match(r"^#\s+(.+?)\s*$", line))), path.stem)
        snippet = ""
        in_frontmatter = False
        for index, line in enumerate(raw.splitlines()):
            text = line.strip()
            if index == 0 and text == "---":
                in_frontmatter = True
                continue
            if in_frontmatter and text == "---":
                in_frontmatter = False
                continue
            if in_frontmatter or not text or text.startswith(("#", "```", ">", "![[")):
                continue
            text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
            text = re.sub(r"[*_`|]", "", text).strip("- ")
            if len(text) >= 18:
                snippet = text[:120] + ("…" if len(text) > 120 else "")
                break
        updated = dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone(dt.timedelta(hours=8)))
        age_days = max(0, (dt.datetime.now(updated.tzinfo) - updated).days)
        score += 3 if age_days <= 30 else 2 if age_days <= 90 else 0
        file_key = rel.with_suffix("").as_posix()
        candidates.append({
            "title": heading,
            "relativePath": rel.as_posix(),
            "obsidianUrl": "obsidian://open?vault=" + quote(os.environ.get("WB_OBSIDIAN_VAULT_NAME", "MyVault"))
                           + "&file=" + quote(file_key, safe=""),
            "updatedAt": updated.isoformat(timespec="seconds"),
            "topics": matched[:3],
            "snippet": snippet or "现有知识库笔记，可作为选题、论据或改写素材。",
            "score": score,
        })

    candidates.sort(key=lambda x: (int(x["score"]), str(x["updatedAt"])), reverse=True)
    return {
        "available": True,
        "vault": KNOWLEDGE_VAULT.name,
        "totalNotes": total,
        "matchedNotes": len(candidates),
        "items": candidates[:8],
        "note": "只读扫描标题与短摘要；点击后在 Obsidian 打开原文，工作台不会改写知识库。",
    }


def weekly_recap(posts: list[dict[str, object]]) -> dict[str, object]:
    """Aggregate a weekly content-performance recap from real post rows.

    逐条 analysis 只回答"单条好不好"；这里回答"这一周整体怎么样"。
    只用真实 metric，缺失指标的平台如实说"未核验"，不造数。
    """
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    week_start = (now - dt.timedelta(days=now.weekday())).date()
    best = None
    worst = None
    per_platform: dict[str, dict[str, object]] = {}
    for post in posts:
        published = str(post.get("publishedAt") or "")
        # X 用 RFC2822（英文月份），其他平台是 ISO；解析失败按"样本外"处理
        try:
            try:
                day = dt.datetime.strptime(published, "%a %b %d %H:%M:%S %z %Y").astimezone(dt.timezone(dt.timedelta(hours=8))).date()
            except ValueError:
                day = dt.date.fromisoformat(published[:10])
        except ValueError:
            continue
        platform = str(post.get("platform") or "未知")
        bucket = per_platform.setdefault(platform, {"posts": 0, "thisWeek": 0})
        bucket["posts"] += 1
        if day >= week_start:
            bucket["thisWeek"] += 1
        metrics = post.get("metrics") or {}
        reads = metrics.get("reads")
        likes = metrics.get("likes")
        comments = metrics.get("comments")
        if not isinstance(reads, (int, float)) or reads <= 0:
            continue  # 没有真实阅读数的条目不参与好坏排名（样本未成熟或平台不给）
        score = int(reads) + 3 * int(likes or 0) + 5 * int(comments or 0)
        entry = {"platform": platform, "title": str(post.get("title") or "未命名"), "url": post.get("url"),
                 "reads": int(reads), "likes": int(likes or 0), "comments": int(comments or 0), "date": day.isoformat(), "score": score}
        if best is None or score > best["score"]:
            best = entry
        if worst is None or score < worst["score"]:
            worst = entry
    return {
        "weekStart": week_start.isoformat(),
        "generatedAt": now_iso(),
        "perPlatform": per_platform,
        "best": best,
        "worst": worst,
        "note": "排名只用真实阅读/互动数据，阅读数未核验的条目不参与；互动权重：赞×3、评×5。",
    }

def build() -> dict[str, object]:
    zsxq_live, zsxq_live_posts = zsxq_live_snapshot()
    zsxq, zsxq_posts, zsxq_analysis = zsxq_snapshot()
    wechat, wechat_posts, wechat_analysis, wechat_status = wechat_api_snapshot()
    mpdt, mpdt_posts, mpdt_status = mpdt_snapshot()
    x_live, x_posts, x_analysis = xreach_live_snapshot()
    xhs_live, xhs_posts, xhs_analysis = xhs_live_snapshot()
    x = x_live or x_snapshot()
    # 实时登录态优先；拉不到时退回历史导出，不合并去重冒充双份样本。
    zsxq_merged = zsxq_live or zsxq
    zsxq_posts_merged = zsxq_live_posts or zsxq_posts
    posts = x_posts + xhs_posts + wechat_posts + mpdt_posts + zsxq_posts_merged
    # analysis 里可能引用上一轮数据源的旧文章 ID（如官方 API 换成 mpdt 后），渲染前过滤到真实存在的 posts。
    post_ids = {str(p.get("id")) for p in posts}
    analysis = [a for a in (x_analysis + xhs_analysis + wechat_analysis + zsxq_analysis) if a.get("postId") in post_ids]
    knowledge = knowledge_snapshot()
    accounts = [
        x,
        # mpdt 有真实统计时用它替换官方 API 快照（未认证订阅号拿不到 datacube）。
        (mpdt if mpdt and (mpdt.get("metrics") or {}) else wechat),
        xhs_live or {
            "platform": "小红书", "account": "未配置账号数据", "profileUrl": "", "dashboardUrl": "https://creator.xiaohongshu.com/",
            "asOf": None, "sourceKind": "无指标源", "freshness": "missing", "metrics": {}, "note": "已有草稿管线，但尚未接入笔记表现和当天发布回执。",
        },
        zsxq_merged,
    ]
    today = dt.date.today().isoformat()
    return {
        "version": 1,
        "generatedAt": now_iso(),
        "dataPolicy": "未知不计为 0；历史快照不冒充实时数据；原创与转发分开分析。",
        "accounts": accounts,
        "todayPlan": [
            {"id": "x", "platform": "X", "date": today, "defaultStatus": "unknown", "url": f"https://x.com/{X_HANDLE}", "actionUrl": "https://x.com/compose/post"},
            {"id": "wechat", "platform": "公众号", "date": today, "defaultStatus": "unknown", "url": "https://mp.weixin.qq.com/", "actionUrl": "https://mp.weixin.qq.com/"},
            {"id": "xiaohongshu", "platform": "小红书", "date": today, "defaultStatus": "published" if any(str(p.get("publishedAt") or "")[:10] == today for p in xhs_posts) else "unknown", "url": xhs_live.get("profileUrl") if xhs_live else "https://creator.xiaohongshu.com/", "actionUrl": "https://creator.xiaohongshu.com/"},
            {"id": "zsxq", "platform": "知识星球", "date": today, "defaultStatus": "unknown", "url": "https://wx.zsxq.com/", "actionUrl": "https://wx.zsxq.com/"},
        ],
        "posts": posts,
        "analysis": analysis,
        "weeklyRecap": weekly_recap(posts),
        "automation": automation(x_live is not None, xhs_live is not None) + [
            {"platform": "公众号数据", "dashboardUrl": "https://mp.weixin.qq.com/", **(mpdt_status if mpdt_posts else wechat_status)},
            {"platform": "知识星球数据", "dashboardUrl": "https://wx.zsxq.com/",
             **({"state": "available", "label": "登录态已接通", "detail": "网页登录态只读拉取最近话题。"}
                if zsxq_live else {"state": "partial", "label": "历史快照 + 可选实时通道",
                                   "detail": f"把 wx.zsxq.com 的 zsxq_access_token Cookie 存到 {ZSXQ_TOKEN} 即可实时拉取；当前仍用历史导出。"})},
        ],
        "knowledgeBase": knowledge,
        "sources": [
            {"name": "X 公开账号与最近推文" if x_live else "X Analytics / SuperX PDF", "kind": "public-timeline" if x_live else "historical-third-party", "asOf": x.get("asOf"), "modifiedAt": now_iso() if x_live else file_time(X_PDF), "records": len(x_posts) if x_live else 1, "available": bool(x_live) or X_PDF.exists()},
            {"name": "小红书本人主页与公开笔记", "kind": "authenticated-readonly", "asOf": xhs_live.get("asOf") if xhs_live else None, "modifiedAt": now_iso() if xhs_live else None, "records": len(xhs_posts), "available": bool(xhs_live)},
            {"name": "公众号官方 API", "kind": "official-readonly", "asOf": wechat.get("asOf"), "modifiedAt": now_iso(), "records": len(wechat_posts), "available": bool(wechat_posts) or wechat.get("connectionState") in {"blocked", "connected", "limited"}, "state": wechat_status.get("state"), "note": wechat_status.get("detail")},
            {"name": "公众号 mpdt 本地库", "kind": "login-state-readonly", "asOf": (mpdt or {}).get("asOf"), "modifiedAt": now_iso() if mpdt_posts else None, "records": len(mpdt_posts), "available": bool(mpdt_posts), "note": "复用本机 Chrome 登录态（mpdt fetch），只读抓取发表列表与文章统计。"},
            {"name": "知识星球 Web API", "kind": "login-state-readonly", "asOf": (zsxq_live or {}).get("asOf"), "modifiedAt": now_iso() if zsxq_live_posts else None, "records": len(zsxq_live_posts), "available": bool(zsxq_live_posts), "note": "zsxq_access_token Cookie 只读拉取；无 token 时用历史导出。"},
            {"name": "知识星球历史全量导出", "kind": "post-export", "asOf": zsxq.get("asOf"), "modifiedAt": file_time(ZSXQ_CSV), "records": int((zsxq.get("metrics") or {}).get("posts") or 0), "available": ZSXQ_CSV.exists()},
            {"name": "知识星球近 41 条明细导出", "kind": "post-export", "asOf": zsxq.get("asOf"), "modifiedAt": file_time(ZSXQ_XLSX), "records": len(zsxq_posts), "available": ZSXQ_XLSX.exists()},
            {"name": "本地知识库", "kind": "local-knowledge", "asOf": dt.date.today().isoformat(), "modifiedAt": file_time(KNOWLEDGE_VAULT), "records": knowledge["matchedNotes"], "available": KNOWLEDGE_VAULT.is_dir()},
        ],
    }


def main() -> int:
    obj = build()
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, OUT)
    print(f"wrote {OUT.name}: {len(obj['posts'])} post rows, {len(obj['accounts'])} accounts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
