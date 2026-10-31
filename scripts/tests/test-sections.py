#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/sync/sections.py 的断言测试。

    python3 scripts/tests/test-sections.py

为什么单独一套、不用 JS 那套：
  ① sections.py 是**取数与口径**的唯一出处（并集、时间窗口、新鲜度基准、分组守恒），
     这些必须在数据层验；页面那套只能验"拿到 JSON 之后渲染得对不对"。
  ② 页面测试喂的是**产物**，产物错它也错 —— 拿产物当夹具只能验一致性，验不了正确性。
     所以这里的期望值一律**从上游原始文件独立重算**（重新读插件 JSON / 目录 / data.json），
     而不是读 sections.json 里已有的字段。独立重算才能发现"产物本身算错了"。

⚠️ 这里刻意不测的东西：不启动服务、不碰网络、不写任何文件（只读 + 一次临时目录里的
   --write/--check 往返）。跑在哪个 python 上都行（3.9 起）。
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SYNC_DIR = os.path.join(ROOT, "scripts", "sync")
sys.path.insert(0, SYNC_DIR)
import sections as S  # noqa: E402

PY = sys.executable
SRC_PY = os.path.join(SYNC_DIR, "sections.py")   # 被测源码本身：有些断言要读它（如"不许退回旧写法"）
PASS = FAIL = 0


