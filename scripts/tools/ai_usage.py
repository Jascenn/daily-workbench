#!/usr/bin/env python3
"""AI 用量快照：Jev 模型连通性 + token 面板基线 + 本机 Codex 明细下钻。

三个数据源、三个口径，绝不互相加总（面板含 Codex，相加就是重复计数）：
  1. panel   —— vibecodingapi.ai 中转后台面板的固定基线
                （2026-10-12 用户确认：7.94B tokens / $5,739.82 / 69,941 请求，
                覆盖全部走中转的工具和旧设备；接口侧 access token 未配置，
                配好后在 TOKEN_FILE 里放 accessToken 即可自动切实时查询）
  2. codex   —— 本机 ~/.codex 会话日志统计（可选模块 token-usage.py，开源版默认
                不带；存在时自动启用，不存在时如实返回 unavailable）
  3. jev     —— TypeSafe Jev 语义模型实时探测（走本机 7890 代理，
                一次最小 systemone 判断调用，同时取回官方 token 用量）

纯标准库。给 web-monitor.py 调用：上层永不抛异常，失败降级为上次缓存 +
错误备注。Jev 探测最多 6 小时一次；Codex 扫描最多 12 小时一次（全量扫
jsonl 约 9 秒）；面板基线不联网。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "ai-usage-cache.json"

PANEL_FILE = Path(os.path.expanduser("~/.config/daily-workbench/relay-usage.json"))
JEV_ENV = Path(os.path.expanduser("~/.config/jev-jarvis/env"))
TOKEN_USAGE = Path(__file__).resolve().parent / "token-usage.py"

PROXY = urllib.request.ProxyHandler({
    "http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"})
OPENER = urllib.request.build_opener(PROXY)

JEV_REFRESH_HOURS = 6
HTTP_TIMEOUT = 25

# 2026-10-12 从 vibecodingapi.ai 面板人工确认的基线（全工具口径，含旧设备）。
PANEL_BASELINE = {
    "tokens": 7_940_000_000,
    "costUsd": 5739.82,
    "requests": 69941,
    "cacheHitRate": 0.857,
    "confirmedAt": "2026-10-12",
}

JEV_PROBE_QUESTIONS = {
    "intent": {
        "type": "choice",
        "instructions": "这句话的真实意图是什么？",
        "criteria": {"任务": "需要执行动作", "提问": "需要回答", "闲聊": "寒暄"},
    }
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def _read_cache() -> dict:
    try:
        value = json.loads(CACHE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _section_fresh(cached: dict, key: str, hours: float) -> bool:
    sec = cached.get(key)
    if not isinstance(sec, dict) or sec.get("error"):
        return False
    try:
        gen = dt.datetime.fromisoformat(str(sec.get("checkedAt", "")))
        age = dt.datetime.now(dt.timezone.utc).astimezone(gen.tzinfo) - gen
        return age < dt.timedelta(hours=hours)
    except (ValueError, TypeError):
        return False


def _read_env(path: Path) -> dict:
    out = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _jev_probe() -> dict:
    """一次最小 Jev systemone 调用：既验证连通，也取回官方 token 用量。"""
    cfg = _read_env(JEV_ENV)
    key = cfg.get("TYPESAFE_API_KEY", "")
    base = (cfg.get("TYPESAFE_BASE_URL") or "https://api.typesafe.ai").rstrip("/")
    model = cfg.get("TYPESAFE_MODEL") or "jev-latest"
    if not key:
        return {"configured": False, "note": "未找到 TYPESAFE_API_KEY（~/.config/jev-jarvis/env）"}
    payload = {
        "model": model,
        "state": "工作台每日健康检查：请判断这句示例消息的意图。",
        "questions": JEV_PROBE_QUESTIONS,
    }
    req = urllib.request.Request(
        base + "/v1/systemone", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        resp = OPENER.open(req, timeout=HTTP_TIMEOUT)
        data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 快照采集不向上抛
        code = getattr(exc, "code", None)
        detail = ""
        if code is not None:
            try:
                detail = exc.read().decode("utf-8")[:160]  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                detail = ""
        return {"configured": True, "ok": False,
                "error": "HTTP %s %s" % (code or "?", detail or str(exc)[:120])}
    usage = data.get("usage") or {}
    return {
        "configured": True, "ok": True,
        "model": data.get("model") or model,
        "requestModel": model,
        "state": "已连通（走本机 7890 代理）",
        "inputTokens": usage.get("input_tokens"),
        "outputTokens": usage.get("output_tokens"),
        "checkedAt": now_iso(),
    }


def _panel_section() -> dict:
    """面板口径：默认固定基线；TOKEN_FILE 配了 accessToken 后走实时接口。"""
    section: dict = {
        "source": "vibecodingapi.ai 面板",
        "scope": "全部走中转的工具（Codex + Claude Code + 其他），含旧设备",
        "mode": "baseline",
        **PANEL_BASELINE,
    }
    try:
        token = PANEL_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token or token.startswith("{{"):
        section["note"] = "当前为人工确认的基线值；在 relay-usage.json 放入 accessToken 即切换为实时查询"
        return section
    req = urllib.request.Request(
        "https://vibecodingapi.ai/api/user/self",
        headers={"Authorization": "Bearer " + token, "New-Api-User": "5"})
    try:
        resp = urllib.request.build_opener().open(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        section["mode"] = "baseline"
        section["error"] = "实时查询失败，仍显示基线：%s" % str(exc)[:120]
        return section
    if not (data.get("success") and isinstance(data.get("data"), dict)):
        section["error"] = "接口返回异常，仍显示基线：%s" % str(data.get("message"))[:120]
        return section
    d = data["data"]
    quota, used = d.get("quota"), d.get("used_quota")
    if not isinstance(quota, (int, float)) or not isinstance(used, (int, float)):
        section["error"] = "接口字段缺失，仍显示基线"
        return section
    section.update({
        "mode": "live", "remainingUsd": round(quota / 500000, 2),
        "usedUsd": round(used / 500000, 2), "totalUsd": round((quota + used) / 500000, 2),
        "checkedAt": now_iso(),
    })
    section.pop("note", None)
    return section


def _codex_drilldown(cached: dict) -> dict:
    """本机 Codex 会话日志统计（每次监控轮询都重新扫描，保证总量准实时）。"""
    prev = cached.get("codex")
    if not TOKEN_USAGE.exists():
        return {"ok": False, "unavailable": True, "note": "optional module token-usage.py not present (not shipped in the open-source repo)"}
    try:
        proc = subprocess.run(
            ["python3", str(TOKEN_USAGE), "--json"], capture_output=True, timeout=120)
        data = json.loads(proc.stdout.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        if isinstance(prev, dict) and prev.get("totals"):
            out = dict(prev)
            out["error"] = "刷新失败，显示上次统计：%s" % str(exc)[:120]
            return out
        return {"ok": False, "error": str(exc)[:160]}
    totals = data.get("totals") or {}
    return {
        "ok": True, "totals": totals,
        "totalTokens": totals.get("total_tokens"),
        "sessions": data.get("unique_sessions_counted"),
        "firstDate": data.get("first_session_date"),
        "lastDate": data.get("last_session_date"),
        "checkedAt": now_iso(),
        "scope": "仅本机 Codex 会话日志（面板口径的子集，不与面板相加）",
    }


def build(cached: dict | None = None) -> dict:
    cached = cached or {}
    jev = _jev_probe() if not _section_fresh(cached, "jev", JEV_REFRESH_HOURS) else cached["jev"]
    return {
        "version": 1,
        "generatedAt": now_iso(),
        "panel": _panel_section(),
        "codex": _codex_drilldown(cached),
        "jev": jev,
        "dataPolicy": "面板为全工具总口径；Codex 明细是其中本机部分的下钻；两者不相加。Jev 为实时探测。",
    }


def snapshot() -> dict:
    """给 web-monitor.py 的入口。永不抛异常，失败回退上次缓存。"""
    try:
        fresh = build(_read_cache())
        tmp = CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(fresh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, CACHE)
        return fresh
    except Exception as exc:  # noqa: BLE001 - monitor 主循环不能死
        cached = _read_cache()
        if cached:
            cached = dict(cached)
            cached["error"] = "刷新失败，显示上次快照：%s" % str(exc)[:160]
            return cached
        return {"error": str(exc)[:200]}


if __name__ == "__main__":
    print(json.dumps(snapshot(), ensure_ascii=False, indent=2)[:3000])
