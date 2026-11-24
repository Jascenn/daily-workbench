"""
[INPUT]: 依赖本地 ~/.config/daily-workbench/wechat-topic-state.json（上轮摘要缓存）与 wechat-groups.json（白名单群配置），可选依赖 wechat_semantic 的本地 Ollama 能力
[OUTPUT]: 导出 apply_progress_diff(accounts, state_path) 与 collect_learning_topics(client, base, listed, now, config_path, sem)
[POS]: scripts/sync/wechat-sync.py 的批奏折增强插件，负责跨轮次进展 Diff 对比与白名单高价值微信群选题提炼，严格只读不发消息
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_STATE_PATH = Path.home() / ".config" / "daily-workbench" / "wechat-topic-state.json"
DEFAULT_GROUPS_PATH = Path.home() / ".config" / "daily-workbench" / "wechat-groups.json"
MAX_STATE_ENTRIES = 300


def _state_path(override: str | Path | None = None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("WECHAT_TOPIC_STATE_PATH", "").strip()
    return Path(env) if env else DEFAULT_STATE_PATH


def _groups_path(override: str | Path | None = None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("WECHAT_GROUPS_CONFIG", "").strip()
    return Path(env) if env else DEFAULT_GROUPS_PATH


def _parse_ts(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _item_summary(item: dict, kind: str) -> str:
    if kind == "waiting":
        return str(item.get("want") or item.get("preview") or "").strip()[:120]
    if kind == "opportunities":
        return str(item.get("evidence") or item.get("whyRelated") or "").strip()[:120]
    if kind == "consultations":
        return str(item.get("surfaceQuestion") or item.get("underlyingNeed") or "").strip()[:120]
    if kind == "learningTopics":
        return str(item.get("summary") or item.get("title") or "").strip()[:120]
    return ""


def _state_key(account_id: str, kind: str, item: dict) -> str:
    cid = str(item.get("conversationId") or item.get("id") or item.get("name") or item.get("contact") or "").strip()
    return f"{account_id}:{kind}:{cid}" if cid else ""


def apply_progress_diff(accounts: list[dict], state_path: str | Path | None = None) -> int:
    """对比上轮同步状态：当同会话出现更新消息且摘要变化时，写入 prevSummary / latestDelta / hasProgress。"""
    path = _state_path(state_path)
    raw_state = _load_json(path)
    prev_topics: dict[str, dict] = {}
    if isinstance(raw_state, dict) and isinstance(raw_state.get("topics"), dict):
        prev_topics = dict(raw_state["topics"])

    next_topics = dict(prev_topics)
    progressed = 0
    now_iso = dt.datetime.now().astimezone().isoformat(timespec="seconds")

    for acc in accounts or []:
        aid = str((acc.get("account") or {}).get("id") or "default")
        for kind in ("waiting", "opportunities", "consultations", "learningTopics"):
            for item in acc.get(kind) or []:
                if not isinstance(item, dict):
                    continue
                key = _state_key(aid, kind, item)
                if not key:
                    continue
                cur_at = str(item.get("at") or "")
                cur_sum = _item_summary(item, kind)
                if not cur_sum:
                    continue
                old = prev_topics.get(key)
                if isinstance(old, dict):
                    old_sum = str(old.get("summary") or "").strip()
                    old_ts = _parse_ts(old.get("at"))
                    cur_ts = _parse_ts(cur_at)
                    if old_sum and old_sum != cur_sum and cur_ts > old_ts:
                        item["prevSummary"] = old_sum
                        item["latestDelta"] = cur_sum
                        item["hasProgress"] = True
                        progressed += 1
                next_topics[key] = {
                    "at": cur_at,
                    "summary": cur_sum,
                    "updatedAt": now_iso,
                }

    # 按最近更新时间裁剪，避免状态文件无限膨胀
    if len(next_topics) > MAX_STATE_ENTRIES:
        ordered = sorted(
            next_topics.items(),
            key=lambda kv: str((kv[1] or {}).get("updatedAt") or ""),
            reverse=True,
        )[:MAX_STATE_ENTRIES]
        next_topics = dict(ordered)

    try:
        _atomic_write_json(path, {"version": 1, "updatedAt": now_iso, "topics": next_topics})
    except OSError:
        pass
    return progressed


def load_whitelist_groups(config_path: str | Path | None = None) -> tuple[list[str], int]:
    """读取白名单群配置；支持 ["群A", "群B"] 或 {"groups": [...], "lookbackHours": 24}。"""
    cfg = _load_json(_groups_path(config_path))
    if isinstance(cfg, list):
        names = [str(x).strip() for x in cfg if str(x).strip()]
        return names, 24
    if isinstance(cfg, dict):
        names = [str(x).strip() for x in (cfg.get("groups") or []) if str(x).strip()]
        hours = int(cfg.get("lookbackHours") or 24)
        return names, max(1, min(168, hours))
    return [], 24


def _summarize_group_with_ollama(group_name: str, lines: list[str], model: str) -> dict | None:
    if not lines or os.environ.get("WECHAT_GROUP_LLM_DISABLE") == "1":
        return None
    url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/chat").strip()
    prompt = (
        "请阅读以下高价值微信群的最近聊天片段，提炼出最值得记录的 1 个核心学习点或内容选题。"
        "严格只输出 JSON 对象，键名必须是 title（≤28字）、summary（≤70字）、action（≤50字，给出一句可落地的选题或复盘建议）。\n"
        f"群名：{group_name}\n聊天记录：\n" + "\n".join(lines[:20])
    )
    payload = json.dumps({
        "model": model,
        "stream": False,
        "think": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": {"temperature": 0.3, "num_predict": 220},
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        content = str(((data.get("message") or {}).get("content")) or "")
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        m = re.search(r"\{.*\}", content, flags=re.S)
        parsed = json.loads(m.group(0)) if m else {}
        if isinstance(parsed, dict) and str(parsed.get("summary") or "").strip():
            return {
                "title": str(parsed.get("title") or "").strip()[:40],
                "summary": str(parsed.get("summary") or "").strip()[:120],
                "action": str(parsed.get("action") or "").strip()[:80],
            }
    except Exception:
        pass
    return None


def collect_learning_topics(
    client: Any,
    base: dict,
    listed: list[dict],
    now: dt.datetime,
    text_preview_fn: Any,
    fingerprint_fn: Any,
    config_path: str | Path | None = None,
    llm_model: str = "",
) -> list[dict]:
    """仅抓取用户白名单内高价值微信群的近 N 小时讨论，提炼为 learningTopics。"""
    whitelist, lookback_hours = load_whitelist_groups(config_path)
    if not whitelist:
        return []
    cutoff_ts = (now - dt.timedelta(hours=lookback_hours)).timestamp()
    matched = []
    for conv in listed or []:
        cid = str(conv.get("conversation_id") or "")
        name = str(conv.get("name") or conv.get("display_name") or "").strip()
        if not name or ("@chatroom" not in cid and conv.get("kind") != "group"):
            continue
        if any(w == name or w in name for w in whitelist):
            matched.append((cid, name))

    topics: list[dict] = []
    for cid, name in matched[:8]:
        try:
            resp = client.call("messages.list", {**base, "conversation_id": cid, "limit": 20}) or {}
            msgs = resp.get("messages") or []
        except Exception:
            continue
        recent_lines: list[str] = []
        latest_at = ""
        for m in msgs:
            ts = _parse_ts(m.get("created_at") or m.get("timestamp"))
            if ts and ts < cutoff_ts:
                continue
            txt = str(text_preview_fn(m) or "").strip()
            if not txt or txt in ("[图片]", "[表情]", "[语音]"):
                continue
            sender = str(m.get("sender_name") or m.get("sender") or "群友").strip()[:16]
            recent_lines.append(f"{sender}：{txt[:140]}")
            if not latest_at and (m.get("created_at") or m.get("timestamp")):
                latest_at = str(m.get("created_at") or m.get("timestamp"))
        if len(recent_lines) < 2:
            continue
        llm_res = _summarize_group_with_ollama(name, recent_lines, llm_model) if llm_model else None
        fp = fingerprint_fn(cid)
        tid = "learn_" + hashlib.sha256(f"{fp}:{latest_at[:10]}".encode("utf-8")).hexdigest()[:12]
        topics.append({
            "id": tid,
            "conversationId": fp,
            "name": name,
            "kind": "group",
            "at": latest_at or now.isoformat(timespec="seconds"),
            "title": (llm_res or {}).get("title") or f"{name} 近 {lookback_hours}h 讨论精选",
            "summary": (llm_res or {}).get("summary") or recent_lines[0][:100],
            "action": (llm_res or {}).get("action") or "挑 1 个高价值观点沉淀为星球帖或写作素材",
            "sourcePreview": "\n".join(recent_lines[:6]),
            "messageCount": len(recent_lines),
            "analyzed": bool(llm_res),
        })
    return topics
