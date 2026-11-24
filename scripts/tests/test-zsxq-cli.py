#!/usr/bin/env python3
"""知识星球 CLI 通道的离线回归：映射、失败回退、脏数据处理。

不联网、不执行 zsxq-cli：所有输入都是固定 JSON。
"""
import importlib.util
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location("social_sync", os.path.join(ROOT, "scripts", "sync", "social-sync.py"))
m = importlib.util.module_from_spec(spec)
sys.modules["social_sync"] = m
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


GROUPS = {"groups": [
    {"group_id": "111", "name": "凌一 | 成长·写作·赚钱",
     "statistics": {"members": {"count": 242}}},
    {"group_id": "222", "name": "凌一的碎屑",
     "statistics": {"members": {"count": 1}}},
    {"group_id": "333", "name": "别人的星球",
     "statistics": {"members": {"count": 9999}}},
]}

TOPICS = {
    "111": [{"topics_brief": [
        {"topic_id": 9001, "create_time": "2026-10-19T20:32:57.627+0800",
         "talk": {"text": "最近都在使用 agent 批量投稿自己的站\n\n大家可以借机优化付费计划"},
         "counts": {"readers": 14, "likes": 1, "comments": 0}},
        {"topic_id": 9002, "create_time": "2026-08-25T21:32:27.227+0800",
         "talk": {"title": "豆包工作限时免费一个月\n\n豆包", "text": ""},
         "counts": {"readers": 18, "likes": 1, "comments": 0}},
        {"topic_id": 9003, "create_time": "2026-07-01T13:03:58.396+0800",
         "talk": {"text": ""}, "counts": {}},
    ]}],
    "222": [{"topics_brief": [
        {"topic_id": 9100, "create_time": "2026-06-01T10:00:00.000+0800",
         "talk": {"text": "碎屑一条"}, "counts": {"readers": 3, "likes": 0, "comments": 0}},
    ]}],
    "333": [{"topics_brief": [
        {"topic_id": 9200, "create_time": "2026-06-02T10:00:00.000+0800",
         "talk": {"text": "不该出现"}, "counts": {}},
    ]}],
}

account, posts = m.zsxq_cli_map(GROUPS, TOPICS, ["凌一 | 成长·写作·赚钱", "凌一的碎屑"])
check("只聚合配置里列出的星球，别人的星球不进来",
      all("不该出现" not in (p.get("title") or "") for p in posts), [p["title"] for p in posts])
check("空内容主题被丢弃，不生成空标题条目", len(posts) == 3, len(posts))
check("标题里的换行被压成单行",
      all("\n" not in (p.get("title") or "") for p in posts), [p["title"] for p in posts])
check("按发布时间倒序",
      [p["publishedAt"][:10] for p in posts] == ["2026-10-19", "2026-08-25", "2026-06-01"],
      [p["publishedAt"] for p in posts])
check("指标取自 counts（阅读/点赞/评论）",
      posts[0]["metrics"] == {"reads": 14, "likes": 1, "comments": 0}, posts[0]["metrics"])
check("asOf 取最新一条日期", account["asOf"] == "2026-10-19", account["asOf"])
check("成员数按星球分别记录",
      account["metrics"]["members"] == {"凌一 | 成长·写作·赚钱": 242, "凌一的碎屑": 1},
      account["metrics"]["members"])
check("perGroup 计数与实际条目一致",
      account["metrics"]["perGroup"] == {"凌一 | 成长·写作·赚钱": 2, "凌一的碎屑": 1},
      account["metrics"]["perGroup"])
check("来源写成 CLI，不冒充 Web API",
      "zsxq-cli" in account["sourceKind"], account["sourceKind"])

# ── 帖子带上星球归属（时间线要按星球筛）───────────────────────────
_topic = {"topic_id": "1", "create_time": "2026-11-22T10:00:00+08:00",
          "talk": {"text": "最近试了个新东西，感觉还行。", "title": "t"},
          "counts": {"readers": 3, "likes": 1, "comments": 0}}
