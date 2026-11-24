"""
[INPUT]: 依赖 ~/.openclaw/.env 公众号凭证与本机 ~/.venvs/mpdt（Chrome 登录态）
[OUTPUT]: 导出 wechat_api_snapshot 与 mpdt_snapshot
[POS]: scripts/sync/social/ 微信公众号平台抓手（官方 API + mpdt 双通道）
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from social.common import (
    MPDT_BIN, MPDT_PROFILE, OPENCLAW_ENV, ROOT,
    env_values, freshness_fields, metric_int, parse_date, safe_http_url,
)

def wechat_api_snapshot() -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]], dict[str, str]]:
    """Read official-account metrics without publishing or changing the account.

    Credentials can be valid while the official API still rejects the current
    outbound IP.  Keep that state distinct from missing credentials so the UI
    can show the one concrete next step instead of a vague empty card.
    """
    dashboard = "https://mp.weixin.qq.com/"
    account: dict[str, object] = {
        "platform": "公众号", "account": "凌一同学", "profileUrl": "", "dashboardUrl": dashboard,
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
        **freshness_fields(yesterday if core_connected else latest_post_date),
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
        "platform": "公众号", "account": "凌一同学", "profileUrl": "", "dashboardUrl": "https://mp.weixin.qq.com/",
        "asOf": newest[:10] if newest else None,
        "sourceKind": "mpdt 登录态抓取（Chrome CDP）",
        **freshness_fields(newest[:10] if newest else None),
        "metrics": {"samplePosts": len(posts), "avgReads": sum(reads) // len(reads) if reads else None,
                    "detailPosts": len(detail_rows),
                    "avgCompletion": (sum(completion) / len(completion)) if completion else None},
        "note": "数据来自本机 mpdt 本地库（每次同步自动抓取）；当天数据微信侧 T+1 才可得。" +
                (f" 完读率/渠道构成仅覆盖最近 7 天发表的文章（30 天窗口口径），当前有 {len(detail_rows)} 篇明细。" if detail_rows else " 完读率/渠道构成只对发表后 30 天内的文章有效；当前没有窗口内文章，下次发文后自动出现。"),
    }
    fetch_part = f"本轮 fetch {'成功' if fetch_rc == 0 else f'失败({fetch_rc})：{fetch_note[:80]}'}；" if cdp_ok else "调试 Chrome 未在线，用上次快照；"
    detail_part = f"完读率明细「{detail_note[0][:60]}」；" if cdp_ok and detail_note and detail_note[0] else ""
    return account, posts, {"state": "available" if fetch_rc == 0 else "partial", "label": "mpdt 已接通" if fetch_rc == 0 else "mpdt 用上次快照", "detail": f"{fetch_part}{detail_part}本地库 {len(posts)} 篇文章统计；只读抓取，不影响公众号。"}


