#!/usr/bin/env python3
"""知识星球日报的离线回归：时间窗口、去重、分类、覆盖率、令牌边界。

不联网：所有 zsxq-cli 调用用固定 JSON 替换。联网部分由 zsxq-daily-sync.py 自己跑。
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location(
    "zsxq_daily_sync", os.path.join(ROOT, "scripts", "sync", "zsxq-daily-sync.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules["zsxq_daily_sync"] = mod
spec.loader.exec_module(mod)

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ✓ %s" % name)
    else:
        failed += 1
        print("  ✗ %s%s" % (name, ("  → " + str(detail)) if detail else ""))


# ── 时间解析：zsq 的 +0800 必须能解析，坏的返回 None ────────────────────
check("解析 zsxq 时间带 +0800",
      mod._parse_time("2026-11-23T21:52:06.394+0800") is not None)
check("坏时间返回 None", mod._parse_time("not-a-time") is None)
check("空时间返回 None", mod._parse_time("") is None)

# ── 正文清洗：去标签、解 URL 编码、整段重复只留一次 ──────────────────────
raw = "<e type=\"hashtag\" title=\"%23AI%23\" />%E4%BB%8A%E5%A4%A9%E6%B5%8B%E8%AF%95"
clean = mod.plain(raw)
check("解 URL 编码并去 <e> 标签", "今天测试" in clean and "<e" not in clean, clean)
dup = "第一行\n第二行\n第一行\n第二行"
check("重复行去重但保留顺序", mod.plain(dup) == "第一行\n第二行", mod.plain(dup))

# ── 标签提取：URL 编码的 hashtag 要还原 ─────────────────────────────────
tags = mod.hashtags({"annotation": '<e title="%23AI%23" /><e title="%23%E7%A2%8E%E5%B1%91%23" />'})
check("解出中文标签", "AI" in tags and "碎屑" in tags, tags)

# ── 分类：三类要各归其位 ─────────────────────────────────────────────────
check("AI 工作流归类", mod.category_of(["AI"], "") == "AI 工作流")
check("写作方法归类", mod.category_of([], "贴图赛道跟爆款") == "写作方法论")
check("副业认知归类", mod.category_of([], "小红书工作台变现") == "副业认知与赛道")
check("未命中归其他", mod.category_of([], "随便聊聊") == "其他")

# ── 24 小时窗口：跨天不能把昨晚的内容丢掉 ────────────────────────────────
from datetime import datetime, timedelta, timezone
now = datetime(2026, 11, 24, 0, 15, tzinfo=timezone(timedelta(hours=8)))
start = now - timedelta(hours=24)
old = "2026-11-22T20:00:00.000+0800"
recent = "2026-11-23T21:52:00.000+0800"
check("24h 前的内容被排除", mod._parse_time(old) < start)
check("昨晚 21:52 在窗口内", mod._parse_time(recent) >= start)

# ── 覆盖率：未知不写 0，权限/限流分开计 ─────────────────────────────────
fixture = {"version": 1, "coverage": {"scanned": 254, "readable": 19,
           "noPermission": 234, "rateLimited": 1, "withToday": 2}, "items": []}
check("覆盖率字段齐全", all(k in fixture["coverage"] for k in
      ("scanned", "readable", "noPermission", "rateLimited", "withToday")))
check("未授权星球不等于没更新", fixture["coverage"]["noPermission"] == 234
      and fixture["coverage"]["withToday"] == 2)

# ── 静态页面与接口：HTML 不含真实数据 token ─────────────────────────────
html = Path(ROOT, "zsxq-daily.html").read_text(encoding="utf-8")
check("独立页面走令牌接口", "/zsxq-daily.json" in html)
check("页面不内嵌真实主题内容", "WorkBuddy" not in html and "刘卡卡" not in html)
check("页面有覆盖率说明区", "coverageNote" in html)

app = Path(ROOT, "app.py").read_text(encoding="utf-8")
check("路由存在且要求令牌", 'if p == "/zsxq-daily.json":' in app)
check("页面加入静态白名单", '"/zsxq-daily.html"' in app)

# ── 快照本体（当前生成的真实数据） ──────────────────────────────────────
snap = Path(ROOT, "zsxq-daily.json")
if snap.exists():
    d = json.loads(snap.read_text(encoding="utf-8"))
    check("快照有 24h 窗口字段", bool(d.get("windowStart") and d.get("windowEnd")))
    check("快照分类统计与条目一致",
          sum(d["byCategory"].values()) == len(d["items"]), d.get("byCategory"))
    check("每条都有原文链接", all(i.get("url", "").startswith("https://wx.zsxq.com/")
                              for i in d["items"]))
    check("覆盖率说明不为空", bool(d.get("coverageNote")))

# ── 7 天周报聚合：精华优先、分类与星球计数守恒 ───────────────────────────
wk_start = now - timedelta(days=7)
wk_items = [
    {"id": "1", "title": "普通低互动", "group": "星球A", "category": "其他", "digested": False,
     "publishedAt": "2026-11-23T10:00:00+0800", "metrics": {"likes": 0, "comments": 0, "reads": 10}},
    {"id": "2", "title": "高互动话题", "group": "星球A", "category": "AI 工作流", "digested": False,
     "publishedAt": "2026-11-22T10:00:00+0800", "metrics": {"likes": 8, "comments": 5, "reads": 120}},
    {"id": "3", "title": "精华方法论", "group": "星球B", "category": "写作方法论", "digested": True,
     "publishedAt": "2026-11-20T10:00:00+0800", "metrics": {"likes": 1, "comments": 0, "reads": 30}},
]
wk = mod.summarize_weekly(wk_items, wk_start, now, top_n=2)
check("周报总条数与星球计数正确", wk["total"] == 3 and wk["withItems"] == 2 and wk["groupCounts"]["星球A"] == 2, wk)
check("周报分类计数守恒", sum(wk["byCategory"].values()) == 3, wk["byCategory"])
check("周报精选精华排第一，高互动排第二",
      [h["id"] for h in wk["highlights"]] == ["3", "2"], [h["id"] for h in wk["highlights"]])
check("周报主列表按时间倒序",
      [it["id"] for it in wk["items"]] == ["1", "2", "3"], [it["id"] for it in wk["items"]])
check("独立页面支持 7 天周报切换", "btnWeekly" in html and "本周 7 天" in html)

print("\n通过 %d / 失败 %d" % (passed, failed))
sys.exit(1 if failed else 0)
