#!/usr/bin/env python3
"""生成自媒体与知识库只读快照 social.json（编排入口）。

具体平台抓手已拆分至 scripts/sync/social/ 插件包：
  * common.py    —— 路径常量、时间/表格/PDF/命令行基础工具
  * zsxq.py      —— 知识星球（zsxq-cli → Web API → 历史导出）
  * wechat_mp.py —— 微信公众号（官方 API + mpdt 本地库）
  * x_xhs.py     —— X（xreach + PDF 兜底）与小红书
  * knowledge.py —— Obsidian 知识库只读索引、自动化状态与周汇总

用法：
  python3 scripts/sync/social-sync.py                     # 全量刷新
  python3 scripts/sync/social-sync.py --only zsxq         # 单平台增量快刷（zsxq/x/xhs/wechat_mp/kb）
  python3 scripts/sync/social-sync.py --check             # 校验现有 social.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

_SYNC_DIR = str(Path(__file__).resolve().parent)
if _SYNC_DIR not in sys.path:
    sys.path.insert(0, _SYNC_DIR)

from social.common import (  # noqa: E402,F401
    ROOT, OUT, KNOWLEDGE_VAULT, XREACH_SESSION, OPENCLAW_ENV, WECHAT_PUSH,
    XHS_PIPELINE, MCPORTER_CONFIG, X_PDF, ZSXQ_XLSX, ZSXQ_CSV, ZSXQ_TOKEN,
    ZSXQ_GROUPS, MPDT_BIN, MPDT_PROFILE, XML_NS,
    bind_entry_globals, now_iso, annotate_sync_status, safe_http_url,
    column_index, read_simple_xlsx, parse_date, freshness_fields, file_time,
    env_flags, env_values, tool_binary, xreach_ready, run_json, metric_int, pdf_text,
)
from social.zsxq import (  # noqa: E402,F401
    ZSXQ_CLI, ZSXQ_DASHBOARD_FIELDS,
    _find_cli, _cli_env, _zsxq_cli_json, zsxq_cli_dashboard,
    _zsxq_plain, _zsxq_hashtag_names, _zsxq_excerpt, _zsxq_topic_to_post,
    zsxq_cli_map, zsxq_cli_snapshot, zsxq_live_snapshot, zsxq_snapshot,
)
from social.wechat_mp import wechat_api_snapshot, mpdt_snapshot  # noqa: E402,F401
from social.x_xhs import (  # noqa: E402,F401
    xreach_command, xreach_live_snapshot, x_snapshot, xhs_note_time, xhs_live_snapshot,
)
from social.knowledge import automation, knowledge_snapshot, weekly_recap  # noqa: E402,F401

bind_entry_globals(globals())


def build() -> dict[str, object]:
    attempted_at = now_iso()
    # 知识星球三条通道，按可靠性排序：zsxq-cli（登录态，最稳）→ Web API Cookie → 历史导出。
    zsxq_live, zsxq_live_posts = zsxq_cli_snapshot()
    if not zsxq_live_posts:
        zsxq_live, zsxq_live_posts = zsxq_live_snapshot()
    zsxq, zsxq_posts, zsxq_analysis = zsxq_snapshot()
    wechat, wechat_posts, wechat_analysis, wechat_status = wechat_api_snapshot()
    mpdt, mpdt_posts, mpdt_status = mpdt_snapshot()
    x_live, x_posts, x_analysis = xreach_live_snapshot()
    xhs_live, xhs_posts, xhs_analysis = xhs_live_snapshot()
    xhs_live_available = xhs_live is not None
    x = x_live or x_snapshot()

    # The output timestamp is the snapshot build time, not a claim that every
    # platform returned fresh data.  Record the distinction on each account.
    official_connection = str(wechat.get("connectionState") or "")
    official_has_data = bool(wechat_posts) or bool(wechat.get("metrics"))
    official_sync_status = (
        "blocked" if official_connection == "blocked" else
        "updated" if official_connection == "connected" and official_has_data else
        "partial" if official_connection in {"connected", "limited"} else
        "not_connected"
    )
    official_reason = (
        "本轮官方 API 返回了可用数据。" if official_sync_status == "updated" else
        "本轮官方 API 返回了部分数据，仍有统计权限缺口。" if official_sync_status == "partial" else
        str(wechat.get("note") or "官方 API 本轮没有返回可用数据。")
    )
    mpdt_sync_status = (
        "updated" if mpdt_posts and mpdt_status.get("state") == "available" else
        "partial" if mpdt_posts else
        "not_connected"
    )
    mpdt_reason = (
        "本轮 mpdt 抓取并导出了文章数据。" if mpdt_sync_status == "updated" else
        str(mpdt_status.get("detail") or "沿用本地历史库，未获得本轮新数据。")
    )
    if mpdt and mpdt_posts:
        annotate_sync_status(mpdt, attempted_at=attempted_at, status=mpdt_sync_status,
                             reason=mpdt_reason, source="mpdt", updated=mpdt_sync_status == "updated")
    annotate_sync_status(wechat, attempted_at=attempted_at, status=official_sync_status,
                         reason=official_reason, source="公众号官方 API",
                         updated=official_sync_status == "updated")

    zsxq_sync_status = (
        "updated" if zsxq_live_posts else
        "partial" if zsxq_live is not None else
        "history" if zsxq_posts else
        "not_connected"
    )
    # 数据实际来自哪条通道，就写哪条：CLI 与 Web API 都叫"登录态"，
    # 但排障时差别很大（CLI 挂了通常是没登录/没装，API 挂了通常是 Cookie 过期）。
    zsxq_source = (
        str((zsxq_live or {}).get("sourceKind") or "").strip()
        or ("知识星球 Web API" if zsxq_live is not None else "知识星球历史导出")
    )
    annotate_sync_status(
        zsxq_live or zsxq, attempted_at=attempted_at, status=zsxq_sync_status,
        reason=("本轮通过 %s 返回话题数据。" % zsxq_source if zsxq_live_posts else
                "本轮已尝试登录态通道但没有返回话题，字段保持为空。" if zsxq_live is not None else
                "本轮未取到登录态数据，沿用历史导出。" if zsxq_posts else
                "没有可用的知识星球登录态或历史导出。"),
        source=zsxq_source,
        updated=bool(zsxq_live_posts),
    )
    x_sync_status = (
        "updated" if x_live and x_posts else
        "partial" if x_live is not None else
        "history" if x.get("asOf") else
        "not_connected"
    )
    annotate_sync_status(
        x, attempted_at=attempted_at, status=x_sync_status,
        reason=("本轮公开时间线返回数据。" if x_sync_status == "updated" else
                "本轮公开账号接口可用，但没有返回推文明细。" if x_sync_status == "partial" else
                "本轮未取到公开时间线，沿用本地历史快照。" if x_sync_status == "history" else
                "没有可用的 X 登录态或历史快照。"),
        source="X 公开时间线" if x_live is not None else "X 历史快照",
        updated=x_sync_status == "updated",
    )

    if xhs_live:
        xhs_status = "updated" if xhs_posts else "partial"
        annotate_sync_status(
            xhs_live, attempted_at=attempted_at, status=xhs_status,
            reason=("本轮本人主页接口返回公开笔记。" if xhs_posts else "本轮账号接口可用，但没有返回公开笔记明细。"),
            source="小红书本人主页", updated=bool(xhs_posts),
        )
    else:
        xhs_fallback = {
            "platform": "小红书", "account": "未配置账号数据", "profileUrl": "",
            "dashboardUrl": "https://creator.xiaohongshu.com/", "asOf": None,
            "sourceKind": "无指标源", "freshness": "missing", "metrics": {},
            "note": "已有草稿管线，但尚未接入笔记表现和当天发布回执。",
        }
        annotate_sync_status(xhs_fallback, attempted_at=attempted_at, status="not_connected",
                             reason="本轮没有可用的小红书只读数据通道。",
                             source="小红书公开数据", updated=False)
        xhs_live = xhs_fallback

    # 实时登录态优先；拉不到时退回历史导出，不合并去重冒充双份样本。
    zsxq_merged = zsxq_live or zsxq
    zsxq_posts_merged = zsxq_live_posts or zsxq_posts
    posts = x_posts + xhs_posts + wechat_posts + mpdt_posts + zsxq_posts_merged
    # analysis 里可能引用上一轮数据源的旧文章 ID（如官方 API 换成 mpdt 后），渲染前过滤到真实存在的 posts。
    post_ids = {str(p.get("id")) for p in posts}
    analysis = [a for a in (x_analysis + xhs_analysis + wechat_analysis + zsxq_analysis) if a.get("postId") in post_ids]
    knowledge = knowledge_snapshot()
    accounts = [
        # mpdt 有真实统计时用它替换官方 API 快照（未认证订阅号拿不到 datacube）。
        (mpdt if mpdt and (mpdt.get("metrics") or {}) else wechat),
        zsxq_merged,
        x,
        xhs_live,
    ]
    today = dt.date.today().isoformat()
    return {
        "version": 2,
        "generatedAt": now_iso(),
        "syncRun": {
            "attemptedAt": attempted_at,
            "completedAt": now_iso(),
            "status": "completed",
            "updatedAccounts": sum(1 for a in accounts if (a.get("sync") or {}).get("updatedThisRun")),
            "historyAccounts": sum(1 for a in accounts if (a.get("sync") or {}).get("status") == "history"),
            "partialAccounts": sum(1 for a in accounts if (a.get("sync") or {}).get("status") == "partial"),
            "blockedAccounts": sum(1 for a in accounts if (a.get("sync") or {}).get("status") == "blocked"),
            "notConnectedAccounts": sum(1 for a in accounts if (a.get("sync") or {}).get("status") == "not_connected"),
        },
        "dataPolicy": "未知不计为 0；历史快照不冒充实时数据；原创与转发分开分析。generatedAt 只表示快照生成时间。",
        "accounts": accounts,
        "todayPlan": [
            {"id": "wechat", "platform": "公众号", "date": today, "defaultStatus": "unknown", "url": "https://mp.weixin.qq.com/", "actionUrl": "https://mp.weixin.qq.com/"},
            {"id": "zsxq", "platform": "知识星球", "date": today, "defaultStatus": "unknown", "url": "https://wx.zsxq.com/", "actionUrl": "https://wx.zsxq.com/"},
            {"id": "x", "platform": "X", "date": today, "defaultStatus": "unknown", "url": "https://x.com/LingYi_Stu", "actionUrl": "https://x.com/compose/post"},
            {"id": "xiaohongshu", "platform": "小红书", "date": today, "defaultStatus": "published" if any(str(p.get("publishedAt") or "")[:10] == today for p in xhs_posts) else "unknown", "url": xhs_live.get("profileUrl") if xhs_live else "https://creator.xiaohongshu.com/", "actionUrl": "https://creator.xiaohongshu.com/"},
        ],
        "posts": posts,
        "analysis": analysis,
        "weeklyRecap": weekly_recap(posts),
        "automation": automation(x_live is not None, xhs_live_available) + [
            {"platform": "公众号数据", "dashboardUrl": "https://mp.weixin.qq.com/", **(mpdt_status if mpdt_posts else wechat_status)},
            {"platform": "知识星球数据", "dashboardUrl": "https://wx.zsxq.com/",
             **({"state": "available", "label": "登录态已接通", "detail": "网页登录态只读拉取最近话题。"}
                if zsxq_live else {"state": "partial", "label": "历史快照 + 可选实时通道",
                                   "detail": f"把 wx.zsxq.com 的 zsxq_access_token Cookie 存到 {ZSXQ_TOKEN} 即可实时拉取；当前仍用历史导出。"})},
        ],
        "knowledgeBase": knowledge,
        "sources": [
            {"name": "X 公开账号与最近推文" if x_live else "X Analytics / SuperX PDF", "kind": "public-timeline" if x_live else "historical-third-party", "asOf": x.get("asOf"), "modifiedAt": now_iso() if x_live else file_time(X_PDF), "records": len(x_posts) if x_live else 1, "available": bool(x_live) or X_PDF.exists(), "syncStatus": (x.get("sync") or {}).get("status"), "attemptedAt": (x.get("sync") or {}).get("attemptedAt")},
            {"name": "小红书本人主页与公开笔记", "kind": "authenticated-readonly", "asOf": xhs_live.get("asOf") if xhs_live else None, "modifiedAt": now_iso() if xhs_live_available and xhs_live.get("sync", {}).get("updatedThisRun") else None, "records": len(xhs_posts), "available": xhs_live_available, "syncStatus": (xhs_live.get("sync") or {}).get("status"), "attemptedAt": (xhs_live.get("sync") or {}).get("attemptedAt")},
            {"name": "公众号官方 API", "kind": "official-readonly", "asOf": wechat.get("asOf"), "modifiedAt": now_iso() if wechat.get("sync", {}).get("updatedThisRun") else None, "records": len(wechat_posts), "available": bool(wechat_posts) or wechat.get("connectionState") in {"blocked", "connected", "limited"}, "state": wechat_status.get("state"), "note": wechat_status.get("detail"), "syncStatus": (wechat.get("sync") or {}).get("status"), "attemptedAt": (wechat.get("sync") or {}).get("attemptedAt")},
            {"name": "公众号 mpdt 本地库", "kind": "login-state-readonly", "asOf": (mpdt or {}).get("asOf"), "modifiedAt": now_iso() if mpdt and mpdt.get("sync", {}).get("updatedThisRun") else None, "records": len(mpdt_posts), "available": bool(mpdt_posts), "note": "复用本机 Chrome 登录态（mpdt fetch），只读抓取发表列表与文章统计。", "syncStatus": (mpdt.get("sync") or {}).get("status") if mpdt else "not_connected", "attemptedAt": (mpdt.get("sync") or {}).get("attemptedAt") if mpdt else attempted_at},
            {"name": "知识星球 Web API", "kind": "login-state-readonly", "asOf": (zsxq_live or {}).get("asOf"), "modifiedAt": now_iso() if zsxq_live_posts else None, "records": len(zsxq_live_posts), "available": zsxq_live is not None, "note": "zsxq_access_token Cookie 只读拉取；无 token 时用历史导出。", "syncStatus": "updated" if zsxq_live_posts else "partial" if zsxq_live is not None else "not_connected", "attemptedAt": attempted_at},
            {"name": "知识星球历史全量导出", "kind": "post-export", "asOf": zsxq.get("asOf"), "modifiedAt": file_time(ZSXQ_CSV), "records": int((zsxq.get("metrics") or {}).get("posts") or 0), "available": ZSXQ_CSV.exists(), "syncStatus": "history", "updatedThisRun": False, "attemptedAt": attempted_at, "reason": "仅作历史回退，不代表本轮平台数据已更新。"},
            {"name": "知识星球近 41 条明细导出", "kind": "post-export", "asOf": zsxq.get("asOf"), "modifiedAt": file_time(ZSXQ_XLSX), "records": len(zsxq_posts), "available": ZSXQ_XLSX.exists(), "syncStatus": "history", "updatedThisRun": False, "attemptedAt": attempted_at, "reason": "本地导出只提供历史内容，不能当作实时平台数据。"},
            {"name": "Obsidian-Tech 本地知识库", "kind": "local-knowledge", "asOf": dt.date.today().isoformat(), "modifiedAt": file_time(KNOWLEDGE_VAULT), "records": knowledge["matchedNotes"], "available": KNOWLEDGE_VAULT.is_dir(), "syncStatus": "updated" if KNOWLEDGE_VAULT.is_dir() else "not_connected", "updatedThisRun": KNOWLEDGE_VAULT.is_dir(), "attemptedAt": attempted_at, "reason": "本轮重新读取本地知识库索引。" if KNOWLEDGE_VAULT.is_dir() else "本地知识库目录不存在。"},
        ],
    }


def validate_snapshot(obj: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if not isinstance(obj.get("generatedAt"), str):
        errors.append("generatedAt missing")
    run = obj.get("syncRun")
    if not isinstance(run, dict) or not isinstance(run.get("attemptedAt"), str):
        errors.append("syncRun.attemptedAt missing")
    accounts = obj.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        errors.append("accounts missing")
    else:
        for account in accounts:
            if not isinstance(account, dict):
                errors.append("account is not an object")
                continue
            sync = account.get("sync")
            if not isinstance(sync, dict) or sync.get("status") not in {"updated", "partial", "history", "not_connected", "blocked"}:
                errors.append(f"{account.get('platform', 'unknown')}: invalid sync status")
    sources = obj.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("sources missing")
    else:
        for source in sources:
            if not isinstance(source, dict):
                errors.append("source is not an object")
                continue
            if not isinstance(source.get("attemptedAt"), str):
                errors.append(f"{source.get('name', 'unknown')}: attemptedAt missing")
            if source.get("syncStatus") not in {"updated", "partial", "history", "not_connected", "blocked"}:
                errors.append(f"{source.get('name', 'unknown')}: invalid sync status")
    return errors


ONLY_PLATFORMS = {
    "zsxq": "知识星球",
    "wechat_mp": "公众号",
    "x": "X",
    "xhs": "小红书",
    "kb": "知识库",
}


def build_partial(existing: dict[str, object], targets: set[str]) -> dict[str, object]:
    """按平台增量刷新，只抓取指定目标并合并进现有 social.json，避免每次全量跑 100 秒。"""
    attempted_at = now_iso()
    merged = json.loads(json.dumps(existing, ensure_ascii=False))
    accounts = list(merged.get("accounts") or [])
    posts = list(merged.get("posts") or [])
    analysis = list(merged.get("analysis") or [])

    def _replace_platform(platform_name: str, new_account: dict | None, new_posts: list[dict], new_analysis: list[dict]):
        nonlocal accounts, posts, analysis
        if new_account is not None:
            replaced = False
            for idx, acc in enumerate(accounts):
                if isinstance(acc, dict) and acc.get("platform") == platform_name:
                    accounts[idx] = new_account
                    replaced = True
                    break
            if not replaced:
                accounts.append(new_account)
        old_ids = {str(p.get("id")) for p in posts if isinstance(p, dict) and p.get("platform") == platform_name}
        posts = [p for p in posts if not (isinstance(p, dict) and p.get("platform") == platform_name)] + new_posts
        analysis = [a for a in analysis if a.get("postId") not in old_ids] + new_analysis

    if "zsxq" in targets:
        zsxq_live, zsxq_live_posts = zsxq_cli_snapshot()
        if not zsxq_live_posts:
            zsxq_live, zsxq_live_posts = zsxq_live_snapshot()
        zsxq, zsxq_posts, zsxq_analysis = zsxq_snapshot()
        zsxq_sync_status = (
            "updated" if zsxq_live_posts else
            "partial" if zsxq_live is not None else
            "history" if zsxq_posts else
            "not_connected"
        )
        zsxq_source = (
            str((zsxq_live or {}).get("sourceKind") or "").strip()
            or ("知识星球 Web API" if zsxq_live is not None else "知识星球历史导出")
        )
        acc = zsxq_live or zsxq
        annotate_sync_status(
            acc, attempted_at=attempted_at, status=zsxq_sync_status,
            reason=("本轮通过 %s 返回话题数据。" % zsxq_source if zsxq_live_posts else
                    "本轮已尝试登录态通道但没有返回话题，字段保持为空。" if zsxq_live is not None else
                    "本轮未取到登录态数据，沿用历史导出。" if zsxq_posts else
                    "没有可用的知识星球登录态或历史导出。"),
            source=zsxq_source, updated=bool(zsxq_live_posts),
        )
        _replace_platform("知识星球", acc, zsxq_live_posts or zsxq_posts, zsxq_analysis)

    if "x" in targets:
        x_live, x_posts, x_analysis = xreach_live_snapshot()
        x = x_live or x_snapshot()
        x_sync_status = (
            "updated" if x_live and x_posts else
            "partial" if x_live is not None else
            "history" if x.get("asOf") else
            "not_connected"
        )
        annotate_sync_status(
            x, attempted_at=attempted_at, status=x_sync_status,
            reason=("本轮公开时间线返回数据。" if x_sync_status == "updated" else
                    "本轮公开账号接口可用，但没有返回推文明细。" if x_sync_status == "partial" else
                    "本轮未取到公开时间线，沿用本地历史快照。" if x_sync_status == "history" else
                    "没有可用的 X 登录态或历史快照。"),
            source="X 公开时间线" if x_live is not None else "X 历史快照",
            updated=x_sync_status == "updated",
        )
        _replace_platform("X", x, x_posts, x_analysis)

    if "xhs" in targets:
        xhs_live, xhs_posts, xhs_analysis = xhs_live_snapshot()
        if xhs_live:
            xhs_status = "updated" if xhs_posts else "partial"
            annotate_sync_status(
                xhs_live, attempted_at=attempted_at, status=xhs_status,
                reason=("本轮本人主页接口返回公开笔记。" if xhs_posts else "本轮账号接口可用，但没有返回公开笔记明细。"),
                source="小红书本人主页", updated=bool(xhs_posts),
            )
            _replace_platform("小红书", xhs_live, xhs_posts, xhs_analysis)

    if "wechat_mp" in targets:
        wechat, wechat_posts, wechat_analysis, _ = wechat_api_snapshot()
        mpdt, mpdt_posts, mpdt_status = mpdt_snapshot()
        official_connection = str(wechat.get("connectionState") or "")
        official_has_data = bool(wechat_posts) or bool(wechat.get("metrics"))
        official_sync_status = (
            "blocked" if official_connection == "blocked" else
            "updated" if official_connection == "connected" and official_has_data else
            "partial" if official_connection in {"connected", "limited"} else
            "not_connected"
        )
        annotate_sync_status(
            wechat, attempted_at=attempted_at, status=official_sync_status,
            reason=str(wechat.get("note") or "公众号官方 API 已刷新。"),
            source="公众号官方 API", updated=official_sync_status == "updated",
        )
        if mpdt and mpdt_posts:
            mpdt_sync_status = "updated" if mpdt_status.get("state") == "available" else "partial"
            annotate_sync_status(
                mpdt, attempted_at=attempted_at, status=mpdt_sync_status,
                reason=str(mpdt_status.get("detail") or "mpdt 已刷新。"),
                source="mpdt", updated=mpdt_sync_status == "updated",
            )
        chosen = mpdt if mpdt and (mpdt.get("metrics") or {}) else wechat
        _replace_platform("公众号", chosen, wechat_posts + mpdt_posts, wechat_analysis)

    if "kb" in targets:
        merged["knowledgeBase"] = knowledge_snapshot()

    post_ids = {str(p.get("id")) for p in posts if isinstance(p, dict)}
    merged["generatedAt"] = now_iso()
    merged["accounts"] = accounts
    merged["posts"] = posts
    merged["analysis"] = [a for a in analysis if a.get("postId") in post_ids]
    merged["weeklyRecap"] = weekly_recap(posts)
    merged["syncRun"] = {
        "attemptedAt": attempted_at,
        "completedAt": now_iso(),
        "status": "completed",
        "only": sorted(targets),
        "updatedAccounts": sum(1 for a in accounts if (a.get("sync") or {}).get("updatedThisRun")),
        "historyAccounts": sum(1 for a in accounts if (a.get("sync") or {}).get("status") == "history"),
        "partialAccounts": sum(1 for a in accounts if (a.get("sync") or {}).get("status") == "partial"),
        "blockedAccounts": sum(1 for a in accounts if (a.get("sync") or {}).get("status") == "blocked"),
        "notConnectedAccounts": sum(1 for a in accounts if (a.get("sync") or {}).get("status") == "not_connected"),
    }
    return merged


def main() -> int:
    args = sys.argv[1:]
    if "--check" in args:
        try:
            obj = json.loads(OUT.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"snapshot validation failed: {error}", file=sys.stderr)
            return 8
        errors = validate_snapshot(obj)
        if errors:
            print("snapshot validation failed: " + "; ".join(errors), file=sys.stderr)
            return 8
        print(f"valid {OUT.name}: {len(obj.get('posts') or [])} post rows, {len(obj.get('accounts') or [])} accounts")
        return 0

    only_targets: set[str] = set()
    for i, a in enumerate(args):
        if a == "--only" and i + 1 < len(args):
            only_targets.update(t.strip() for t in args[i + 1].split(",") if t.strip())
        elif a.startswith("--only="):
            only_targets.update(t.strip() for t in a.split("=", 1)[1].split(",") if t.strip())

    if only_targets:
        unknown = only_targets - set(ONLY_PLATFORMS)
        if unknown:
            print(f"unknown --only target(s): {', '.join(sorted(unknown))} (valid: {', '.join(sorted(ONLY_PLATFORMS))})",
                  file=sys.stderr)
            return 2
        if OUT.exists():
            existing = json.loads(OUT.read_text(encoding="utf-8"))
            obj = build_partial(existing, only_targets)
        else:
            obj = build()
    else:
        obj = build()
    errors = validate_snapshot(obj)
    if errors:
        print("snapshot validation failed: " + "; ".join(errors), file=sys.stderr)
        return 8
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, OUT)
    print(f"wrote {OUT.name}: {len(obj['posts'])} post rows, {len(obj['accounts'])} accounts; updated {obj['syncRun']['updatedAccounts']}, partial {obj['syncRun']['partialAccounts']}, history {obj['syncRun']['historyAccounts']}, blocked {obj['syncRun']['blockedAccounts']}, not_connected {obj['syncRun']['notConnectedAccounts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
