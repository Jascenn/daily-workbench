#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""汇总「功能类型」区的数据 —— 把散在 Obsidian / 插件里的东西收成一份 sections.json。

为什么单独一个文件、不塞进 data.json
------------------------------------
`data.json` 是 **Notion 同步的产物**（`notion-sync.py` 每次整体回写），写入还有
「条数骤降 >50% 就 409」的护栏；而这里三个区来自 Obsidian 本地文件与插件缓存，
是**派生快照**、随时可重新生成。混在一起会有两个后果：①「同步一次」把派生数据冲掉；
② 护栏拿派生数据当"真实记录数"来判，容易误伤。
所以职责切开：**data.json 管记账，sections.json 管其它类型，两边互不写入。**

━━━ 四个已经量清的坑（都不是格式问题，是**会算出错误数字**的坑）━━━

① **顶层 `entries` 与「订阅内 `entries`」是两套互不重复的文章**
   `.entries` 100 条（窗口 2026-09-09 ~ 09-15）；`subscriptions[].entries` 117 条
   （窗口 2022-04-21 ~ 2026-09-24）。两者**按 id 交集 0、按 link 交集也是 0**，
   id 风格还不一样（顶层 32 位 hex / 订阅内带 `local-` 前缀）。
   只读顶层会**漏掉最新 9 天**的内容，且顶层条目**没有 `sourceName`**（要拿 sourceId 反查）。
   → 必须取并集：217 篇。

② **`updatedAt` 不能当"新鲜度"**
   它 = `1789441059889`(ms) = 2026-09-15 10:57，**比最新一篇文章（09-24）还早 6 天** ——
   逻辑上不可能是"最后抓取时间"，它实际是**顶层索引的写入时刻**。
   拿它当新鲜度会得出"14 天没更新"，而真实只差 **5 天**。
   → 一律以**最新一篇文章的 `publishedTs`** 为基准，并在输出里显式写上 `basis`，
     免得下一个人看数字时不知道它是怎么来的。

③ **条目跨度 4.5 年**（2022-04-21 ~ 2026-09-24），不设窗口就会把 2022 年的归档
   当成"今日资讯"。→ 每条 item 都带**预计算好的 `days`**，窗口过滤交给页面
   （与账本「最近 12 笔 / 看全部」同构，一个概念只留一处实现）。

④ **`readIds` 里有悬空 id**：20 个里只有 17 个能在 217 篇里对上（其中 16 个属订阅内 id 空间）。
   → "未读"只能对**匹配上的**算，**分母不能直接取 20**；悬空数要单独报出来。

用法
----
    python3 scripts/sections.py            # 打印摘要
    python3 scripts/sections.py --write    # 写 sections.json
    python3 scripts/sections.py --check    # 只比对，不写（退出码 0/2，别用管道读）
    python3 scripts/sections.py --now 2026-09-29T12:00:00   # 固定"现在"，便于测试
