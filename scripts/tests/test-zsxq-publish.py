#!/usr/bin/env python3
"""星球发帖的离线回归：正文拼装、digest、防重复、目标白名单。

不联网、不真发：只测纯函数与防护逻辑。
"""
import importlib.util
import os
import sys
import tempfile
import time

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


# ── 正文拼装 ────────────────────────────────────────────────
out = app.zsxq_compose("正文第一段。\n\n第二段。", ["AI"], "aigc")
check("aigc 自动补 bot 标签", out.splitlines()[0] == "#AI #bot", out.splitlines()[0])
check("正文保留段落", "第二段。" in out and out.count("\n") >= 2, repr(out))

# 发布路由自己生成富文本标签行，compose 传空标签时不能再补一条纯文本 #bot
out0 = app.zsxq_compose("正文", [], "aigc")
check("空标签 + aigc 时 compose 不插 #bot 行", out0 == "正文", repr(out0))

out2 = app.zsxq_compose("#AI #bot\n正文", ["AI"], "aigc")
check("首行已有标签时不重复加", out2.count("#AI") == 1, out2.splitlines()[0])

out3 = app.zsxq_compose("正文", ["个人观点"], "personal_perspective")
check("非 aigc 不强行加 bot", "bot" not in out3.splitlines()[0], out3.splitlines()[0])

out4 = app.zsxq_compose("正文", [], "none")
check("没有标签时只有正文", out4 == "正文", repr(out4))

# ── digest 与防重复 ─────────────────────────────────────────
d1 = app.zsxq_publish_digest("1", "内容A", "aigc")
d2 = app.zsxq_publish_digest("1", "内容A", "aigc")
d3 = app.zsxq_publish_digest("1", "内容B", "aigc")
d4 = app.zsxq_publish_digest("2", "内容A", "aigc")
check("同内容同星球 digest 稳定", d1 == d2, (d1, d2))
check("内容或星球变化 digest 就变", d1 != d3 and d1 != d4)

with tempfile.TemporaryDirectory() as tmp:
    app.ZSXQ_PUBLISH_LOG = os.path.join(tmp, "publish.jsonl")
    app.zsxq_log_publish({"groupId": "1", "digest": d1, "ok": True})
    recent = app.zsxq_recent_publishes(30)
    check("发布记录可回读（用于 30 分钟防重复）",
          len(recent) == 1 and recent[0]["digest"] == d1, recent)
    check("超出时间窗的记录不算在内", app.zsxq_recent_publishes(0) == [])

# ── 平台标签 annotation（正文写 #AI 不等于标签）───────────────
ann = app.zsxq_annotation(["AI", "碎屑", "bot"])
check("三个标签生成三个 hashtag 富文本", ann.count("<e ") == 3, ann)
check("标签名做了 URL 编码", "%23AI%23" in ann, ann)
check("重复标签只保留一个", app.zsxq_annotation(["AI", "AI", "#AI"]).count("<e ") == 1)
check("空标签被忽略", app.zsxq_annotation(["", "  ", "#"]) == "", app.zsxq_annotation(["", "#"]))

# ── 多星球发布目标 ────────────────────────────────────────
tg = app.zsxq_publish_targets({"groupId": "1", "groupName": "A",
                               "groups": [{"groupId": "1", "groupName": "A"},
                                          {"groupId": "2", "groupName": "B"}]})
check("groups 列表读成多目标且不重复",
      [t["groupId"] for t in tg] == ["1", "2"], [t["groupId"] for t in tg])
tg2 = app.zsxq_publish_targets({"groupId": "9", "groupName": "X"})
check("老配置（单星球）仍然兼容",
      len(tg2) == 1 and tg2[0]["groupId"] == "9", tg2)
check("没有任何配置时返回空列表（发布前会被拒绝）",
      app.zsxq_publish_targets({}) == [], app.zsxq_publish_targets({}))
check("缺 groupName 时用 id 兜底",
      app.zsxq_publish_targets({"groups": [{"groupId": "7"}]})[0]["groupName"] == "7")

# ── 预览警告（不能误报）─────────────────────────────────────
check("aigc + bot 标签时不告警",
      app.zsxq_preview_warnings("x" * 30, ["AI", "bot"], "aigc") == [],
      app.zsxq_preview_warnings("x" * 30, ["AI", "bot"], "aigc"))
check("aigc 缺 bot 时才告警",
      any("bot" in w for w in app.zsxq_preview_warnings("x" * 30, ["AI"], "aigc")),
      app.zsxq_preview_warnings("x" * 30, ["AI"], "aigc"))
check("正文过短会提示",
      any("少于 20 字" in w for w in app.zsxq_preview_warnings("短", ["AI"], "aigc")))
check("非 aigc 不要求 bot",
      app.zsxq_preview_warnings("x" * 30, ["AI"], "none") == [])

