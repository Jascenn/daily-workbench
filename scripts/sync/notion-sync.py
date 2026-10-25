#!/usr/bin/env python3
"""工作台 ↔ Notion 只读同步（稳定版）。

为什么不走 WorkBuddy MCP：
  WorkBuddy 的 Electron 出口不读 macOS 系统代理，直连 mcp.notion.com
  延迟在 0.18s ~ 12s 之间剧烈波动，实测探针成功率约 1/5，连接器反复掉线。
解决办法：
  改用 Notion 官方 CLI `ntn`（Rust/reqwest，尊重 HTTPS_PROXY），
  强制走 Surge(127.0.0.1:7890)，实测 5/5 成功、0.28~0.9s。
  ntn 的凭据在 macOS 钥匙串（notion-cli），无需额外配置。

数据源（全部只读，绝不写用户的 Notion）：
  副业收入  collection://<YOUR_INCOME_DS_ID>
  产品支出  collection://<YOUR_EXPENSE_DS_ID>
  项目管理  collection://<YOUR_PROJECT_DS_ID>
  客户/人脉 collection://<YOUR_CLIENT_DS_ID>
            （2026-10-07 新增，只读；客户页从本地示例数据切到真实库）
            （注意：不是 24db66ad-8b35-8090-937b-000b5a5ec527 ——
              那个库只有「项目一/项目二」两条 Notion 模板示例，字段也对不上）

用法：
  python3 scripts/sync/notion-sync.py --dry     # 只拉取并打印，不写文件
  python3 scripts/sync/notion-sync.py           # 拉取并写入 data.json（经同步服务，自动备份）
"""
import datetime
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

DS_INCOME = os.environ.get("WB_NOTION_INCOME_DS", "")
DS_EXPENSE = os.environ.get("WB_NOTION_EXPENSE_DS", "")
DS_PROJECT = os.environ.get("WB_NOTION_PROJECT_DS", "")
DS_CLIENT = os.environ.get("WB_NOTION_CLIENT_DS", "")   # 客户/人脉库


# ---------- Notion 访问（经 ntn + Surge） ----------

def ntn_api(path, body=None, method="POST"):
    cmd = [NTN, "api", path, "-X", method]
    if body is not None:
        cmd += ["-d", json.dumps(body, ensure_ascii=False)]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       env=PROXY_ENV, timeout=120)
    out = (p.stdout or "").strip()
    if not out:
        raise RuntimeError("ntn 无输出。stderr=" + (p.stderr or "")[:400])
    return json.loads(out)


def fetch_all(ds_id):
    rows, cur = [], None
    while True:
        body = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        d = ntn_api("/v1/data_sources/%s/query" % ds_id, body)
        rows += d.get("results", [])
        if not d.get("has_more"):
            break
        cur = d.get("next_cursor")
    return rows


# ---------- 属性取值 ----------

def plain(rt):
    return "".join(x.get("plain_text", "") for x in (rt or []))


def val(v):
    if not v:
        return None
    t = v.get("type")
    if t in ("title",):
        return plain(v["title"])
    if t == "rich_text":
        return plain(v.get("rich_text"))
    if t in ("select", "status"):
        return (v.get(t) or {}).get("name")
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
        return r.get("number") if r.get("type") == "number" else None
    if t == "url":
        return v.get("url")
    if t == "people":
        return [x.get("name") for x in v.get("people", [])]
    return None


# ---------- 三个数据源的映射 ----------

def build_earnings(income_rows, expense_rows):
    out = []

    def add(rows, amount_key, date_key, negate, product_label):
        for r in rows:
            p = r.get("properties", {})
            amt = val(p.get(amount_key))
            if amt is None:
                continue
            d = val(p.get(date_key))
            if not d:
                continue
            name = (val(p.get("Name")) or "").strip()
            memo = (val(p.get("备注")) or "").strip()
            prod = val(p.get("产品")) or product_label
            # 保留原始精度，绝不可逐条四舍五入：
            # Notion 里有 9.996 / 10.728 / 76.841 这类值，逐条 round(2) 再求和
            # 会让总额从 26958.49 漂成 26958.50（差 1 分），与 Notion 自身 SUM 不符。
            out.append({
                "_src": r["id"],
                "date": d[:10],
                "amount": round(-abs(float(amt)) if negate else float(amt), 4),
                "type": prod if not negate else (prod or "其他"),
                "product": product_label,
                "note": (name + (" | " + memo if memo else "")) if name else memo,
                "_prod": prod,
            })

    # 注意：日期属性的真实键名是 `时间` / `日期`。
    # MCP 的 SQL 模式里看到的 `date:时间:start` 是它的列名转换，原生 API 不叫这个。
    add(income_rows, "分成金额", "时间", False, None)
    add(expense_rows, "金额", "日期", True, "支出")

    # 支出的 product 统一为「支出」；收入的 product 用产品名（与历史口径一致）
    for e in out:
        if e["product"] is None:
            e["product"] = e["_prod"]
    out.sort(key=lambda x: (x["date"], x["_src"]))
    return out


def build_projects(rows):
    out = []
    for i, r in enumerate(rows, 1):
        p = r.get("properties", {})
        pct = val(p.get("完成百分比"))
        if isinstance(pct, (int, float)) and 0 <= pct <= 1:
            pct = round(pct * 100, 1)
        tags = val(p.get("标签")) or []
        out.append({
            "id": "np%03d" % i,
            "notionId": r["id"],
            "name": (val(p.get("项目名称")) or "(未命名)").strip(),
            "status": (val(p.get("状态")) or "").split(" (")[0].strip(),
            "category": (tags[0] if tags else (val(p.get("类型")) or "")),
            "priority": val(p.get("优先级")) or "",
            "start": val(p.get("开始日期")) or "",
            "due": val(p.get("截止日期")) or "",
            "doneDate": None,
            "progress": pct if isinstance(pct, (int, float)) else None,
            "note": (val(p.get("备注")) or "").strip(),
            "source": "notion",
            "readOnly": True,
        })
    return out


