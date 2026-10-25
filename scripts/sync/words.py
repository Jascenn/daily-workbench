#!/usr/bin/env python3
"""New-word radar snapshot for the WorkBuddy workbench.

Pure standard library: no pip packages needed. Fetches the public
words.json from the word-radar site and reports whether a new week of
words has appeared since the last acknowledged one.

Called from web-monitor.py (every 5 min). The remote fetch runs at most
once every REFRESH_HOURS; otherwise the on-disk cache is returned.
This module never raises: on any failure it returns the last good cache
with an error note.

Wiring in web-monitor.py (add once):

    def _words_section() -> dict | None:
        try:
            from words import words_snapshot
            return words_snapshot()
        except Exception as exc:
            return {"error": "新词模块加载失败：%s" % str(exc)[:160], "isNew": False}

    # in build(), after the GSC alerts block:
    words = _words_section()
    if isinstance(words, dict):
        latest = words.get("latestWeek") or {}
        if words.get("isNew") and latest.get("words"):
            names = "、".join(w.get("term", "") for w in latest["words"][:6])
            if len(latest["words"]) > 6:
                names += "等"
            alerts.insert(0, {
                "tone": "ok",
                "title": "新词雷达：本周捕获 %d 个新词" % len(latest["words"]),
                "detail": names,
            })
            try:
                from words import mark_seen
                mark_seen(str(latest.get("id") or ""))
            except Exception:
                pass
    # and add "words": words to the returned snapshot dict.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "words-cache.json"
SEEN_FILE = ROOT / "words-seen.json"

WORDS_URL = os.environ.get("WB_WORDS_URL", "")  # 你的每日一词 JSON 源；留空则该模块禁用

REFRESH_HOURS = 6
HTTP_TIMEOUT = 15


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _cache_fresh_enough(cached: dict | None) -> bool:
    if not cached:
        return False
    try:
        gen = dt.datetime.fromisoformat(str(cached.get("generatedAt", "")))
        now = dt.datetime.now(dt.timezone.utc)
        try:
            age = now.astimezone(gen.tzinfo) - gen
        except (TypeError, ValueError):
            age = now - gen.replace(tzinfo=dt.timezone.utc)
        return age < dt.timedelta(hours=REFRESH_HOURS)
    except (ValueError, TypeError):
        return False


def _refresh() -> dict:
    req = urllib.request.Request(
        WORDS_URL, headers={"User-Agent": "WorkBuddy-words/1.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    weeks = payload.get("weeks") or []
    if not weeks:
        raise RuntimeError("words.json has no weeks")
    latest = weeks[0]
    seen = _read_json(SEEN_FILE) or {}
    seen_id = seen.get("weekId")
    return {
        "version": 1,
        "generatedAt": dt.datetime.now(
            dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
        "latestWeek": {
            "id": latest.get("id"),
            "label": latest.get("label"),
            "date": latest.get("date"),
            "words": [
                {"term": w.get("term"), "desc": w.get("desc"),
                 "source": w.get("source"), "date": w.get("date")}
                for w in (latest.get("words") or [])
            ],
        },
        "isNew": bool(latest.get("id")) and latest.get("id") != seen_id,
        "seenWeekId": seen_id,
    }


def words_snapshot() -> dict:
    """Cached new-word radar state for web-monitor.py. Never raises."""
    try:
        cached = _read_cache()
        if _cache_fresh_enough(cached):
            # re-evaluate isNew against the seen file in case it changed
            seen = _read_json(SEEN_FILE) or {}
            cached = dict(cached)
            latest_id = (cached.get("latestWeek") or {}).get("id")
            cached["isNew"] = bool(latest_id) and latest_id != seen.get("weekId")
            cached["seenWeekId"] = seen.get("weekId")
            return cached
        fresh = _refresh()
        _write_json(CACHE, fresh)
        return fresh
    except Exception as e:  # noqa: BLE001 - monitor loop must not die
        cached = _read_json(CACHE)
        if cached:
            cached = dict(cached)
            cached["error"] = "刷新失败，显示上次成功数据：%s" % str(e)[:160]
            return cached
        return {"error": str(e)[:200], "isNew": False}


def _read_cache() -> dict | None:
    return _read_json(CACHE)


def mark_seen(week_id: str) -> None:
    """Acknowledge a week after its alert has been queued. Never raises."""
    try:
        _write_json(SEEN_FILE, {
            "weekId": week_id,
            "seenAt": dt.datetime.now(
                dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
        })
    except Exception:
        pass


if __name__ == "__main__":
    snap = words_snapshot()
    print(json.dumps(snap, ensure_ascii=False, indent=2)[:2000])
