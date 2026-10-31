#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Obsidian 写作存储与本地 API 的无网络测试。

只使用临时目录，不读取或修改真实知识库。
"""
import base64
import http.server
import importlib.util
import json
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("daily_workbench_app_writing", ROOT / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
ws = app.writing_store


class WritingFixture:
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self.temp_dir.name)
        self.obsidian = self.tmp / "Obsidian-Tech"
        self.projects = self.obsidian / "projects"
        self.project = self.projects / "测试作品"
        self.project.mkdir(parents=True)
        (self.project / "章节目录.md").write_text(
            "# 测试作品\n\n## 第一章 开端\n\n## 第二章 继续\n",
            encoding="utf-8",
        )
        self.chapter = self.project / "第一章.md"
        self.chapter.write_text("---\ntype: 正文\n---\n\n# 第一章\n\n原始正文。\n", encoding="utf-8")
        self.creative = self.obsidian / "创作"
        self.documents = self.creative / "公众号"
        self.documents.mkdir(parents=True)
        self.document = self.documents / "测试文章.md"
        self.document.write_text(
            "---\ntype: 内容笔记\ncontent-type: 公众号\ntitle: 测试文章\nstatus: 草稿\n---\n\n"
            "# 测试文章\n\n原始稿件。\n",
            encoding="utf-8",
        )

        self.old_root = ws.WRITING_ROOT
        self.old_obsidian = ws.OBSIDIAN_ROOT
        ws.WRITING_ROOT = str(self.projects)
        ws.OBSIDIAN_ROOT = str(self.obsidian)

    def tearDown(self):
        ws.WRITING_ROOT = self.old_root
        ws.OBSIDIAN_ROOT = self.old_obsidian
        self.temp_dir.cleanup()

    def ids(self):
        books = ws.discover_chapters(ws.WRITING_ROOT)
        self.assertEqual(len(books), 1)
        return books[0]["bookId"], books[0]["chapters"]

    def document_id(self):
        docs = ws.discover_documents()
        self.assertEqual(len(docs), 1)
        return docs[0]["documentId"]


class WritingStoreTest(WritingFixture, unittest.TestCase):
    def test_stable_ids_and_payload_do_not_leak_paths(self):
        book_id, chapters = self.ids()
        self.assertRegex(book_id, r"^book_[0-9a-f]{20}$")
        self.assertTrue(all(re_match(r"^chapter_[0-9a-f]{20}$", c["chapterId"])
                            for c in chapters))
        payload = ws.read_chapter(book_id, chapters[0]["chapterId"], ws.WRITING_ROOT)
        self.assertIn("原始正文", payload["content"])
        self.assertEqual(payload["obsidianUrl"].split("?", 1)[0], "obsidian://open")
        self.assertNotIn(str(self.obsidian), json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("path", payload)

        again = ws.discover_chapters(ws.WRITING_ROOT)[0]
        self.assertEqual(again["bookId"], book_id)
        self.assertEqual([c["chapterId"] for c in again["chapters"]],
                         [c["chapterId"] for c in chapters])

    def test_unwritten_chapter_opens_empty_and_symlink_is_rejected(self):
        book_id, chapters = self.ids()
        empty = ws.read_chapter(book_id, chapters[1]["chapterId"], ws.WRITING_ROOT)
        self.assertFalse(empty["written"])
        self.assertEqual(empty["content"], "")
        self.assertEqual(empty["sha256"], ws._sha256(b""))
        self.assertEqual(empty["obsidianUrl"], "")

        outside = self.tmp / "outside.md"
        outside.write_text("不应读取", encoding="utf-8")
        self.chapter.unlink()
        self.chapter.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "路径不在 Obsidian 创作目录内"):
            ws.read_chapter(book_id, chapters[0]["chapterId"], ws.WRITING_ROOT)

    def test_write_requires_matching_hash_and_creates_backup(self):
        book_id, chapters = self.ids()
        current = ws.read_chapter(book_id, chapters[0]["chapterId"], ws.WRITING_ROOT)
        backup_dir = self.tmp / "backups"
        new_text = current["content"] + "\n新增一段。\n"
        saved = ws.write_chapter(
            book_id, chapters[0]["chapterId"], new_text, current["sha256"],
            str(backup_dir), ws.WRITING_ROOT,
        )
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), new_text)
        self.assertNotEqual(saved["sha256"], current["sha256"])
        backups = list(backup_dir.glob("*.md.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), current["content"])

        with self.assertRaises(ws.WritingConflict):
            ws.write_chapter(
                book_id, chapters[0]["chapterId"], "覆盖失败", current["sha256"],
                str(backup_dir), ws.WRITING_ROOT,
            )
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), new_text)

    def test_first_save_atomically_creates_unwritten_chapter(self):
        book_id, chapters = self.ids()
        target = chapters[1]
        self.assertFalse(target["written"])
        saved = ws.write_chapter(
            book_id,
            target["chapterId"],
            "# 第二章\n\n第一次正文。\n",
            ws._sha256(b""),
            str(self.tmp / "backups"),
            ws.WRITING_ROOT,
        )
        self.assertTrue(saved["written"])
        self.assertTrue(saved["created"])
        self.assertEqual(
            (self.project / "第二章.md").read_text(encoding="utf-8"),
            "# 第二章\n\n第一次正文。\n",
        )

    def test_local_asset_only_reads_images_inside_chapter_directory(self):
        book_id, chapters = self.ids()
        chapter_id = chapters[0]["chapterId"]
        image = self.project / "cover.png"
        image.write_bytes(b"fake-png")
        ctype, data = ws.read_asset(book_id, chapter_id, "cover.png", ws.WRITING_ROOT)
        self.assertEqual(ctype, "image/png")
        self.assertEqual(data, b"fake-png")

        outside = self.tmp / "outside.png"
        outside.write_bytes(b"outside")
        with self.assertRaisesRegex(ValueError, "图片路径格式不正确"):
            ws.read_asset(book_id, chapter_id, "../outside.png", ws.WRITING_ROOT)
        image.unlink()
        image.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "图片路径不在 Obsidian 创作目录内"):
            ws.read_asset(book_id, chapter_id, "cover.png", ws.WRITING_ROOT)

    def test_document_create_read_save_conflict_and_asset(self):
        document_id = self.document_id()
        current = ws.read_document(document_id)
        self.assertIn("原始稿件", current["content"])
        self.assertEqual(current["contentType"], "公众号")

        image = self.documents / "cover.png"
        image.write_bytes(b"document-png")
        ctype, data = ws.read_document_asset(document_id, "cover.png")
        self.assertEqual(ctype, "image/png")
        self.assertEqual(data, b"document-png")

        backup_dir = self.tmp / "document-backups"
        next_text = current["content"] + "\n保存后的段落。\n"
        saved = ws.write_document(
            document_id, next_text, current["sha256"], str(backup_dir))
        self.assertEqual(self.document.read_text(encoding="utf-8"), next_text)
        self.assertNotEqual(saved["sha256"], current["sha256"])
        self.assertEqual(len(list(backup_dir.glob("*.md.bak"))), 1)
        with self.assertRaises(ws.WritingConflict):
            ws.write_document(
                document_id, "不应覆盖", current["sha256"], str(backup_dir))

        created = ws.create_document("Twitter", "一条新推文")
        self.assertEqual(created["contentType"], "Twitter")
        self.assertTrue((self.creative / "Twitter" / "一条新推文.md").is_file())
        with self.assertRaisesRegex(ValueError, "不支持"):
            ws.create_document("未知类型", "不应创建")

    def test_create_novel_uses_creative_novel_root(self):
        created = ws.create_novel("新小说")
        self.assertRegex(created["bookId"], r"^book_[0-9a-f]{20}$")
        self.assertRegex(created["chapterId"], r"^chapter_[0-9a-f]{20}$")
        self.assertTrue((self.creative / "小说" / "新小说" / "章节目录.md").is_file())
        books = ws.discover_books()
        self.assertEqual({book["bookTitle"] for book in books}, {"测试作品", "新小说"})
        empty = ws.read_chapter(created["bookId"], created["chapterId"])
        self.assertEqual(empty["content"], "")

    def test_delete_document_moves_to_obsidian_trash(self):
        document_id = self.document_id()
        result = ws.delete_document(document_id)
        self.assertTrue(result["ok"])
        self.assertFalse(self.document.exists())
        trash = self.obsidian / ".trash"
        self.assertTrue(any(p.name.startswith("测试文章") for p in trash.iterdir()))
        self.assertEqual(ws.discover_documents(), [])
        with self.assertRaises(ws.WritingNotFound):
            ws.delete_document(document_id)
        trash_items = ws.list_trash()
        self.assertEqual(len(trash_items), 1)
        self.assertEqual(trash_items[0]["kind"], "document")
        self.assertNotIn(str(self.obsidian), json.dumps(trash_items, ensure_ascii=False))
        restored = ws.restore_from_trash(trash_items[0]["name"])
        self.assertTrue(self.document.exists())
        self.assertEqual(restored["contentType"], "公众号")
        self.assertEqual(ws.list_trash(), [])
        deleted_again = ws.delete_document(self.document_id())
        # 回收站为空时删除不加时间戳；在同位置放一个同名文件，验证恢复绝不覆盖。
        (self.documents / "测试文章.md").write_text("占位，验证恢复不覆盖", encoding="utf-8")
        ws.restore_from_trash(deleted_again["trashName"])
        names = sorted(p.name for p in self.documents.glob("测试文章*"))
        self.assertTrue(any("恢复" in name for name in names))
        self.assertEqual((self.documents / "测试文章.md").read_text(encoding="utf-8"),
                         "占位，验证恢复不覆盖")

    def test_delete_novel_moves_whole_book_to_trash(self):
        created = ws.create_novel("待删小说")
        result = ws.delete_novel(created["bookId"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["chapters"], 1)
        self.assertFalse((self.creative / "小说" / "待删小说").exists())
        trash = self.obsidian / ".trash"
        self.assertTrue(any(p.name.startswith("待删小说") for p in trash.iterdir()))
        with self.assertRaises(ws.WritingNotFound):
            ws.delete_novel(created["bookId"])
        with self.assertRaises(ws.WritingNotFound):
            ws.delete_novel("chapter_" + created["bookId"].split("_")[1])
        self.assertEqual([item["kind"] for item in ws.list_trash()], ["novel"])
        restored = ws.restore_from_trash(ws.list_trash()[0]["name"])
        self.assertTrue((self.creative / "小说" / "待删小说").exists())
        self.assertEqual(restored["kind"], "novel")


class WritingApiTest(WritingFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.old_backup = app.WRITING_BACKUP_DIR
        self.old_hosts = set(app.ALLOWED_HOSTS)
        self.old_regen = app._regen_sections
        self.old_review_dir = app.REVIEW_DIR
        self.review_dir = self.obsidian / "调试经验"
        self.review_dir.mkdir(parents=True, exist_ok=True)
        (self.review_dir / "调试经验-测试问题.md").write_text(
            "---\ntype: 复盘\n---\n\n# 调试经验-测试问题\n\n正文内容，用于站内预览。\n",
            encoding="utf-8",
        )
        app.REVIEW_DIR = str(self.review_dir)
        app.WRITING_BACKUP_DIR = str(self.tmp / "api-backups")
        app._regen_sections = lambda: None

        class QuietHandler(app.Handler):
            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.port = self.server.server_address[1]
        app.ALLOWED_HOSTS = {"127.0.0.1:%d" % self.port}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        app.WRITING_BACKUP_DIR = self.old_backup
        app.ALLOWED_HOSTS = self.old_hosts
        app._regen_sections = self.old_regen
        app.REVIEW_DIR = self.old_review_dir
        super().tearDown()

    def url(self, path, token=True):
        query = ("token=" + urllib.parse.quote(app.TOKEN)) if token else ""
        if not query:
            return "http://127.0.0.1:%d%s" % (self.port, path)
        separator = "&" if "?" in path else "?"
        return "http://127.0.0.1:%d%s%s%s" % (self.port, path, separator, query)

    def request(self, path, body=None, token=True):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url(path, token=token),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_get_post_auth_validation_conflict_and_save(self):
        book_id, chapters = self.ids()
        chapter_id = chapters[0]["chapterId"]
        path = "/api/writing/chapter?bookId=%s&chapterId=%s" % (book_id, chapter_id)

        self.assertEqual(self.request(path, token=False)[0], 401)
        self.assertEqual(self.request(
            "/api/writing/chapter?bookId=../../x&chapterId=../../y")[0], 400)
        status, current = self.request(path)
        self.assertEqual(status, 200)
        self.assertIn("原始正文", current["content"])

        self.chapter.write_text(current["content"] + "\nObsidian 外部修改。\n", encoding="utf-8")
        status, conflict = self.request("/api/writing/save", {
            "bookId": book_id,
            "chapterId": chapter_id,
            "content": current["content"] + "\n工作台修改。\n",
            "baseSha256": current["sha256"],
        })
        self.assertEqual(status, 409)
        self.assertTrue(conflict["conflict"])

        status, latest = self.request(path)
        self.assertEqual(status, 200)
        saved_text = latest["content"] + "\n明确覆盖后的正文。\n"
        status, saved = self.request("/api/writing/save", {
            "bookId": book_id,
            "chapterId": chapter_id,
            "content": saved_text,
            "baseSha256": latest["sha256"],
        })
        self.assertEqual(status, 200)
        self.assertTrue(saved["ok"])
        self.assertEqual(self.chapter.read_text(encoding="utf-8"), saved_text)
        self.assertEqual(len(list(pathlib.Path(app.WRITING_BACKUP_DIR).glob("*.md.bak"))), 1)

    def test_asset_api_returns_image_without_path_escape(self):
        book_id, chapters = self.ids()
        chapter_id = chapters[0]["chapterId"]
        image = self.project / "cover.png"
        image.write_bytes(b"fake-png")
        path = "/api/writing/asset?bookId=%s&chapterId=%s&path=cover.png" % (
            book_id, chapter_id)
        status, payload = self.request(path)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(payload["data"]), b"fake-png")
        self.assertEqual(payload["contentType"], "image/png")
        self.assertNotIn(str(self.obsidian), json.dumps(payload, ensure_ascii=False))
        self.assertEqual(self.request(
            "/api/writing/asset?bookId=%s&chapterId=%s&path=../outside.png" % (
                book_id, chapter_id))[0], 400)

    def test_review_note_api_reads_by_stable_id_without_paths(self):
        note = self.review_dir / "调试经验-测试问题.md"
        note_id = ws.stable_id("note", str(note), ws.OBSIDIAN_ROOT)
        path = "/api/review/note?noteId=%s" % note_id
        self.assertEqual(self.request(path, token=False)[0], 401)
        self.assertEqual(self.request("/api/review/note?noteId=note_zzz")[0], 400)
        status, payload = self.request(path)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["title"], "调试经验-测试问题")
        self.assertIn("站内预览", payload["content"])
        self.assertNotIn(str(self.obsidian), json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("path", payload)
        self.assertEqual(self.request(
            "/api/review/note?noteId=%s" % ("note_" + "0" * 20))[0], 404)

    def test_document_and_create_api(self):
        document_id = self.document_id()
        path = "/api/writing/document?documentId=%s" % document_id
        self.assertEqual(self.request(path, token=False)[0], 401)
        status, current = self.request(path)
        self.assertEqual(status, 200)
        self.assertIn("原始稿件", current["content"])

        image = self.documents / "cover.png"
        image.write_bytes(b"document-png")
        status, payload = self.request(
            "/api/writing/document/asset?documentId=%s&path=cover.png" % document_id)
        self.assertEqual(status, 200)
        self.assertEqual(base64.b64decode(payload["data"]), b"document-png")

        status, saved = self.request("/api/writing/document/save", {
            "documentId": document_id,
            "content": current["content"] + "\nAPI 保存。\n",
            "baseSha256": current["sha256"],
        })
        self.assertEqual(status, 200)
        self.assertTrue(saved["ok"])

        status, created = self.request("/api/writing/create", {
            "kind": "document",
            "contentType": "知识星球",
            "title": "星球文档",
        })
        self.assertEqual(status, 200)
        self.assertEqual(created["contentType"], "知识星球")
        self.assertTrue((self.creative / "知识星球" / "星球文档.md").is_file())

        status, novel = self.request("/api/writing/create", {
            "kind": "novel",
            "title": "第二部小说",
        })
        self.assertEqual(status, 200)
        self.assertRegex(novel["bookId"], r"^book_[0-9a-f]{20}$")

    def test_delete_api_moves_to_trash_and_rejects_unknown_kind(self):
        document_id = self.document_id()
        status, result = self.request("/api/writing/delete", {
            "kind": "document",
            "documentId": document_id,
        })
        self.assertEqual(status, 200)
        self.assertTrue(result["ok"])
        self.assertFalse(self.document.exists())
        status, err = self.request("/api/writing/delete", {
            "kind": "document",
            "documentId": document_id,
        })
        self.assertEqual(status, 404)
        status, err = self.request("/api/writing/delete", {"kind": "book"})
        self.assertEqual(status, 400)
        self.assertIn("kind", err["error"])
        status, trash = self.request("/api/writing/trash")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["kind"] == "document" for item in trash["items"]))
        status, restored = self.request("/api/writing/restore", {
            "name": trash["items"][0]["name"],
        })
        self.assertEqual(status, 200)
        self.assertTrue(self.document.exists())
        status, err = self.request("/api/writing/restore", {"name": "../escape"})
        self.assertEqual(status, 400)


def re_match(pattern, value):
    import re
    return bool(re.fullmatch(pattern, value))


if __name__ == "__main__":
    unittest.main()