# ── AI 续写：只生成草稿 ────────────────────────────────────
prompt = app.zsxq_draft_prompt("今天踩了个坑")
check("续写提示词带上凌一的写法约束",
      "口语短句" in prompt and "不要标题" in prompt and "100 到 250 字" in prompt)
check("续写提示词禁止编造具体事实", "不要编造具体事实" in prompt, prompt[-80:])
check("续写提示词带上触发点", "今天踩了个坑" in prompt)

draft = app.zsxq_clean_draft("好的，以下是正文：\n#AI #bot\n## 标题\n**最近**试了个新东西。\n\n就这样。")
check("清洗掉开场白/标签/markdown 标题",
      draft.startswith("最近试了个新东西"), draft)
check("清洗保留段落", "\n\n" in draft, repr(draft))
check("清洗去掉首尾引号", app.zsxq_clean_draft('「就这样吧」') == "就这样吧", app.zsxq_clean_draft('「就这样吧」'))

ok, err = app.zsxq_generate_draft("  ")
check("空触发点直接拒绝，不打模型", ok is False and "触发点" in err.get("error", ""), err)

# ── 常用标签排序（接口侧）───────────────────────────────────
raw = [{"name": "AI", "count": 48}, {"name": "碎屑", "count": 500},
       {"name": "踩坑记录", "count": 31}, {"name": "", "count": 9},
       {"name": "同分B", "count": 5}, {"name": "同分A", "count": 5}]
sorted_rows = app.sort_tag_options(raw)
check("标签按使用次数降序", [r["name"] for r in sorted_rows[:3]] == ["碎屑", "AI", "踩坑记录"],
      [r["name"] for r in sorted_rows])
check("空名字被过滤", all(r["name"] for r in sorted_rows), sorted_rows)
check("同分按名字稳定排序", [r["name"] for r in sorted_rows[-2:]] == ["同分A", "同分B"],
      [r["name"] for r in sorted_rows])

# ── 星球专属同步的合并逻辑 ──────────────────────────────────
social = {
    "accounts": [{"platform": "公众号"}, {"platform": "知识星球", "sync": {"status": "history"}}],
    "posts": [{"platform": "公众号", "title": "g"}, {"platform": "知识星球", "title": "old"}],
    "generatedAt": "2026-11-22T10:00:00+08:00",
}
new_acc = {"platform": "知识星球", "sync": {"status": "updated"}, "metrics": {"groups": 5}}
new_posts = [{"platform": "知识星球", "title": "new1"}, {"platform": "知识星球", "title": "new2"}]
merged = app.merge_zsxq_snapshot(social, new_acc, new_posts)
check("只替换知识星球账号，别的平台保留",
      [a["platform"] for a in merged["accounts"]] == ["公众号", "知识星球"], merged["accounts"])
check("星球帖子整体替换（旧的不留）",
      [p["title"] for p in merged["posts"] if p["platform"] == "知识星球"] == ["new1", "new2"],
      merged["posts"])
check("其它平台帖子原样保留",
      any(p["title"] == "g" for p in merged["posts"]), merged["posts"])
check("记录本轮同步时间", bool(merged.get("zsxqSyncedAt")), merged.get("zsxqSyncedAt"))
check("不改动原对象（纯函数）",
      len(social["posts"]) == 2 and social["accounts"][1]["sync"]["status"] == "history")

# ── 正文首行富文本标签（蓝色胶囊的关键）────────────────────
real = app.zsxq_group_hashtags
try:
    app.zsxq_group_hashtags = lambda gid: {"AI": "155", "碎屑": "888"}
    line = app.zsxq_tag_line(["AI", "碎屑", "bot"], "1")
    check("标签行用真实 hid", 'hid="155"' in line and 'hid="888"' in line, line)
    check("查不到的标签退 0，不阻塞发布", 'hid="0"' in line and "%23bot%23" in line, line)
    check("标签之间用空格分隔（与历史帖子一致）", line.count("<e ") == 3 and " /> <e " in line, line)
finally:
    app.zsxq_group_hashtags = real

# ── 目标白名单与参数校验 ────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    cfg = os.path.join(tmp, "publish.json")
    with open(cfg, "w", encoding="utf-8") as fh:
        fh.write('{"groupId": "511", "groupName": "测试"}')
    app.ZSXQ_PUBLISH_CONFIG = cfg
    check("配置能读到目标星球", app.zsxq_publish_config().get("groupId") == "511")
    bad = app.zsxq_publish("999", "内容", "aigc")
    check("不在白名单的星球拒绝发布",
          bad.get("ok") is False and "允许列表" in (bad.get("error") or ""), bad)
    bad2 = app.zsxq_publish("511", "内容", "whatever")
    check("非法 ai_mode 拒绝发布", bad2.get("ok") is False and "ai_mode" in bad2.get("error", ""), bad2)

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(1 if failed else 0)
