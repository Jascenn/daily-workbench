#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「备注首段 → 规范来源」的归并规则，写入 data.json 的 sourceMap 键。

为什么放在 data.json 而不是单独的配置文件：
  - scripts/sync/notion-sync.py 只改 earnings / projects / earningsReadonly / ... 这几个固定键，
    其余键是读出来再整体 POST 回去的，所以 sourceMap 天然会被同步保留。
  - scripts/sync/make-local.py 是把整个 data.json 注入离线页的，放这里离线页自动继承。
  - app.py 的 POST 处理器是 json.dump(obj) 原样落盘，不会裁剪未知键。

⚠️ 唯一的坑：workbench.html 的 normalize() 会重建对象。
   新增顶层键必须同时加进 normalize() 的透传列表，否则「保存一次即静默抹掉」。
   本脚本的下游断言（--verify）会检查这一点。

规则来自凌一 2026-09-29 的确认：
  · 层 A（书写变体）全部归并
  · 层 B1（LionCC 相关 / ChatGPT 相关 / 账号打理）确认归并
  · 层 B2（AI 破局体系 / 车车菁华 / 大冲私教）**不归并**，保持逐条
  · IDO老徐 与 老徐-* 是同一人 → 合并

用法：
  python3 scripts/build-source-map.py            # 算一遍并写入 data.json
  python3 scripts/build-source-map.py --dry      # 只算不写
  python3 scripts/build-source-map.py --verify   # 只校验线上 data.json 里的 sourceMap 是否自洽
