#!/usr/bin/env python3
"""Build the read-only 知识星球日报 snapshot consumed by the workbench.

只读取 zsxq-cli 能读到的星球当天新增主题，聚合成日报快照。设计约定：

* 只读：全程 group +list / group +topics，绝不发帖、评论或修改任何内容。
* 未知不写 0：未开通 Skill 权限、限流未取到的星球如实计入 coverage，
  不把「没抓到」说成「今天没更新」。
* 数据边界：统计过去 24 小时（滚动窗口）内的主题，而不是自然日。
  自然日在午夜会把昨晚的内容全过滤掉（2026-11-24 00:04 实测：昨天 9 条→当天 0 条），
  滚动窗口保证任何时刻跑都有内容。窗口起点/终点写进快照，前端如实标注。
  无内容时 items 为空，但 coverage 仍写明扫描了多少星球、多少可读。
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "zsxq-daily.json"

CLI = shutil.which("zsxq-cli")
if not CLI:
    for d in ("/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")):
        c = os.path.join(d, "zsxq-cli")
        if os.access(c, os.X_OK):
            CLI = c
            break


def _cli_env() -> dict:
    """补全 launchd 缺失的 node PATH，避免 zsxq-cli 报 env: node: No such file。"""
    env = dict(os.environ)
    extra: list[str] = []
    node = shutil.which("node")
    if node:
        extra.append(os.path.dirname(node))
    else:
        cands = sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin")), reverse=True)
        cands += [os.path.expanduser("~/Library/Application Support/fnm/aliases/default/bin"),
                  "/opt/homebrew/bin", "/usr/local/bin"]
        for d in cands:
            if os.access(os.path.join(d, "node"), os.X_OK):
                extra.append(d)
                break
    for d in ("/opt/homebrew/bin", "/usr/local/bin"):
        if d not in extra:
            extra.append(d)
    env["PATH"] = ":".join(extra + [env.get("PATH", "")])
    return env


def cli_json(args: list[str], timeout: int = 60) -> dict:
    """执行 zsxq-cli 并解析 JSON；失败抛真实原因，不静默当空。"""
    if not CLI:
        raise RuntimeError("zsxq-cli 未安装")
    p = subprocess.run([CLI, *args], capture_output=True, text=True, timeout=timeout, env=_cli_env())
    blob = (p.stdout or "") + (p.stderr or "")
    if "暂未开通 Skill 权限" in blob:
        raise PermissionError("星球未开通 Skill 权限")
    if "Rate limit exceeded" in blob:
        raise RuntimeError("rate limit")
    try:
        val = json.loads(p.stdout)
    except ValueError:
        raise RuntimeError(blob.strip()[:160] or "zsxq-cli 返回非 JSON")
    if not isinstance(val, dict):
        raise RuntimeError("zsxq-cli 返回不是对象")
    return val


def plain(text: str) -> str:
    """去掉星球正文里的 <e> 标签并解 URL 编码，压缩成可读摘要。"""
    s = urllib.parse.unquote(text or "")
    s = re.sub(r"<e [^>]*/>", "", s)
    s = re.sub(r"!\[\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\n{2,}", "\n", s).strip()
    # 星球正文常整段重复（接口特性），按换行切块去重，保留首次出现顺序
    seen, lines = set(), []
    for line in s.split("\n"):
        key = line.strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        lines.append(line)
    return "\n".join(lines).strip()


def excerpt(text: str, limit: int = 260) -> str:
    s = plain(text)
    # 取正文首段做摘要，去掉纯标签行
    body = "\n".join(l for l in s.split("\n") if l.strip() and not l.strip().startswith("#"))
    return body[:limit]


def hashtags(topic: dict) -> list[str]:
    raw = (topic.get("annotation") or "") + " " + (topic.get("title") or "")
    found = re.findall(r"title=\"%23([^%]+)%23\"", urllib.parse.unquote(raw))
    if found:
        return [urllib.parse.unquote(x) for x in found[:6]]
    return [x.strip("# ") for x in re.findall(r"#[^\s#]+", urllib.parse.unquote(raw))[:6]]


def category_of(tags: list[str], text: str) -> str:
    """按标签/关键词把主题归入日报的三大类；未命中归入「其他」。"""
    blob = " ".join(tags) + " " + text
    if re.search(r"WorkBuddy|AI|ima|bot|Radar|雷达|智能体|agent", blob, re.I):
        return "AI 工作流"
    if re.search(r"贴图|选题|写作|软文|带货|公众号|burst|爆款|认知壁垒|输出", blob):
        return "写作方法论"
    if re.search(r"副业|变现|工作台|赛道|赚钱|成长|韧性|小红书", blob):
        return "副业认知与赛道"
    return "其他"


def _parse_time(value: str) -> dt.datetime | None:
    """解析 zsxq 的 create_time（如 2026-11-23T21:52:06.394+0800）。"""
    try:
        return dt.datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _topic_to_item(t: dict, gid: str, name: str) -> dict:
    """把单条 topics_brief 映射为日报/周报统一条目。"""
    text = plain(t.get("content") or "")
    tags = hashtags(t)
    cats = category_of(tags, text)
    counts = t.get("counts") or {}
    return {
        "id": str(t.get("topic_id") or ""),
        "title": (t.get("title") or "").split("\n")[0].strip() or text[:40],
        "excerpt": excerpt(t.get("content") or ""),
        "group": name,
        "groupId": gid,
        "publishedAt": t.get("create_time"),
        "type": t.get("type"),
        "category": cats,
        "tags": tags,
        "digested": bool(t.get("digested")),
        "metrics": {
            "likes": counts.get("likes"),
            "comments": counts.get("comments"),
            "reads": counts.get("readers"),
        },
        "url": f"https://wx.zsxq.com/group/{gid}/topic/{t.get('topic_id')}",
        "mobileUrl": f"https://wx.zsxq.com/mweb/views/topicdetail/topicdetail.html?topic_id={t.get('topic_id')}&group_id={gid}",
    }


def _highlight_score(it: dict) -> tuple[int, int, str]:
    """周报精选排序键：精华优先，其次按互动权重（评×4 + 赞×3 + 阅//20），最后按时间倒序。"""
    m = it.get("metrics") or {}
    likes = int(m.get("likes") or 0)
    comments = int(m.get("comments") or 0)
    reads = int(m.get("reads") or 0)
    score = comments * 4 + likes * 3 + reads // 20
    return (1 if it.get("digested") else 0, score, str(it.get("publishedAt") or ""))


