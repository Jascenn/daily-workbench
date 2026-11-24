#!/usr/bin/env python3
"""选题雷达的离线回归：解析、去噪、失败降级。

不联网：所有网络函数都用固定 HTML/JSON 替换。联网部分由 radarsync 自己跑。
"""
import importlib.util
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location(
    "radar_sync", os.path.join(ROOT, "scripts", "sync", "radar-sync.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules["radar_sync"] = mod
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


# 榜单页结构：<td class="al"><a href=...>标题</a>
FIXTURE = """
<table><tr><td class="al" align="center"><img src="x"></td>
<td class="al"><div><a href="https://36kr.com/p/1" target="_blank">36氪：某公司发布新模型</a></div></td></tr>
<tr><td class="al"><a href="https://36kr.com/p/2">  带空格与 &amp; 实体  </a></td></tr>
<tr><td class="al"><a href="https://36kr.com/p/3"></a></td></tr>
<tr><td class="al"><a href="/relative/link">相对链接不该出现</a></td></tr>
</table>
"""

rows = mod.parse_tophub(FIXTURE)
check("解析出榜单条目并保留顺序", [r["title"] for r in rows] == ["36氪：某公司发布新模型", "带空格与 & 实体"],
      rows)
check("跳过空标题与相对链接", all(r["url"].startswith("http") for r in rows) and len(rows) == 2, rows)
check("rank 从 1 连续编号", [r["rank"] for r in rows] == [1, 2], rows)

# 复述标题的角度等于没写：字符重合率闸门
title = "腾讯姚顺雨团队深夜回应一切：算力不足、模型刷榜"
check("把标题换个说法会被判为复述",
      mod._repeats_title("腾讯姚顺雨团队深夜回应算力不足", title))
check("真正的角度不会误杀",
      not mod._repeats_title("AI 模型训练成本测算", title))
check("空文本不误判", not mod._repeats_title("", title))

# 来源配置：微博是 JS 渲染，必须如实标 unsupported 而不是 ok/0 条
weibo = [s for s in mod.SOURCES if s["id"] == "weibo"]
check("微博热搜标为 unsupported 并写明原因",
      weibo and weibo[0]["kind"] == "unsupported" and "JS" in weibo[0].get("reason", ""), weibo)

# Jev 不可用时：条目保留，但绝不假装分析过
items = [{"title": "标题%d" % i, "url": "https://example.com/%d" % i,
          "source": "hn", "sourceName": "Hacker News", "rank": i} for i in range(3)]
mod.jev_client.judge = lambda *a, **k: (False, {})
judged = mod.judge(items)
check("Jev 失败时条目仍保留，但 analyzed=false",
      len(judged) == 3 and all(r["analyzed"] is False for r in judged), judged)
check("Jev 失败时不写 judgmentModel，页面必须显示未解析",
      all(not r.get("judgmentModel") for r in judged))

# ── 缓存：2 小时一跑，同一批标题不能反复烧 Jev ───────────────────────
import json
import tempfile
import time
from pathlib import Path

check("缓存键忽略大小写与多余空白",
      mod._cache_key("  Hello   WORLD ") == mod._cache_key("hello world"),
      mod._cache_key("  Hello   WORLD "))

fresh = {mod._cache_key("某条标题"): {"relevance": 7.2, "angle": 4.9,
                                     "judgmentModel": "jev", "at": time.time()}}
hit = mod._cached_verdict(fresh, "某条标题")
check("命中缓存时直接复用判定，不重新问 Jev",
      hit and hit["analyzed"] and hit["fromCache"] and hit["relevance"] == 7.2, hit)

stale = {mod._cache_key("旧标题"): {"relevance": 6, "angle": 4, "at": time.time() - 3 * 86400}}
check("三天前的缓存已过期，必须重新判断", mod._cached_verdict(stale, "旧标题") is None)
check("没缓存的标题返回 None（必须真的去判断）", mod._cached_verdict({}, "从未见过的标题") is None)

narration = {mod._cache_key("角度标题"): {"whyRelated": "能用到企业咨询", "angles": ["成本测算"],
                                        "nextAction": "写一篇", "at": time.time()}}
narr = mod._cached_narrative(narration, "角度标题")
check("角度与下一步也走缓存", narr and narr["angles"] == ["成本测算"], narr)
check("空叙述不写缓存（避免把一次失败缓存成结论）",
      mod._cached_narrative({mod._cache_key("空"): {"at": time.time()}}, "空") is None)

with tempfile.TemporaryDirectory() as tmp:
    mod.CACHE = Path(tmp) / "radar-cache.json"
    now = time.time()
    mod.save_cache({"keep": {"at": now}, "drop": {"at": now - 8 * 86400}})
    saved = json.loads(mod.CACHE.read_text(encoding="utf-8"))
    check("缓存落盘时清掉 7 天前的条目", set(saved) == {"keep"}, saved)

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(1 if failed else 0)
