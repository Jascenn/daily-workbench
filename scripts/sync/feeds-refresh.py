#!/usr/bin/env python3
"""刷新订阅源（rss-store.json）。

为什么需要它：服务端的 `_refresh_stale_feeds()` 只补「从没抓过 / 上次报错」的源，
对「有旧条目但很久没更新」的源不动作。结果订阅库一个月停在 10-31，
而 sections.json 每次都忠实地把这份旧数据算成"过期"。

这里按 rss-store.json 里的 url 逐个调 `/api/feeds/refresh`，失败不中断。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
STORE = ROOT / "rss-store.json"
TOKEN_FILE = Path.home() / ".config" / "daily-workbench" / "sync-token"
API = "http://127.0.0.1:8787/api/feeds/refresh"


def subscriptions() -> list[dict]:
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [s for s in (data.get("subscriptions") or []) if isinstance(s, dict)]


def main(argv: list[str]) -> int:
    limit = int(os.environ.get("FEEDS_REFRESH_LIMIT", "20"))
    token = TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else ""
    subs = [s for s in subscriptions() if not s.get("paused") and str(s.get("url") or "").startswith("http")]
    if not subs:
        print("没有可刷新的订阅源")
        return 0
    ok = fail = 0
    for sub in subs[:limit]:
        url = str(sub["url"])
        name = str(sub.get("name") or sub.get("title") or url)[:28]
        req = urllib.request.Request(
            API + (("?token=" + urllib.parse.quote(token)) if token else ""),
            data=json.dumps({"url": url}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                json.loads(resp.read().decode("utf-8") or "{}")
            ok += 1
            print("  ✓ %s" % name)
        except Exception as exc:  # noqa: BLE001
            fail += 1
            print("  ✗ %s (%s)" % (name, type(exc).__name__))
    print("订阅刷新：%d 成功 / %d 失败（共 %d 个源）" % (ok, fail, len(subs)))
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    import urllib.parse  # noqa: E402  (放在顶部也行，这里集中用)
    sys.exit(main(sys.argv[1:]))