def summarize_weekly(items_7d: list[dict], window_start: dt.datetime, window_end: dt.datetime,
                     top_n: int = 5) -> dict:
    """从过去 7 天条目汇总知识星球周报结构（纯函数，便于离线回归测试）。"""
    ordered = sorted(items_7d, key=lambda x: x.get("publishedAt") or "", reverse=True)
    by_cat: dict[str, int] = {}
    group_counts: dict[str, int] = {}
    for it in ordered:
        cat = str(it.get("category") or "其他")
        by_cat[cat] = by_cat.get(cat, 0) + 1
        grp = str(it.get("group") or "未知星球")
        group_counts[grp] = group_counts.get(grp, 0) + 1
    ranked_groups = dict(sorted(group_counts.items(), key=lambda kv: (-kv[1], kv[0])))
    highlights = sorted(ordered, key=_highlight_score, reverse=True)[:top_n]
    return {
        "days": 7,
        "windowStart": window_start.isoformat(timespec="seconds"),
        "windowEnd": window_end.isoformat(timespec="seconds"),
        "total": len(ordered),
        "withItems": len(ranked_groups),
        "digestedCount": sum(1 for it in ordered if it.get("digested")),
        "byCategory": by_cat,
        "groupCounts": ranked_groups,
        "highlights": highlights,
        "items": ordered,
    }


