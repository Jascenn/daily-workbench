#!/usr/bin/env python3
"""项目回写 Notion 的服务端单元测试。

不访问网络，也不碰真实 data.json：只验证 Notion schema 白名单、类型校验
和本地项目缓存更新。
"""
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("daily_workbench_app", ROOT / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def fake_schema():
    return {
        "properties": {
            "项目名称": {"type": "title", "title": {}},
            "状态": {
                "type": "status",
                "status": {"options": [
                    {"name": "未开始"},
                    {"name": "进行中"},
                    {"name": "已延迟"},
                    {"name": "已完成"},
                    {"name": "已取消"},
                ]},
            },
            "优先级": {
                "type": "select",
                "select": {"options": [{"name": "P1 - 高"}, {"name": "P2 - 中"}]},
            },
            "类型": {
                "type": "select",
                "select": {"options": [{"name": "产品开发"}, {"name": "客户服务"}]},
            },
            "冲刺": {
                "type": "select",
                "select": {"options": [{"name": "冲刺 1"}, {"name": "冲刺 2"}]},
            },
            "标签": {"type": "multi_select", "multi_select": {}},
            "开始日期": {"type": "date", "date": {}},
            "截止日期": {"type": "date", "date": {}},
            "完成日期": {"type": "date", "date": {}},
            "冲刺开始日期": {"type": "date", "date": {}},
            "冲刺结束日期": {"type": "date", "date": {}},
            "预计工时": {"type": "number", "number": {}},
            "实际工时": {"type": "number", "number": {}},
            "备注": {"type": "rich_text", "rich_text": {}},
            "相关链接": {"type": "url", "url": {}},
            "上级 项目": {"type": "relation", "relation": {}},
            "关联任务": {"type": "relation", "relation": {}},
            "来源记录": {"type": "relation", "relation": {}},
            "负责人": {"type": "people", "people": {}},
            "完成百分比": {"type": "rollup", "rollup": {}},
            "子任务数量": {"type": "rollup", "rollup": {}},
            "子级 项目": {"type": "relation", "relation": {}},
        }
    }


class ProjectWritebackTest(unittest.TestCase):
    def setUp(self):
        self.schema = fake_schema()
        self.parent_id = "22222222-2222-4222-8222-222222222222"
        self.task_id = "44444444-4444-4444-8444-444444444444"
        self.owner_id = "33333333-3333-4333-8333-333333333333"

    def test_properties_are_whitelisted_and_mapped(self):
        props = app._build_project_properties({
            "name": "回写项目探针",
            "status": "已延迟",
            "priority": "P1 - 高",
            "projectType": "产品开发",
            "tags": ["重要", "本周"],
            "start": "2026-10-01",
            "due": "2026-10-30",
            "doneDate": "",
            "sprint": "冲刺 2",
            "sprintStart": "2026-10-15",
            "sprintEnd": "2026-10-28",
            "estHours": 10,
            "actHours": 12.5,
            "note": "通过工作台回写",
            "relatedLink": "https://example.com/project",
            "parentNotionId": self.parent_id,
            "relatedTaskIds": [self.task_id],
            "sourceRecordIds": [],
            "ownerIds": [self.owner_id],
        }, self.schema)
        self.assertEqual(set(props), {
            "项目名称", "状态", "优先级", "类型", "标签",
            "开始日期", "截止日期", "完成日期", "冲刺",
            "冲刺开始日期", "冲刺结束日期", "预计工时", "实际工时",
            "备注", "相关链接", "上级 项目", "关联任务", "来源记录", "负责人",
        })
        self.assertEqual(props["项目名称"]["title"][0]["text"]["content"], "回写项目探针")
        self.assertEqual(props["状态"]["status"]["name"], "已延迟")
        self.assertEqual(props["优先级"]["select"]["name"], "P1 - 高")
        self.assertEqual(
            [x["name"] for x in props["标签"]["multi_select"]],
            ["重要", "本周"],
        )
        self.assertIsNone(props["完成日期"]["date"])
        self.assertEqual(props["预计工时"]["number"], 10.0)
        self.assertEqual(props["上级 项目"]["relation"], [{"id": self.parent_id}])
        self.assertEqual(props["负责人"]["people"], [{"id": self.owner_id}])
        self.assertNotIn("完成百分比", props)
        self.assertNotIn("子任务数量", props)
        self.assertNotIn("子级 项目", props)

    def test_rejects_derived_and_unknown_fields(self):
        for field in ("progress", "childCount", "childIds", "statusColor"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "不支持回写"):
                    app._build_project_properties({field: 50}, self.schema)

    def test_rejects_invalid_select_date_number_url_and_relation(self):
        cases = [
            ({"status": "风险"}, "不是 Notion 中的有效选项"),
            ({"due": "2026-02-30"}, "YYYY-MM-DD"),
            ({"estHours": -1}, "0–100000"),
            ({"actHours": "Infinity"}, "0–100000"),
            ({"relatedLink": "javascript:alert(1)"}, "http\\(s\\) URL"),
            ({"parentNotionId": "not-a-page-id"}, "上级项目"),
            ({"ownerIds": ["not-a-page-id"]}, "负责人"),
            ({"tags": "重要"}, "标签必须是数组"),
        ]
        for fields, message in cases:
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(ValueError, message):
                    app._build_project_properties(fields, self.schema)

    def test_ntn_retries_only_transient_proxy_failure(self):
        transient = app.subprocess.CompletedProcess(
            args=["ntn"], returncode=5, stdout="",
            stderr="error: Failed to execute public API request\n")
        success = app.subprocess.CompletedProcess(
            args=["ntn"], returncode=0, stdout='{"ok": true}\n', stderr="")
        with mock.patch.object(app.subprocess, "run", side_effect=[transient, success]) as run:
            with mock.patch.object(app.time, "sleep") as sleep:
                out = app._ntn_api_call(["ntn", "api", "/v1/test"])
        self.assertEqual(json.loads(out), {"ok": True})
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(1.0)

        invalid = app.subprocess.CompletedProcess(
            args=["ntn"], returncode=1, stdout="",
            stderr="error: invalid request\n")
        with mock.patch.object(app.subprocess, "run", return_value=invalid) as run:
            with self.assertRaisesRegex(RuntimeError, "invalid request"):
                app._ntn_api_call(["ntn", "api", "/v1/test"])
        self.assertEqual(run.call_count, 1)

    def test_schema_prefers_recent_sync_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_path = pathlib.Path(tmp) / "data.json"
            data_path.write_text(json.dumps({
                "notionMeta": {"projectSchema": {
                    "status": ["进行中", "已延迟", "已完成"],
                    "priority": ["P1 - 高"],
                    "type": ["产品开发"],
                    "sprint": ["冲刺 2"],
                    "tags": ["重要", "本周"],
                }},
            }, ensure_ascii=False), encoding="utf-8")
            old_data = app.DATA
            old_cache = dict(app._PROJECT_SCHEMA_CACHE)
            app.DATA = str(data_path)
            app._PROJECT_SCHEMA_CACHE.update({"at": 0.0, "schema": None})
            try:
                schema = app._notion_project_schema()
                self.assertEqual(
                    app._project_option_names(schema, "状态", "status"),
                    ["进行中", "已延迟", "已完成"],
                )
                props = app._build_project_properties(
                    {"status": "已延迟", "tags": ["重要"]}, schema)
                self.assertEqual(props["状态"]["status"]["name"], "已延迟")
                self.assertEqual(props["标签"]["multi_select"], [{"name": "重要"}])
            finally:
                app.DATA = old_data
                app._PROJECT_SCHEMA_CACHE.clear()
                app._PROJECT_SCHEMA_CACHE.update(old_cache)

    def test_relation_options_are_compact_and_kind_is_validated(self):
        page = {
            "id": self.task_id,
            "properties": {
                "任务名称": {
                    "type": "title",
                    "title": [{"plain_text": "发布工作台更新"}],
                },
                "状态": {"type": "status", "status": {"name": "还未开始"}},
                "截止日期": {"type": "date", "date": {"start": "2026-10-30"}},
                "正文": {"type": "rich_text", "rich_text": [
                    {"plain_text": "这段内容不应进入选择器"},
                ]},
            },
        }
        self.assertEqual(app._relation_option_from_page(page), {
            "id": self.task_id,
            "title": "发布工作台更新",
            "status": "还未开始",
            "date": "2026-10-30",
        })
        with self.assertRaisesRegex(ValueError, "task 或 record"):
            app._project_relation_kind("unknown")

    def test_relation_lookup_reads_titles_and_caches_by_kind_and_id(self):
        page = {
            "id": self.task_id,
            "properties": {
                "任务名称": {
                    "type": "title",
                    "title": [{"plain_text": "关系回显探针"}],
                },
                "状态": {"type": "status", "status": {"name": "进行中"}},
                "截止日期": {"type": "date", "date": {"start": "2026-10-30"}},
            },
        }
        old_cache = dict(app._PROJECT_RELATION_CACHE)
        app._PROJECT_RELATION_CACHE.clear()
        try:
            with mock.patch.object(
                    app, "_ntn_api_call", return_value=json.dumps(page, ensure_ascii=False)
            ) as api_call:
                first = app._project_relation_lookup("task", [self.task_id])
                second = app._project_relation_lookup("task", [self.task_id.upper()])
            self.assertEqual(first, [{
                "id": self.task_id,
                "title": "关系回显探针",
                "status": "进行中",
                "date": "2026-10-30",
            }])
            self.assertEqual(second, first)
            self.assertEqual(api_call.call_count, 1)
            self.assertEqual(
                api_call.call_args.args[0],
                [app.NTN, "api", "/v1/pages/%s" % self.task_id, "-X", "GET"],
            )
            with self.assertRaisesRegex(ValueError, "关联任务 含无效页面 ID"):
                app._project_relation_lookup("task", ["not-a-page-id"])
        finally:
            app._PROJECT_RELATION_CACHE.clear()
            app._PROJECT_RELATION_CACHE.update(old_cache)

    def test_relation_search_filters_by_title_and_returns_safe_summaries(self):
        page = {
            "results": [
                {
                    "id": self.task_id,
                    "properties": {
                        "记录标题": {
                            "type": "title",
                            "title": [{"plain_text": "发布复盘"}],
                        },
                        "状态": {"type": "select", "select": {"name": "已归档"}},
                        "正文": {
                            "type": "rich_text",
                            "rich_text": [{"plain_text": "不应返回正文"}],
                        },
                    },
                },
                {"properties": {}},
            ],
        }
        with mock.patch.object(
                app, "_ntn_api_call", return_value=json.dumps(page, ensure_ascii=False)
        ) as api_call:
            items = app._project_relation_search("record", "发布", limit=999)
        self.assertEqual(items, [{
            "id": self.task_id,
            "title": "发布复盘",
            "status": "已归档",
            "date": "",
        }])
        cmd, body = api_call.call_args.args
        payload = json.loads(body)
        self.assertEqual(cmd[-2:], ["-X", "POST"])
        self.assertTrue(cmd[2].endswith("/v1/data_sources/%s/query" % app.DS_RECORDS))
        self.assertEqual(payload["page_size"], 50)
        self.assertEqual(payload["filter"], {
            "property": "记录标题",
            "title": {"contains": "发布"},
        })

    def test_local_update_preserves_rollup_and_other_data(self):
        page_id = "11111111-1111-4111-8111-111111111111"
        parent_id = self.parent_id
        source_id = "55555555-5555-4555-8555-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            data_path = pathlib.Path(tmp) / "data.json"
            data_path.write_text(json.dumps({
                "earnings": [{"id": "e1"}],
                "projects": [
                    {
                        "id": "parent-local",
                        "notionId": parent_id,
                        "name": "父项目",
                    },
                    {
                        "id": "project-write",
                        "notionId": page_id,
                        "name": "回写项目探针",
                        "status": "进行中",
                        "progress": 50,
                        "tags": ["旧标签"],
                        "projectType": "产品开发",
                        "estHours": 8,
                        "actHours": 6,
                        "doneDate": None,
                        "parent": None,
                        "parentNotionId": None,
                        "relatedTaskIds": [self.task_id],
                    },
                ],
                "notionMeta": {"syncedAt": "2026-10-28T10:00:00+08:00"},
            }, ensure_ascii=False), encoding="utf-8")
            old_data, old_backup = app.DATA, app.BACKUP_DIR
            app.DATA = str(data_path)
            app.BACKUP_DIR = str(pathlib.Path(tmp) / "backups")
            try:
                updated = app._write_project_update(page_id, {
                    "name": "回写项目探针-已改",
                    "status": "已延迟",
                    "tags": ["重要", "本周"],
                    "doneDate": "2026-10-28",
                    "estHours": 10,
                    "actHours": 12.5,
                    "parentNotionId": parent_id,
                    "ownerIds": [self.owner_id],
                    "relatedTaskIds": [self.task_id],
                    "sourceRecordIds": [source_id],
                })
                self.assertEqual(updated["name"], "回写项目探针-已改")
                self.assertEqual(updated["status"], "已延迟")
                self.assertEqual(updated["progress"], 50)
                self.assertEqual(updated["category"], "重要")
                self.assertEqual(updated["parent"], "parent-local")
                self.assertEqual(updated["parentNotionId"], parent_id)
                self.assertEqual(updated["relatedTaskIds"], [self.task_id])
                self.assertEqual(updated["ownerIds"], [self.owner_id])
                self.assertEqual(updated["sourceRecordIds"], [source_id])
                self.assertTrue(updated["writable"])

                saved = json.loads(data_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["earnings"], [{"id": "e1"}])
                self.assertEqual(saved["projects"][1]["progress"], 50)
                self.assertEqual(saved["projects"][1]["doneDate"], "2026-10-28")
                self.assertIn("lastProjectWritebackAt", saved["notionMeta"])
            finally:
                app.DATA, app.BACKUP_DIR = old_data, old_backup


if __name__ == "__main__":
    unittest.main(verbosity=2)