post = m._zsxq_topic_to_post(_topic, "凌一的碎屑")
check("帖子带上 group 字段", post and post.get("group") == "凌一的碎屑", post)
check("没传星球时 group 为空字符串（不是 None）",
      m._zsxq_topic_to_post(_topic, "").get("group") == "",
      m._zsxq_topic_to_post(_topic, "").get("group"))

# ── 运营大盘：只对付费星球有意义 ───────────────────────────────────
DASH = {
    "111": {"status": "unsupported", "reason": "免费星球没有官方运营大盘（接口返回 17208）"},
    "222": {"status": "ok", "yesterdayActive": 0, "yesterdayIncrease": 0, "yesterdayIncome": 0,
            "avg30Active": 0.4, "avg30Increase": 0, "todayActive": 0, "todayIncrease": 0},
}
acc2, _ = m.zsxq_cli_map(GROUPS, TOPICS, ["凌一 | 成长·写作·赚钱", "凌一的碎屑"], DASH)
dash = acc2["metrics"]["dashboard"]
check("付费星球的昨日/近30日大盘被写进 metrics",
      dash["凌一的碎屑"]["status"] == "ok" and dash["凌一的碎屑"]["avg30Active"] == 0.4,
      dash.get("凌一的碎屑"))
check("免费星球标 unsupported 并给出原因，不写成失败",
      dash["凌一 | 成长·写作·赚钱"]["status"] == "unsupported"
      and "17208" in dash["凌一 | 成长·写作·赚钱"]["reason"],
      dash.get("凌一 | 成长·写作·赚钱"))
check("note 里说清哪个星球没有大盘，且写名字不写 ID",
      "凌一 | 成长·写作·赚钱" in acc2["note"] and "10000000000001" not in acc2["note"]
      and "免费星球" in acc2["note"], acc2["note"])

# 17208 必须是 unsupported，别的错误码才是 error——两者处置完全不同
real_json = m._zsxq_cli_json
try:
    m._zsxq_cli_json = lambda *a, **k: {"body": {"succeeded": False, "code": 17208,
                                                 "error": "星球不存在或不是付费星球"}}
    check("17208 映射为 unsupported", m.zsxq_cli_dashboard("1")["status"] == "unsupported",
          m.zsxq_cli_dashboard("1"))
    m._zsxq_cli_json = lambda *a, **k: {"body": {"succeeded": False, "code": 1059,
                                                 "error": "请求过于频繁"}}
    check("限流等其它错误码映射为 error", m.zsxq_cli_dashboard("1")["status"] == "error")
    def _fake_ok(*a, **k):
        return {"body": {"succeeded": True, "resp_data": {
            "daily": {"member_active_count": 3},
            "statistics": {
                "yesterday": {"member_active_count": 5, "income_count": 12},
                "within_30days_average": {"member_active_count": 4.5},
            }}}}

    m._zsxq_cli_json = _fake_ok
    ok_dash = m.zsxq_cli_dashboard("1")
    check("大盘字段按天/近30日拆开，收入单独取",
          ok_dash["yesterdayActive"] == 5 and ok_dash["yesterdayIncome"] == 12
          and ok_dash["avg30Active"] == 4.5 and ok_dash["todayActive"] == 3, ok_dash)
    def _boom(*a, **k):
        raise RuntimeError("zsxq-cli 崩了")
    m._zsxq_cli_json = _boom
    check("CLI 异常时返回 error 而不是抛出", m.zsxq_cli_dashboard("1")["status"] == "error")
finally:
    m._zsxq_cli_json = real_json

empty_account, empty_posts = m.zsxq_cli_map(GROUPS, TOPICS, ["不存在的星球"])
check("星球名对不上时返回 (None, [])，交给上层回退",
      empty_account is None and empty_posts == [], (empty_account, empty_posts))

# CLI 不可用：必须安静回退，不能抛异常打断整条同步
real_cli = m.ZSXQ_CLI
m.ZSXQ_CLI = ""
try:
    no_cli = m.zsxq_cli_snapshot()
    check("zsxq-cli 未安装时返回 (None, [])，不抛异常", no_cli == (None, []), no_cli)
finally:
    m.ZSXQ_CLI = real_cli

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(1 if failed else 0)
