#!/usr/bin/env python3
"""同步超时的进程清理与日志留痕测试（不访问网络）。"""
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import time


ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("daily_workbench_app", ROOT / "app.py")
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


print("被测：同步超时后的进程组清理")
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    script = root / "spawn-child.sh"
    marker = root / "child-survived"
    script.write_text(
        "sleep 2; touch %s &\nwait\n" % marker,
        encoding="utf-8",
    )
    old_script = app.SYNC_SCRIPT
    app.SYNC_SCRIPT = str(script)
    try:
        started = time.time()
        try:
            app._run_sync_process(timeout=0.2)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
        elapsed = time.time() - started
    finally:
        app.SYNC_SCRIPT = old_script

    check("超过时限会抛出 TimeoutExpired", timed_out)
    check("不会等满子进程的自然时长", elapsed < 1.5, "实际 %.2fs" % elapsed)
    time.sleep(2.2)
    check("超时后后代进程已被终止", not marker.exists())

print("\n被测：同步超时写入历史日志")
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    data_path = root / "data.json"
    log_path = root / "sync-log.json"
    data_path.write_text(json.dumps({
        "earnings": [{"id": "e1"}, {"id": "e2"}],
        "projects": [{"id": "p1"}],
    }), encoding="utf-8")

    old_data, old_log = app.DATA, app.SYNC_LOG
    app.DATA, app.SYNC_LOG = str(data_path), str(log_path)
    try:
        entry = app._sync_timeout_entry(time.time() - 1.2, "manual")
        app._sync_log_append(entry)
        saved = json.loads(log_path.read_text(encoding="utf-8"))
    finally:
        app.DATA, app.SYNC_LOG = old_data, old_log

    check("超时记录标记为失败", saved[-1]["ok"] is False)
    check("超时记录保留当时的数据数量",
          saved[-1]["earnings"] == 2 and saved[-1]["projects"] == 1)
    check("超时记录包含明确错误与耗时",
          "同步超时" in saved[-1]["error"] and saved[-1]["elapsed"] >= 1)
    check("超时记录保留触发来源", saved[-1]["trigger"] == "manual")

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(1 if failed else 0)
