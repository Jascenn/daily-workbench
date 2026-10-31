#!/usr/bin/env python3
"""从「项目管理」(真实项目库) 只读拉取全部项目，字段映射到 data.json 的 projects 结构。

数据源：collection://264b66ad-8b35-806b-bb64-000bb903657c（"项目管理"）
注意：不是自动化里写的 24db66ad-8b35-8090-937b-000b5a5ec527 ——
那个库只有「项目一/项目二」两条 Notion 模板示例数据，字段也对不上。
本脚本是早期探针，经 ntn CLI + Surge 代理读取 Notion 公共 API。
"""
import json
import os
import subprocess
import sys

NTN = "/usr/local/bin/ntn"
ENV = dict(os.environ, HTTPS_PROXY="http://127.0.0.1:7890",
           HTTP_PROXY="http://127.0.0.1:7890")
DS = "264b66ad-8b35-806b-bb64-000bb903657c"


def api(path, body):
    p = subprocess.run([NTN, "api", path, "-X", "POST",
                        "-d", json.dumps(body, ensure_ascii=False)],
                       capture_output=True, text=True, env=ENV, timeout=90)
    if not p.stdout.strip():
        raise RuntimeError("ntn 无输出：" + (p.stderr or "")[:300])
    return json.loads(p.stdout)


def fetch_all():
    rows, cur = [], None
    while True:
        b = {"page_size": 100}
        if cur:
            b["start_cursor"] = cur
        d = api("/v1/data_sources/%s/query" % DS, b)
        rows += d.get("results", [])
        if not d.get("has_more"):
            break
        cur = d.get("next_cursor")
    return rows


def plain(rt):
    return "".join(x.get("plain_text", "") for x in (rt or []))


def val(v):
    if not v:
        return None
    t = v.get("type")
    if t == "title":
        return plain(v["title"])
    if t == "rich_text":
        return plain(v.get("rich_text"))
    if t in ("select", "status"):
        o = v.get(t) or {}
        return o.get("name")
    if t == "multi_select":
        return [o["name"] for o in v.get("multi_select", [])]
    if t == "date":
        return (v.get("date") or {}).get("start")
    if t == "number":
        return v.get("number")
    if t == "formula":
        f = v.get("formula") or {}
        return f.get(f.get("type"))
    if t == "rollup":
        r = v.get("rollup") or {}
        if r.get("type") == "number":
            return r.get("number")
        if r.get("type") == "array":
            return None
        return r.get(r.get("type"))
    if t == "url":
        return v.get("url")
    if t == "people":
        return [p_.get("name") for p_ in v.get("people", [])]
    if t == "unique_id":
        u = v.get("unique_id") or {}
        return "%s-%s" % (u.get("prefix") or "", u.get("number"))
    return "<%s>" % t


def main():
    rows = fetch_all()
    out = []
    for i, r in enumerate(rows, 1):
        p = r.get("properties", {})
        name = val(p.get("项目名称")) or "(未命名)"
        pct = val(p.get("完成百分比"))
        if isinstance(pct, (int, float)) and pct <= 1:
            pct = round(pct * 100, 1)
        status = val(p.get("状态")) or ""
        # 归一化状态文案：去掉 Notion 的英文后缀
        status = status.split(" (")[0].strip()
        out.append({
            "id": "np%03d" % i,
            "notionId": r["id"],
            "name": name,
            "status": status,
            "category": (val(p.get("标签")) or [None])[0] or val(p.get("类型")) or "",
            "priority": val(p.get("优先级")) or "",
            "start": val(p.get("开始日期")),
            "due": val(p.get("截止日期")),
            "doneDate": None,
            "progress": pct if isinstance(pct, (int, float)) else None,
            "note": (val(p.get("备注")) or "").strip(),
            "source": "notion",
            "readOnly": True,
        })

    print("拉取到 %d 个项目" % len(out))
    for o in out:
        pg = ("%g%%" % o["progress"]) if o["progress"] is not None else "—"
        print("  %-42s | %-6s | %-5s | %s ~ %s | %s" % (
            o["name"][:42], o["status"], o["priority"] or "-",
            o["start"] or "-", o["due"] or "-", pg))

    if "--write" in sys.argv:
        dest = sys.argv[sys.argv.index("--write") + 1]
        json.dump(out, open(dest, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("\n已写入", dest)


if __name__ == "__main__":
    main()