def build_clients(rows):
    """客户库 → 客户页数组 [姓名, 关系/角色, 状态, 跟进天数, 备注]。
    Notion 库没有"下一次跟进倒计时"字段，页面上的天数列只对本地草稿有意义；
    真实行统一显示 "—"，不伪造倒计时。收入/利润来自库内 rollup/公式，直接透传。"""
    out = []
    for r in rows:
        p = r.get("properties", {})
        name = (val(p.get("Name")) or "").strip()
        if not name:
            continue
        tags = val(p.get("Tags")) or []
        inc = val(p.get("收入总额"))
        exp = val(p.get("支出总额"))
        profit = val(p.get("纯利润（自动）"))
        money = ""
        if isinstance(profit, (int, float)) and profit != 0:
            money = "利润 ¥%s" % format(round(profit, 2), ",.2f").rstrip("0").rstrip(".")
        elif isinstance(inc, (int, float)) and inc != 0:
            money = "收入 ¥%s" % format(round(inc, 2), ",.2f").rstrip("0").rstrip(".")
        memo = (val(p.get("备注")) or "").strip()
        out.append({
            "name": name,
            "role": (tags[0] if tags else (val(p.get("认识渠道")) or "贵人")),
            "status": "已成交" if (isinstance(inc, (int, float)) and inc > 0) else "线索",
            "dueDays": None,
            "note": memo or money,
            "income": inc if isinstance(inc, (int, float)) else 0,
            "expense": exp if isinstance(exp, (int, float)) else 0,
            "profit": profit if isinstance(profit, (int, float)) else None,
            "source": "notion",
            "readOnly": True,
        })
    return out


# ---------- 写入 ----------

def push(state):
    token = TOKEN_FILE.read_text().strip()
    body = json.dumps(state, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        API, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main():
    dry = "--dry" in sys.argv
    print("经 ntn + Surge 只读拉取…")
    inc = fetch_all(DS_INCOME)
    exp = fetch_all(DS_EXPENSE)
    proj = fetch_all(DS_PROJECT)
    cli = fetch_all(DS_CLIENT)
    print("  副业收入 %d 条 / 产品支出 %d 条 / 项目管理 %d 条 / 客户库 %d 条"
          % (len(inc), len(exp), len(proj), len(cli)))

    earnings = build_earnings(inc, exp)
    projects = build_projects(proj)
    clients = build_clients(cli)

    income_total = round(sum(e["amount"] for e in earnings if e["amount"] > 0), 2)
    expense_total = round(sum(-e["amount"] for e in earnings if e["amount"] < 0), 2)
    print("  收益 %d 条：收入 %.2f − 支出 %.2f = 净 %.2f"
          % (len(earnings), income_total, expense_total,
             round(income_total - expense_total, 2)))
    print("  项目 %d 条：进行中 %d / 未开始 %d / 已完成 %d"
          % (len(projects),
             sum(1 for p in projects if p["status"] == "进行中"),
             sum(1 for p in projects if p["status"] == "未开始"),
             sum(1 for p in projects if p["status"] == "已完成")))

    if dry:
        print("\n--dry：未写文件。")
        return

    state = json.loads(DATA.read_text(encoding="utf-8"))
    old_n = (len(state.get("earnings", [])), len(state.get("projects", [])))

    # 收益记录：保留历史 id 口径，按日期重排
    for i, e in enumerate(earnings, 1):
        e["id"] = "n%04d" % i
        e["source"] = "notion"
        e["readOnly"] = True
        e.pop("_src", None)
        e.pop("_prod", None)

    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    state["earnings"] = earnings
    state["projects"] = projects
    state["clients"] = clients
    state["earningsReadonly"] = True
    state["earningsSource"] = "notion"
    state["notionStatus"] = "connected"
    state["lastNotionSync"] = now
    state["projectsSource"] = {
        "ds": DS_PROJECT,
        "name": "项目管理",
        "status": "connected",
        "note": ("只读拉取自你的 Notion「项目管理」库。原配置的 "
                 "24db66ad-8b35-8090-937b-000b5a5ec527（「我的项目」）"
                 "只有 2 条 Notion 模板示例数据、字段也对不上，已于 2026-09-29 换掉。"),
    }
    state["notionMeta"] = {
        "earningsDs": DS_INCOME, "earningsDsName": "副业收入",
        "expenseDs": DS_EXPENSE, "expenseDsName": "产品支出",
        "projectsDs": DS_PROJECT, "projectsDsName": "项目管理",
        "clientsDs": DS_CLIENT, "clientsDsName": os.environ.get("WB_NOTION_CLIENT_NAME", "客户库"),
        "wrongDs": ["24db66ad-8b35-8090-937b-000b5a5ec527"],
        "deprecatedDs": ["df55265b-330c-48cc-b133-c8947a323917",
                         "a57f3b1c-47d7-40b8-937e-22296d4ce2d5"],
        "syncedAt": now,
        "channel": "ntn CLI + Surge(127.0.0.1:7890)，不经 WorkBuddy MCP",
        "verifiedTotals": {"income": income_total, "expense": expense_total,
                           "net": round(income_total - expense_total, 2)},
    }

    code, resp = push(state)
    print("  写入同步服务：HTTP %s %s   (原 %s → 新 %s)"
          % (code, resp, old_n, (len(earnings), len(projects))))
    if code != 200:
        sys.exit("写入失败，data.json 未被修改。")


if __name__ == "__main__":
    main()
