#!/usr/bin/env python3
"""知识星球 → 待办任务 的离线回归：清洗、时间窗、去重、限量。

不联网、不碰 Notion：所有输入都是固定数据。
"""
import datetime as dt
import importlib.util
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location(
    "task_suggest", os.path.join(ROOT, "scripts", "sync", "zsxq-task-suggest.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["task_suggest"] = m
spec.loader.exec_module(m)

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ✓ %s" % name)
    else:
        failed += 1
        print("  ✗ %s%s" % (name, ("  → " + str(detail)) if detail else ""))


RAW = ('<e type="hashtag" hid="888" title="%23%E9%87%8C%E7%A8%8B%E7%A2%91%23" /> '
       '纯 AI 完成的的首个全栈项目踩坑记录。第二个坑是签名。')
clean = m.clean_text(RAW)
check("剥掉 <e> 标签、URL 解码，并去掉话题标签",
      "%23" not in clean and "<e" not in clean and "纯 AI 完成" in clean, clean)
check("不留 HTML 残留", "<e" not in clean and "/>" not in clean, clean)
check("首句取有信息量的那句",
      m.first_sentence(RAW) == "纯 AI 完成的的首个全栈项目踩坑记录", m.first_sentence(RAW))
check("任务名带阅读数，方便判断",
      m.build_task_name(20, "某条内容") == "二次创作：某条内容（星球 20 阅读）",
      m.build_task_name(20, "某条内容"))

NOW = dt.datetime(2026, 11, 22, 20, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
CFG = {"topN": 3, "windowDays": 30, "minReads": 5}


def post(topic, reads, days_ago):
    when = (NOW - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")
    return {"platform": "知识星球", "title": "某标题%s" % topic, "url": "https://wx.zsxq.com/topic/%s" % topic,
            "publishedAt": when, "metrics": {"reads": reads, "likes": 1, "comments": 0}}


posts = [post("1", 30, 3), post("2", 12, 10), post("3", 8, 1), post("4", 100, 90), post("5", 2, 2)]
state = {"2": {"taskId": "t2"}}
picks = m.select_candidates(posts, state, CFG, NOW)
check("超过时间窗的旧帖不选", all(p["topicId"] != "4" for p in picks), [p["topicId"] for p in picks])
check("已建过任务的 topic 跳过", all(p["topicId"] != "2" for p in picks), [p["topicId"] for p in picks])
check("阅读数低于下限的不选", all(p["topicId"] != "5" for p in picks), [p["topicId"] for p in picks])
check("按阅读数从高到低", [p["topicId"] for p in picks] == ["1", "3"], [p["topicId"] for p in picks])
# 窗口内合格的是 1(30) / 2(12) / 3(8)：4 太旧、5 低于阅读下限
check("topN 只限制上限，不够就少取", len(m.select_candidates(posts, {}, {**CFG, "topN": 5}, NOW)) == 3,
      len(m.select_candidates(posts, {}, {**CFG, "topN": 5}, NOW)))

old_only = [post("9", 50, 200)]
check("窗口内没有新内容时返回空（不拿旧帖反复生成）",
      m.select_candidates(old_only, {}, CFG, NOW) == [], m.select_candidates(old_only, {}, CFG, NOW))

no_reads = [{"platform": "知识星球", "url": "https://wx.zsxq.com/topic/7",
             "publishedAt": NOW.isoformat(), "metrics": {"reads": None}}]
check("缺阅读数的不选（None 不是 0 阅读）",
      m.select_candidates(no_reads, {}, CFG, NOW) == [])
check("非知识星球的帖子不选",
      m.select_candidates([{**post("8", 99, 1), "platform": "小红书"}], {}, CFG, NOW) == [])

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(1 if failed else 0)
