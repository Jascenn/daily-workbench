#!/bin/bash
# 用 ntn（走 Surge 代理）查一个 Notion data source 的结构与行数。
# 用法: bash probe-ds.sh <data_source_id> [page_size]
export HTTPS_PROXY=http://127.0.0.1:7890
export HTTP_PROXY=http://127.0.0.1:7890
NTN=/usr/local/bin/ntn
PY=/opt/homebrew/bin/python3

DS="$1"
SIZE="${2:-3}"
[ -z "$DS" ] && { echo "用法: bash probe-ds.sh <data_source_id> [page_size]"; exit 1; }

echo "=== data_source: $DS ==="
"$NTN" api "/v1/data_sources/$DS/query" -X POST -d "{\"page_size\":$SIZE}" 2>&1 \
  | "$PY" -c '
import sys, json
raw = sys.stdin.read()
try:
    d = json.loads(raw)
except Exception:
    print("  原始返回:", raw[:900]); raise SystemExit
res = d.get("results", [])
print("  返回行数:", len(res), " has_more:", d.get("has_more"))
for i, r in enumerate(res):
    props = r.get("properties", {})
    vals = {}
    for k, v in props.items():
        t = v.get("type")
        if t == "title":
            vals[k] = "".join(x.get("plain_text","") for x in v["title"])
        elif t == "select":
            vals[k] = (v.get("select") or {}).get("name")
        elif t == "status":
            vals[k] = (v.get("status") or {}).get("name")
        elif t == "multi_select":
            vals[k] = [o["name"] for o in v.get("multi_select", [])]
        elif t == "date":
            vals[k] = (v.get("date") or {}).get("start")
        elif t == "number":
            vals[k] = v.get("number")
        elif t == "rich_text":
            vals[k] = "".join(x.get("plain_text","") for x in v.get("rich_text", []))[:40]
        else:
            vals[k] = "<%s>" % t
    print("   [%d] %s" % (i, json.dumps(vals, ensure_ascii=False)))
'
