"""
[INPUT]: 依赖 server.runtime 绑定的入口模块命名空间与 Handler 实例
[OUTPUT]: 导出 handle_post(handler)，承载全部 POST /api/* 写操作路由（含白名单校验、锁保护与防重）
[POS]: server/ POST 路由分发子模块，由 app.py 的 Handler.do_POST 委托调用
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from urllib.parse import parse_qs, urlparse

from server.runtime import cfg as _cfg

_SOCIAL_SINGLE_LOCK = {"running": False}
_ZSXQ_DAILY_LOCK = {"running": False}
_SOCIAL_SINGLE_TARGETS = {"x", "xhs", "wechat_mp", "zsxq", "kb"}


def _fallback_wechat_reply_drafts(body: dict) -> dict:
    """无模型或离线时的结构化三口吻兜底草稿（严格只读，零副作用）。"""
    want = str(body.get("want") or "").strip()[:40]
    action = str(body.get("action") or "确认细节后再回复你").strip()[:80]
    base = str(body.get("suggestedReply") or "").strip()
    return {
        "pro": base or f"收到，关于「{want or '刚才提到的事'}」我先核对一下细节，{action}，有进展第一时间同步您。",
        "warm": (f"{base} 有什么补充随时跟我说～" if base else f"收到啦！我这边先帮你看下，{action}，晚点给你消息～"),
        "short": (base[:60] if base else f"收到，{action}。"),
    }


def _generate_wechat_reply_drafts(body: dict) -> tuple[dict, str, str]:
    """只读生成微信回应三口吻草稿：优先调用本地 Ollama，不可用时自动降级。"""
    fallback = _fallback_wechat_reply_drafts(body)
    if os.environ.get("WECHAT_REPLY_LLM_DISABLE") == "1":
        return fallback, "fallback", ""
    model = os.environ.get("WECHAT_LLM_MODEL", "qwen3:8b").strip() or "qwen3:8b"
    url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat").strip()
    prompt = (
        "你是用户的个人微信回复草稿助手。请严格只输出一个 JSON 对象，键名必须是 pro、warm、short，"
        "分别对应：pro（专业利落）、warm（温和体贴）、short（简短确认）。每条不超过 90 字，口语自然，不编造未提及的报价或承诺。\n"
        f"联系人：{str(body.get('contact') or '')[:40]}（{str(body.get('kind') or '私聊')[:10]} · {str(body.get('type') or '')[:16]}）\n"
        f"对方诉求：{str(body.get('want') or '')[:160]}\n"
        f"建议行动：{str(body.get('action') or '')[:160]}\n"
        f"原话摘录：{str(body.get('raw') or '')[:400]}"
    )
    payload = json.dumps({
        "model": model,
        "stream": False,
        "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0.4, "num_predict": 320},
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        content = str(((data.get("message") or {}).get("content")) or "")
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        m = re.search(r"\{.*\}", content, flags=re.S)
        parsed = json.loads(m.group(0)) if m else {}
        if isinstance(parsed, dict) and any(str(parsed.get(k) or "").strip() for k in ("pro", "warm", "short")):
            return {
                "pro": str(parsed.get("pro") or "").strip() or fallback["pro"],
                "warm": str(parsed.get("warm") or "").strip() or fallback["warm"],
                "short": str(parsed.get("short") or "").strip() or fallback["short"],
            }, "ollama", model
    except Exception:
        pass
    return fallback, "fallback", model


def handle_post(self):
    # 运行时动态绑定最新符号，确保单元测试对 app.<NAME> 的 monkeypatch 100% 透明生效
    CLIENT_BODY_BYTES = _cfg('CLIENT_BODY_BYTES')
    DATA = _cfg('DATA')
    DIR = _cfg('DIR')
    GUARD_MIN_EXISTING = _cfg('GUARD_MIN_EXISTING')
    GUARD_MIN_RATIO = _cfg('GUARD_MIN_RATIO')
    MAX_BODY_BYTES = _cfg('MAX_BODY_BYTES')
    MAX_WRITING_REQUEST_BYTES = _cfg('MAX_WRITING_REQUEST_BYTES')
    REQUIRED_KEYS = _cfg('REQUIRED_KEYS')
    RSS_DATA = _cfg('RSS_DATA')
    SOCIAL = _cfg('SOCIAL')
    SYNC_SCRIPT = _cfg('SYNC_SCRIPT')
    SYNC_TIMEOUT = _cfg('SYNC_TIMEOUT')
    WRITING_BACKUP_DIR = _cfg('WRITING_BACKUP_DIR')
    _PROJECT_LOCAL_KEYS = _cfg('_PROJECT_LOCAL_KEYS')
    _ZSXQ_DETAIL_CACHE = _cfg('_ZSXQ_DETAIL_CACHE')
    _ZSXQ_SYNC_LOCK = _cfg('_ZSXQ_SYNC_LOCK')
    _build_client_properties = _cfg('_build_client_properties')
    _build_focus_properties = _cfg('_build_focus_properties')
    _build_project_properties = _cfg('_build_project_properties')
    _build_sync_steps = _cfg('_build_sync_steps')
    _data_write_lock = _cfg('_data_write_lock')
    _feed_add = _cfg('_feed_add')
    _feed_delete = _cfg('_feed_delete')
    _feed_pause = _cfg('_feed_pause')
    _feed_refresh = _cfg('_feed_refresh')
    _feed_url_key = _cfg('_feed_url_key')
    _jev_call = _cfg('_jev_call')
    _jev_settings_read = _cfg('_jev_settings_read')
    _jev_settings_write = _cfg('_jev_settings_write')
    _notion_create_focus_page = _cfg('_notion_create_focus_page')
    _notion_update_page = _cfg('_notion_update_page')
    _probe_rss = _cfg('_probe_rss')
    _re = _cfg('_re')
    _read_current = _cfg('_read_current')
    _refresh_stale_feeds = _cfg('_refresh_stale_feeds')
    _regen_sections = _cfg('_regen_sections')
    _rotate_backup = _cfg('_rotate_backup')
    _rss_read = _cfg('_rss_read')
    _rss_write = _cfg('_rss_write')
    _run_sync_process = _cfg('_run_sync_process')
    _strip_sync_step_timings = _cfg('_strip_sync_step_timings')
    _sync_error_line = _cfg('_sync_error_line')
    _sync_lock = _cfg('_sync_lock')
    _sync_log_append = _cfg('_sync_log_append')
    _sync_timeout_entry = _cfg('_sync_timeout_entry')
    _write_client_update = _cfg('_write_client_update')
    _write_focus_update = _cfg('_write_focus_update')
    _write_project_update = _cfg('_write_project_update')
    _writing_write_lock = _cfg('_writing_write_lock')
    threading = _cfg('threading')
    writing_store = _cfg('writing_store')
    zsxq_compose = _cfg('zsxq_compose')
    zsxq_generate_draft = _cfg('zsxq_generate_draft')
    zsxq_log_publish = _cfg('zsxq_log_publish')
    zsxq_only_sync = _cfg('zsxq_only_sync')
    zsxq_preview_warnings = _cfg('zsxq_preview_warnings')
    zsxq_publish = _cfg('zsxq_publish')
    zsxq_publish_config = _cfg('zsxq_publish_config')
    zsxq_publish_digest = _cfg('zsxq_publish_digest')
    zsxq_publish_targets = _cfg('zsxq_publish_targets')
    zsxq_recent_publishes = _cfg('zsxq_recent_publishes')
    zsxq_set_annotation = _cfg('zsxq_set_annotation')
    zsxq_tag_line = _cfg('zsxq_tag_line')
    if not self._host_ok():
        self._send(403, {"error": "bad host"})
        return
    p = urlparse(self.path).path
    if p == "/api/wechat/action":
        # 微信写操作保持预留，不执行任何外部副作用。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        self._send(501, {
            "ok": False,
            "reserved": True,
            "readOnly": True,
            "error": "微信经营写入能力尚未启用",
            "hint": "当前只允许本地显示状态；不会发送微信，也不会写入 Notion。",
            "endpoint": p,
        })
        return
    if p == "/api/wechat/reply-draft":
        # 严格只读的回应草稿生成器：仅返回三口吻文本供前端复制，绝不发送微信。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            body = self._read_json_body(32 * 1024)
        except ValueError as e:
            self._send(400, {"ok": False, "error": str(e)})
            return
        if not isinstance(body, dict):
            self._send(400, {"ok": False, "error": "body must be a JSON object"})
            return
        drafts, source, model = _generate_wechat_reply_drafts(body)
        self._send(200, {
            "ok": True,
            "readOnly": True,
            "source": source,
            "model": model,
            "drafts": drafts,
        })
        return
    if p == "/api/zsxq/daily-sync":
        # 单飞刷新知识星球 24h 日报 + 7 天周报（约 2 分钟），失败保留旧快照。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        if _ZSXQ_DAILY_LOCK["running"]:
            self._send(409, {"ok": False, "error": "上一次星球简报刷新还没结束"})
            return
        _ZSXQ_DAILY_LOCK["running"] = True
        started = time.time()
        try:
            script = os.path.join(DIR, "scripts", "sync", "zsxq-daily-sync.py")
            proc = subprocess.run([sys.executable, script], cwd=DIR,
                                  capture_output=True, text=True, timeout=240)
            elapsed = round(time.time() - started, 1)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip()[:200] or f"exit {proc.returncode}"
                self._send(502, {"ok": False, "error": err, "elapsed": elapsed})
                return
            self._send(200, {"ok": True, "elapsed": elapsed,
                             "summary": (proc.stdout or "").strip()[:200]})
        except subprocess.TimeoutExpired:
            self._send(504, {"ok": False, "error": "星球简报刷新超时（>240s），旧快照已保留"})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
        finally:
            _ZSXQ_DAILY_LOCK["running"] = False
        return
    if p == "/api/social/sync":
        # 按平台单源快刷（走 social-sync.py --only <target>），避免每次跑 100 秒全量同步。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(min(max(length, 0), 4096)) if length > 0 else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send(400, {"ok": False, "error": "请求体不是合法 JSON"})
            return
        target = str((body or {}).get("target") or "").strip().lower()
        if target not in _SOCIAL_SINGLE_TARGETS:
            self._send(400, {"ok": False, "error": "不支持的平台参数",
                             "allowed": sorted(_SOCIAL_SINGLE_TARGETS)})
            return
        if _SOCIAL_SINGLE_LOCK["running"]:
            self._send(409, {"ok": False, "error": "上一次单源刷新还没结束"})
            return
        _SOCIAL_SINGLE_LOCK["running"] = True
        started = time.time()
        try:
            script = os.path.join(DIR, "scripts", "sync", "social-sync.py")
            proc = subprocess.run([sys.executable, script, "--only", target], cwd=DIR,
                                  capture_output=True, text=True, timeout=120)
            elapsed = round(time.time() - started, 1)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or "").strip()[:200] or f"exit {proc.returncode}"
                self._send(502, {"ok": False, "error": err, "target": target, "elapsed": elapsed})
                return
            self._send(200, {"ok": True, "target": target, "elapsed": elapsed,
                             "summary": (proc.stdout or "").strip()[:200]})
        except subprocess.TimeoutExpired:
            self._send(504, {"ok": False, "error": f"{target} 单源刷新超时（>120s）", "target": target})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])})
        finally:
            _SOCIAL_SINGLE_LOCK["running"] = False
        return
    if p == "/api/zsxq/sync":
        # 只刷星球，不重跑整条 social-sync（那条 85 秒且会抓 X）。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        if _ZSXQ_SYNC_LOCK["running"]:
            self._send(409, {"ok": False, "error": "上一次刷新还没结束"})
            return
        _ZSXQ_SYNC_LOCK["running"] = True
        try:
            result = zsxq_only_sync()
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
        finally:
            _ZSXQ_SYNC_LOCK["running"] = False
        if result.get("ok"):
            _ZSXQ_DETAIL_CACHE.clear()
        self._send(200 if result.get("ok") else 502, result)
        return
    if p == "/api/zsxq/draft":
        # 只生成草稿：不写星球、不落日志，返回文本由人或改或弃。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send(400, {"error": "bad length"})
            return
        if length <= 0 or length > 8192:
            self._send(413, {"error": "bad body size"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": "invalid json: %s" % str(exc)[:160]})
            return
        seed = str((body or {}).get("seed") or "").strip()[:300]
        ok, data = zsxq_generate_draft(seed)
        if not ok:
            self._send(200, dict({"ok": False}, **data))
            return
        self._send(200, dict({"ok": True, "readOnly": True}, **data))
        return

    if p in ("/api/zsxq/preview", "/api/zsxq/publish"):
        # 发帖是**公开写入**：预览与发布分离，发布必须 confirm + 与预览一致的 digest。
        # 支持一次发到多个星球：每个目标各自算 digest（标签 hid 各星球不同），
        # 逐条发布、逐条回报，部分失败不回滚已成功的。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send(400, {"error": "bad length"})
            return
        if length <= 0 or length > 65536:
            self._send(413, {"error": "bad body size"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": "invalid json: %s" % str(exc)[:160]})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "invalid body"})
            return
        cfg = zsxq_publish_config()
        targets = zsxq_publish_targets(cfg)
        if not targets:
            self._send(409, {"ok": False,
                             "error": "没有配置可发布星球（~/.config/daily-workbench/zsxq-publish.json）"})
            return
        allowed = {t["groupId"]: t["groupName"] for t in targets}
        ai_mode = str(body.get("aiMode") or "aigc")
        tags_in = list(body.get("tags") or [])
        if ai_mode == "aigc" and "bot" not in [str(t).lstrip("#") for t in tags_in]:
            tags_in.append("bot")
        body_text = zsxq_compose(body.get("text") or "", [], ai_mode)

        wanted: list[str] = []
        for raw in (body.get("groupIds") or []):
            gid = str(raw or "").strip()
            if gid and gid not in wanted:
                wanted.append(gid)
        if not wanted:
            wanted = [targets[0]["groupId"]]
        unknown = [g for g in wanted if g not in allowed]
        if unknown:
            self._send(403, {"ok": False,
                             "error": "星球不在可发布白名单：%s" % "、".join(unknown)})
            return

        planned = []
        for gid in wanted:
            tag_line = zsxq_tag_line(tags_in, gid)
            final = (tag_line + "\n" + body_text).strip() if tag_line else body_text
            planned.append({"groupId": gid, "groupName": allowed[gid], "finalText": final,
                            "digest": zsxq_publish_digest(gid, final, ai_mode)})
        first = planned[0]

        if p.endswith("/preview"):
            self._send(200, {
                "ok": True, "readOnly": True,
                "groupId": first["groupId"], "groupName": first["groupName"],
                "aiMode": ai_mode, "finalText": first["finalText"],
                "chars": len(first["finalText"]), "digest": first["digest"],
                "warnings": zsxq_preview_warnings(first["finalText"], tags_in, ai_mode),
                "targets": [{"groupId": t["groupId"], "groupName": t["groupName"],
                             "digest": t["digest"]} for t in planned],
            })
            return

        if body.get("confirm") is not True:
            self._send(400, {"ok": False, "error": "发布需要 confirm=true"})
            return
        digests_in = body.get("digests") if isinstance(body.get("digests"), dict) else {}
        legacy_digest = str(body.get("digest") or "")
        results = []
        for t in planned:
            want = str(digests_in.get(t["groupId"]) or "")
            if not want and len(planned) == 1:
                want = legacy_digest          # 兼容单目标的老调用
            if want != t["digest"]:
                results.append({"groupId": t["groupId"], "groupName": t["groupName"],
                                "ok": False, "error": "内容与预览不一致，请重新预览再确认"})
                continue
            if any(r.get("digest") == t["digest"] for r in zsxq_recent_publishes(30)):
                results.append({"groupId": t["groupId"], "groupName": t["groupName"],
                                "ok": False, "error": "30 分钟内已发过同样内容，拒绝重复发布"})
                continue
            res = zsxq_publish(t["groupId"], t["finalText"], ai_mode)
            if res.get("ok") and res.get("topicId"):
                tagged = zsxq_set_annotation(res["topicId"], tags_in)
                res["tagsSet"] = bool(tagged.get("ok"))
                if not tagged.get("ok"):
                    res["tagError"] = tagged.get("error", "")
            zsxq_log_publish({"groupId": t["groupId"], "digest": t["digest"],
                              "aiMode": ai_mode, "chars": len(t["finalText"]),
                              "ok": bool(res.get("ok")), "topicId": res.get("topicId"),
                              "error": res.get("error", "")})
            results.append({"groupId": t["groupId"], "groupName": t["groupName"],
                            "ok": bool(res.get("ok")), "topicId": res.get("topicId"),
                            "error": res.get("error", ""), "tagsSet": res.get("tagsSet")})
        ok_all = bool(results) and all(r["ok"] for r in results)
        ok_any = any(r["ok"] for r in results)
        if ok_any:
            _ZSXQ_DETAIL_CACHE.clear()
        self._send(200 if ok_all else (207 if ok_any else 502), {
            "ok": ok_all,
            "partial": ok_any and not ok_all,
            "topicId": results[0].get("topicId") if results else None,
            "error": "" if ok_all else (results[0].get("error", "") if results else ""),
            "digest": first["digest"],
            "results": results,
        })
        return
    if p == "/api/writing/save":
        # 正文写回 Obsidian 的唯一入口。baseSha256 不匹配时返回 409，
        # 由页面让用户选择重新加载，避免静默覆盖外部编辑。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send(400, {"error": "bad length"})
            return
        if length <= 0 or length > MAX_WRITING_REQUEST_BYTES:
            self._send(413, {
                "error": "bad body size",
                "limit": MAX_WRITING_REQUEST_BYTES,
            })
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": "invalid json: %s" % str(exc)[:160]})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "invalid body"})
            return
        book_id = str(body.get("bookId") or "").strip()
        chapter_id = str(body.get("chapterId") or "").strip()
        if not _re.fullmatch(r"book_[0-9a-f]{20}", book_id) or \
                not _re.fullmatch(r"chapter_[0-9a-f]{20}", chapter_id):
            self._send(400, {"error": "bookId 或 chapterId 格式不正确"})
            return
        try:
            with _writing_write_lock:
                payload = writing_store.write_chapter(
                    book_id,
                    chapter_id,
                    body.get("content"),
                    str(body.get("baseSha256") or "").strip(),
                    WRITING_BACKUP_DIR,
                )
            _regen_sections()
            self._send(200, {"ok": True, **payload})
        except writing_store.WritingConflict as exc:
            self._send(409, {
                "error": str(exc),
                "conflict": True,
                "hint": "重新加载最新正文，确认后再保存；不要直接覆盖。",
            })
        except writing_store.WritingNotFound as exc:
            self._send(404, {"error": str(exc)})
        except ValueError as exc:
            code = 413 if "2 MB" in str(exc) else 400
            self._send(code, {"error": str(exc)})
        except OSError as exc:
            self._send(500, {"error": "保存章节失败：%s" % str(exc)[:240]})
        return
    if p == "/api/writing/document/save":
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send(400, {"error": "bad length"})
            return
        if length <= 0 or length > MAX_WRITING_REQUEST_BYTES:
            self._send(413, {
                "error": "bad body size",
                "limit": MAX_WRITING_REQUEST_BYTES,
            })
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": "invalid json: %s" % str(exc)[:160]})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "invalid body"})
            return
        document_id = str(body.get("documentId") or "").strip()
        if not _re.fullmatch(r"doc_[0-9a-f]{20}", document_id):
            self._send(400, {"error": "documentId 格式不正确"})
            return
        try:
            with _writing_write_lock:
                payload = writing_store.write_document(
                    document_id,
                    body.get("content"),
                    str(body.get("baseSha256") or "").strip(),
                    WRITING_BACKUP_DIR,
                )
            _regen_sections()
            self._send(200, {"ok": True, **payload})
        except writing_store.WritingConflict as exc:
            self._send(409, {
                "error": str(exc),
                "conflict": True,
                "hint": "重新加载最新正文，确认后再保存；不要直接覆盖。",
            })
        except writing_store.WritingNotFound as exc:
            self._send(404, {"error": str(exc)})
        except ValueError as exc:
            code = 413 if "2 MB" in str(exc) else 400
            self._send(code, {"error": str(exc)})
        except OSError as exc:
            self._send(500, {"error": "保存稿件失败：%s" % str(exc)[:240]})
        return
    if p == "/api/writing/create":
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send(400, {"error": "bad length"})
            return
        if length <= 0 or length > 64 * 1024:
            self._send(413, {"error": "bad body size"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": "invalid json: %s" % str(exc)[:160]})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "invalid body"})
            return
        kind = str(body.get("kind") or "document").strip()
        title = str(body.get("title") or "").strip()
        try:
            with _writing_write_lock:
                if kind == "novel":
                    payload = writing_store.create_novel(title)
                elif kind == "document":
                    payload = writing_store.create_document(
                        str(body.get("contentType") or "").strip(), title)
                else:
                    raise ValueError("kind 必须是 novel 或 document")
            _regen_sections()
            self._send(200, {"ok": True, **payload})
        except FileExistsError:
            self._send(409, {"error": "同名内容已存在"})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except OSError as exc:
            self._send(500, {"error": "创建内容失败：%s" % str(exc)[:240]})
        return
    if p == "/api/writing/delete":
        # 删除不是物理删除：文件整体移入 Obsidian 回收站，可在 Obsidian 内恢复。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send(400, {"error": "bad length"})
            return
        if length <= 0 or length > 4 * 1024:
            self._send(413, {"error": "bad body size"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": "invalid json: %s" % str(exc)[:160]})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "invalid body"})
            return
        kind = str(body.get("kind") or "").strip()
        try:
            with _writing_write_lock:
                if kind == "document":
                    payload = writing_store.delete_document(
                        str(body.get("documentId") or "").strip())
                elif kind == "novel":
                    payload = writing_store.delete_novel(
                        str(body.get("bookId") or "").strip())
                else:
                    raise ValueError("kind 必须是 document 或 novel")
            _regen_sections()
            self._send(200, {"ok": True, **payload})
        except writing_store.WritingNotFound as exc:
            self._send(404, {"error": str(exc)})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except OSError as exc:
            self._send(500, {"error": "删除失败：%s" % str(exc)[:240]})
        return
    if p == "/api/writing/restore":
        # 恢复 = 从回收站移回原位置；目标重名时加后缀，绝不覆盖现有文件。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send(400, {"error": "bad length"})
            return
        if length <= 0 or length > 4 * 1024:
            self._send(413, {"error": "bad body size"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._send(400, {"error": "invalid json: %s" % str(exc)[:160]})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "invalid body"})
            return
        try:
            with _writing_write_lock:
                payload = writing_store.restore_from_trash(
                    str(body.get("name") or "").strip())
            _regen_sections()
            self._send(200, {"ok": True, **payload})
        except writing_store.WritingNotFound as exc:
            self._send(404, {"error": str(exc)})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
        except OSError as exc:
            self._send(500, {"error": "恢复失败：%s" % str(exc)[:240]})
        return
    if p == "/api/weekly":
        # 周报生成：跑 scripts/sync/weekly-report.py 重新生成本周 Markdown，
        # 落盘 reports/ 后返回最新一份全文。脚本只读本地快照、只写 reports/。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        script = os.path.join(DIR, "scripts", "sync", "weekly-report.py")
        if not os.path.exists(script):
            self._send(500, {"error": "weekly-report script missing", "path": script})
            return
        try:
            proc = subprocess.run(
                [sys.executable, script],
                cwd=DIR, capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            self._send(504, {"error": "周报生成超时（30s）"})
            return
        if proc.returncode != 0:
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            self._send(500, {"error": out[-1000:] or "周报生成失败"})
            return
        reports_dir = os.path.join(DIR, "reports")
        candidates = sorted(
            f for f in os.listdir(reports_dir)
            if f.startswith("weekly-") and f.endswith(".md")
        ) if os.path.isdir(reports_dir) else []
        if not candidates:
            self._send(500, {"error": "脚本执行成功但没有产出周报文件"})
            return
        latest = os.path.join(reports_dir, candidates[-1])
        try:
            with open(latest, encoding="utf-8") as f:
                content = f.read()
        except FileNotFoundError:
            self._send(404, {"error": "周报文件不存在"})
            return
        self._send(200, {"ok": True, "path": os.path.basename(latest),
                         "content": content})
        return
    if p in ("/data.json", "/api/data"):
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send(400, {"error": "bad length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send(413, {"error": "bad body size", "limit": MAX_BODY_BYTES})
            return
        raw = self.rfile.read(length)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._send(400, {"error": str(e)})
            return
        if not isinstance(obj, dict) or not all(k in obj for k in REQUIRED_KEYS):
            self._send(400, {"error": "invalid schema"})
            return
        # Guard: refuse a write that would wipe out most of the data.
        prev = _read_current()
        if prev is not None and not self._force():
            for key in REQUIRED_KEYS:
                before = prev.get(key)
                after = obj.get(key)
                before = len(before) if isinstance(before, list) else 0
                after = len(after) if isinstance(after, list) else 0
                if before > GUARD_MIN_EXISTING and after < before * GUARD_MIN_RATIO:
                    self._send(409, {
                        "error": "refused: record count would drop sharply",
                        "field": key, "before": before, "after": after,
                        "hint": "append ?force=1 to the request URL to override",
                    })
                    return
        # 写入互斥：并发保存曾共用同一个 data.json.tmp，第一个 os.replace 把
        # 临时文件消费掉后第二个直接 FileNotFoundError（浏览器侧表现为
        # 「保存失败：连不上同步服务」）。读-备-写整体持锁，临时文件再加
        # 随机后缀双保险。
        with _data_write_lock:
            _rotate_backup()
            tmp = "%s.tmp.%d" % (DATA, threading.get_ident())
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
            os.replace(tmp, DATA)
        self._send(200, {"ok": True, "earnings": len(obj.get("earnings", [])),
                         "projects": len(obj.get("projects", []))})
        return
    if p == "/api/focus/push":
        # 单条专注记录回写：必须显式点击、只写已完成记录、同一本地 ID 只创建一次。
        # 与同步/客户回写共用 _sync_lock，避免回写期间发生全量拉取覆盖。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send(400, {"error": "bad length"})
            return
        if length <= 0 or length > CLIENT_BODY_BYTES:
            self._send(413, {"error": "bad body size", "limit": CLIENT_BODY_BYTES})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            self._send(400, {"error": str(e)})
            return
        session_id = str((body or {}).get("sessionId") or "").strip()
        if not _re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id):
            self._send(400, {"error": "sessionId 格式不正确"})
            return
        prev = _read_current() or {}
        session = next((s for s in (prev.get("focusSessions") or [])
                        if str(s.get("id") or "") == session_id), None)
        if not session:
            self._send(404, {"error": "专注记录不存在，或尚未保存到 data.json"})
            return
        if session.get("notionPageId"):
            self._send(200, {
                "ok": True,
                "alreadyPushed": True,
                "session": session,
                "notionPageId": session.get("notionPageId"),
                "notionUrl": session.get("notionUrl") or "",
            })
            return
        try:
            properties = _build_focus_properties(session)
        except ValueError as e:
            self._send(400, {"error": str(e)})
            return
        if not _sync_lock.acquire(blocking=False):
            self._send(409, {"error": "同步正在进行，请稍后再回写"})
            return
        try:
            notion_page = _notion_create_focus_page(properties)
            try:
                updated = _write_focus_update(session_id, notion_page)
                local_updated = True
            except Exception:
                updated = dict(session)
                updated["notionPageId"] = notion_page.get("id")
                updated["notionUrl"] = notion_page.get("url") or ""
                local_updated = False
            self._send(200, {
                "ok": True,
                "session": updated,
                "localUpdated": local_updated,
                "notionPageId": notion_page.get("id"),
                "notionUrl": notion_page.get("url") or "",
            })
        except subprocess.TimeoutExpired:
            self._send(504, {"error": "Notion 回写超时（45s）"})
        except Exception as e:
            self._send(502, {"error": str(e)[:1200]})
        finally:
            _sync_lock.release()
        return
    if p == "/api/projects/update":
        # 项目回写：只允许修改 data.json 中已经存在的 Notion 项目页。
        # 可写字段与类型由 Notion schema 现场校验，rollup / 公式不可写。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send(400, {"error": "bad length"})
            return
        if length <= 0 or length > CLIENT_BODY_BYTES:
            self._send(413, {"error": "bad body size", "limit": CLIENT_BODY_BYTES})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            self._send(400, {"error": str(e)})
            return
        page_id = str((body or {}).get("pageId") or "").strip().lower()
        fields = (body or {}).get("fields")
        if not _re.fullmatch(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", page_id):
            self._send(400, {"error": "pageId 格式不正确"})
            return
        prev = _read_current() or {}
        projects = prev.get("projects") or []
        project = next((p for p in projects
                        if str(p.get("notionId") or "").lower() == page_id), None)
        if not project:
            self._send(404, {"error": "项目不存在，或尚未同步到本地"})
            return
        if isinstance(fields, dict) and fields.get("parentNotionId"):
            parent_id = str(fields.get("parentNotionId") or "").strip().lower()
            if parent_id == page_id:
                self._send(400, {"error": "项目不能把自己设为上级项目"})
                return
            if not any(str(p.get("notionId") or "").lower() == parent_id for p in projects):
                self._send(400, {"error": "上级项目不在当前同步结果中"})
                return
        try:
            properties = _build_project_properties(fields)
        except subprocess.TimeoutExpired:
            self._send(504, {"error": "读取 Notion 项目 schema 超时（45s）"})
            return
        except ValueError as e:
            self._send(400, {"error": str(e)})
            return
        except Exception as e:
            self._send(502, {"error": str(e)[:1200]})
            return
        if not _sync_lock.acquire(blocking=False):
            self._send(409, {"error": "同步正在进行，请稍后再回写"})
            return
        try:
            notion_page = _notion_update_page(page_id, properties)
            try:
                updated = _write_project_update(page_id, fields)
                local_updated = True
            except Exception:
                updated = dict(project)
                for key, local_key in _PROJECT_LOCAL_KEYS.items():
                    if key in fields and key != "parentNotionId":
                        updated[local_key] = fields.get(key)
                local_updated = False
            self._send(200, {
                "ok": True,
                "pageId": page_id,
                "project": updated,
                "localUpdated": local_updated,
                "notionPageId": notion_page.get("id") or page_id,
            })
        except subprocess.TimeoutExpired:
            self._send(504, {"error": "Notion 项目回写超时（45s）"})
        except Exception as e:
            self._send(502, {"error": str(e)[:1200]})
        finally:
            _sync_lock.release()
        return
    if p == "/api/clients/update":
        # 客户资料回写：只允许改 data.json 里已经存在的客户页，且字段走白名单。
        # 与同步共用 _sync_lock，避免一边 PATCH Notion、一边全量拉取造成本地覆盖。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send(400, {"error": "bad length"})
            return
        if length <= 0 or length > CLIENT_BODY_BYTES:
            self._send(413, {"error": "bad body size", "limit": CLIENT_BODY_BYTES})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            self._send(400, {"error": str(e)})
            return
        page_id = str((body or {}).get("pageId") or "").strip().lower()
        fields = (body or {}).get("fields")
        if not _re.fullmatch(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", page_id):
            self._send(400, {"error": "pageId 格式不正确"})
            return
        prev = _read_current()
        client = next((c for c in ((prev or {}).get("clients") or [])
                       if str(c.get("notionId") or "").lower() == page_id), None)
        if not client:
            self._send(404, {"error": "客户不存在，或尚未同步到本地"})
            return
        try:
            properties = _build_client_properties(fields)
        except ValueError as e:
            self._send(400, {"error": str(e)})
            return
        if not _sync_lock.acquire(blocking=False):
            self._send(409, {"error": "同步正在进行，请稍后再回写"})
            return
        try:
            notion_page = _notion_update_page(page_id, properties)
            try:
                updated = _write_client_update(page_id, fields)
                local_updated = True
            except Exception:
                updated = dict(client)
                updated.update({k: ("" if fields.get(k) is None else str(fields.get(k)))
                                for k in fields})
                local_updated = False
            self._send(200, {
                "ok": True,
                "pageId": page_id,
                "client": updated,
                "localUpdated": local_updated,
                "notionPageId": notion_page.get("id") or page_id,
            })
        except subprocess.TimeoutExpired:
            self._send(504, {"error": "Notion 回写超时（45s）"})
        except Exception as e:
            self._send(502, {"error": str(e)[:1200]})
        finally:
            _sync_lock.release()
        return
    if p == "/api/notes/push":
        # 把「记一笔」的本地草稿（source:"local"）真正写进 Notion「副业收入」库。
        # 复用 notion-push-drafts.py --confirm：它只碰 source=="local" 的记录，
        # Notion 拉来的只读条目绝不动；写入成功后本地自动转为「已入 Notion」。
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        script = os.path.join(DIR, "scripts", "sync", "notion-push-drafts.py")
        if not os.path.exists(script):
            self._send(500, {"error": "push script missing", "path": script})
            return
        try:
            proc = subprocess.run(
                [sys.executable, script, "--confirm"],
                cwd=DIR, capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            self._send(504, {"error": "推送超时（300s）"})
            return
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        self._send(200 if proc.returncode == 0 else 500, {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "output": out[-4000:],
        })
        return
    if p == "/api/sync":
        qs = parse_qs(urlparse(self.path).query)
        trigger = qs.get("src", ["manual"])[0]
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        if not os.path.exists(SYNC_SCRIPT):
            self._send(500, {"error": "sync script missing", "path": SYNC_SCRIPT})
            return
        if not _sync_lock.acquire(blocking=False):
            self._send(409, {"error": "已有一次同步正在进行，请稍候"})
            return
        try:
            # 同步前兜底：服务端代抓插件拿不到的源（直连失败走代理），失败不影响同步
            try:
                _refresh_stale_feeds()
            except Exception:  # noqa: BLE001
                pass
            started = time.time()
            try:
                proc = _run_sync_process()
            except subprocess.TimeoutExpired:
                _sync_log_append(_sync_timeout_entry(started, trigger))
                self._send(504, {"error": "同步超时（%ds）" % SYNC_TIMEOUT})
                return
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            visible_out = _strip_sync_step_timings(out)
            state = _read_current() or {}
            ok = proc.returncode == 0
            steps = _build_sync_steps(proc.returncode, out, state)
            # 先落日志再发响应：浏览器超时断开时 _send 会抛 BrokenPipeError，
            # 若把它放在后面，真实已完成的同步会从历史里消失（2026-10-29 实测）。
            _sync_log_append({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "ok": ok,
                "code": proc.returncode,
                "elapsed": round(time.time() - started, 1),
                "earnings": len(state.get("earnings", [])),
                "projects": len(state.get("projects", [])),
                "error": "" if ok else _sync_error_line(visible_out, proc.returncode),
                "trigger": trigger if trigger in ("manual", "auto") else "manual",
                "steps": steps,
            })
            self._send(200 if ok else 500, {
                "ok": proc.returncode == 0,
                "code": proc.returncode,
                "elapsed": round(time.time() - started, 1),
                "output": visible_out[-4000:],
                "earnings": len(state.get("earnings", [])),
                "projects": len(state.get("projects", [])),
                "lastNotionSync": state.get("lastNotionSync"),
            })
        finally:
            _sync_lock.release()
        return
    if p in ("/api/feeds/probe", "/api/feeds/add", "/api/feeds/refresh",
             "/api/feeds/pause", "/api/feeds/delete"):
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            self._send(400, {"error": "bad length"})
            return
        if length <= 0 or length > 200_000:
            self._send(413, {"error": "bad body size"})
            return
        try:
            obj = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            self._send(400, {"error": str(e)})
            return
        if not isinstance(obj, dict):
            self._send(400, {"error": "invalid body"})
            return
        if p == "/api/feeds/probe":
            url = str(obj.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                self._send(400, {"error": "url must start with http(s)://"})
                return
            hits = _probe_rss(url)
            current = _rss_read() or {}
            by_key = {
                _feed_url_key(s.get("url")): s.get("name") or ""
                for s in (current.get("subscriptions") or [])
            }
            for h in hits:
                h["existing"] = _feed_url_key(h.get("url")) in by_key
                h["existingName"] = by_key.get(_feed_url_key(h.get("url")), "")
            self._send(200, {"hits": hits})
            return
        d = _rss_read()
        if not isinstance(d, dict):
            self._send(404, {"error": "RSS data file not found",
                             "hint": RSS_DATA})
            return
        if p == "/api/feeds/refresh":
            url = str(obj.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                self._send(400, {"error": "url must start with http(s)://"})
                return
            try:
                result = _feed_refresh(d, url)
            except ValueError as e:
                self._send(400, {"error": str(e)})
                return
            except Exception as e:  # noqa: BLE001
                # 抓取失败也要落 lastAttemptAt/error，别让源一直停在「未尝试」
                for s in (d.get("subscriptions") or []):
                    if _feed_url_key(s.get("url")) == _feed_url_key(url):
                        s["lastAttemptAt"] = int(time.time() * 1000)
                        s["lastErrorAt"] = int(time.time() * 1000)
                        s["errorCount"] = int(s.get("errorCount") or 0) + 1
                        s["error"] = str(e)[:300]
                        break
                _rss_write(d)
                self._send(502, {"ok": False, "error": str(e)[:300]})
                return
            _rss_write(d)
            _regen_sections()
            self._send(200, {"ok": True, **result})
            return
        if p == "/api/feeds/add":
            url = str(obj.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                self._send(400, {"error": "url must start with http(s)://"})
                return
            try:
                name = _feed_add(d, url, str(obj.get("title") or "").strip(),
                                 str(obj.get("group") or "").strip())
            except ValueError as e:
                self._send(409, {"error": str(e)})
                return
            _rss_write(d)
            _regen_sections()
            self._send(200, {"ok": True, "name": name})
            return
        if p == "/api/feeds/pause":
            url = str(obj.get("url") or "").strip()
            if "paused" not in obj:
                self._send(400, {"error": "missing paused"})
                return
            feed = _feed_pause(d, url, bool(obj.get("paused")))
            if feed is None:
                self._send(404, {"error": "没有找到这个订阅源"})
                return
            _rss_write(d)
            _regen_sections()
            self._send(200, {"ok": True, "paused": bool(feed.get("paused")),
                             "name": feed.get("name") or url})
            return
        # /api/feeds/delete
        url = str(obj.get("url") or "").strip()
        if obj.get("purge") is not True:
            self._send(400, {"error": "永久删除需要明确传入 purge=true；日常停用请用 /api/feeds/pause"})
            return
        removed = _feed_delete(d, url)
        if removed is None:
            self._send(404, {"error": "没有找到这个订阅源"})
            return
        _rss_write(d)
        _regen_sections()
        self._send(200, {"ok": True, **removed})
        return
    if p in ("/api/jev/toggle", "/api/jev/analyze"):
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        obj = {}
        if length:
            try:
                obj = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                obj = {}
        if not isinstance(obj, dict):
            obj = {}
        cfg = _jev_settings_read()
        if p == "/api/jev/toggle":
            cfg["enabled"] = bool(obj.get("enabled"))
            _jev_settings_write(cfg)
            self._send(200, {"ok": True, "enabled": cfg["enabled"]})
            return
        # /api/jev/analyze：把 social.json 的内容信号喂给 Jev 评质量，
        # 结果缓存进 jev-settings.json（每次分析覆盖上一次）。
        if not cfg.get("enabled"):
            self._send(409, {"error": "Jev 增强未开启",
                             "hint": "先在更多菜单里打开 Jev 增强"})
            return
        try:
            with open(SOCIAL, encoding="utf-8") as f:
                social = json.load(f)
        except (OSError, ValueError):
            self._send(404, {"error": "没有 social.json 快照"})
            return
        posts = social.get("posts") or []
        if not posts:
            self._send(404, {"error": "快照里没有逐条内容"})
            return
        results = []
        for post in posts[:8]:  # 最多 8 条，控制探测成本
            title = str(post.get("title") or "")[:120]
            # 结合真实表现数据：质量分要与阅读/互动对照，才回答「写得好但没人看」还是「内容本身弱」。
            m = post.get("metrics") or {}
            perf_bits = []
            if isinstance(m.get("reads"), (int, float)):
                perf_bits.append(f"阅读 {int(m['reads'])}")
            if isinstance(m.get("likes"), (int, float)) and m["likes"]:
                perf_bits.append(f"赞 {int(m['likes'])}")
            if isinstance(m.get("comments"), (int, float)) and m["comments"]:
                perf_bits.append(f"评论 {int(m['comments'])}")
            if isinstance(m.get("shares"), (int, float)) and m["shares"]:
                perf_bits.append(f"分享 {int(m['shares'])}")
            perf = "；真实表现：" + "、".join(perf_bits) if perf_bits else ""
            text = title + perf
            ok, data = _jev_call(text)
            if not ok:
                results.append({"title": title, "error": data.get("error")})
                break
            ans = ((data.get("answers") or {}).get("quality") or {})
            results.append({
                "title": title, "url": post.get("url"),
                "platform": post.get("platform"),
                "score": ans.get("score"),
                "confidence": ans.get("confidence"),
                "reason": ans.get("reason") or ans.get("explanation"),
                "usage": data.get("usage"),
                "metrics": {k: m.get(k) for k in ("reads", "likes", "comments", "shares") if m.get(k) is not None},
            })
        ok_any = any("error" not in r for r in results)
        if not ok_any:
            self._send(502, {"error": results[0].get("error") or "Jev 调用失败"})
            return
        cfg["lastAnalysis"] = results
        cfg["analyzedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        _jev_settings_write(cfg)
        self._send(200, {"ok": True, "results": results,
                         "analyzedAt": cfg["analyzedAt"]})
        return
    self._send(404, {"error": "not found"})

