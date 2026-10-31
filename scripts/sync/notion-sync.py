#!/usr/bin/env python3
"""工作台 ↔ Notion 只读同步（稳定版）。

通过 Notion 官方 CLI `ntn` 直连 Public API。凭据保存在 macOS 钥匙串
（notion-cli），无需额外配置。现在不硬编码本机代理端口：代理故障时直连仍可
工作，也不会再出现 HTTP(S)_PROXY 与系统代理重复叠加导致的 TLS 中断。

数据源（全部只读，绝不写用户的 Notion）：
  副业收入  collection://8f50f480-8449-4dcd-8b1a-7edbb68d3214
  产品支出  collection://fbd55d2c-89fc-43ce-b1e1-72877493744b
  项目管理  collection://264b66ad-8b35-806b-bb64-000bb903657c
  贵人计划  collection://e79adb08-40f7-4dd3-b8fc-d3c647a8497e
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

DIR = pathlib.Path(__file__).resolve().parent.parent.parent
DATA = DIR / "data.json"
TOKEN_FILE = pathlib.Path.home() / ".config/daily-workbench/sync-token"
API = "http://127.0.0.1:8787/data.json"

NTN = "/usr/local/bin/ntn"
NTN_ENV = dict(os.environ)
for _proxy_key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy"):
    NTN_ENV.pop(_proxy_key, None)

DS_INCOME = "8f50f480-8449-4dcd-8b1a-7edbb68d3214"
DS_EXPENSE = "fbd55d2c-89fc-43ce-b1e1-72877493744b"
DS_PROJECT = "264b66ad-8b35-806b-bb64-000bb903657c"
DS_CLIENT = "e79adb08-40f7-4dd3-b8fc-d3c647a8497e"   # 贵人计划
DS_FOLLOWUP = "e183b80d-b6e9-42c7-bf4c-99cad95bfd7a" # 客户跟进记录
DS_FOCUS = "238b66ad-8b35-81bc-8fb4-000b255bc3da"   # 今日番茄追踪表（仅显式回写）


# ---------- Notion 访问（经 ntn + Surge） ----------

def ntn_api(path, body=None, method="POST"):
    cmd = [NTN, "api", path, "-X", method]
    if body is not None:
        cmd += ["-d", json.dumps(body, ensure_ascii=False)]
    p = subprocess.run(cmd, capture_output=True, text=True,
                       env=NTN_ENV, timeout=120)
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


def fetch_client_schema():
    """读取客户库字段定义，供工作台回写时渲染真实选项。

    选项不在前端写死：Notion 里新增「客户状态 / 客户类型 / 关系来源」后，
    下一次同步就能出现，避免工作台把合法值误报成非法。
    """
    d = ntn_api("/v1/data_sources/%s" % DS_CLIENT, method="GET")
    props = d.get("properties") or {}
    out = {}
    for key, name in (("customerType", "客户类型"),
                      ("status", "客户状态"),
                      ("relationSource", "关系来源")):
        prop = props.get(name) or {}
        typ = prop.get("type")
        if typ in ("select", "status"):
            out[key] = [o.get("name") for o in (prop.get(typ) or {}).get("options", [])
                        if o.get("name")]
    return out


def fetch_project_schema():
    """读取项目库字段定义，供工作台渲染真实状态与选项。

    选项不在前端硬编码。Notion 里新增优先级、类型或冲刺后，下一次同步
    就能直接出现在回写表单里。
    """
    d = ntn_api("/v1/data_sources/%s" % DS_PROJECT, method="GET")
    props = d.get("properties") or {}
    out = {}
    for key, name in (("status", "状态"),
                      ("priority", "优先级"),
                      ("type", "类型"),
                      ("sprint", "冲刺"),
                      ("tags", "标签")):
        prop = props.get(name) or {}
        typ = prop.get("type")
        if typ in ("select", "status", "multi_select"):
            out[key] = [o.get("name") for o in (prop.get(typ) or {}).get("options", [])
                        if o.get("name")]
    return out


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
        rt = r.get("type")
        if rt == "number":
            return r.get("number")
        if rt == "date":
            return (r.get("date") or {}).get("start")
        return None
    if t == "url":
        return v.get("url")
    if t == "people":
        return [x.get("name") for x in v.get("people", [])]
    if t == "relation":
        # 上级项目是自关联 relation，元素形如 {"id": "<page-uuid>"}；只取 id，
        # 前端/脚本用 notionId 建父子映射。多父场景取第一个（当前库均为单选父级）。
        rels = v.get("relation", [])
        return rels[0].get("id") if rels and isinstance(rels[0], dict) else None
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
    id2nid = {r["id"]: "np%03d" % i for i, r in enumerate(rows, 1)}
    for i, r in enumerate(rows, 1):
        p = r.get("properties", {})
        pct = val(p.get("完成百分比"))
        if isinstance(pct, (int, float)) and 0 <= pct <= 1:
            pct = round(pct * 100, 1)
        tags = val(p.get("标签")) or []
        project_type = val(p.get("类型")) or ""
        status = (val(p.get("状态")) or "").split(" (")[0].strip()
        parent_nid = val(p.get("上级 项目"))
        owner_rows = (p.get("负责人") or {}).get("people") or []
        owner_ids = [x.get("id") for x in owner_rows if isinstance(x, dict) and x.get("id")]
        owner_names = [x.get("name") for x in owner_rows if isinstance(x, dict) and x.get("name")]
        child_ids = [x.get("id") for x in ((p.get("子级 项目") or {}).get("relation") or [])
                     if isinstance(x, dict) and x.get("id")]
        related_task_ids = [x.get("id") for x in ((p.get("关联任务") or {}).get("relation") or [])
                            if isinstance(x, dict) and x.get("id")]
        source_record_ids = [x.get("id") for x in ((p.get("来源记录") or {}).get("relation") or [])
                             if isinstance(x, dict) and x.get("id")]
        out.append({
            "id": "np%03d" % i,
            "notionId": r["id"],
            "name": (val(p.get("项目名称")) or "(未命名)").strip(),
            "status": status,
            "category": (tags[0] if tags else project_type),
            "projectType": project_type,
            "tags": tags,
            "priority": val(p.get("优先级")) or "",
            "start": val(p.get("开始日期")) or "",
            "due": val(p.get("截止日期")) or "",
            "doneDate": val(p.get("完成日期")) or None,
            # 树状视图的父子关系：存本地 id（np00N），与数组内 id 对应；
            # 根项目（无上级）为 null。孤儿引用（父级不在库内）由前端兜底为根。
            "parent": id2nid.get(parent_nid) if parent_nid else None,
            # 工时（小时）。库内几乎没填，但字段已存在——前端按「空就诚实空态」处理，
            # 填了自动显示超支/结余，不再需要二次改脚本。
            "estHours": val(p.get("预计工时")) if isinstance(val(p.get("预计工时")), (int, float)) else None,
            "actHours": val(p.get("实际工时")) if isinstance(val(p.get("实际工时")), (int, float)) else None,
            "sprint": val(p.get("冲刺")) or "",
            "sprintStart": val(p.get("冲刺开始日期")) or "",
            "sprintEnd": val(p.get("冲刺结束日期")) or "",
            "owner": owner_names[0] if owner_names else "",
            "ownerIds": owner_ids,
            "relatedTaskIds": related_task_ids,
            "sourceRecordIds": source_record_ids,
            "childIds": child_ids,
            "childCount": val(p.get("子任务数量")),
            "relatedLink": val(p.get("相关链接")) or "",
            "progress": pct if isinstance(pct, (int, float)) else None,
            "note": (val(p.get("备注")) or "").strip(),
            "source": "notion",
            "readOnly": True,
        })
    return out


def build_clients(rows):
    """贵人计划 → 客户经营数据。

    新字段只透传 Notion 的真实值；没填就是空，不用收入反推客户阶段或来源。
    收入/利润继续来自库内 rollup/公式，避免工作台与 Notion 各算一套。
    """
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
        customer_type = val(p.get("客户类型")) or ""
        stage = val(p.get("客户状态")) or ""
        relation_source = val(p.get("关系来源")) or ""
        owners = val(p.get("负责人")) or []
        followup_rel = (p.get("跟进记录") or {}).get("relation") or []
        followup_count = val(p.get("跟进次数"))
        last_followup = val(p.get("最近跟进")) or ""
        next_followup_auto = val(p.get("下次跟进（跟进记录）")) or ""
        out.append({
            "notionId": r.get("id"),
            "notionUrl": r.get("url"),
            "name": name,
            "role": (tags[0] if tags else (val(p.get("认识渠道")) or "贵人")),
            "status": stage,
            "dueDays": None,
            "note": memo or money,
            "memo": memo,
            "income": inc if isinstance(inc, (int, float)) else 0,
            "expense": exp if isinstance(exp, (int, float)) else 0,
            "profit": profit if isinstance(profit, (int, float)) else None,
            "customerType": customer_type,
            "relationSource": relation_source,
            "owner": owners[0] if owners else "",
            "firstContact": val(p.get("首次接触")) or "",
            "lastContact": val(p.get("上次联系")) or "",
            "nextFollowUp": val(p.get("下次跟进")) or "",
            "wechatId": val(p.get("微信ID")) or "",
            "coordinate": val(p.get("坐标")) or "",
            "todo": val(p.get("待办")) or "",
            "benefits": val(p.get("已送福利")) or "",
            "address": val(p.get("快递地址")) or "",
            "channel": val(p.get("渠道")) or "",
            "knownChannel": val(p.get("认识渠道")) or "",
            # Notion 原生 rollup：来自「跟进记录」relation，不复制记录本身。
            "followupCount": int(followup_count) if isinstance(followup_count, (int, float)) else 0,
            "lastFollowup": last_followup,
            "nextFollowupAuto": next_followup_auto,
            "followupIds": [x.get("id") for x in followup_rel
                            if isinstance(x, dict) and x.get("id")],
            "source": "notion",
            "readOnly": True,
        })
    return out


def build_followups(rows, clients):
    """客户跟进记录 → 跟进工作台数据。

    relation 的显示名不依赖额外接口：先用客户页 id 反查名称，再回退到标题。
    """
    client_names = {c.get("notionId"): c.get("name") for c in clients if c.get("notionId")}
    out = []
    for r in rows:
        p = r.get("properties", {})
        title = (val(p.get("跟进标题")) or "").strip()
        if not title:
            continue
        rels = (p.get("关联客户") or {}).get("relation") or []
        client_ids = [x.get("id") for x in rels if isinstance(x, dict) and x.get("id")]
        owners = val(p.get("负责人")) or []
        out.append({
            "id": r.get("id"),
            "notionId": r.get("id"),
            "notionUrl": r.get("url"),
            "title": title,
            "type": val(p.get("跟进类型")) or "",
            "status": val(p.get("跟进状态")) or "",
            "date": val(p.get("发生日期")) or "",
            "nextFollowUp": val(p.get("下次跟进")) or "",
            "nextStep": (val(p.get("下一步")) or "").strip(),
            "summary": (val(p.get("沟通摘要")) or "").strip(),
            "owner": owners[0] if owners else "",
            "clientIds": client_ids,
            "clientNames": [client_names.get(x, "未匹配客户") for x in client_ids],
            "source": "notion",
            "readOnly": True,
        })
    out.sort(key=lambda x: (x.get("date") or "", x.get("notionId") or ""), reverse=True)
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
    fol = fetch_all(DS_FOLLOWUP)
    client_schema = fetch_client_schema()
    project_schema = fetch_project_schema()
    print("  副业收入 %d 条 / 产品支出 %d 条 / 项目管理 %d 条 / 贵人计划 %d 条 / 跟进记录 %d 条"
          % (len(inc), len(exp), len(proj), len(cli), len(fol)))

    earnings = build_earnings(inc, exp)
    projects = build_projects(proj)
    clients = build_clients(cli)
    followups = build_followups(fol, clients)

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
    state["followups"] = followups
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
        "clientsDs": DS_CLIENT, "clientsDsName": "贵人计划",
        "followupsDs": DS_FOLLOWUP, "followupsDsName": "客户跟进记录",
        "focusDs": DS_FOCUS, "focusDsName": "今日番茄追踪表",
        "clientSchema": client_schema,
        "projectSchema": project_schema,
        "wrongDs": ["24db66ad-8b35-8090-937b-000b5a5ec527"],
        "deprecatedDs": ["df55265b-330c-48cc-b133-c8947a323917",
                         "a57f3b1c-47d7-40b8-937e-22296d4ce2d5"],
        "syncedAt": now,
        "channel": "ntn CLI 直连 Notion Public API",
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
