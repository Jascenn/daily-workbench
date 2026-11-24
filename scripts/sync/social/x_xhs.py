"""
[INPUT]: 依赖 xreach CLI、~/.config/xfetch/session.json、小红书本地管线与 X Analytics PDF 兜底
[OUTPUT]: 导出 xreach_command、xreach_live_snapshot、x_snapshot、xhs_note_time、xhs_live_snapshot
[POS]: scripts/sync/social/ X（Twitter）与小红书平台抓手
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import socket
import subprocess
from pathlib import Path

from social.common import (
    XHS_PIPELINE, XREACH_SESSION, X_PDF,
    freshness_fields, metric_int, parse_date, pdf_text, run_json, safe_http_url, tool_binary, xreach_ready,
)

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



def xreach_live_snapshot() -> tuple[dict[str, object] | None, list[dict[str, object]], list[dict[str, object]]]:
    """Read public X profile/timeline after the user has explicitly authenticated xreach."""
    if not xreach_ready():
        return None, [], []
    profile = run_json(xreach_command("user", "@LingYi_Stu"))
    timeline = run_json(xreach_command(
        "tweets", "@LingYi_Stu", "-n", "30", "--all", "--max-pages", "5"
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
            "url": f"https://x.com/LingYi_Stu/status/{ident}",
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
        "platform": "X", "account": "@LingYi_Stu", "profileUrl": "https://x.com/LingYi_Stu",
        "dashboardUrl": "https://analytics.x.com/", "asOf": newest.date().isoformat() if newest else None,
        "sourceKind": "公开账号与最近推文", **freshness_fields(newest.date().isoformat() if newest else None),
        "metrics": {
            "posts": profile.get("tweetCount"), "following": profile.get("followingCount"),
            "followers": profile.get("followersCount"), "recentPublicViews": recent_public_views,
            "samplePosts": len(posts),
        },
        "note": "只读公开指标；不包含 X Analytics 的点击、关注转化或私有曝光明细。",
    }
    return account, posts, analysis



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
        "platform": "小红书", "account": str(basic.get("nickname") or "凌一同学"),
        "profileUrl": f"https://www.xiaohongshu.com/user/profile/{user_id}" if user_id else "https://www.xiaohongshu.com/",
        "dashboardUrl": "https://creator.xiaohongshu.com/",
        "asOf": newest.date().isoformat() if newest else None,
        "sourceKind": "本人主页与公开笔记列表", **freshness_fields(newest.date().isoformat() if newest else None),
        "metrics": {
            "posts": len(posts), "following": counts.get("follows"), "followers": counts.get("fans"),
            "interactions": counts.get("interaction"), "samplePosts": len(posts),
        },
        "note": "只读本人主页与公开互动；当前接口不提供曝光、点击或关注转化，缺失值不会写成 0。",
    }
    return account, posts, analysis



def x_snapshot() -> dict[str, object]:
    text = pdf_text(X_PDF)
    tweets = re.search(r"With\s+(\d+)\s+tweets", text)
    counts = re.search(r"(\d+)\s+following\s+(\d+)\s+followers", text)
    return {
        "platform": "X",
        "account": "@LingYi_Stu",
        "profileUrl": "https://x.com/LingYi_Stu",
        "dashboardUrl": "https://analytics.x.com/",
        "asOf": "2025-09-16" if text else None,
        "sourceKind": "第三方历史快照",
        **freshness_fields("2025-09-16" if text else None),
        "metrics": {
            "posts": int(tweets.group(1)) if tweets else None,
            "following": int(counts.group(1)) if counts else None,
            "followers": int(counts.group(2)) if counts else None,
            "last30DayImpressions": 274 if "Last 30 days engagement" in text and re.search(r"\b274\b", text) else None,
        },
        "note": "报告把转发卡片上原作者的浏览与互动也展示出来，不能当作本账号原创内容表现。",
    }


