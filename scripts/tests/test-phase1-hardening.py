#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 1 回归测试：HttpOnly Cookie 鉴权、rss-store 废弃字段裁剪与 social-sync --only 参数校验。"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
spec = importlib.util.spec_from_file_location("wb_app_p1", os.path.join(ROOT, "app.py"))
app = importlib.util.module_from_spec(spec)
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


class _DummyHandler:
    _cookie_token = app.Handler._cookie_token
    _authorized = app.Handler._authorized

    def __init__(self, path, headers):
        self.path = path
        self.headers = headers


print("被测：Phase 1 安全边界、存储瘦身与单源同步参数")
tok = app.TOKEN
check("合法 wb_session Cookie 鉴权通过", _DummyHandler("/data.json", {"Cookie": f"wb_session={tok}"})._authorized())
check("非法 wb_session Cookie 被拒绝", not _DummyHandler("/data.json", {"Cookie": "wb_session=invalid"})._authorized())
check("Bearer Header 仍兼容通过", _DummyHandler("/data.json", {"Authorization": f"Bearer {tok}"})._authorized())
check("Query ?token= 仍兼容通过", _DummyHandler(f"/data.json?token={tok}", {})._authorized())
check("无凭证请求被拒绝", not _DummyHandler("/data.json", {})._authorized())

# _rss_write 应裁剪历史废弃的 channelStates，保留业务键并触发轮转
orig_rss, orig_bak, orig_keep = app.RSS_DATA, app.BACKUP_DIR, app.KEEP_RSS_BACKUPS
try:
    with tempfile.TemporaryDirectory() as td:
        app.RSS_DATA = os.path.join(td, "rss-store.json")
        app.BACKUP_DIR = os.path.join(td, "backups")
        app.KEEP_RSS_BACKUPS = 3
        sample = {"entries": [{"id": "e1"}], "subscriptions": [], "channelStates": {"old": [1, 2, 3]}}
        for _ in range(5):
            app._rss_write(sample)
        saved = json.loads(open(app.RSS_DATA, encoding="utf-8").read())
        baks = [f for f in os.listdir(app.BACKUP_DIR) if f.startswith("rss-store.")]
        check("_rss_write 自动裁剪废弃 channelStates", "channelStates" not in saved and saved["entries"] == [{"id": "e1"}])
        check("_rss_write 不修改调用方原始 dict", "channelStates" in sample)
        check("_rss_write 备份轮转不超过 KEEP_RSS_BACKUPS", len(baks) <= 3, f"baks={len(baks)}")
finally:
    app.RSS_DATA, app.BACKUP_DIR, app.KEEP_RSS_BACKUPS = orig_rss, orig_bak, orig_keep

# social-sync.py --only 非法目标应明确拒绝并返回 2
res = subprocess.run(
    [sys.executable, os.path.join(ROOT, "scripts", "sync", "social-sync.py"), "--only", "unknown_plat"],
    capture_output=True, text=True, timeout=15,
)
check("social-sync.py --only 拒绝非法平台名", res.returncode == 2 and "unknown --only" in res.stderr, res.stderr.strip())

# 验证 POST 路由分发（routes_post.handle_post）无局部变量遮蔽且鉴权生效
import io


class _FakePostHandler(app.Handler):
    def __init__(self, path, headers=None, body=b""):
        self.command = "POST"
        self.path = path
        self.headers = {"Host": "127.0.0.1:8787", **(headers or {})}
        if body and "Content-Length" not in self.headers:
            self.headers["Content-Length"] = str(len(body))
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status_code = None

    def send_response(self, code, message=None):
        self.status_code = code

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass


_h_unauth = _FakePostHandler("/api/sync", {}, b"{}")
_h_unauth.do_POST()
check("POST /api/sync 未鉴权返回 401 且不抛 UnboundLocalError", _h_unauth.status_code == 401, _h_unauth.status_code)

_h_preview = _FakePostHandler("/api/zsxq/preview", {"Authorization": f"Bearer {tok}"}, b"{}")
_h_preview.do_POST()
check("POST /api/zsxq/preview 鉴权后正常执行返回 200", _h_preview.status_code == 200, _h_preview.status_code)

_h_social_unauth = _FakePostHandler("/api/social/sync", {}, b'{"target":"x"}')
_h_social_unauth.do_POST()
check("POST /api/social/sync 未鉴权返回 401", _h_social_unauth.status_code == 401, _h_social_unauth.status_code)

_h_social_bad = _FakePostHandler("/api/social/sync", {"Authorization": f"Bearer {tok}"}, b'{"target":"rm -rf"}')
_h_social_bad.do_POST()
check("POST /api/social/sync 拒绝非法平台参数（防命令注入）", _h_social_bad.status_code == 400, _h_social_bad.status_code)

_h_daily_unauth = _FakePostHandler("/api/zsxq/daily-sync", {}, b"{}")
_h_daily_unauth.do_POST()
check("POST /api/zsxq/daily-sync 未鉴权返回 401", _h_daily_unauth.status_code == 401, _h_daily_unauth.status_code)

print("\n结果：%d 通过 / %d 失败" % (passed, failed))
raise SystemExit(1 if failed else 0)
