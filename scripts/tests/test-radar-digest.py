#!/usr/bin/env python3
"""每日选题汇总的离线回归：挑选口径、空结果诚实、百分比展示。"""
import importlib.util
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location(
    "radar_digest", os.path.join(ROOT, "scripts", "sync", "radar-digest.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules["radar_digest"] = mod
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


def item(title, rel=None, ang=None, why="", angles=None, needs=False, analyzed=True):
    return {"title": title, "url": "https://example.com/" + title, "sourceName": "36氪",
            "relevance": rel, "angle": ang, "whyRelated": why, "angles": angles or [],
            "nextAction": "明天上午写一版", "needsHuman": needs, "analyzed": analyzed}


radar = {
    "generatedAt": "2026-11-21T23:00:00+08:00",
    "coverage": {"sourcesTotal": 5, "sourcesOk": 4, "itemsJudged": 12, "keptForWriting": 2},
    "items": [
        item("高分但没写角度", 8.0, 8.0, why="", angles=[]),            # 不可写，排除
        item("中高分可写", 7.0, 5.0, why="能用到企业咨询", angles=["成本测算"]),
        item("低分可写", 5.2, 6.0, why="可做社群分享", angles=["避坑清单"]),
        item("需人工判断", 9.0, 9.0, why="", angles=[], needs=True),     # 排除
        item("未解析", 9.5, 9.5, why="", angles=[], analyzed=False),     # 排除
    ],
}

picked = mod.pick(radar)
titles = [p["title"] for p in picked]
check("只挑有可写角度的条目", titles == ["中高分可写", "低分可写"], titles)
check("按相关度加权排序，不是按标题顺序", picked[0]["title"] == "中高分可写", titles)
check("needsHuman 与未解析的条目不进汇总",
      "需人工判断" not in titles and "未解析" not in titles)
check("Top N 可配，默认最多 3 条", len(mod.pick(radar, 1)) == 1)

text = mod.render(radar, picked)
check("汇总包含标题、来源、为什么、能写",
      all(k in text for k in ("中高分可写", "36氪", "为什么：能用到企业咨询", "能写：成本测算")), text[:120])
check("相关度折成整数百分比", "相关 78%" in text, text.splitlines()[2] if len(text.splitlines()) > 2 else "")
check("百分比对越界值做夹取", mod._pct(12) == "100%" and mod._pct(-3) == "0%")
check("缺分数时不显示百分比而不是 NaN", mod._pct(None) == "" and mod._pct("x") == "")

import datetime as _dt
same_day_a = mod.render(radar, picked, _dt.datetime(2026, 11, 21, 9, 0))
same_day_b = mod.render(radar, picked, _dt.datetime(2026, 11, 21, 21, 30))
check("同一天不同时刻渲染的正文完全一致（否则内容去重永远失效）",
      same_day_a == same_day_b, (same_day_a.splitlines()[0], same_day_b.splitlines()[0]))

empty_radar = {"coverage": {"sourcesTotal": 5, "sourcesOk": 4, "itemsJudged": 12}, "items": []}
empty_text = mod.render(empty_radar, mod.pick(empty_radar))
check("没有可写条目时如实说没有，并带上源与判断数",
      "没有达到可写线" in empty_text and "4/5 源可用" in empty_text, empty_text)

# ── 群用排版：内容一条不少，只调空行与缩进 ────────────────────────
group_text = mod.render_group(radar, picked, __import__("datetime").datetime(2026, 11, 21, 9, 0))
check("群版保留「为什么 / 能写 / 下一步 / 链接」四项",
      all(k in group_text for k in ("为什么：", "能写：", "下一步：", "🔗 http")), group_text[:120])
check("群版保留原文链接（用户要的就是详细信息）", "https://example.com/" in group_text)
check("群版不用 markdown 记号（微信不渲染 ** 和 ##）",
      "**" not in group_text and "##" not in group_text)
check("条目之间留空行，手机上更好扫读", "\n\n2️⃣" in group_text, group_text[:40])
check("emoji 只做路标：为什么/能写/下一步/链接各有记号",
      all(k in group_text for k in ("💡 为什么：", "✍️ 能写：", "✅ 下一步：", "🔗 https://")),
      group_text[:100])
check("条目用 1️⃣2️⃣ 编号",
      "1️⃣ " in group_text and "2️⃣ " in group_text, group_text[:20])
check("emoji 没有替代文字（去掉 emoji 后字段名仍在）",
      all(k in group_text.replace("💡", "").replace("✍️", "").replace("✅", "")
          for k in ("为什么：", "能写：", "下一步：")))

check("标题原样保留，不做省略号截断",
      "中高分可写" in group_text and "…" not in group_text)

long_title = [{"title": "这是一个特别特别长的标题" * 5, "url": "https://x", "sourceName": "36氪",
               "relevance": 7.0, "angle": 5.0, "whyRelated": "有", "angles": ["角度"], "analyzed": True}]
check("超长标题也完整保留", mod.render_group(radar, long_title).count("特别") == 10,
      mod.render_group(radar, long_title)[:40])

empty_group = mod.render_group(empty_radar, [], __import__("datetime").datetime(2026, 11, 21))
check("群里没有可写条目时如实说，不装忙",
      "没有达到可写线" in empty_group and "4/5" in empty_group, empty_group)


# ── 微信推送：幂等键、内容去重、未配置时安静跳过 ──────────────────
import json
import tempfile
from pathlib import Path

check("幂等键随内容变化（同键换内容会被 MCP 拒绝）",
      mod.request_id_for("g@chatroom", "内容A") != mod.request_id_for("g@chatroom", "内容B"))
check("同会话同内容得到同一个幂等键（重跑可去重）",
      mod.request_id_for("g@chatroom", "内容A") == mod.request_id_for("g@chatroom", "内容A"))
check("幂等键带日期，跨天不会互相顶掉",
      mod.request_id_for("g@chatroom", "内容A", __import__("datetime").date(2026, 11, 21))
      != mod.request_id_for("g@chatroom", "内容A", __import__("datetime").date(2026, 11, 22)))

with tempfile.TemporaryDirectory() as tmp:
    tmpdir = Path(tmp)
    mod.RADAR = tmpdir / "radar.json"
    mod.STATE = tmpdir / "state.json"
    mod.CONFIG = tmpdir / "config.json"
    mod.CONFIG.write_text(json.dumps({"wechat": {"conversationId": "g@chatroom", "label": "测试群"}}),
                          encoding="utf-8")
    mod.RADAR.write_text(json.dumps(radar, ensure_ascii=False), encoding="utf-8")

    calls = {"reminder": 0, "notify": 0, "wechat": 0}
    mod.push_reminder = lambda *a, **k: (calls.__setitem__("reminder", calls["reminder"] + 1), (True, "created"))[1]
    mod.push_notification = lambda *a, **k: (calls.__setitem__("notify", calls["notify"] + 1), (True, ""))[1]
    mod.push_wechat = lambda *a, **k: (calls.__setitem__("wechat", calls["wechat"] + 1), (True, "submitted"))[1]

    import contextlib, io
    def run_main(argv=None):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            return mod.main(argv or [])

    rc1 = run_main()
    check("首次运行会推群", rc1 == 0 and calls["wechat"] == 1, calls)
    rc2 = run_main()
    check("内容没变时第二天也不会重复刷群", rc2 == 0 and calls["wechat"] == 1, calls)
    check("提醒事项仍然只更新当天的同一条", calls["reminder"] == 2, calls)

    mod.RADAR.write_text(json.dumps({**radar, "items": radar["items"] + [
        {"title": "新增条目", "url": "https://example.com/new", "sourceName": "36氪",
         "relevance": 6.5, "angle": 6.5, "whyRelated": "新素材", "angles": ["新角度"],
         "nextAction": "", "needsHuman": False, "analyzed": True}]}, ensure_ascii=False),
        encoding="utf-8")
    run_main()
    check("内容变化后会重新推送", calls["wechat"] == 2, calls)

    # 没有可写条目：不打扰群
    mod.RADAR.write_text(json.dumps({"coverage": {"sourcesTotal": 5, "sourcesOk": 4, "itemsJudged": 0},
                                     "items": []}, ensure_ascii=False), encoding="utf-8")
    before = calls["wechat"]
    run_main()
    check("空结果不往群里发", calls["wechat"] == before, calls)

    # 提醒失败不能连带掐掉群推送（2026-11-22 早上真丢过一次）
    calls["wechat"] = 0
    mod.push_reminder = lambda *a, **k: (False, "TimeoutExpired: osascript")
    mod.RADAR.write_text(json.dumps(radar, ensure_ascii=False), encoding="utf-8")
    mod.CONFIG.write_text(json.dumps({"wechat": {"conversationId": "g@chatroom"}}), encoding="utf-8")
    mod.STATE.unlink(missing_ok=True)
    rc_remind = run_main()
    check("提醒事项失败时仍然推群，退出码 3 只表示「提醒没写成」",
          calls["wechat"] == 1 and rc_remind == 3, (calls["wechat"], rc_remind))

    # 未配置会话：安静跳过，不报错（先把提醒恢复成成功，隔离变量）
    mod.push_reminder = lambda *a, **k: (calls.__setitem__("reminder", calls["reminder"] + 1), (True, "created"))[1]
    mod.CONFIG.write_text(json.dumps({}), encoding="utf-8")
    mod.RADAR.write_text(json.dumps(radar, ensure_ascii=False), encoding="utf-8")
    before = calls["wechat"]
    rc3 = run_main()
    check("未配置群时跳过且退出码为 0", rc3 == 0 and calls["wechat"] == before, (rc3, calls))

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(1 if failed else 0)