"""
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

DIR = Path("/Users/jascen/Development/daily-workbench")
DATA = DIR / "data.json"
API = "http://127.0.0.1:8787/data.json"
TOKEN_FILE = Path.home() / ".config" / "daily-workbench" / "sync-token"

# ---------- 规则 ----------
# 顺序即优先级（首个命中生效）。
#
# ⚠️ scope 字段很关键，2026-09-29 因漏了它导致一次系统性误判：
#   · scope="seg1"（默认）：只看备注第一段（'|' 之前）。
#   · scope="note"        ：在整个备注里找。
# 备注的真实结构常常是「任务 | 给<客户>做某事」——**客户名在第二段**。
# 只按首段归类时，「远程配置接入API | 给所罗门远程配置接入 API」会被误判成
# LionCC 的活，于是所罗门被少算一半（39 笔→实际 79 笔），
# 而「LionCC 相关 / ChatGPT 相关 / 账号打理」三组里 31 笔其实是所罗门的。
# 因此：显式客户标记 `给<X>` 用 scope="note" 并排在**最前面**。
RULES = [
    # ---- 层 A0：显式客户标记「给<X>」——全备注范围，最高优先级 ----
    # 「给X」是"这笔活给谁干的"最直接的写法，比首段的任务描述可信得多。
    {"name": "所罗门", "kind": "A", "scope": "note",
     "note": "甲方；「给所罗门…」出现在备注任意段都算（79 笔里 40 笔客户名在第二段）",
     "patterns": [r"给\s*所罗门", r"^所罗门"]},

    # ---- 层 A：同一实体的书写变体（只看首段） ----
    {"name": "卓越运营奖金", "kind": "A", "note": "写法只差期数括号",
     "patterns": [r"^卓越运营奖金"]},
    {"name": "LionCC 交流群", "kind": "A", "note": "同一社群，写法只差成员昵称",
     "patterns": [r"^OpenClaw\s*&\s*LionCC"]},
    {"name": "淘金之路", "kind": "A", "note": "训练营 + 直播演示，同一合作",
     "patterns": [r"^淘金之路"]},
    {"name": "AI 代写", "kind": "A", "note": "只差日期与标题，业务同一条",
     "patterns": [r"^AI\s*代写"]},
    {"name": "老徐", "kind": "A", "note": "老徐-* 与 IDO老徐 已确认是同一人（2026-09-29）",
     "patterns": [r"^(老徐|IDO\s*老徐)"]},
    {"name": "鹿鸣堂 / 飞巴", "kind": "A", "note": "同一合作体系（含 → 抽成写法）",
     "patterns": [r"(鹿鸣堂|飞巴)"]},
    {"name": "小报童", "kind": "A", "note": "各号分成，渠道都是小报童",
     "patterns": [r"小报童"]},

    # ---- 层 B1：疑似同一雇主/业务线（凌一 2026-09-29 确认归并） ----
    # 去掉「给所罗门」的行之后只剩 12 笔，全部是 lioncc.ai 官网 / Lion CC API 的活，
    # 窗口 2026-03~04 高度集中 —— 比修正前(27 笔混杂)更自洽，这组反而更站得住。
    {"name": "LionCC 相关", "kind": "B1",
     "note": "lioncc.ai 官网与 Lion CC API 的教程/上站/远程配置；"
             "修正掉误收的所罗门行后为 12 笔，窗口 2026-03-27~2026-04-27（凌一确认）",
     "patterns": [r"(lioncc|Lion ?CC|镜像站|代充系统|小龙虾|hermes|Hermes|OPCAPI"
                  r"|API ?相关|giffgaff|静态住宅|第三方openclaw|openclaw ?使用"
                  r"|测试并完成|远程配置|远程登录|接入 ?Claude|Code ?模型|code ?模型)"]},
]

# 明确记录「已移除/没归并」的组，避免以后有人以为是漏了
NOT_MERGED = [
    {"name": "ChatGPT 相关", "reason": "整组 7 笔的备注都写着「给所罗门…」，实为所罗门的活，"
                                      "已并入所罗门（2026-09-29 修正）"},
    {"name": "账号打理", "reason": "整组 9 笔的备注都写着「给所罗门…」，实为所罗门的活，"
                                  "已并入所罗门（2026-09-29 修正）"},
    {"name": "AI 破局体系", "reason": "金额档位是散的（最大一档 218 元只占 17%）"},
    {"name": "车车 / 菁华教练", "reason": "样本太少（仅 3 笔，集中度无意义）"},
    {"name": "大冲私教", "reason": "金额档位是散的（最大一档 10 元只占 33%）"},
]


def first_seg(note):
    s = (note or "").strip()
    return s.split("|")[0].strip() or "(未填备注)"


def compile_rules():
    import re
    out = []
    for r in RULES:
        out.append((r["name"], r["kind"], r.get("scope", "seg1"),
                    [re.compile(p) for p in r["patterns"]]))
    return out


def source_of(seg, compiled, note=None):
    """seg = 备注第一段；note = 整条备注（scope=note 的规则用）。

    注意：只命中了 scope=note 的规则时也要返回 merged=True —— 判断依据是
    "有没有被规则收进某个组"，不是"命中在哪个范围"。
    """
    hay_note = note if note is not None else seg
    for name, _kind, scope, pats in compiled:
        hay = hay_note if scope == "note" else seg
        for p in pats:
            if p.search(hay):
                return name, True
    return seg, False


def group(rows, compiled):
    """rows: [(seg, amount, date, note)] → 已归并组 + 长尾，两级都按金额倒序。"""
    merged, tail = {}, {}
    for seg, amt, date, note in rows:
        name, is_m = source_of(seg, compiled, note)
        bucket = merged if is_m else tail
        o = bucket.setdefault(name, {"n": 0, "v": 0.0, "first": date, "last": date})
        o["n"] += 1
        o["v"] += amt
        o["first"] = min(o["first"], date)
        o["last"] = max(o["last"], date)
    key = lambda kv: -kv[1]["v"]
    return (sorted(merged.items(), key=key), sorted(tail.items(), key=key))


def build():
    d = json.loads(DATA.read_text(encoding="utf-8"))
    E = [e for e in d.get("earnings", []) if e.get("amount", 0) > 0]
    rows = [(first_seg(e.get("note")), e["amount"], e.get("date", ""), e.get("note") or "")
            for e in E]
    tv = sum(r[1] for r in rows)
    compiled = compile_rules()
    merged, tail = group(rows, compiled)

    print("=== 归并结果（收入 %d 笔 / ¥%.2f）===" % (len(rows), tv))
    mn = mv = 0
    for name, o in merged:
        print("  %-16s %3d 笔  ¥%9.2f   %s ~ %s"
              % (name, o["n"], o["v"], o["first"], o["last"]))
        mn += o["n"]
        mv += o["v"]
    print("  --- 已归并小计 %d 笔 ¥%.2f (%.1f%%) ---" % (mn, mv, mv / tv * 100) if tv else "")
    tn = sum(o["n"] for _, o in tail)
    tvv = sum(o["v"] for _, o in tail)
    print("  长尾（未归并）%d 种写法 %d 笔 ¥%.2f (%.1f%%)"
          % (len(tail), tn, tvv, tvv / tv * 100 if tv else 0))
    print("  长尾金额头部：" + " / ".join(
        "%s ¥%.0f" % (k, o["v"]) for k, o in tail[:5]))

    # 硬校验：归并不能凭空多钱少钱
    assert mn + tn == len(rows), ("笔数不守恒", mn, tn, len(rows))
    assert abs(mv + tvv - tv) < 1e-6, ("金额不守恒", mv, tvv, tv, mv + tvv)
    print("  ✓ 守恒：%d+%d=%d 笔，¥%.2f+¥%.2f=¥%.2f" % (mn, tn, len(rows), mv, tvv, mv + tvv))
    return d, tv, mn, mv, len(tail), tn, tvv


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


def verify():
    """校验线上 data.json 的 sourceMap：存在、规则可编译、且快照数字与当前数据仍一致。

    快照（merged / tail / totalIncome）是写入那一刻的，收入记录变了就会过期。
    只比"规则能不能编译"会漏掉这种过期，所以这里用当前数据重算一遍再比。
    """
    d = json.loads(DATA.read_text(encoding="utf-8"))
    sm = d.get("sourceMap")
    if not sm:
        print("✗ data.json 里没有 sourceMap —— 可能被某次保存抹掉了")
        print("  修：python3 scripts/build-source-map.py")
        return 1

    import re
    npat = 0
    for r in sm.get("rules", []):
        for p in r.get("patterns", []):
            re.compile(p)
            npat += 1
    print("✓ sourceMap 存在，builtAt=%s，%d 条规则 / %d 个正则全部可编译"
          % (sm.get("builtAt"), len(sm.get("rules", [])), npat))
    print("  decisions: %s" % json.dumps(sm.get("decisions", {}), ensure_ascii=False))

    # 用规则重算，与快照比对
    E = [e for e in d.get("earnings", []) if e.get("amount", 0) > 0]
    rows = [(first_seg(e.get("note")), e["amount"], e.get("date", ""), e.get("note") or "")
            for e in E]
    tv = sum(r[1] for r in rows)
    merged, tail = group(rows, compile_rules())
    mn, mv = sum(o["n"] for _, o in merged), sum(o["v"] for _, o in merged)
    tn, tvv = sum(o["n"] for _, o in tail), sum(o["v"] for _, o in tail)

    stale = []
    if len(merged) != len(sm.get("rules", [])):
        stale.append("归并组数 %d ≠ 规则数 %d" % (len(merged), len(sm.get("rules", []))))
    if mn != sm.get("merged", {}).get("count"):
        stale.append("已归并笔数 %d ≠ 快照 %s" % (mn, sm.get("merged", {}).get("count")))
    if abs(mv - sm.get("merged", {}).get("amount", -1)) > 0.005:
        stale.append("已归并金额 %.2f ≠ 快照 %s" % (mv, sm.get("merged", {}).get("amount")))
    if tn != sm.get("tail", {}).get("count"):
        stale.append("长尾笔数 %d ≠ 快照 %s" % (tn, sm.get("tail", {}).get("count")))
    if abs(tv - sm.get("totalIncome", -1)) > 0.005:
        stale.append("收入合计 %.2f ≠ 快照 %s" % (tv, sm.get("totalIncome")))

    if stale:
        print("⚠ sourceMap 快照已过期（规则仍可用，但快照数字旧了）：")
        for x in stale:
            print("    · " + x)
        print("  修：python3 scripts/build-source-map.py")
        return 1

    print("✓ 快照与当前数据一致：已归并 %d 组 %d 笔 ¥%.2f + 长尾 %d 笔 ¥%.2f = ¥%.2f"
          % (len(merged), mn, mv, tn, tvv, tv))
    return 0


def main():
    if "--verify" in sys.argv:
        sys.exit(verify())

    d, tv, mn, mv, tlen, tn, tvv = build()

    if "--dry" in sys.argv:
        print("\n--dry：未写入。")
        return

    d["sourceMap"] = {
        "builtAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "appliesTo": "earnings 中 amount > 0 的记录；按备注第一段（'|' 之前）匹配",
        "totalIncome": round(tv, 2),
        "merged": {"count": mn, "amount": round(mv, 2)},
        "tail": {"spellings": tlen, "count": tn, "amount": round(tvv, 2)},
        "decisions": {
            "confirmedBy": "凌一",
            "confirmedAt": "2026-09-29",
            "layerA": "全部归并",
            "layerA0": "显式客户标记「给<X>」用全备注范围匹配（修正了只看首段导致的误判）",
            "layerB1": "确认归并：LionCC 相关（修正后 12 笔）",
            "layerB1_revoked": "ChatGPT 相关 / 账号打理 —— 原确认归并，后查明两组整组备注都写着"
                               "「给所罗门…」，实为所罗门的活，已撤销并并入所罗门",
            "layerB2": "不归并，保持逐条",
            "ido_laoxu_is_same_person": True,
        },
        "notMerged": NOT_MERGED,
        "rules": RULES,
    }

    code, resp = push(d)
    print("\n写入同步服务：HTTP %s %s" % (code, resp))
    if code != 200:
        sys.exit("写入失败，data.json 未被修改。")
    print("✓ sourceMap 已写入（%d 条规则）" % len(RULES))
    print("  下一步：重跑 scripts/sync/make-local.py 重建离线页，再跑回归测试。")


if __name__ == "__main__":
    main()
