#!/usr/bin/env python3
"""把工作台「+ 记一笔」产生的本地草稿写入你自己的 Notion「副业收入」库。

⚠️ 这是**写操作**，会真的改动你的 Notion。
   默认只演练（dry-run），必须显式加 --confirm 才会真写。

这是唯一会写 Notion 的动作，只在用户明确说「整理到 Notion」时才该跑。
只碰 source=="local" 的记录；source=="notion"（只读拉取来的）绝不动。

用法：
  python3 scripts/sync/notion-push-drafts.py              # 演练，只打印将要写什么
  python3 scripts/sync/notion-push-drafts.py --confirm    # 真的写入

写入成功后，本地那几条会被标成 source:"notion" / readOnly:true，
下次同步会以 Notion 为准（不会重复写）。
"""
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

DIR = pathlib.Path(__file__).resolve().parent.parent
DATA = DIR / "data.json"
TOKEN_FILE = pathlib.Path.home() / ".config/daily-workbench/sync-token"
API = "http://127.0.0.1:8787/data.json"

NTN = "/usr/local/bin/ntn"
PROXY_ENV = dict(os.environ,
                 HTTPS_PROXY="http://127.0.0.1:7890",
                 HTTP_PROXY="http://127.0.0.1:7890")
DS_INCOME = os.environ.get("WB_NOTION_INCOME_DS", "")  # 副业收入库


def ntn_api(path, body, method="POST"):
    p = subprocess.run([NTN, "api", path, "-X", method,
                        "-d", json.dumps(body, ensure_ascii=False)],
                       capture_output=True, text=True, env=PROXY_ENV, timeout=120)
    out = (p.stdout or "").strip()
    if not out:
        raise RuntimeError("ntn 无输出。stderr=" + (p.stderr or "")[:400])
    return json.loads(out)


def build_page(rec):
    """把一条本地草稿映射成 Notion 页创建请求。字段名取自「副业收入」真实 schema。"""
    note_parts = []
    prod = rec.get("product") or rec.get("type") or "其他"
    props = {
        "Name": {"title": [{"text": {"content": (rec.get("note") or "").split(" | ")[0][:100] or "工作台记账"}}]},
        "分成金额": {"number": abs(float(rec.get("amount") or 0))},
        "时间": {"date": {"start": rec.get("date")}},
        "产品": {"select": {"name": prod}},
    }
    memo = rec.get("note") or ""
    if memo:
        props["备注"] = {"rich_text": [{"text": {"content": memo[:1900]}}]}
    return {"parent": {"type": "data_source_id", "data_source_id": DS_INCOME},
            "properties": props}


def push(state):
    token = TOKEN_FILE.read_text().strip()
    req = urllib.request.Request(
        API, data=json.dumps(state, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main():
    confirm = "--confirm" in sys.argv
    state = json.loads(DATA.read_text(encoding="utf-8"))
    drafts = [e for e in state.get("earnings", []) if e.get("source") == "local"]

    if not drafts:
        print("没有本地草稿（source:\"local\"）需要整理。")
        return

    print("找到 %d 条本地草稿：" % len(drafts))
    for d in drafts:
        print("  %s  %+10.2f  %-10s  %s" % (
            d.get("date"), float(d.get("amount") or 0),
            (d.get("type") or "")[:10], (d.get("note") or "")[:40]))
    print()

    if not confirm:
        print("【演练模式】以上内容**尚未**写入 Notion。")
        print("确认无误后加 --confirm 真写：")
        print("  python3 scripts/sync/notion-push-drafts.py --confirm")
        print()
        print("将要提交的请求体示例（第 1 条）：")
        print(json.dumps(build_page(drafts[0]), ensure_ascii=False, indent=2)[:900])
        return

    ok, failed = [], []
    for d in drafts:
        try:
            r = ntn_api("/v1/pages", build_page(d))
            if r.get("id"):
                ok.append((d, r["id"]))
                print("  ✓ 已写入 %s (%+.2f)" % (d.get("date"), float(d.get("amount") or 0)))
            else:
                failed.append((d, str(r)[:200]))
                print("  ✗ 失败 %s -> %s" % (d.get("date"), str(r)[:200]))
        except Exception as e:  # noqa: BLE001
            failed.append((d, str(e)[:200]))
            print("  ✗ 异常 %s -> %s" % (d.get("date"), str(e)[:200]))

    # 写成功的本条目转为「已入 Notion」，避免下次重复写
    if ok:
        done_ids = {d.get("id") for d, _ in ok}
        for e in state["earnings"]:
            if e.get("id") in done_ids:
                e["source"] = "notion"
                e["readOnly"] = True
        code, resp = push(state)
        print("\n本地标记更新：HTTP %s %s" % (code, resp))

    print("\n完成：成功 %d 条，失败 %d 条。" % (len(ok), len(failed)))
    if failed:
        print("失败的没有改本地标记，可修好后重跑。")
        sys.exit(1)


if __name__ == "__main__":
    main()
