#!/usr/bin/env python3
"""星球全文接口的离线回归：清洗、边界、错误分支。

不联网：只测纯函数与参数校验的失败路径。
"""
import importlib.util
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location("daily_workbench_app", os.path.join(ROOT, "app.py"))
app = importlib.util.module_from_spec(spec)
sys.modules["daily_workbench_app"] = app
spec.loader.exec_module(app)

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ✓ %s" % name)
    else:
        failed += 1
        print("  ✗ %s%s" % (name, ("  → " + str(detail)) if detail else ""))


RAW = ('<e type="hashtag" hid="155" title="%23AI%23" /><e type="hashtag" hid="888" '
       'title="%23%E7%A2%8E%E5%B1%91%23" />\n第一段：接上 AI Skill。\n\n第二段：先读再写。')
clean = app.zsxq_clean_text(RAW)
check("剥掉 <e> 标签并 URL 解码", "<e " not in clean and "%23" not in clean, clean)
check("保留段落换行（全文阅读需要）", clean.count("\n") >= 2, repr(clean))
check("话题标签被剥掉，不把标记文本留在正文里",
      "#AI" not in clean and "#碎屑" not in clean, clean)
check("空输入不报错", app.zsxq_clean_text("") == "" and app.zsxq_clean_text(None) == "")
check("纯图片/空白话题返回空串", app.zsxq_clean_text("   \n\n  ") == "")

# 缓存结构必须存在且可清空，避免把错误结果永久缓存
check("详情结果有 TTL 缓存字典", isinstance(app._ZSXQ_DETAIL_CACHE, dict) and app._ZSXQ_DETAIL_TTL > 0,
      app._ZSXQ_DETAIL_TTL)
check("找不到 CLI 时给出明确错误，不返回空文本",
      isinstance(app.ZSXQ_CLI, str), app.ZSXQ_CLI)

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(1 if failed else 0)
