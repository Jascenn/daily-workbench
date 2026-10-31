#!/usr/bin/env python3
# 统计本机 Codex 历史会话的 token 总用量
# 数据源: ~/.codex/sessions、~/.codex/archived_sessions 及备份目录中的 rollout-*.jsonl
# 规则: 按会话去重，取每个会话最后一次 token_count 的 total_token_usage（会话内累计值）
import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

CODEX_HOME = Path.home() / ".codex"
SCAN_DIRS = [
    CODEX_HOME / "sessions",
    CODEX_HOME / "archived_sessions",
    CODEX_HOME / "backup-20260626-093641-session-visibility-repair",
    CODEX_HOME / "backups",
]
FIELDS = ["input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens"]


def iter_rollout_files():
    seen_paths = set()
    for root in SCAN_DIRS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("rollout-*.jsonl")):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            yield path


def session_id_of(path):
    # rollout 文件名末尾的 UUID 即会话 id（行数少时不必解析首行）
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$", path.name)
    return m.group(1) if m else None


def last_token_usage(path):
    last = None
    meta_ts = None
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                if b'"token_count"' not in raw and meta_ts is None:
                    if b'"session_meta"' in raw:
                        try:
                            meta_ts = json.loads(raw).get("timestamp")
                        except Exception:
                            pass
                    continue
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                if event.get("type") == "event_msg" and event.get("payload", {}).get("type") == "token_count":
                    info = event["payload"].get("info") or {}
                    if info.get("total_token_usage"):
                        last = info["total_token_usage"]
    except OSError as exc:
        print(f"跳过不可读文件 {path}: {exc}", file=sys.stderr)
    return meta_ts, last


def main():
    ap = argparse.ArgumentParser(description="统计 Codex 本地历史 token 用量")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    # 会话 id -> (最佳文件, 最后累计用量)；同一会话多份文件时取 total_tokens 最大的一份
    best = {}
    scanned = 0
    with_usage = 0
    for path in iter_rollout_files():
        scanned += 1
        sid = session_id_of(path) or str(path)
        ts, usage = last_token_usage(path)
        if not usage:
            continue
        with_usage += 1
        prev = best.get(sid)
        if prev is None or usage.get("total_tokens", 0) > prev[1].get("total_tokens", 0):
            best[sid] = (ts, usage)

    totals = defaultdict(int)
    for _, usage in best.values():
        for f in FIELDS:
            totals[f] += usage.get(f, 0)

    dates = sorted(ts[:10] for ts, _ in best.values() if ts)
    first_date, last_date = (dates[0], dates[-1]) if dates else (None, None)

    result = {
        "scanned_files": scanned,
        "sessions_with_token_data": with_usage,
        "unique_sessions_counted": len(best),
        "first_session_date": first_date,
        "last_session_date": last_date,
        "totals": dict(totals),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    t = totals
    fresh_input = t["input_tokens"] - t["cached_input_tokens"]
    print(f"扫描范围: {first_date} ~ {last_date}")
    print(f"扫描文件: {scanned}  含用量数据的文件: {with_usage}  去重后会话: {len(best)}")
    print(f"总 tokens:          {t['total_tokens']:,}")
    print(f"  input_tokens:     {t['input_tokens']:,}  (其中缓存命中 {t['cached_input_tokens']:,}, 非缓存 {fresh_input:,})")
    print(f"  output_tokens:    {t['output_tokens']:,}  (含推理 {t['reasoning_output_tokens']:,})")


if __name__ == "__main__":
    main()
