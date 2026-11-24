#!/usr/bin/env python3
"""把知识星球的高阅读内容变成待办任务（每周一次）。

为什么是"候选"而不是"自动派活"：阅读量只说明有人看，不说明值得写。
所以生成的是一条可丢弃的任务卡，标题里带读物与阅读数，方便你一眼判断要不要做。

三条硬约束（都踩过）：
1. 主题标题里全是 `<e type="hashtag" .../>` 和 URL 编码，必须剥干净再当任务名。
2. 项目的「关联任务」和任务的「所属项目」是两条独立 relation，**两边都要写**，
   否则项目那边永远是 0。
3. 同一个主题不能每周重复建任务：按 topicId 记状态，已建过就跳过。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SOCIAL = ROOT / "social.json"
CONFIG = Path.home() / ".config" / "daily-workbench" / "zsxq-task-suggest.json"
STATE = Path.home() / ".config" / "daily-workbench" / "zsxq-task-suggest-state.json"

DEFAULTS = {
    "projectId": "00000000-0000-4000-8000-000000000008",   # 知识星球 · 内容生产
    "projectName": "知识星球 · 内容生产",
    "taskDataSourceId": "00000000-0000-4000-8000-000000000003",
    "topN": 3,
    # 星球发帖节奏很稀（有时一月一篇），30 天窗口经常是空的；用 60 天更实在，
    # 旧帖靠 topicId 去重挡，不会重复建任务。
    "windowDays": 60,
    "minReads": 5,
}


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def config() -> dict:
    merged = dict(DEFAULTS)
    merged.update(load_json(CONFIG, {}) or {})
    return merged


def clean_text(text: str) -> str:
    """剥掉星球的富文本标记与 URL 编码，得到能读的纯文本。"""
    t = str(text or "")
    t = re.sub(r"<e[^>]*/>", "", t)
    t = re.sub(r"<e[^>]*>.*?</e>", "", t, flags=re.S)
    t = urllib.parse.unquote(t)
    t = re.sub(r"#\S+#", "", t)
    t = re.sub(r"#\S+", "", t)   # 单个话题标签（如 #碎屑）也要去掉
    return " ".join(t.split())


def first_sentence(text: str, limit: int = 24) -> str:
    """取第一句有信息量的话，并且**不要切在半句上**。

    "最近都在使用agent批量投稿自己的站，特别是做" 这种结尾读起来像被截断，
    优先停在标点上；实在没有标点才硬截，并去掉尾随虚词。
    """
    body = clean_text(text)
    chunk = ""
    for part in re.split(r"[。！？\n]", body):
        part = part.strip(" ，,、")
        if len(part) >= 6:
            chunk = part
            break
    if not chunk:
        chunk = body.strip()
    if len(chunk) <= limit:
        return chunk
    cut = max((i for i, ch in enumerate(chunk[:limit]) if ch in "，、：；,;"), default=-1)
    if cut >= int(limit * 0.5):
        return chunk[:cut].strip(" ，,、")
    head = chunk[:limit].rstrip(" ，,、")
    while head and head[-1] in "的了和与是在把被对":
        head = head[:-1]
    return head


def fetch_topic_text(topic_id: str) -> str:
    """拉主题详情拿正文。列表接口给的是 60 字截断预览，直接拿来当任务名会带"..."。"""
    try:
        proc = subprocess.run(["zsxq-cli", "topic", "+detail", "--topic-id", str(topic_id), "--json"],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return ""
        data = json.loads(proc.stdout or "{}")
        topic = data.get("topic") or data.get("resp_data", {}).get("topic") or {}
        return str(topic.get("content") or (topic.get("talk") or {}).get("text") or "")
    except Exception:
        return ""


def build_task_name(reads, snippet: str) -> str:
    return "二次创作：%s（星球 %s 阅读）" % (snippet or "无标题", reads)


def select_candidates(posts: list[dict], state: dict, cfg: dict, now: dt.datetime | None = None) -> list[dict]:
    """挑出本轮要建任务的条目。

    时间窗内没有新内容就返回空——不拿三个月前的旧帖反复生成任务。
    """
    now = now or dt.datetime.now().astimezone()
    cutoff = now - dt.timedelta(days=int(cfg["windowDays"]))
    picked = []
    for p in posts:
        if "知识星球" not in (p.get("platform") or ""):
            continue
        metrics = p.get("metrics") or {}
        reads = metrics.get("reads")
        if not isinstance(reads, (int, float)) or reads < cfg["minReads"]:
            continue
        topic_id = str((p.get("url") or "").rstrip("/").split("/")[-1] or "")
        if not topic_id or topic_id in state:
            continue
        try:
            published = dt.datetime.fromisoformat(str(p.get("publishedAt") or ""))
        except ValueError:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=now.tzinfo)
        if published < cutoff:
            continue
        picked.append({"topicId": topic_id, "reads": reads, "title": p.get("title") or "",
                       "url": p.get("url") or "", "publishedAt": str(p.get("publishedAt") or "")})
    picked.sort(key=lambda x: (-x["reads"], x["publishedAt"]), reverse=False)
    picked.sort(key=lambda x: x["reads"], reverse=True)
    return picked[: int(cfg["topN"])]


def run_ntn(args: list[str], payload: dict | None = None, timeout: int = 90) -> dict:
    cmd = ["ntn", "api", *args]
    if payload is not None:
        cmd += ["-d", json.dumps(payload, ensure_ascii=False)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip().splitlines()[-1][:200])
    return json.loads(proc.stdout or "{}")


def create_task(cfg: dict, name: str) -> str:
    payload = {
        "parent": {"type": "data_source_id", "data_source_id": cfg["taskDataSourceId"]},
        "properties": {
            "任务名称": {"title": [{"text": {"content": name}}]},
            "状态": {"status": {"name": "还未开始"}},
            "所属项目": {"relation": [{"id": cfg["projectId"]}]},
        },
    }
    data = run_ntn(["/v1/pages", "-X", "POST"], payload)
    task_id = data.get("id")
    if not task_id:
        raise RuntimeError("创建任务未返回 id")
    return task_id


def link_tasks_to_project(cfg: dict, task_ids: list[str]) -> int:
    """把新任务并进项目的「关联任务」。必须先读再写，否则会覆盖已有关系。"""
    page = run_ntn(["/v1/pages/%s" % cfg["projectId"]])
    existing = [x.get("id") for x in ((page.get("properties") or {}).get("关联任务") or {}).get("relation") or []]
    merged = list(dict.fromkeys([i for i in existing if i] + task_ids))
    run_ntn(["/v1/pages/%s" % cfg["projectId"], "-X", "PATCH"],
            {"properties": {"关联任务": {"relation": [{"id": i} for i in merged]}}})
    return len(merged)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印会建什么，不动 Notion")
    ap.add_argument("--window-days", type=int, help="覆盖时间窗（排查或补跑用）")
    ap.add_argument("--top-n", type=int, help="覆盖本轮条数")
    ap.add_argument("--min-reads", type=float, help="覆盖阅读数下限")
    args = ap.parse_args(argv)

    cfg = config()
    if args.window_days:
        cfg["windowDays"] = args.window_days
    if args.top_n:
        cfg["topN"] = args.top_n
    if args.min_reads is not None:
        cfg["minReads"] = args.min_reads
    social = load_json(SOCIAL, {})
    posts = social.get("posts") or []
    state = load_json(STATE, {})
    picks = select_candidates(posts, state, cfg)
    if not picks:
        print("本周没有新的高阅读内容（窗口 %d 天 / 最少 %s 阅读），不生成任务。"
              % (cfg["windowDays"], cfg["minReads"]))
        return 0

    created = []
    for item in picks:
        body = fetch_topic_text(item["topicId"])
        snippet = first_sentence(body) if body.strip() else first_sentence(item["title"])
        name = build_task_name(item["reads"], snippet)
        if args.dry_run:
            print("[dry-run] 会建：%s" % name)
            continue
        task_id = create_task(cfg, name)
        created.append(task_id)
        state[item["topicId"]] = {"taskId": task_id, "name": name,
                                  "at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        print("✓ %s" % name)

    if args.dry_run:
        return 0

    total = link_tasks_to_project(cfg, created)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE)
    print("已挂到「%s」，项目现有 %d 条关联任务。" % (cfg["projectName"], total))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
