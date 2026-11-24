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

# 步骤数量与顺序不能在这里写死：脚本一加步骤，写死的断言就变成"过期绿灯"。
# 直接从 sync-now.sh 反解步骤名（emit_step / run_optional 两类），再与页面拿到的一一对照。
import re as _re
_script = open(os.path.join(ROOT, "scripts", "sync-now.sh"), encoding="utf-8").read()
_expected = []
for _m in _re.finditer(r'(?:emit_step|run_optional)\s+"([^"]+)"', _script):
    _name = _m.group(1)
    # run_optional 函数体内也有 emit_step "$name"，那是变量不是真实步骤名
    if not _name.startswith("$") and _name not in _expected:
        _expected.append(_name)
check("步骤名与顺序和 sync-now.sh 完全一致",
      [s["name"] for s in steps] == _expected,
      "页面=" + ",".join(s["name"] for s in steps) + " | 脚本=" + ",".join(_expected))
check("有计时的步骤都合并了 elapsed，未计时的步骤不伪造耗时",
      all("elapsed" in s for s in steps if s["name"] in timings)
      and all("elapsed" not in s for s in steps if s["name"] not in timings),
      json.dumps({s["name"]: s.get("elapsed") for s in steps}, ensure_ascii=False))
check("合并不改变步骤成功状态与结果文案",
      all(s["ok"] for s in steps) and "收益 1 条 / 项目 1 条" in steps[1]["detail"],
      json.dumps(steps, ensure_ascii=False))

failed_sample = "__WB_STEP__\t前置检查\t0.3\n__WB_STEP__\tNotion 拉取\t92.7\n✗ 同步失败"
failed_steps = app._build_sync_steps(4, failed_sample, {"earnings": [], "projects": []})
check("失败时只列到挂掉的那一步，且仍带耗时",
      len(failed_steps) == 2 and failed_steps[-1]["ok"] is False
      and failed_steps[-1]["elapsed"] == 92.7,
      json.dumps(failed_steps, ensure_ascii=False))

# 可选步骤（自媒体/微信/网站）不再中断链路，失败只能靠显式标记上报。
# 如果标记被忽略，同步日志会把失败渲染成 ✔ —— 那等于骗自己。
optional_fail = "\n".join([
    "__WB_STEP__\t前置检查\t0.4",
    "__WB_STEP__\t自媒体快照\t1.0",
    "__WB_STEP_FAIL__\t自媒体快照",
    "__WB_STEP__\t微信快照\t2.0",
    "__WB_STEP_FAIL__\t微信快照",
    "__WB_STEP__\t网站监控\t0.5",
])
opt_steps = app._build_sync_steps(0, optional_fail, {"earnings": [], "projects": []})
by_name = {s["name"]: s for s in opt_steps}
check("可选步骤失败会被标成 ✗，不会被渲染成 ✔",
      by_name["自媒体快照"]["ok"] is False and by_name["微信快照"]["ok"] is False,
      json.dumps(opt_steps, ensure_ascii=False))
check("失败的可选步骤保留上一份快照的说法",
      by_name["自媒体快照"]["detail"] == "失败，保留上一份快照",
      by_name["自媒体快照"]["detail"])
check("没有标记的可选步骤仍是成功", by_name["网站监控"]["ok"] is True,
      by_name["网站监控"]["detail"])
check("给用户看的输出里不出现失败标记行",
      "__WB_STEP_FAIL__" not in app._strip_sync_step_timings(optional_fail))

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(1 if failed else 0)
