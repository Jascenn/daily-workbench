#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workbench.html 模块化拼装同构性与 CSS 重复选择器门禁测试。"""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILDER = os.path.join(ROOT, "scripts", "build-workbench.py")

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ✓ %s" % name)
    else:
        failed += 1
        print("  ✗ %s%s" % (name, ("  → " + str(detail)) if detail else ""))


print("被测：scripts/build-workbench.py 模块拼装同构性与 CSS 门禁")

r_check = subprocess.run([sys.executable, BUILDER, "--check"], capture_output=True, text=True)
check("workbench.html 与 web/ 源码逐字节同构", r_check.returncode == 0, r_check.stderr.strip() or r_check.stdout.strip())

r_lint = subprocess.run([sys.executable, BUILDER, "--lint-css"], capture_output=True, text=True)
check("CSS 重复选择器未超过基线门禁", r_lint.returncode == 0, r_lint.stderr.strip() or r_lint.stdout.strip())

r_strict = subprocess.run([sys.executable, BUILDER, "--lint-css", "--max-dup", "-1"], capture_output=True, text=True)
check("CSS 门禁在超标时能准确阻断（返回非 0）", r_strict.returncode == 3, f"rc={r_strict.returncode}")

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(1 if failed else 0)
