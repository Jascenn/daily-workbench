"""
[INPUT]: 依赖 Obsidian-Tech 本地知识库目录与已抓取的自媒体帖子列表
[OUTPUT]: 导出 automation、knowledge_snapshot、weekly_recap
[POS]: scripts/sync/social/ 自动化状态汇总、Obsidian 知识库只读索引与周复盘聚合
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import quote

from social.common import (
    KNOWLEDGE_VAULT, MCPORTER_CONFIG, OPENCLAW_ENV, WECHAT_PUSH, XHS_PIPELINE, ZSXQ_TOKEN,
    env_flags, now_iso, parse_date, tool_binary, xreach_ready,
)

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
            "obsidianUrl": "obsidian://open?vault=" + quote(KNOWLEDGE_VAULT.name)
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

