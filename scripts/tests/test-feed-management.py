#!/usr/bin/env python3
"""订阅源写回的数据安全与分组一致性测试（不触碰真实 Obsidian 数据）。"""
import importlib.util
import json
import os
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("daily_workbench_app", os.path.join(ROOT, "app.py"))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

passed = 0
failed = 0


def t(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  ✓ " + name + (("  → " + detail) if detail else ""))
    else:
        failed += 1
        print("  ✗ " + name + (("  → " + detail) if detail else ""))


def fixture():
    return {
        "subscriptionGroups": [
            {"id": "g-product", "name": "产品与工具", "order": 0},
        ],
        "subscriptions": [
            {
                "id": "local:old", "url": "https://old.example/feed",
                "name": "旧源", "group": "产品与工具", "paused": False,
                "entries": [{"id": "e1"}, {"id": "e2"}],
            },
        ],
        "sourceMeta": {
            "local:old": {"groupId": "g-product", "name": "", "order": 0},
        },
        "readIds": ["e1", "outside"],
        "readAt": {"e1": 1, "outside": 2},
        "favorites": {"e2": {"id": "e2"}, "outside": {"id": "outside"}},
        "readLater": ["e1", "outside"],
        "savedArticles": {"e2": {"entry": {"id": "e2"}}, "outside": {}},
    }


print("被测：app.py 订阅源新增 / 暂停 / 永久删除")

d = fixture()
name = app._feed_add(d, "https://new.example/feed", "新源", "AI 与技术")
new = d["subscriptions"][0]
new_group = next(g for g in d["subscriptionGroups"] if g["name"] == "AI 与技术")
t("新增源写入 subscriptions", name == "新源" and new["url"] == "https://new.example/feed")
t("新增源创建并关联真实 sourceMeta.groupId",
  d["sourceMeta"][new["id"]]["groupId"] == new_group["id"],
  d["sourceMeta"][new["id"]]["groupId"])
t("sourceMeta.order 与订阅列表顺序一致",
  [d["sourceMeta"][s["id"]]["order"] for s in d["subscriptions"]] == [0, 1])

try:
    app._feed_add(d, "HTTPS://NEW.EXAMPLE/feed/", "重复", "")
    duplicate_ok = False
except ValueError:
    duplicate_ok = True
t("重复 URL 忽略大小写与末尾斜杠后被拒绝", duplicate_ok)

paused = app._feed_pause(d, "https://new.example/feed", True)
t("暂停只改 paused，不删条目",
  paused is not None and paused["paused"] is True and len(paused["entries"]) == 0)
t("暂停后订阅数量不变", len(d["subscriptions"]) == 2)

removed = app._feed_delete(d, "https://old.example/feed")
t("永久删除返回名称与条目数",
  removed == {"name": "旧源", "entries": 2}, str(removed))
t("永久删除移除订阅与 sourceMeta",
  all(s["url"] != "https://old.example/feed" for s in d["subscriptions"])
  and "local:old" not in d["sourceMeta"])
t("永久删除同步清理该源条目关联状态",
  d["readIds"] == ["outside"] and d["readAt"] == {"outside": 2}
  and d["favorites"] == {"outside": {"id": "outside"}}
  and d["readLater"] == ["outside"]
  and list(d["savedArticles"]) == ["outside"])
t("永久删除后 order 重新连续",
  [d["sourceMeta"][s["id"]]["order"] for s in d["subscriptions"]] == [0])
t("删除不存在的源返回 None", app._feed_delete(d, "https://missing.example/feed") is None)

with tempfile.TemporaryDirectory(prefix="wb-feed-write-") as td:
    old_data, old_backups = app.RSS_DATA, app.BACKUP_DIR
    app.RSS_DATA = os.path.join(td, "data.json")
    app.BACKUP_DIR = os.path.join(td, "backups")
    with open(app.RSS_DATA, "w", encoding="utf-8") as f:
        json.dump(fixture(), f, ensure_ascii=False)
    app._rss_write(fixture())
    app._rss_write(fixture())
    backups = [x for x in os.listdir(app.BACKUP_DIR) if x.endswith(".bak")]
    t("同一秒连续写入生成两个独立备份", len(backups) == 2, ", ".join(backups))
    app.RSS_DATA, app.BACKUP_DIR = old_data, old_backups

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(0 if failed == 0 else 1)
