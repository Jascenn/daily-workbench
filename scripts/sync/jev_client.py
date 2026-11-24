#!/usr/bin/env python3
"""Small Jev client shared by offline snapshot builders.

Jev is the primary judgment layer for opportunity/consultation analysis.
Local Ollama remains the explicit fallback when Jev is unavailable.
"""
from __future__ import annotations

import json
import os
import urllib.request

ENV_PATH = os.path.expanduser("~/.config/jev-jarvis/env")


def _config() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in open(ENV_PATH, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def available() -> bool:
    cfg = _config()
    return bool(cfg.get("TYPESAFE_API_KEY"))


def judge(prompt: str, *, timeout: int = 60, questions: dict | None = None) -> tuple[bool, dict]:
    """Ask Jev for a structured judgment. Never raises to the caller."""
    cfg = _config()
    key = cfg.get("TYPESAFE_API_KEY")
    if not key:
        return False, {"error": "jev_not_configured"}
    base = (cfg.get("TYPESAFE_BASE_URL") or "https://api.typesafe.ai").rstrip("/")
    model = cfg.get("TYPESAFE_MODEL") or "jev-latest"
    questions = questions or {
        "opportunity": {
            "type": "score",
            "instructions": "机会价值 0-9：是否存在明确、与用户业务相关的合作/咨询/项目机会。只按证据评分，不要把普通资讯或群发广告算机会。",
            "criteria": ["无机会或广告", "几乎无关", "弱相关", "可能是线索", "有明确需求但缺背景", "值得跟进", "需求较明确", "高价值机会", "强机会且有明确下一步", "立即跟进"],
        },
        "consultation": {
            "type": "score",
            "instructions": "咨询价值 0-9：是否有人正在寻求判断、方案、教学或问题解决。纯资讯、闲聊、广告要低分。",
            "criteria": ["不是咨询", "几乎不是", "弱问题", "一般问题", "有具体问题", "值得回答", "需求明确", "高价值咨询", "强咨询且可转服务", "立即跟进"],
        },
    }
    body = {
        "model": model,
        "state": prompt,
        "questions": questions,
    }
    req = urllib.request.Request(
        base + "/v1/systemone",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return True, data
    except Exception as exc:  # noqa: BLE001
        return False, {"error": type(exc).__name__ + ": " + str(exc)[:160]}


def extract_text(data: dict) -> str:
    """Extract text from the observed TypeSafe response shapes."""
    if not isinstance(data, dict):
        return ""
    for key in ("text", "answer", "value", "output"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    result = data.get("result")
    if isinstance(result, dict):
        text = extract_text(result)
        if text:
            return text
    answers = data.get("answers")
    if isinstance(answers, dict):
        for value in answers.values():
            text = extract_text(value) if isinstance(value, dict) else (value if isinstance(value, str) else "")
            if text:
                return text
    if isinstance(answers, list):
        for value in answers:
            text = extract_text(value) if isinstance(value, dict) else (value if isinstance(value, str) else "")
            if text:
                return text
    return ""


def scores(data: dict) -> dict[str, dict]:
    """Return Jev score answers without exposing the provider response shape."""
    answers = data.get("answers") if isinstance(data, dict) else None
    return answers if isinstance(answers, dict) else {}