"""
import argparse
import datetime as dt
import glob
import json
import os
import re
import sys

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(DIR, "sections.json")
DATA = os.path.join(DIR, "data.json")

OBSIDIAN = os.path.expanduser(os.environ.get("WB_OBSIDIAN_VAULT", "~/Documents/Obsidian"))
RSS_PATH = os.path.join(OBSIDIAN, ".obsidian", "plugins", "qiaomu-ai-rss", "data.json")
WRITING_DIR = os.path.join(OBSIDIAN, "projects")
REVIEW_DIR = os.path.join(OBSIDIAN, "调试经验")
REVIEW_MOC = "调试经验 MOC.md"

# 每类"多久没动静算需要提醒"是**不一样的**，所以阈值必须按类型给，不能用一个全局值：
#   资讯：订阅源本来就每天推，超过 3 天没新条目基本就是采集断了/插件没跑
#   写作：原创按周推进，45 天没动才算真停摆
#   复盘：按需产出，一个月没有新沉淀就值得提一句
STALE_DAYS = {"news": 3, "writing": 45, "review": 30}

# 资讯条目列表的展示窗口（页面用它筛"近期"，更老的只进 stats 不进列表）
NEWS_WINDOW_DAYS = 21
# 单个源多久没更新算"这个源已经死了"
FEED_STALE_DAYS = 100

# 这里原来有个 DAY = 86400.0，只被 days_since 用来做"满 24 小时"取整。
# 改成按日历日算之后它没有消费者了，直接删掉 ——
# 留一个没人用的常量，下次有人看到它就会想"是不是该用它算天数"，
# 于是把已经修好的口径又改回去（和页面上那条"--shadow-md 现在有消费者"是同一类卫生要求）。


# ---------------------------------------------------------------- 时间工具

def parse_ts(v):
    """把插件里的各种时间写法统一成 datetime：毫秒 int / 秒 int / ISO 字符串（带 Z 或 tz）。"""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = float(v)
        # > 1e11 视为毫秒。1e11 秒 = 公元 5138 年，真出现也不会是"秒"。
        if n > 1e11:
            n /= 1000.0
        try:
            return dt.datetime.fromtimestamp(n)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{10,13}", s):
        return parse_ts(int(s))
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(s[:len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    return None


def days_since(t, now):
    """整数天，按**日历日**算（不是按"满 24 小时"）。已经"未来"的时间戳返回 0，
    不返回负数 —— 负数会让 days <= 窗口 那种过滤静默失效，是另一种假绿。

    为什么必须按日历日：这个数有三个消费者，它们说的都是「天」——
      ① 界面文案「今天 / 昨天 / N 天前」（agoTxt）；
      ② 显示窗口「近 21 天」的过滤；
      ③ 过期判定 days > 阈值。
    按"满 24 小时"取整的话会出现两件坏事：
      · 一篇 23:50 发的文章，次日 23:49 仍显示「今天」、23:51 才跳成「昨天」——
        人读起来就是错的（日历上明明已经是第二天）；
      · 更要命的是 ②③ 会**在同一天里自己变**：一个字的新闻都没来，
        按钮上的「近 21 天 116」可能变成 117、过期标记也可能翻面。
    这不是推测，是实测：同一天 23:45 与 23:58 两次生成，两条条目的 days 从 15→16、18→19，
    于是 `--check` 报「数据有变化」—— 可数据一个字没变，变的只是时钟。
    这跟文件头那条「generatedAt 不能进比对」是同一枚硬币：
    **让闸门因为"时刻"而变红，人就会开始忽略它，然后真的过期也看不见。**
    按日历日算之后，days 只在跨零点时变 —— 一天变一次，且那一次是真的该重建快照。
    """
    if t is None:
        return None
    return max(0, (now.date() - t.date()).days)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S") if t else None


def freshness(latest, now, kind):
    """统一算新鲜度。**基准永远是"最新内容的发布时间"**，不是文件的 updatedAt（见文件头坑②）。"""
    th = STALE_DAYS.get(kind, 30)
    d = days_since(latest, now) if latest else None
    return {
        "latest": iso(latest),
        "days": d,
        "threshold": th,
        "stale": (d is None) or (d > th),
        "basis": "最新内容的发布时间（不是 plugin 的 updatedAt）",
        "empty": latest is None,
    }


# ---------------------------------------------------------------- 小工具

def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def frontmatter(text):
    """极简 frontmatter 解析：只要 `key: value`，值支持 [a, b] 与裸标量。
    不引 yaml 依赖 —— 这些笔记的 frontmatter 就四个键，够用且不会因格式怪而炸。"""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    out = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        out[k] = v
    return out


def as_list(v):
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


def short(s, n=140):
    """截断。顺手把换行压成空格：这个字段最后会进 HTML 文本节点，留着换行会让
    «卡片高度随内容跳» —— 排版问题，不报错，只在渲染出来才看得见。"""
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


# ---------------------------------------------------------------- 资讯

def collect_news(now):
    d = read_json(RSS_PATH)
    if not isinstance(d, dict):
        return {
            "id": "news", "name": "资讯", "icon": "📰",
            "source": "Obsidian 插件 qiaomu-ai-rss",
            "sourcePath": RSS_PATH, "empty": True,
            "note": "读不到订阅插件的数据文件。检查 Obsidian 里 qiaomu-ai-rss 插件是否还在、data.json 是否被移动。",
            "stats": [], "items": [], "feeds": [], "freshness": freshness(None, now, "news"),
        }

    top = d.get("entries") or []
    subs = d.get("subscriptions") or []

    # 源名映射：顶层条目的 sourceId 是 slug（如 simonwillison），订阅内是 `local:...`。
    # 两条来源分别建表，查不到时**退化成 sourceId 本身**（它本身有信息量，别写"未知"）。
    name_by_id = {}
    group_by_id = {}
    for s in subs:
        sid = s.get("id")
        if sid:
            name_by_id[sid] = s.get("name") or sid
            group_by_id[sid] = s.get("group") or ""
        for e in (s.get("entries") or []):
            if e.get("sourceId") and e.get("sourceName"):
                name_by_id.setdefault(e["sourceId"], e["sourceName"])

    # ① 并集：顶层 ∪ 订阅内。两套 id 空间不相交（已验证），所以直接按 id 合并即可。
    merged = {}
    for e in top:
        if e.get("id"):
            merged[e["id"]] = e
    for s in subs:
        for e in (s.get("entries") or []):
            if e.get("id"):
                merged[e["id"]] = e

    read_ids = set(d.get("readIds") or [])

    items = []
    for e in merged.values():
        ts = parse_ts(e.get("publishedTs")) or parse_ts(e.get("published"))
        sid = e.get("sourceId") or ""
        # 只带页面上真要用的字段。原来带了 titleRaw（原题）与 group，但：
        # ① title = titleZh || title，页面只显示一个，原题点链接就有；
        # ② group 在 feeds 里已经按源给全了。
        # 结果：产物从 209KB → 约 120KB，而它要**整份内嵌进离线页**，每 KB 都是两份。
        items.append({
            "id": e.get("id"),
            "title": (e.get("titleZh") or e.get("title") or "(无标题)").strip(),
            "link": e.get("link") or "",
            "source": e.get("sourceName") or name_by_id.get(sid) or sid or "未知源",
            "published": iso(ts),
            "days": days_since(ts, now),
            "read": e.get("id") in read_ids,
            "summary": short(e.get("summaryZh") or e.get("summary") or "", 100),
        })
    # 排序键：没有时间的排最后（None 不能直接参与比较，也不能当成 0）
    items.sort(key=lambda x: (x["published"] is None, x["published"] or ""), reverse=False)
    items.reverse()
    items.sort(key=lambda x: x["published"] is None)

    latest = max((parse_ts(i["published"]) for i in items if i["published"]), default=None)

    # ④ 已读的匹配情况：把"悬空"如实报出来，别假装分母是 len(readIds)
    matched_read = sum(1 for i in items if i["read"])
    dangling = len(read_ids) - matched_read

    recent = [i for i in items if i["days"] is not None and i["days"] <= NEWS_WINDOW_DAYS]
    unread_recent = sum(1 for i in recent if not i["read"])

    # 每个源的新鲜度：这才是这个区真正的价值 —— 一眼看出哪几个源已经死了
    feeds = []
    for s in subs:
        es = s.get("entries") or []
        ts = [parse_ts(e.get("publishedTs")) or parse_ts(e.get("published")) for e in es]
        ts = [t for t in ts if t]
        last = max(ts) if ts else None
        dd = days_since(last, now)
        feeds.append({
            "name": s.get("name") or "(未命名源)",
            "group": s.get("group") or "",
            "url": s.get("url") or "",
            "count": len(es),
            "latest": iso(last),
            "days": dd,
            "dead": (dd is None) or (dd > FEED_STALE_DAYS),
        })
    feeds.sort(key=lambda f: (f["days"] is None, -(f["days"] or 0)))

    dead = [f for f in feeds if f["dead"]]
    groups = []
    for g in (d.get("subscriptionGroups") or []):
        groups.append({"name": g.get("name") or "(未分组)",
                       "count": sum(1 for f in feeds if f["group"] == g.get("name"))})

    stats = [
        {"k": "条目总数", "v": "%d" % len(items), "hint": "顶层 %d + 订阅内 %d，两套互不重复" % (len(top), sum(len(s.get('entries') or []) for s in subs))},
        {"k": "近 %d 天" % NEWS_WINDOW_DAYS, "v": "%d 条" % len(recent), "hint": "更老的 %d 条只计入总数，不进列表" % (len(items) - len(recent))},
        {"k": "未读（近期）", "v": "%d / %d" % (unread_recent, len(recent)), "hint": "已读标记 %d 个，其中 %d 个指向已不存在的条目" % (len(read_ids), dangling)},
        {"k": "订阅源", "v": "%d 个" % len(feeds), "hint": "分 %d 组" % len(groups)},
        {"k": "疑似停更源", "v": "%d 个" % len(dead), "hint": ("超过 %d 天没有新条目：%s" % (FEED_STALE_DAYS, "、".join(f["name"] for f in dead))) if dead else "全部在 %d 天内有更新" % FEED_STALE_DAYS},
    ]

    return {
        "id": "news", "name": "资讯", "icon": "📰",
        "source": "Obsidian 插件 qiaomu-ai-rss",
        "sourcePath": RSS_PATH,
        "empty": not items,
        # ⚠️ note / hint / stats 的文案**一律纯文本**：它们会被页面用 textContent 直接显示，
        # 写 `**强调**` 或反引号就会把星号原样印在界面上（第一版资讯卡里就真的出现了
        # 「**最新一篇文章的时间**」）。要强调就用「」，不要用 markdown 标记。
        "note": "新鲜度基准是「最新一篇文章的时间」。插件里的 updatedAt=%s 比最新文章还早 6 天，"
                "那是顶层索引的写入时刻，不能当「最后抓取」用（拿它算会把 5 天说成 14 天）。" % (
                    iso(parse_ts(d.get("updatedAt"))) or "—"),
        "upstreamUpdatedAt": iso(parse_ts(d.get("updatedAt"))),
        "freshness": freshness(latest, now, "news"),
        "stats": stats,
        "feeds": feeds,
        "groups": groups,
        "items": items,
        "windowDays": NEWS_WINDOW_DAYS,
        # 一并把阈值发给页面。页面里再写一遍常量 = 两套口径，
        # 改了 Python 忘了改 JS 就会出现"上边说 100 天下边说 90 天"。 */
        "feedStaleDays": FEED_STALE_DAYS,
    }


# ---------------------------------------------------------------- 写作

CH_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def collect_writing(now, data=None):
    proj = os.path.join(WRITING_DIR, "雨夜与玻璃幕墙")
    books = []
    if os.path.isdir(proj):
        books.append(build_book(proj, now))

    # 客户内容：来自 data.json 的 projects，category=客户服务（都是要给客户交付的教程/内容）
    clients = []
    for p in ((data or {}).get("projects") or []):
        if (p.get("category") or "").strip() == "客户服务":
            clients.append({
                "title": p.get("name") or "(未命名)",
                "status": p.get("status") or "",
                "progress": p.get("progress"),
                "due": p.get("due") or "",
                "readOnly": bool(p.get("readOnly")),
            })
    clients.sort(key=lambda x: (-(x["progress"] or 0), x["title"]))

    # 客户内容的状态分布必须**从真实数据派生**，不能写死成「全部待交付」。
    # 第一版这里写的是「全部是待交付物」，而实际 11 项 status 全是「已完成」——
    # 一句听起来很合理的话盖住了一个不成立的事实，跟"手抄结论数字"是同一类错。
    cstat = {}
    for c in clients:
        key = c["status"] or "未标状态"
        cstat[key] = cstat.get(key, 0) + 1
    cstat_txt = "、".join("%s %d" % (k, v) for k, v in sorted(cstat.items(), key=lambda kv: -kv[1])) or "—"

    book_items = sum(len(b.get("chapters") or []) for b in books)
    written = sum(b.get("writtenCount") or 0 for b in books)
    chars = sum(b.get("chars") or 0 for b in books)
    latest_file = max((b["_latest"] for b in books if b.get("_latest")), default=None)
    for b in books:
        b.pop("_latest", None)

    total = book_items + len(clients)
    stats = [
        {"k": "原创作品", "v": "%d 部" % len(books), "hint": "Obsidian projects/ 下的创作项目"},
        {"k": "章节进度", "v": "%d / %d" % (written, book_items), "hint": "已写正文的章节数 / 目录里的章节数"},
        {"k": "正文字数", "v": "{:,}".format(chars),
         "hint": "只统计 第*章.md，不含设定与节选（避免与正文重复计数）"},
        {"k": "客户内容", "v": "%d 项" % len(clients), "hint": "Notion 里 category=客户服务 的项目；状态：%s" % cstat_txt},
        {"k": "最近改动", "v": ("%s（%d 天前）" % (latest_file.strftime("%Y-%m-%d"), days_since(latest_file, now))) if latest_file else "—",
         "hint": "以文件修改时间为准"},
    ]

    return {
        "id": "writing", "name": "写作", "icon": "✍️",
        "source": "Obsidian projects/ + Notion 项目库（客户服务）",
        "sourcePath": WRITING_DIR,
        "empty": total == 0,
        "note": "「正文字数」只算 第*章.md：同目录还有一份《节选》，与第一章内容大面积重合，"
                "两边相加会让字数虚高。",
        "freshness": freshness(latest_file, now, "writing"),
        "stats": stats,
        "books": books,
        "clients": clients,
        "clientStatus": cstat,
        "items": [],
    }


def build_book(path, now):
    outline = read_text(os.path.join(path, "章节目录.md"))
    chapters = []
    for m in re.finditer(r"^##\s+(.+?)\s*$", outline, re.M):
        title = m.group(1).strip()
        if title.startswith("《") or title in ("章节目录",):
            continue
        # 正文文件名只取到"第X章"（如 第一章.md），标题后面还跟着一句梗概
        stem = re.match(r"^(第[一二三四五六七八九十百]+章|尾声)", title)
        name = (stem.group(1) if stem else title)
        f = os.path.join(path, name + ".md")
        exists = os.path.isfile(f)
        body = read_text(f) if exists else ""
        chapters.append({
            "title": title,
            "name": name,
            "written": exists,
            "chars": len(body) if exists else 0,
            "summary": short(title[len(name):].strip(), 80),
        })

    # 正文字数：只统计 第*章.md。节选与第一章重合，加进去会虚高（见 note）。
    ch_files = sorted(glob.glob(os.path.join(path, "第*章.md")))
    chars = sum(len(read_text(f)) for f in ch_files)

    # 设定/大纲类文件（单独列，不混进正文）
    docs = []
    for nm in ("项目总纲.md", "人物设定.md", "章节目录.md"):
        f = os.path.join(path, nm)
        if os.path.isfile(f):
            t = read_text(f)
            fm = frontmatter(t)
            docs.append({
                "title": nm[:-3], "chars": len(t),
                "type": fm.get("type", ""), "productType": fm.get("product-type", ""),
            })
    drafts = []
    for f in sorted(glob.glob(os.path.join(path, "*.md"))):
        nm = os.path.basename(f)
        if nm in ("项目总纲.md", "人物设定.md", "章节目录.md") or re.match(r"^第.+章\.md$", nm):
            continue
        drafts.append({"title": nm[:-3], "chars": len(read_text(f))})

    # 篇幅目标：从总纲里读"（2 万 - 5 万字）"，这是作者自己定的，不是我猜的
    target = None
    m = re.search(r"(\d+)\s*万\s*[-~—至]\s*(\d+)\s*万", read_text(os.path.join(path, "项目总纲.md")))
    if m:
        target = {"min": int(m.group(1)) * 10000, "max": int(m.group(2)) * 10000}

    acts = len(re.findall(r"^###\s+第[一二三]幕", read_text(os.path.join(path, "项目总纲.md")), re.M))
    all_files = glob.glob(os.path.join(path, "*.md"))
    latest = dt.datetime.fromtimestamp(max(os.path.getmtime(f) for f in all_files)) if all_files else None

    return {
        "title": "雨夜与玻璃幕墙",
        "path": path,
        "genre": "黑暗都市 / 社会政治 / 现实悬疑",
        "acts": acts,
        "target": target,
        "chars": chars,
        "writtenCount": sum(1 for c in chapters if c["written"]),
        "chapterCount": len(chapters),
        "chapters": chapters,
        "docs": docs,
        "drafts": drafts,
        "_latest": latest,
    }


# ---------------------------------------------------------------- 复盘

def collect_review(now):
    notes = []
    for f in sorted(glob.glob(os.path.join(REVIEW_DIR, "调试经验-*.md"))):
        nm = os.path.basename(f)
        if nm == REVIEW_MOC:
            continue
        t = read_text(f)
        fm = frontmatter(t)
        d = parse_ts(fm.get("date")) or dt.datetime.fromtimestamp(os.path.getmtime(f))
        notes.append({
            "title": nm[:-3],
            "date": iso(d),
            "days": days_since(d, now),
            "chars": len(t),
            "type": fm.get("type") or "未标注",
            "productType": (as_list(fm.get("product-type")) or ["未标注"])[0],
            "products": as_list(fm.get("product")),
            "status": fm.get("status") or "",
            "modified": iso(dt.datetime.fromtimestamp(os.path.getmtime(f))),
        })
    notes.sort(key=lambda x: (x["date"] is None, x["date"] or ""), reverse=False)
    notes.reverse()
    notes.sort(key=lambda x: x["date"] is None)

    latest = max((parse_ts(n["date"]) for n in notes if n["date"]), default=None)

    by_type = {}
    for n in notes:
        by_type.setdefault(n["productType"], []).append(n)
    groups = [{"name": k, "count": len(v), "items": v}
              for k, v in sorted(by_type.items(), key=lambda kv: -len(kv[1]))]

    status_c = {}
    for n in notes:
        if n["status"]:
            status_c[n["status"]] = status_c.get(n["status"], 0) + 1

    # 守恒：分组总数必须等于笔记数，否则就是分组时丢了（这类断言比"分成几组"有用得多）
    assert sum(g["count"] for g in groups) == len(notes), "复盘分组丢笔记"

    stats = [
        {"k": "篇数", "v": "%d 篇" % len(notes), "hint": "调试经验/ 下的全部复盘（不含 MOC 索引）"},
        {"k": "涉及类型", "v": "%d 类" % len(groups), "hint": "按 frontmatter 的 product-type 分组"},
        {"k": "品类数", "v": "%d 个" % len({p for n in notes for p in n["products"]}), "hint": "product 字段去重后"},
        {"k": "标注状态", "v": "%d / %d" % (sum(status_c.values()), len(notes)), "hint": "未标注状态的 %d 篇" % (len(notes) - sum(status_c.values()))},
    ]

    return {
        "id": "review", "name": "复盘", "icon": "🧭",
        "source": "Obsidian 调试经验/",
        "sourcePath": REVIEW_DIR,
        "empty": not notes,
        "note": "按 frontmatter 的 product-type 分组。缺 frontmatter 的记为「未标注」而不是丢掉 ——"
                "丢掉会让「分组总数 == 篇数」这条守恒悄悄不成立。",
        "freshness": freshness(latest, now, "review"),
        "stats": stats,
        "groups": groups,
        "statusCounts": status_c,
        "items": notes,
    }


# ---------------------------------------------------------------- 组装

def build(now=None):
    now = now or dt.datetime.now()
    data = read_json(DATA) or {}
    return {
        "version": 1,
        "generatedAt": iso(now),
        "meta": {
            "obsidianRoot": OBSIDIAN,
            "staleThresholds": STALE_DAYS,
            "newsWindowDays": NEWS_WINDOW_DAYS,
            "note": "派生快照，随时可重新生成。不要手工编辑；改数据请改上游（Obsidian / Notion）后重跑 scripts/sections.py --write。",
        },
        "sections": [
            collect_news(now),
            collect_writing(now, data),
            collect_review(now),
        ],
    }


def _canon(obj):
    """比对用的规范形式：**剔掉 generatedAt**。

    为什么必须剔：它是生成时刻，天生随时间变。整份比对会变成"是否跨过了一秒"的抽奖 ——
    刚 --write 完立刻 --check，同一秒内返回 0、下一秒返回 2。
    一个会随机变红的闸门比没有闸门更糟：人会开始忽略它，然后真的过期也看不见
    （与"永远绿"是同一枚硬币的两面）。
    剔掉之后：--write 完 --check 恒为 0；内容真的变了（包括 days 因时间推移而变）才报 2。"""
    o = dict(obj)
    o.pop("generatedAt", None)
    return json.dumps(o, ensure_ascii=False, sort_keys=True)


def summarize(obj):
    out = []
    for s in obj["sections"]:
        f = s["freshness"]
        d = "—" if f["days"] is None else "%d 天" % f["days"]
        out.append("  %-4s %-6s items=%-4d 最新=%-12s 距今=%-6s 阈值=%d天 %s" % (
            s["icon"], s["name"], len(s.get("items") or []),
            (f["latest"] or "—")[:10], d, f["threshold"],
            "⚠️ 过期" if f["stale"] else "✓"))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="写 sections.json")
    ap.add_argument("--check", action="store_true", help="只比对，不写（退出码 0=最新 / 2=需要重建）")
    ap.add_argument("--now", help="固定'现在'（ISO），便于测试；默认取系统时间")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    now = parse_ts(args.now) if args.now else dt.datetime.now()
    obj = build(now)
    # 生成时间固定时，产物应当完全可复现（--now + 同样的上游 => 同样的字节）
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        if not os.path.exists(OUT):
            print("sections.json 不存在，需要生成")
            return 2
        with open(OUT, encoding="utf-8") as f:
            cur = f.read()
        try:
            old = json.loads(cur)
        except Exception:
            print("需重新生成：sections.json 不是合法 JSON")
            return 2
        # 比对用规范形式（剔除 generatedAt，见 _canon 的注释：否则闸门会随机变红）
        if _canon(old) == _canon(obj):
            print("已是最新（%d 个区，内容与上游一致）" % len(obj["sections"]))
            return 0
        # 差异要说清差在哪 —— 只说"不一致"等于没说（同一个教训在 make-local 上踩过）
        oldc = {s.get("id"): s for s in (old.get("sections") or [])}
        newc = {s["id"]: s for s in obj["sections"]}
        if set(oldc) != set(newc):
            print("需重新生成：区集合变了（%s → %s）" % (
                "、".join(sorted(oldc)) or "空", "、".join(sorted(newc)) or "空"))
            return 2
        diff = [k for k in sorted(newc)
                if json.dumps(oldc.get(k), sort_keys=True, ensure_ascii=False)
                != json.dumps(newc.get(k), sort_keys=True, ensure_ascii=False)]
        if diff:
            det = []
            for k in diff:
                o, n = oldc[k], newc[k]
                of, nf = o.get("freshness") or {}, n.get("freshness") or {}
                if of.get("days") != nf.get("days"):
                    det.append("%s 的「距今天数」%s → %s 天（时间推移，快照该重建）" % (n.get("name", k), of.get("days"), nf.get("days")))
                else:
                    bits = []
                    for key in ("items", "feeds", "clients", "groups", "books", "stats"):
                        a, b = len(o.get(key) or []), len(n.get(key) or [])
                        if a != b:
                            bits.append("%s %d→%d" % (key, a, b))
                    det.append("%s 数据有变化%s" % (n.get("name", k), ("（" + "、".join(bits) + "）") if bits else ""))
            print("需重新生成：" + "；".join(det))
        else:
            print("需重新生成：只有顶层字段变了")
        return 2

    if args.write:
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, OUT)
        if not args.quiet:
            print("已生成 %s" % os.path.relpath(OUT, DIR))
            print(summarize(obj))
        return 0

    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