def build() -> dict:
    now = dt.datetime.now().astimezone()
    window_start = now - dt.timedelta(hours=24)
    weekly_start = now - dt.timedelta(days=7)
    groups = cli_json(["group", "+list", "--limit", "200", "--json"]).get("groups") or []
    groups = [g for g in groups if isinstance(g, dict) and g.get("group_id")]

    items: list[dict] = []
    weekly_items: list[dict] = []
    stat = {"scanned": len(groups), "readable": 0, "noPermission": 0,
            "rateLimited": 0, "error": 0, "withToday": 0, "withWeekly": 0}
    failed_names: list[str] = []

    # 低并发由调用节奏控制：串行 + 小间隔，254 个星球约 2 分钟，避免触发限流。
    # 单次扫描同时产出 24h 日报与 7 天周报（每个可读星球已取回最近 30 条），零额外 API 请求。
    for idx, g in enumerate(groups, 1):
        gid, name = str(g["group_id"]), str(g.get("name") or "")
        time.sleep(0.12 + (idx % 5) * 0.03)
        topics: list[dict] = []
        for attempt in range(3):
            try:
                d = cli_json(["group", "+topics", "--group-id", gid, "--limit", "30", "--json"])
                topics = [t for t in (d.get("topics_brief") or []) if isinstance(t, dict)]
                stat["readable"] += 1
                break
            except PermissionError:
                stat["noPermission"] += 1
                break
            except RuntimeError as e:
                if "rate limit" in str(e) and attempt < 2:
                    time.sleep(3 * (attempt + 1))
                    continue
                stat["rateLimited"] += 1
                failed_names.append(name)
                break
            except Exception:
                stat["error"] += 1
                failed_names.append(name)
                break

        has_24h = False
        has_7d = False
        for t in topics:
            created = _parse_time(str(t.get("create_time") or ""))
            if not created or created < weekly_start:
                continue
            item = _topic_to_item(t, gid, name)
            weekly_items.append(item)
            has_7d = True
            if created >= window_start:
                items.append(item)
                has_24h = True
        if has_24h:
            stat["withToday"] += 1
        if has_7d:
            stat["withWeekly"] += 1

    items.sort(key=lambda x: x.get("publishedAt") or "", reverse=True)
    # 统计各分类条数
    by_cat: dict[str, int] = {}
    for it in items:
        by_cat[it["category"]] = by_cat.get(it["category"], 0) + 1
    weekly = summarize_weekly(weekly_items, weekly_start, now)

    return {
        "version": 2,
        "date": now.date().isoformat(),
        "windowStart": window_start.isoformat(timespec="seconds"),
        "windowEnd": now.isoformat(timespec="seconds"),
        "generatedAt": now.isoformat(timespec="seconds"),
        "dataPolicy": "只读 zsxq-cli 拉取过去 24 小时日报与 7 天周报主题；未开通权限/限流的星球不计入『没更新』。",
        "coverage": stat,
        "coverageNote": (
            f"共扫描 {stat['scanned']} 个星球，可读 {stat['readable']} 个，"
            f"未开通 Skill 权限 {stat['noPermission']} 个，"
            f"限流 {stat['rateLimited']} 个，其他失败 {stat['error']} 个；"
            f"24h 有更新 {stat['withToday']} 个，7 天有更新 {stat['withWeekly']} 个。"
            + (f" 未取到的星球：{'、'.join(failed_names[:12])}" + ("…" if len(failed_names) > 12 else "") if failed_names else "")
        ),
        "byCategory": by_cat,
        "items": items,
        "weekly": weekly,
    }


def main():
    data = build()
    content = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(content, encoding="utf-8")
    try:
        os.replace(tmp, OUT)  # 首选：原子替换，避免读到半截文件
    except PermissionError:
        # 沙箱/受限环境禁止 rename 覆盖已存在文件，退回直写（派生快照可接受）。
        # tmp 保留不删，避免再触发一次被禁的 unlink。
        OUT.write_text(content, encoding="utf-8")
    c = data["coverage"]
    w = data.get("weekly") or {}
    print(f"知识星球简报 {data['date']}：24h {len(data['items'])} 条 / 7天 {w.get('total', 0)} 条 · "
          f"可读 {c['readable']} / 无权限 {c['noPermission']} / 限流 {c['rateLimited']}")


if __name__ == "__main__":
    main()
