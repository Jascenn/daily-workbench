#!/usr/bin/env python3
"""用 ntn（经 Surge 代理）调用 Notion 公共 API 的小工具。
所有调用都走 HTTPS_PROXY，因此不受 WorkBuddy/Electron 直连问题影响。
"""
import json
import os
import subprocess
import sys

NTN = "/usr/local/bin/ntn"
ENV = dict(os.environ, HTTPS_PROXY="http://127.0.0.1:7890",
           HTTP_PROXY="http://127.0.0.1:7890")


def api(path, method="POST", body=None, timeout=60):
    cmd = [NTN, "api", path, "-X", method]
    if body is not None:
        cmd += ["-d", json.dumps(body, ensure_ascii=False)]
    p = subprocess.run(cmd, capture_output=True, text=True, env=ENV, timeout=timeout)
    out = (p.stdout or "").strip()
    if not out:
        raise RuntimeError("ntn 无输出。stderr=" + (p.stderr or "")[:400])
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError("非 JSON 返回：" + out[:500])


def fetch_all(ds_id, page_size=100, cap=5000):
    rows, cursor = [], None
    while True:
        body = {"page_size": page_size}
        if cursor:
            body["start_cursor"] = cursor
        d = api("/v1/data_sources/%s/query" % ds_id, "POST", body)
        rows += d.get("results", [])
        if not d.get("has_more") or len(rows) >= cap:
            break
        cursor = d.get("next_cursor")
    return rows


def all_data_sources(cap=400):
    out, cursor = [], None
    while True:
        body = {"filter": {"value": "data_source", "property": "object"},
                "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        d = api("/v1/search", "POST", body)
        out += d.get("results", [])
        if not d.get("has_more") or len(out) >= cap:
            break
        cursor = d.get("next_cursor")
    return out


def title_of(obj):
    parts = obj.get("title") or []
    return "".join(t.get("plain_text", "") for t in parts)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    if mode == "list":
        items = all_data_sources()
        print("共 %d 个 data source" % len(items))
        for it in items:
            print("  %s | %s" % (it["id"], title_of(it)))
    elif mode == "count":
        for ds in sys.argv[2:]:
            rows = fetch_all(ds)
            print("%s -> %d 行" % (ds, len(rows)))
    elif mode == "dump":
        rows = fetch_all(sys.argv[2])
        print(json.dumps(rows, ensure_ascii=False, indent=1))