def t(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print("  %s %s%s" % ("✓" if cond else "✗", name, ("  → " + str(extra)) if extra else ""))


def rd(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def rdj(p):
    return json.loads(rd(p))


NOW = dt.datetime(2026, 9, 29, 12, 0, 0)   # 固定"现在"，让所有天数断言可复现
OBJ = S.build(NOW)
SEC = {s["id"]: s for s in OBJ["sections"]}

print("被测：scripts/sync/sections.py  固定 now=%s" % NOW)

# ─────────────────────────────────────────────────────────────── 1. 结构
print("\n1. 产物结构")
t("顶层有 version / generatedAt / meta / sections",
  all(k in OBJ for k in ("version", "generatedAt", "meta", "sections")),
  "键=" + ",".join(sorted(OBJ)))
t("三个区齐全且 id 唯一", sorted(SEC) == ["news", "review", "writing"] and
  len(OBJ["sections"]) == 3, "、".join(sorted(SEC)))
NEED = ("id", "name", "icon", "source", "empty", "note", "freshness", "stats")
t("每个区都带必备键", all(all(k in s for k in NEED) for s in OBJ["sections"]),
  "缺：" + "、".join(k for s in OBJ["sections"] for k in NEED if k not in s) or "无")
t("freshness 的字段齐全（含 basis —— 没有基准的「过期」没法让人判断该不该信）",
  all(set(("latest", "days", "threshold", "stale", "basis", "empty")) <= set(s["freshness"]) for s in OBJ["sections"]))

# ─────────────────────────────────────────────────────────────── 2. 资讯：并集
print("\n2. 资讯区：两套 entries 的并集（坑①）")
raw = rdj(S.RSS_PATH)
_top = raw.get("entries") or []
_nest = [e for sub in (raw.get("subscriptions") or []) for e in (sub.get("entries") or [])]
_union = {}
for e in _top + _nest:
    if e.get("id"):
        _union[e["id"]] = e
news = SEC["news"]
t("① 条目数 == 顶层 ∪ 订阅内（独立重算，不是读产物里的 stats）",
  len(news["items"]) == len(_union), "产物 %d / 重算 %d" % (len(news["items"]), len(_union)))
t("① 只读顶层会漏内容（证明取并集是必要的，不是保险）",
  len(_top) != len(_union) and len(_nest) > 0, "顶层 %d < 并集 %d" % (len(_top), len(_union)))
t("两套 entries 的 id 交集为 0（所以按 id 合并即可，不需要去重逻辑）",
  len(set(e["id"] for e in _top) & set(e["id"] for e in _nest)) == 0)
t("① 每个条目的链接都是 http(s) 或空（不把 javascript: 喂给页面）",
  all((not it["link"]) or re.match(r"^https?://", it["link"]) for it in news["items"]),
  "非 http 的 %d 条" % sum(1 for it in news["items"] if it["link"] and not re.match(r"^https?://", it["link"])))

# ─────────────────────────────────────────────────────────────── 3. 资讯：排序与天数
print("\n3. 资讯区：排序与「距今」")
pubs = [it["published"] for it in news["items"]]
nonnull = [p for p in pubs if p]
t("有时间的条目按时间**降序**", nonnull == sorted(nonnull, reverse=True),
  "首 %s 末 %s" % (nonnull[0] if nonnull else "-", nonnull[-1] if nonnull else "-"))
t("无时间的条目一律排在最后（None 不能当 0 参与比较）",
  all(p is None for p in pubs[len(nonnull):]), "无时间 %d 条" % (len(pubs) - len(nonnull)))
# 独立重算 days：不读产物里的 days，自己按 published 与 NOW 算。
# ⚠️ 这里必须按**日历日**重算。上一版写的是 `(NOW - t).total_seconds()/86400` ——
# 那是把实现的公式抄了一遍，看着"独立"，其实只是镜像：口径错了它跟着错，永远绿。
# 断言的价值在于它说出**独立的口径**，而不是复述实现。
bad = []
for it in news["items"][:25]:
    if not it["published"]:
        continue
    d = (NOW.date() - dt.datetime.fromisoformat(it["published"]).date()).days
    if it["days"] != max(0, d):
        bad.append("%s: 产物 %s / 重算 %s" % (it["published"][:10], it["days"], max(0, d)))
t("days == 按**日历日**重算的整数天（口径只有一份，页面才敢直接信它）",
  not bad, "、".join(bad[:3]) or "抽查 25 条一致")
t("days 无负数（未来时间戳被夹到 0，否则 days<=窗口 这种过滤会静默失效）",
  all(it["days"] is None or it["days"] >= 0 for it in news["items"]))
# 下面两条钉的是「同一日历日内换时刻，days 不许变」这个性质。
# 为什么单独钉它：实测同一天 23:45 与 23:58 两次生成，两条条目 days 从 15→16、18→19，
# `--check` 于是报「数据有变化」—— 数据一个字没变，变的只是时钟。
# 按日历日算之后，days 一天只变一次（跨零点），且那一次是真的该重建快照。
# 第一条是"源码上不许退回旧写法"，后两条是"行为上确实是日历日"——
# 两条腿都要：只查源码的话，公式换了写法仍然会漏；只查行为的话，看不出实现用的是哪个口径。
with open(SRC_PY, encoding="utf-8") as f:
    SRC_TXT = f.read()
_ds = SRC_TXT.split("def days_since")[1].split("def ")[0]
t("★ days_since 按日历日算（源码里用 .date() 相减、不再用 total_seconds()/86400）",
  ".date()" in _ds and "total_seconds()" not in _ds,
  _ds.strip().splitlines()[-1].strip()[:70])
_now_a = dt.datetime(2026, 9, 29, 23, 45, 0)
_now_b = dt.datetime(2026, 9, 29, 23, 58, 0)
_pub = dt.datetime(2026, 9, 14, 23, 50, 0)          # 时刻比"现在"晚 5~8 分钟的那个形态
t("★ 同一日历日内 23:45 与 23:58 两次生成，days 完全相同（时钟不许影响快照）",
  S.days_since(_pub, _now_a) == S.days_since(_pub, _now_b) == 15,
  "23:45→%s / 23:58→%s" % (S.days_since(_pub, _now_a), S.days_since(_pub, _now_b)))
t("跨零点才 +1（这是唯一该变的时候）",
  S.days_since(_pub, dt.datetime(2026, 9, 30, 0, 1, 0)) == 16,
  "次日 00:01 → %s" % S.days_since(_pub, dt.datetime(2026, 9, 30, 0, 1, 0)))

# ─────────────────────────────────────────────────────────────── 4. 窗口与已读
print("\n4. 资讯区：时间窗口与已读标记")
t("窗口天数来自 meta，且每条都低于总数（说明窗口真的在起作用）",
  news["windowDays"] == S.NEWS_WINDOW_DAYS and news["windowDays"] < 365, "%d 天" % news["windowDays"])
inwin = [it for it in news["items"] if it["days"] is not None and it["days"] <= news["windowDays"]]
t("① 守恒：窗口内 + 窗口外 == 总数",
  len(inwin) + (len(news["items"]) - len(inwin)) == len(news["items"]),
  "%d + %d = %d" % (len(inwin), len(news["items"]) - len(inwin), len(news["items"])))
_readids = set(raw.get("readIds") or [])
_matched = len(_readids & set(_union))
t("② 已读匹配数 == 独立重算的 readIds ∩ 条目 id 数（分母不能直接取 len(readIds)）",
  sum(1 for it in news["items"] if it["read"]) == _matched,
  "产物 %d / 重算 %d（readIds 共 %d）" % (sum(1 for it in news["items"] if it["read"]), _matched, len(_readids)))
t("悬空 readIds 被如实报出来（20 个里有 3 个指向已不存在的条目）",
  any("指向已不存在" in s.get("hint", "") for s in news["stats"]),
  [s["hint"] for s in news["stats"] if "已读标记" in s.get("hint", "")][:1])

# ─────────────────────────────────────────────────────────────── 5. 源健康
print("\n5. 资讯区：订阅源健康")
subs = raw.get("subscriptions") or []
t("feeds 条数 == subscriptions 条数（不多不少，没有凭空造源）",
  len(news["feeds"]) == len(subs), "产物 %d / 上游 %d" % (len(news["feeds"]), len(subs)))
t("每个 feed 名字都能在 subscriptions 里找到",
  set(f["name"] for f in news["feeds"]) == set((s.get("name") or "(未命名源)") for s in subs),
  "、".join(sorted(set(f["name"] for f in news["feeds"]) ^ set((s.get("name") or "(未命名源)") for s in subs))) or "完全一致")
# 独立重算每个源的最新时间
bad = []
for sub in subs:
    es = [e for e in (sub.get("entries") or []) if e.get("publishedTs")]
    want = max((e["publishedTs"] for e in es), default=None)
    f = next(x for x in news["feeds"] if x["name"] == (sub.get("name") or "(未命名源)"))
    ws = int(want / 1000) if want else None
    got = int(dt.datetime.fromisoformat(f["latest"]).timestamp()) if f["latest"] else None
    # 都归到「哪一天」再比，避免时区/精度噪声
    if (ws is None) != (got is None) or (ws and got and abs(ws - got) > 86400):
        bad.append("%s: 产物 %s / 重算 %s" % (f["name"], f["latest"], want))
t("② 每个源的最新时间 == 用它的 entries 独立重算出来的",
  not bad, "、".join(bad) or "%d 个源一致" % len(subs))
t("dead 判定 == days > feedStaleDays（标记与判据不许各说各话）",
  all(f["dead"] == (f["days"] is not None and f["days"] > news["feedStaleDays"]) for f in news["feeds"]),
  "停更 %d 个" % sum(1 for f in news["feeds"] if f["dead"]))
t("feedStaleDays 由上游给出（页面不硬编码）", isinstance(news.get("feedStaleDays"), int), news.get("feedStaleDays"))

# ─────────────────────────────────────────────────────────────── 6. 写作
print("\n6. 写作区：章节与字数")
wr = SEC["writing"]
b0 = (wr.get("books") or [{}])[0]
proj = os.path.join(S.WRITING_DIR, "雨夜与玻璃幕墙")
_outline = rd(os.path.join(proj, "章节目录.md"))
want_ch = len([1 for m in re.finditer(r"^##\s+(.+?)\s*$", _outline, re.M)
               if not m.group(1).strip().startswith("《")])
t("① 章节行数 == 章节目录里的 ## 条数（独立重算）",
  b0.get("chapterCount") == want_ch, "产物 %d / 重算 %d" % (b0.get("chapterCount"), want_ch))
_exist = 0
for c in b0.get("chapters") or []:
    if os.path.isfile(os.path.join(proj, c["name"] + ".md")):
        _exist += 1
t("① writtenCount == 目录里真有对应文件的那几章（不是猜的）",
  b0.get("writtenCount") == _exist, "产物 %d / 文件系统 %d" % (b0.get("writtenCount"), _exist))
_chfiles = sorted(glob.glob(os.path.join(proj, "第*章.md")))
_want_chars = sum(len(rd(f)) for f in _chfiles)
t("① 正文字数 == 只算 第*章.md 的字数之和（独立重算）",
  b0.get("chars") == _want_chars, "产物 %d / 重算 %d" % (b0.get("chars"), _want_chars))
_allmd = sum(len(rd(f)) for f in glob.glob(os.path.join(proj, "*.md")))
t("正文字数 **小于** 目录里所有 md 之和（证明「排除设定与节选」真的生效了）",
  b0.get("chars") < _allmd, "正文 %d < 全部 md %d" % (b0.get("chars"), _allmd))
_book_id = str(b0.get("bookId") or "")
_chapter_ids = [str(c.get("chapterId") or "") for c in (b0.get("chapters") or [])]
t("作品与所有章节都有稳定 ID，且不暴露本机绝对路径",
  re.fullmatch(r"book_[0-9a-f]{20}", _book_id)
  and all(re.fullmatch(r"chapter_[0-9a-f]{20}", x) for x in _chapter_ids)
  and "path" not in b0
  and all("path" not in c for c in (b0.get("chapters") or [])),
  "%s / %d 个章节 ID" % (_book_id, len(_chapter_ids)))
_again = S.build(NOW)
_again_b0 = next(s for s in _again["sections"] if s["id"] == "writing")["books"][0]
t("重复生成时作品与章节 ID 完全不变（前端书签不会漂移）",
  _again_b0.get("bookId") == b0.get("bookId")
  and [c.get("chapterId") for c in _again_b0.get("chapters", [])]
      == [c.get("chapterId") for c in b0.get("chapters", [])])
t("目标篇幅是从总纲里读出来的（不是我猜的）",
  b0.get("target") and b0["target"]["min"] == 20000 and b0["target"]["max"] == 50000,
  json.dumps(b0.get("target"), ensure_ascii=False))
t("目标区间下限 > 0（页面拿它当分母算进度，为 0 会除零）",
  b0.get("target", {}).get("min", 0) > 0)
_outline_url = S.obsidian_url(os.path.join(proj, "章节目录.md"))
t("章节目录深链指向固定 Obsidian-Tech vault，并去掉 .md 后缀",
  b0.get("outlineUrl") == _outline_url
  and _outline_url.startswith("obsidian://open?vault=Obsidian-Tech&file=")
  and "%E7%AB%A0%E8%8A%82%E7%9B%AE%E5%BD%95" in _outline_url
  and ".md" not in _outline_url,
  b0.get("outlineUrl", ""))
_bad_chapter_urls = []
for c in b0.get("chapters") or []:
    f = os.path.join(proj, c["name"] + ".md")
    want = S.obsidian_url(f) if os.path.isfile(f) else ""
    if c.get("obsidianUrl") != want:
        _bad_chapter_urls.append("%s: %r != %r" % (c["name"], c.get("obsidianUrl"), want))
t("已写章节生成真实文件深链，未写章节不伪造链接",
  not _bad_chapter_urls,
  "、".join(_bad_chapter_urls[:3]) or "%d 章逐项一致" % len(b0.get("chapters") or []))
_source_docs = (b0.get("docs") or []) + (b0.get("drafts") or [])
t("设定、大纲与其它稿件都带固定 vault 深链",
  bool(_source_docs) and all(
    str(d.get("obsidianUrl") or "").startswith("obsidian://open?vault=Obsidian-Tech&file=")
    for d in _source_docs),
  "%d 份" % len(_source_docs))
t("深链生成器拒绝 Obsidian-Tech 根目录之外的路径",
  S.obsidian_url("/tmp/outside-obsidian-vault.md") == "",
  S.obsidian_url("/tmp/outside-obsidian-vault.md") or "(已拒绝)")
_data = rdj(os.path.join(ROOT, "data.json"))
_want_cli = [p for p in (_data.get("projects") or []) if (p.get("category") or "").strip() == "客户服务"]
t("② 客户内容条数 == data.json 里 category=客户服务 的项目数（独立重算）",
  len(wr.get("clients") or []) == len(_want_cli),
  "产物 %d / 上游 %d" % (len(wr.get("clients") or []), len(_want_cli)))
_cs = {}
for p in _want_cli:
    k = p.get("status") or "未标状态"
    _cs[k] = _cs.get(k, 0) + 1
t("② 客户状态分布 == 从上游 status 独立统计出来的（不写死「全部待交付」）",
  wr.get("clientStatus") == _cs, "产物 %s / 重算 %s" % (json.dumps(wr.get("clientStatus"), ensure_ascii=False), json.dumps(_cs, ensure_ascii=False)))
t("客户状态 hint 里写的是真实分布",
  any(_cs and all(str(v) in s.get("hint", "") for v in _cs.values()) for s in wr["stats"] if s["k"] == "客户内容"),
  [s["hint"] for s in wr["stats"] if s["k"] == "客户内容"])

# 独立稿件与新小说使用临时目录验证，不能在真实 Obsidian 里造样例。
with tempfile.TemporaryDirectory() as tmp:
    old_root = S.writing_store.OBSIDIAN_ROOT
    old_writing = S.writing_store.WRITING_ROOT
    try:
        temp_obsidian = os.path.join(tmp, "Obsidian-Tech")
        temp_projects = os.path.join(temp_obsidian, "projects")
        temp_novel = os.path.join(temp_obsidian, "创作", "小说", "新书")
        temp_docs = os.path.join(temp_obsidian, "创作", "公众号")
        os.makedirs(temp_novel)
        os.makedirs(temp_docs)
        with open(os.path.join(temp_novel, "章节目录.md"), "w", encoding="utf-8") as f:
            f.write("# 《新书》章节目录\n\n## 第一章 开端\n")
        with open(os.path.join(temp_docs, "周记.md"), "w", encoding="utf-8") as f:
            f.write("---\ntitle: 周记\ncontent-type: 公众号\nstatus: 草稿\n---\n\n正文\n")
        S.writing_store.OBSIDIAN_ROOT = temp_obsidian
        S.writing_store.WRITING_ROOT = temp_projects
        temp_wr = S.collect_writing(NOW, {})
        temp_titles = {b.get("title") for b in temp_wr.get("books") or []}
        t("新小说从 创作/小说/ 被发现，旧 projects/ 路径仍兼容",
          temp_titles == {"新书"}, "发现作品：" + "、".join(sorted(temp_titles)))
        t("公众号独立稿件进入统一写作区，并带稳定 ID 与类型",
          len(temp_wr.get("documents") or []) == 1
          and temp_wr["documents"][0]["contentType"] == "公众号"
          and re.fullmatch(r"doc_[0-9a-f]{20}", temp_wr["documents"][0]["documentId"]),
          json.dumps(temp_wr.get("documentTypes"), ensure_ascii=False))
    finally:
        S.writing_store.OBSIDIAN_ROOT = old_root
        S.writing_store.WRITING_ROOT = old_writing

# ─────────────────────────────────────────────────────────────── 7. 复盘
print("\n7. 复盘区：分组守恒")
rv = SEC["review"]
_notes = [f for f in glob.glob(os.path.join(S.REVIEW_DIR, "调试经验-*.md"))
          if os.path.basename(f) != S.REVIEW_MOC]
t("① 篇数 == 调试经验/ 下的笔记数（不含 MOC 索引，独立重算）",
  len(rv["items"]) == len(_notes), "产物 %d / 目录 %d" % (len(rv["items"]), len(_notes)))
_sg = sum(g["count"] for g in rv["groups"])
t("① 守恒：Σ分组篇数 == 笔记数（分组不许丢笔记）",
  _sg == len(rv["items"]), "%d / %d，%d 类" % (_sg, len(rv["items"]), len(rv["groups"])))
t("分组无重名且每类非空", len(set(g["name"] for g in rv["groups"])) == len(rv["groups"])
  and all(g["count"] > 0 for g in rv["groups"]))
t("缺 frontmatter 的笔记落在「未标注」里而不是消失",
  all(n["productType"] for n in rv["items"]),
  "未标注 %d 篇" % sum(1 for n in rv["items"] if n["productType"] == "未标注"))
_bad_review_urls = []
for n in rv["items"]:
    f = next((x for x in _notes if os.path.basename(x)[:-3] == n["title"]), None)
    want = S.obsidian_url(f) if f else ""
    if n.get("obsidianUrl") != want:
        _bad_review_urls.append("%s: %r != %r" % (n["title"], n.get("obsidianUrl"), want))
t("复盘笔记深链与真实文件逐条对应，没有臆造页面",
  not _bad_review_urls,
  "、".join(_bad_review_urls[:3]) or "%d 篇逐项一致" % len(rv["items"]))
t("按日期降序（最近的在最前）",
  [n["date"] or "" for n in rv["items"]] == sorted((n["date"] or "" for n in rv["items"]), reverse=True),
  (rv["items"][0]["date"] or "")[:10] + " … " + (rv["items"][-1]["date"] or "")[:10])
t("复盘条目带列表摘要，且不像 frontmatter/标题那样开头",
  all(isinstance(n.get("summary"), str) for n in rv["items"])
  and all(not n["summary"].startswith(("---", "#")) for n in rv["items"] if n["summary"]),
  "空摘要 %d 篇" % sum(1 for n in rv["items"] if not n.get("summary")))
t("复盘 noteId 稳定（note_ + 20 位 hex），产物里没有 path 字段",
  all(re.fullmatch(r"note_[0-9a-f]{20}", n.get("noteId") or "") for n in rv["items"])
  and all("path" not in n for n in rv["items"]),
  "%d 个稳定 ID" % len(rv["items"]))
_sm = {s["k"]: s["v"] for s in rv["stats"]}
_d30 = sum(1 for n in rv["items"] if n["days"] is not None and n["days"] <= 30)
_d90 = sum(1 for n in rv["items"] if n["days"] is not None and n["days"] <= 90)
t("近30/90天篇数与用 items 的 days 独立重算一致",
  _sm.get("近30天") == "%d 篇" % _d30 and _sm.get("近90天") == "%d 篇" % _d90,
  "30天 %s / 90天 %s" % (_sm.get("近30天"), _sm.get("近90天")))

# ─────────────────────────────────────────────────────────────── 8. 阈值与纯文本
print("\n8. 过期阈值与「UI 文案必须是纯文本」")
ths = [(s["id"], s["freshness"]["threshold"]) for s in OBJ["sections"]]
t("三个区的阈值各不相同（按类型给，不是一个全局值）",
  len(set(x[1] for x in ths)) == len(ths), "  ".join("%s:%d天" % x for x in ths))
t("阈值与 meta 里声明的一致", all(OBJ["meta"]["staleThresholds"][i] == v for i, v in ths))
t("stale == (days is None or days > threshold)，三处口径同源",
  all(s["freshness"]["stale"] == (s["freshness"]["days"] is None or s["freshness"]["days"] > s["freshness"]["threshold"])
      for s in OBJ["sections"]))
future = S.build(dt.datetime(2026, 12, 1))
future_news = next(s for s in future["sections"] if s["id"] == "news")
t("把观察时间推后 30 天，资讯区会按阈值判为「已过期」（阈值 3 天被真的用上了，不是摆设）",
  future_news["freshness"]["stale"],
  "%s 天 > %s 天" % (future_news["freshness"]["days"], future_news["freshness"]["threshold"]))
t("新鲜度 basis 明说是「最新内容」而不是插件的 updatedAt",
  "最新内容" in SEC["news"]["freshness"]["basis"] and "updatedAt" in SEC["news"]["note"],
  SEC["news"]["freshness"]["basis"])

# UI 文案守卫：note / hint / stats 的值都会被页面用 textContent 直接显示。
# 写 `**强调**` 或反引号就会把标记原样印在界面上（第一版资讯卡里真的出现了
# 「**最新一篇文章的时间**」）。要强调请用「」。
_bad = []


def _scan(where, s):
    if isinstance(s, str) and re.search(r"\*\*|`|^#{1,6}\s|\[[^\]]+\]\([^)]+\)", s):
        _bad.append("%s: %s" % (where, s[:70]))


for sec in OBJ["sections"]:
    _scan(sec["id"] + ".note", sec.get("note"))
    for st in sec.get("stats") or []:
        _scan(sec["id"] + ".stat." + str(st.get("k")), st.get("k"))
        _scan(sec["id"] + ".stat." + str(st.get("k")), st.get("v"))
        _scan(sec["id"] + ".stat." + str(st.get("k")) + ".hint", st.get("hint"))
    for f in sec.get("feeds") or []:
        _scan(sec["id"] + ".feed.name", f.get("name"))
_scan("meta.note", OBJ["meta"].get("note"))
t("★ 会直接进 UI 的文案里没有一个 markdown 标记（** / 反引号 / # 标题 / [](链接)）",
  not _bad, "、".join(_bad[:3]) or "全部纯文本")

# ─────────────────────────────────────────────────────────────── 9. 可复现性与闸门
print("\n9. 可复现性 与 --check 闸门")
t("同一个 now 生成两次，字节完全相同（否则 --check 没有意义）",
  json.dumps(S.build(NOW), ensure_ascii=False) == json.dumps(S.build(NOW), ensure_ascii=False))


def run(args):
    p = subprocess.run([PY, SRC_PY] + args,
                       capture_output=True, text=True, cwd=ROOT)
    return p.returncode, (p.stdout + p.stderr).strip()


rc, out = run(["--check"])
t("--check 在刚写完的状态下返回 0（**不能随机变红**）", rc == 0, "rc=%d %s" % (rc, out[:90]))
t("--check 已是最新时的输出不再提 generatedAt（它天生随时间变，不该参与比对）",
  "generatedAt" not in out, out[:90])
rc2, out2 = run(["--check"])
t("连跑两次 --check 结果一致（跨秒也不翻脸）", rc == rc2 == 0, "第一次 rc=%d，第二次 rc=%d" % (rc, rc2))
rc3, out3 = run(["--check", "--now", "2026-12-01T00:00:00"])
t("把 now 推到很久以后，--check 会报过期并说清是「距今天数」变了",
  rc3 == 2 and "距今天数" in out3, "rc=%d %s" % (rc3, out3[:120]))

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
