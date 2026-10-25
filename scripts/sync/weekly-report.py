#!/usr/bin/env python3
# 自动周报：把本周的收益 / 各平台发布 / 内容表现 / 站点 / 同步成功率
# 汇总成一份 Markdown 落盘到 reports/。全部数据来自本地快照，不联网、不写回任何源。
# 手动：python3 scripts/sync/weekly-report.py；服务端：POST /api/weekly。

import json
import os
import sys
from datetime import date, datetime, timedelta

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    try:
        with open(os.path.join(DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _week_bounds(today=None):
    """ISO 周：周一为起点。返回 (周一, 周日, ISO 年, ISO 周号)。"""
    d = today or date.today()
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    y, w, _ = d.isocalendar()
    return monday, sunday, y, w


def _money(v):
    return f"{v:,.2f}"


def build_report(today=None):
    monday, sunday, iso_year, iso_week = _week_bounds(today)
    start, end = monday.isoformat(), sunday.isoformat()
    data = _load("data.json") or {}
    social = _load("social.json") or {}
    web = _load("web-presence.json") or {}
    synclog = _load("sync-log.json") or []

    lines = []
    lines.append(f"# 周报 {iso_year} 第 {iso_week:02d} 周（{start} ~ {end}）")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- 数据窗口：本周（周一至周日，含未来日期的已有记录）")
    lines.append("")

    # —— 收益（data.json，负数记支出）——
    earnings = [e for e in data.get("earnings", []) if start <= (e.get("date") or "") <= end]
    income = sum(e["amount"] for e in earnings if e.get("amount", 0) > 0)
    expense = sum(e["amount"] for e in earnings if e.get("amount", 0) < 0)
    by_type = {}
    for e in earnings:
        if e.get("amount", 0) > 0:
            by_type[e.get("type") or "未分类"] = by_type.get(e.get("type") or "未分类", 0) + e["amount"]
    top_types = sorted(by_type.items(), key=lambda kv: -kv[1])[:5]
    lines.append("## 收益")
    if earnings:
        lines.append(f"- 收入 {_money(income)} 元（{sum(1 for e in earnings if e['amount'] > 0)} 笔），"
                     f"支出 {_money(expense)} 元，净额 {_money(income + expense)} 元")
        if top_types:
            lines.append("- 收入构成：" + "、".join(f"{t} {_money(v)}" for t, v in top_types))
    else:
        lines.append("- 本周暂无收益记录")
    lines.append("")

    # —— 各平台发布（social.json weeklyRecap，口径与页面一致）——
    recap = social.get("weeklyRecap") or {}
    per_platform = recap.get("perPlatform") or {}
    lines.append("## 内容发布")
    if per_platform:
        for name, s in per_platform.items():
            lines.append(f"- {name}：本周 {s.get('thisWeek', 0)} 篇（累计 {s.get('posts', 0)} 篇）")
    else:
        lines.append("- 暂无平台发布数据（social.json 缺 weeklyRecap）")
    best, worst = recap.get("best") or {}, recap.get("worst") or {}
    if best.get("title"):
        lines.append(f"- 表现最好：{best['platform']}「{(best['title'] or '')[:40]}」"
                     + (f"，阅读 {best.get('reads')}" if isinstance(best.get("reads"), (int, float)) else ""))
    if worst.get("title"):
        lines.append(f"- 表现最弱：{worst['platform']}「{(worst['title'] or '')[:40]}」"
                     + (f"，阅读 {worst.get('reads')}" if isinstance(worst.get("reads"), (int, float)) else ""))
    lines.append("")

    # —— 近 7 天发布明细（和运营体检同一口径：Date() 可解析的都算）——
    week_ago = (datetime.now() - timedelta(days=7)).date().isoformat()
    posts = social.get("posts") or []
    recent = []
    for p in posts:
        raw = str(p.get("publishedAt") or "")
        try:
            day = datetime.fromisoformat(raw).date().isoformat()
        except ValueError:
            try:
                day = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y").date().isoformat()
            except ValueError:
                day = raw[:10]
        if day >= week_ago:
            recent.append((day, p))
    if recent:
        lines.append("### 近 7 天发布明细")
        for day, p in sorted(recent, key=lambda x: x[0], reverse=True):
            title = str(p.get("title") or p.get("text") or "").replace("\n", " ")[:50]
            reads = p.get("metrics") or {}
            rd = reads.get("reads")
            lines.append(f"- {day} {p.get('platform')}「{title}」"
                         + (f"，阅读 {rd}" if isinstance(rd, (int, float)) else ""))
        lines.append("")

    # —— 站点（web-presence.json）——
    sites = web.get("sites") or []
    ok_sites = [s for s in sites if s.get("state") == "ok"]
    if sites:
        lines.append("## 网站")
        lines.append(f"- 站点可用：{len(ok_sites)}/{len(sites)} 正常"
                     + ("；" + "、".join(s.get("name") for s in sites if s.get("state") != "ok") if len(ok_sites) < len(sites) else ""))
        search = (web.get("search") or {}).get("sites") or []
        for s in search:
            lines.append(f"- GSC {s.get('name')}：点击 {s.get('clicks', 0)} · 曝光 {s.get('impressions', 0)}（{s.get('period', {}).get('start')} ~ {s.get('period', {}).get('end')}）")
        lines.append("")

    # —— 同步成功率（sync-log.json 最近 20 条）——
    if synclog:
        ok_n = sum(1 for x in synclog if x.get("ok"))
        total_n = len(synclog)
        avg = sum(float(x.get("elapsed") or 0) for x in synclog) / total_n
        lines.append("## 数据链路")
        lines.append(f"- 最近 {total_n} 次同步：成功 {ok_n} 次，平均耗时 {avg:.0f} 秒")
        lines.append("")

    lines.append("---")
    lines.append("*本报告由 scripts/sync/weekly-report.py 从本地快照生成，数据口径与工作台页面一致。*")
    return "\n".join(lines) + "\n"


def main():
    report = build_report()
    _, _, y, w = _week_bounds()
    out_dir = os.path.join(DIR, "reports")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"weekly-{y}-W{w:02d}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
