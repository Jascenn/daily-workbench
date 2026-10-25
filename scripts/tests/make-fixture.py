#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 夹具生成器：结构合法、规模足够的伪数据，让逻辑断言有东西可测。

为什么不用真实数据：仓库开源，绝不入库个人收益。
为什么不用空数据：逻辑断言大多是「期望值从数据现算」，空数据会让一半断言失去意义。

生成规则：
  · data.json：earnings 300 条 + 6 条本地草稿 + 3 条重复 + 1 条零额；
    4 笔大额支出让月度图有负柱；来源名「示例来源A~J」循环。
  · sections.json：news / writing / review 三区，与线上派生快照同形
    （freshness / feeds / groups / stats / books / clients 一应俱全），
    三个区的过期阈值各不相同（3 / 45 / 30），覆盖新鲜度三分支。
  · social.json：四平台账号 + 内容明细 + 今日矩阵 + 逐条分析，
    知识星球全量口径与明细条数严格相等（页面有一条守恒断言）。
  · 所有记录 note / title 带 [CI fixture] 标记，真实环境误导入也一眼可辨。

用法：python3 scripts/tests/make-fixture.py   # 在仓库根生成三份夹具
"""
import json
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)  # 可复现
ROOT = Path(__file__).resolve().parent.parent.parent
TODAY = date.today()
FIXED_TODAY = date(2026, 9, 30)  # 与 test-workbench-logic 的 TEST_TODAY 钟对齐

# ── data.json ────────────────────────────────────────────────────────────
earnings = []
for i in range(300):
    d = FIXED_TODAY - timedelta(days=i * 400 // 300 + (i % 3))
    src = f"示例来源{chr(65 + i % 10)}"
    amount = round(random.uniform(10, 900), 2)
    if i in (60, 120, 180, 240):
        # 造净额为负的月份：加大这几个月的支出，让月度图有负柱
        amount = -round(random.uniform(9000, 14000), 2)
        exp_kind = ["投资", "AI 技能", "其他支出"][(i // 25) % 3]
        earnings.append({
            "id": f"ci-{i:04d}", "date": d.isoformat(), "amount": amount,
            "type": exp_kind, "note": f"[CI fixture] 大额支出 {i}",
            "source": "notion", "readOnly": True,
            "updatedAt": FIXED_TODAY.isoformat(),
        })
        continue
    if i % 25 == 0:
        # 支出：3 个分类轮转，备注不用来源名（来源归并只算收入）
        amount = -round(random.uniform(50, 800), 2)
        exp_kind = ["投资", "AI 技能", "其他支出"][(i // 25) % 3]
        earnings.append({
            "id": f"ci-{i:04d}", "date": d.isoformat(), "amount": amount,
            "type": exp_kind, "note": f"[CI fixture] 支出样例 {i}",
            "source": "notion", "readOnly": True,
            "updatedAt": FIXED_TODAY.isoformat(),
        })
        continue
    earnings.append({
        "id": f"ci-{i:04d}", "date": d.isoformat(), "amount": amount,
        "type": "项目收入",
        "note": f"{src} | [CI fixture] 样例记录 {i}",
        "source": "notion", "readOnly": True,
        "updatedAt": FIXED_TODAY.isoformat(),
    })
# 少量本地草稿（source=local, readOnly=False）覆盖「草稿行可点/可编辑」分支
for i in range(6):
    d = FIXED_TODAY - timedelta(days=i)
    earnings.append({
        "id": f"ci-draft-{i:02d}", "date": d.isoformat(),
        "amount": round(random.uniform(10, 300), 2), "type": "项目收入",
        "note": f"示例来源{chr(75 + i)} | [CI fixture] 本地草稿 {i}",
        "source": "local", "readOnly": False, "updatedAt": FIXED_TODAY.isoformat(),
    })

# 造 3 条完全重复（体检项需要）
for k in range(3):
    e = dict(earnings[10]); e["id"] = f"ci-dup-{k}"
    earnings.append(e)
# 造 1 条零额
z = dict(earnings[20]); z["id"] = "ci-zero"; z["amount"] = 0
earnings.append(z)

projects = []
for i in range(12):
    projects.append({
        "id": f"cip-{i:02d}", "name": f"示例项目{i} [CI fixture]",
        "status": ["进行中", "已完成", "风险", "待启动"][i % 4],
        "progress": (i * 9) % 101, "category": f"分类{i % 3}",
        "note": "", "source": "notion", "readOnly": True,
        "updatedAt": TODAY.isoformat(),
    })

data = {
    "owner": "CI Fixture",
    "monthlyGoal": 30000,
    "notionStatus": "connected",
    "earningsReadonly": True,
    "earningsSource": {"name": "副业收入 / 产品支出", "detail": "Notion 只读（CI fixture）"},
    "projectsSource": {"name": "项目管理", "detail": "Notion 只读（CI fixture）"},
    "notionMeta": {"connected": True, "fixture": True},
    "lastNotionSync": None,
    "projects": projects,
    "earnings": earnings,
    "notes": {FIXED_TODAY.isoformat(): "[CI fixture] 示例备注"},
    "predictions": [],
    "sourceMap": {
        "rules": [
            {"name": "示例来源A", "kind": "A", "scope": "note",
             "note": "CI fixture: 归并规则示例",
             "patterns": [r"^示例来源A"]},
        ],
        "notMerged": [
            {"name": "示例来源Z", "reason": "CI fixture: 明确不归并的组示例（夹具里没有该组的收入记录，用于验证「已撤销的组不再成组」）"},
        ],
        "decisions": {"confirmedBy": "CI", "layerB2": "fixture"},
    },
}

# ── sections.json ────────────────────────────────────────────────────────
FEED_GROUPS = ["产品与工具", "AI 与技术", "设计与审美"]
feeds = []
for i in range(8):
    # 5 个活跃（1~15 天）、3 个停更（200 天+，dead=true）
    days = (1 + i * 3) if i < 5 else (200 + i * 30)
    feeds.append({
        "name": f"示例订阅源{i} [CI fixture]", "group": FEED_GROUPS[i % 3],
        "url": f"https://example.com/feed/{i}.xml",
        "count": 12 + i,
        "latest": (FIXED_TODAY - timedelta(days=days)).isoformat() + "T09:00:00",
        "days": days, "dead": days > 100,
    })

news_items = []
for i in range(120):
    # 前 40 条落在 21 天窗口内（分页与窗口口径都需要窗口里有足够条目），其余是历史
    days = (i * 21 // 40) if i < 40 else 21 + (i - 40) * 4
    feed = feeds[i % 8]
    news_items.append({
        "id": f"ci-news-{i:04d}",
        "title": f"[CI fixture] 示例资讯标题 {i}：一个足够长的假标题",
        "link": f"https://example.com/post/{i}",
        "source": feed["name"],
        "published": (FIXED_TODAY - timedelta(days=days)).isoformat() + "T09:00:00",
        "days": days, "read": i % 3 == 0,
        "summary": f"[CI fixture] 示例摘要 {i}。",
    })

book_chapters = [{
    "title": f"第{c + 1}章 示例章节", "name": f"第{c + 1}章",
    "written": c < 3, "chars": 2000 if c < 3 else 0,
} for c in range(10)]

review_items = []
for i in range(12):
    days = 1 + i * 2
    it = {
        "id": f"ci-review-{i:03d}",
        "title": f"[CI fixture] 示例复盘 {i}",
        "date": (FIXED_TODAY - timedelta(days=days)).isoformat() + "T00:00:00",
        "days": days, "chars": 800 + i * 17,
        "status": "已处理" if i % 2 == 0 else "跟进中",
    }
    if i < 9:  # 后 3 条缺 frontmatter → 页面应归入「未标注」
        it["type"] = ["调试复盘", "运营复盘"][i % 2]
        it["productType"] = f"示例品类{i % 4}"
    review_items.append(it)
review_groups = [
    {"name": "调试复盘", "count": sum(1 for x in review_items if x.get("type") == "调试复盘")},
    {"name": "运营复盘", "count": sum(1 for x in review_items if x.get("type") == "运营复盘")},
    {"name": "未标注", "count": 3},
]

sections = {
    "version": 1,
    "generatedAt": FIXED_TODAY.isoformat() + "T12:00:00",
    "meta": {"guest": True, "fixture": True,
             "staleThresholds": {"news": 3, "writing": 45, "review": 30},
             "newsWindowDays": 21,
             "note": "[CI fixture] 派生快照，结构与线上同形。"},
    "sections": [
        {
            "id": "news", "name": "资讯", "icon": "📰",
            "source": "Obsidian 插件 qiaomu-ai-rss（CI fixture）",
            "sourcePath": "/tmp/ci-fixture-vault", "empty": False,
            "note": "[CI fixture] 新鲜度按最新内容日期算，不是插件 updatedAt（历史踩坑：updatedAt 可能早于最新文章）。",
            "upstreamUpdatedAt": FIXED_TODAY.isoformat() + "T08:00:00",
            "freshness": {"latest": "2026-09-29T09:00:00", "days": 1, "threshold": 3,
                          "stale": False, "basis": "最新内容的发布时间（不是 plugin 的 updatedAt）",
                          "empty": False},
            "stats": [
                {"k": "条目总数", "v": "120", "hint": "[CI fixture]"},
                {"k": "近 21 天", "v": "40 条", "hint": "[CI fixture]"},
                {"k": "未读（近期）", "v": "27 / 40", "hint": "[CI fixture]"},
                {"k": "订阅源", "v": "8 个", "hint": "[CI fixture]"},
                {"k": "疑似停更源", "v": "3 个", "hint": "[CI fixture]"},
            ],
            "windowDays": 21, "feedStaleDays": 100,
            "feeds": feeds, "groups": [
                {"name": g, "count": sum(1 for f in feeds if f["group"] == g)}
                for g in FEED_GROUPS
            ],
            "items": news_items,
        },
        {
            "id": "writing", "name": "写作", "icon": "✍️",
            "source": "Obsidian projects/（CI fixture）",
            "sourcePath": "/tmp/ci-fixture-vault/projects", "empty": False,
            "note": "[CI fixture] 书稿进度来自章节文件扫描。",
            "upstreamUpdatedAt": "2026-08-20T00:00:00",
            "freshness": {"latest": "2026-08-16T00:00:00", "days": 45, "threshold": 45,
                          "stale": False, "basis": "最新内容的发布时间（不是 plugin 的 updatedAt）",
                          "empty": False},
            "stats": [
                {"k": "原创作品", "v": "1 部", "hint": "[CI fixture]"},
                {"k": "章节进度", "v": "3 / 10", "hint": "[CI fixture]"},
                {"k": "正文字数", "v": "6,000", "hint": "[CI fixture]"},
            ],
            "books": [{
                "title": "示例书稿 [CI fixture]", "path": "/tmp/ci-fixture-vault/projects/示例书稿",
                "genre": "示例 / 悬疑", "acts": 3,
                "target": {"min": 20000, "max": 50000},
                "chars": 6000, "writtenCount": 3, "chapterCount": 10,
                "chapters": book_chapters,
            }],
            "clients": [
                {"title": "[CI fixture] 示例交付 A", "status": "进行中", "progress": 60, "due": "2026-10-15", "readOnly": True},
                {"title": "[CI fixture] 示例交付 B", "status": "已完成", "progress": 100, "due": "2026-09-01", "readOnly": True},
                {"title": "[CI fixture] 示例交付 C", "status": "待交付", "progress": 20, "due": "2026-11-01", "readOnly": True},
                {"title": "[CI fixture] 示例交付 D", "status": "进行中", "progress": None, "due": None, "readOnly": True},
            ],
            "items": [],
        },
        {
            "id": "review", "name": "复盘", "icon": "🧭",
            "source": "Obsidian 调试经验/（CI fixture）",
            "sourcePath": "/tmp/ci-fixture-vault/调试经验", "empty": False,
            "note": "[CI fixture] 分布来自 frontmatter 的 product-type。",
            "upstreamUpdatedAt": FIXED_TODAY.isoformat() + "T06:00:00",
            "freshness": {"latest": "2026-09-25T00:00:00", "days": 5, "threshold": 30,
                          "stale": False, "basis": "最新内容的发布时间（不是 plugin 的 updatedAt）",
                          "empty": False},
            "stats": [
                {"k": "篇数", "v": "12 篇", "hint": "[CI fixture]"},
                {"k": "涉及类型", "v": "3 类", "hint": "[CI fixture]"},
            ],
            "groups": review_groups,
            "items": review_items,
        },
    ],
}

# ── social.json ──────────────────────────────────────────────────────────
def post(i, platform, reads, likes=0, comments=0, shares=0):
    tag = {"X": "x", "公众号": "wx", "小红书": "xhs", "知识星球": "zs"}[platform]
    return {
        "id": f"ci-{tag}-{i:04d}",
        "platform": platform,
        "publishedAt": (FIXED_TODAY - timedelta(days=i % 30)).isoformat() + "T10:00:00",
        "title": f"[CI fixture] {platform} 示例内容 {i}",
        "url": f"https://example.com/{platform}/{i}",
        "metrics": {"reads": reads, "likes": likes, "comments": comments, "shares": shares},
    }

posts = ([post(i, "X", random.randint(20, 800), random.randint(0, 9)) for i in range(20)]
         + [post(i, "知识星球", random.randint(30, 500), random.randint(0, 5)) for i in range(15)]
         + [post(i, "小红书", random.randint(50, 2000), random.randint(2, 40)) for i in range(10)]
         + [post(i, "公众号", random.randint(5, 300), random.randint(0, 8)) for i in range(6)])

zs_count = sum(1 for p in posts if p["platform"] == "知识星球")
social = {
    "version": 1,
    "generatedAt": FIXED_TODAY.isoformat() + "T12:00:00+08:00",
    "dataPolicy": "[CI fixture] 未知不计为 0；历史快照不冒充实时数据。",
    "accounts": [
        {"platform": "X", "account": "@ci_fixture", "profileUrl": "https://x.com/ci_fixture",
         "dashboardUrl": "https://analytics.x.com/", "asOf": "2026-09-29",
         "sourceKind": "公开账号与最近推文（CI fixture）", "freshness": "fresh",
         "metrics": {"posts": 743, "following": 80, "followers": 67,
                     "recentPublicViews": 12345, "samplePosts": 20}},
        {"platform": "公众号", "account": "示例公众号（CI fixture）", "profileUrl": "",
         "dashboardUrl": "https://mp.weixin.qq.com/", "asOf": "2026-09-01",
         "sourceKind": "素材库快照（CI fixture）", "freshness": "snapshot",
         "connectionState": "limited", "verified": False,
         "permissionReason": "[CI fixture] 示例：未认证订阅号没有文章统计接口权限。",
         "metrics": {"samplePosts": 6, "avgReads": 42, "detailPosts": 0, "avgCompletion": None}},
        {"platform": "小红书", "account": "示例小红书（CI fixture）", "profileUrl": "",
         "dashboardUrl": "https://creator.xiaohongshu.com/", "asOf": "2026-09-29",
         "sourceKind": "本人主页与公开笔记（CI fixture）", "freshness": "fresh",
         "metrics": {"posts": 62, "following": 53, "followers": 234,
                     "interactions": 2170, "samplePosts": 10}},
        {"platform": "知识星球", "account": "示例星球（CI fixture）", "profileUrl": "",
         "dashboardUrl": "https://wx.zsxq.com/", "asOf": "2026-09-01",
         "sourceKind": "导出明细（CI fixture）", "freshness": "snapshot",
         "metrics": {"posts": zs_count, "groups": 2, "detailedPosts": zs_count,
                     "perGroup": {"示例星球A": zs_count - 3, "示例星球B": 3}}},
    ],
    "todayPlan": [
        {"id": p, "platform": p, "date": FIXED_TODAY.isoformat(),
         "defaultStatus": "unknown", "url": "#",
         "actionUrl": "#"} for p in ("X", "公众号", "小红书", "知识星球")
    ],
    "posts": posts,
    "analysis": [
        {"postId": posts[0]["id"], "label": "X 表现较好", "tone": "ok",
         "reason": "[CI fixture] 公开浏览高于样本中位数；赞 3。"},
        {"postId": posts[20]["id"], "label": "知识星球需要复盘", "tone": "warn",
         "reason": "[CI fixture] 阅读低于同类中位数。"},
        {"postId": posts[35]["id"], "label": "公众号样本未成熟", "tone": "info",
         "reason": "[CI fixture] 发布不足 7 天，暂不评分。"},
    ],
    "weeklyRecap": {
        "weekStart": "2026-09-21", "generatedAt": FIXED_TODAY.isoformat() + "T12:00:00+08:00",
        "perPlatform": {p: {"posts": n, "thisWeek": 1}
                        for p, n in (("X", 20), ("小红书", 10), ("公众号", 6), ("知识星球", 15))},
        "best": {"platform": "X", "title": "[CI fixture] 本周最佳示例",
                 "url": posts[0]["url"], "reads": 800, "likes": 9,
                 "comments": 1, "date": "2026-09-22", "score": 828},
        "worst": {"platform": "公众号", "title": "[CI fixture] 本周最弱示例",
                  "url": "", "reads": 5, "likes": 0, "comments": 0,
                  "date": "2026-09-25", "score": 5},
        "note": "[CI fixture] 排名只用真实阅读/互动数据。",
    },
    "automation": [
        {"platform": "公众号", "state": "available", "label": "[CI fixture] 草稿凭据已就绪",
         "detail": "示例：APPID 与 Secret 从本机环境读取，未自动发布。", "dashboardUrl": "https://mp.weixin.qq.com/"},
        {"platform": "小红书", "state": "available", "label": "[CI fixture] 账号数据已接通",
         "detail": "示例：只读公开数据，不自动互动。", "dashboardUrl": "https://creator.xiaohongshu.com/"},
    ],
    "knowledgeBase": {
        "available": True, "vault": "CI-Vault", "totalNotes": 20, "matchedNotes": 3,
        "items": [
            {"title": "[CI fixture] 与内容相关的示例笔记", "relativePath": "notes/demo.md",
             "obsidianUrl": "obsidian://open?vault=MyVault&file=notes%2Fdemo.md",
             "snippet": "[CI fixture] 示例摘要：本地知识库候选。",
             "topics": ["示例"],
             "updatedAt": "2026-09-28T10:00:00"},
        ],
    },
    "sources": [
        {"name": "X 公开账号与最近推文（CI fixture）", "kind": "public-timeline",
         "asOf": "2026-09-29", "modifiedAt": FIXED_TODAY.isoformat() + "T12:00:00+08:00",
         "records": 20, "available": True},
        {"name": "知识星球导出明细（CI fixture）", "kind": "export",
         "asOf": "2026-09-01", "modifiedAt": FIXED_TODAY.isoformat() + "T12:00:00+08:00",
         "records": 15, "available": True},
    ],
}

(ROOT / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
(ROOT / "sections.json").write_text(json.dumps(sections, ensure_ascii=False, indent=1))
(ROOT / "social.json").write_text(json.dumps(social, ensure_ascii=False, indent=1))
n = len(data["earnings"])
print(f"CI fixture 已生成：data.json({n} 条) / sections.json(3 区 {len(news_items)}+10+12 条) / social.json({len(posts)} 条)")
