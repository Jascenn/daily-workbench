#!/usr/bin/env python3
"""专注回写 Notion 的服务端单元测试。

不访问网络，也不碰真实 data.json：只验证白名单属性映射和本地状态更新。
"""
import importlib.util
import json
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("daily_workbench_app", ROOT / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class FocusWritebackTest(unittest.TestCase):
    def test_only_completed_sessions_are_allowed(self):
        with self.assertRaisesRegex(ValueError, "只有已完成"):
            app._build_focus_properties({
                "id": "focus-1",
                "status": "abandoned",
                "date": "2026-10-28",
                "elapsedSeconds": 300,
            })

    def test_properties_are_whitelisted_and_mapped(self):
        props = app._build_focus_properties({
            "id": "focus-1",
            "status": "completed",
            "date": "2026-10-28",
            "elapsedSeconds": 1500,
            "projectName": "Lion CC Projects",
            "noteLine": "[专注] 09:00–09:25 · Lion CC Projects · 25 分钟 · 完成",
        })
        self.assertEqual(
            set(props),
            {"任务名称", "备注", "完成", "实际番茄", "干扰记录",
             "所属项目", "日期", "状态", "预估番茄"},
        )
        self.assertEqual(props["任务名称"]["title"][0]["text"]["content"],
                         "Lion CC Projects · 25 分钟专注")
        self.assertEqual(props["所属项目"]["rich_text"][0]["text"]["content"],
                         "Lion CC Projects")
        self.assertEqual(props["日期"]["date"]["start"], "2026-10-28")
        self.assertEqual(props["状态"]["status"]["name"], "完成")
        self.assertTrue(props["完成"]["checkbox"])

    def test_local_update_preserves_other_data_and_records_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_path = pathlib.Path(tmp) / "data.json"
            data_path.write_text(json.dumps({
                "earnings": [{"id": "e1"}],
                "projects": [{"id": "p1"}],
                "focusSessions": [
                    {"id": "focus-1", "status": "completed"},
                    {"id": "focus-2", "status": "completed"},
                ],
                "notionMeta": {"syncedAt": "2026-10-28T10:00:00+08:00"},
            }, ensure_ascii=False), encoding="utf-8")
            old_data, old_backup = app.DATA, app.BACKUP_DIR
            app.DATA = str(data_path)
            app.BACKUP_DIR = str(pathlib.Path(tmp) / "backups")
            try:
                updated = app._write_focus_update(
                    "focus-1",
                    {"id": "notion-page-1", "url": "https://app.notion.com/p/notion-page-1"},
                )
                self.assertEqual(updated["notionPageId"], "notion-page-1")
                saved = json.loads(data_path.read_text(encoding="utf-8"))
                self.assertEqual(saved["earnings"], [{"id": "e1"}])
                self.assertEqual(saved["projects"], [{"id": "p1"}])
                self.assertEqual(saved["focusSessions"][1]["id"], "focus-2")
                self.assertEqual(saved["focusSessions"][0]["notionUrl"],
                                 "https://app.notion.com/p/notion-page-1")
                self.assertEqual(saved["notionMeta"]["focusDs"], app.DS_FOCUS)
                self.assertEqual(saved["notionMeta"]["focusDsName"], "今日番茄追踪表")
            finally:
                app.DATA, app.BACKUP_DIR = old_data, old_backup


if __name__ == "__main__":
    unittest.main(verbosity=2)
