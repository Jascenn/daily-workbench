#!/usr/bin/env python3
"""分阶段同步耗时的解析与合并测试（不触发真实同步）。"""
import importlib.util
import json
import os
import tempfile


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location("daily_workbench_app", os.path.join(ROOT, "app.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print("  ✓ %s" % name)
    else:
        failed += 1
        print("  ✗ %s%s" % (name, ("  → " + detail) if detail else ""))


sample = "\n".join([
    "===== 开始同步 =====",
    "__WB_STEP__\t前置检查\t0.6",
    "__WB_STEP__\tNotion 拉取\t148.2",
    "__WB_STEP__\t资讯快照\t4.1",
    "__WB_STEP__\t自媒体快照\t2.4",
    "__WB_STEP__\t网站监控\t1.8",
    "__WB_STEP__\t离线兜底页\t1.2",
    "===== 同步完成 =====",
])

print("被测：app.py 分阶段耗时解析")
timings = app._parse_sync_step_timings(sample)
check("六个阶段都被解析", len(timings) == 6, json.dumps(timings, ensure_ascii=False))
check("步骤名与小数秒正确",
      timings.get("Notion 拉取") == 148.2 and timings.get("前置检查") == 0.6,
      json.dumps(timings, ensure_ascii=False))

visible = app._strip_sync_step_timings(sample)
check("给用户看的输出不包含内部机器标记",
      "__WB_STEP__" not in visible and "===== 同步完成 =====" in visible, visible)

old_timings = app._parse_sync_step_timings("旧脚本只有普通日志")
check("旧日志没有标记时返回空字典，不伪造耗时", old_timings == {}, repr(old_timings))

original_rss = app._rss_read
original_web_presence = app.WEB_PRESENCE
try:
    app._rss_read = lambda: {"subscriptions": [{"name": "源 A"}, {"name": "源 B"}]}
    with tempfile.TemporaryDirectory() as tmp:
        app.WEB_PRESENCE = os.path.join(tmp, "web-presence.json")
        with open(app.WEB_PRESENCE, "w", encoding="utf-8") as f:
            json.dump({"sites": [{"name": "站点 A", "state": "ok"}]}, f)
        steps = app._build_sync_steps(0, sample, {
            "earnings": [{"id": "e1"}],
            "projects": [{"id": "p1"}],
        })
finally:
    app._rss_read = original_rss
    app.WEB_PRESENCE = original_web_presence

check("解析出的耗时会合并到原有六步", len(steps) == 6 and all("elapsed" in s for s in steps),
      json.dumps(steps, ensure_ascii=False))
check("合并不改变步骤成功状态与结果文案",
      all(s["ok"] for s in steps) and "收益 1 条 / 项目 1 条" in steps[1]["detail"],
      json.dumps(steps, ensure_ascii=False))

failed_sample = "__WB_STEP__\t前置检查\t0.3\n__WB_STEP__\tNotion 拉取\t92.7\n✗ 同步失败"
failed_steps = app._build_sync_steps(4, failed_sample, {"earnings": [], "projects": []})
check("失败时只列到挂掉的那一步，且仍带耗时",
      len(failed_steps) == 2 and failed_steps[-1]["ok"] is False
      and failed_steps[-1]["elapsed"] == 92.7,
      json.dumps(failed_steps, ensure_ascii=False))

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(1 if failed else 0)
