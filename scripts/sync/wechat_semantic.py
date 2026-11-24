#!/usr/bin/env python3
"""把微信原始消息转成人话 + 待办。

为什么单独一层：MCP 返回的是**原始素材**——语音是 `[语音]`、图片是 `[图片]`、
群消息前半截是 XML。直接把素材贴到页面上，等于让凌一自己去解码。这里做两件事：

1. **补内容**：语音走 SILK → WAV → SenseVoice 转成文字（真机验证 1.3s/条）。
   拿不到就如实说"语音未转写"，不猜。
2. **转语义**：本地 qwen3:8b 把「谁 + 说了什么」压成「对方要什么 / 我该做什么」，
   并判断这条到底要不要回（待回复 / 广告 / 已结束）。

三条不能破的口径：
* 模型没跑起来时**降级但不撒谎**：保留原始预览，并在条目上标 `analyzed=false`，
  页面据此说"未解析"，而不是把原始 XML 当结论。
* 分类只用来**分组**，不用来**删除**。广告折起来但仍在快照里，
  模型判错时凌一还能自己翻回去看原文。
* 语音转写失败一律留空 + 标记，绝不拿"嗯嗯"之类的默认值填充。
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
import sys

# 该文件既可由 wechat-sync.py 运行，也会被离线单测按路径加载；两种入口都要找到同目录的 Jev client。
_sync_dir = str(Path(__file__).resolve().parent)
if _sync_dir not in sys.path:
    sys.path.insert(0, _sync_dir)
try:
    import jev_client
except ImportError:  # 直接单测此文件时的降级
    jev_client = None

# ── 本地模型 ────────────────────────────────────────────────────────────────
OLLAMA_URL = os.environ.get("WECHAT_OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("WECHAT_OLLAMA_MODEL", "qwen3:8b")
LLM_TIMEOUT = int(os.environ.get("WECHAT_LLM_TIMEOUT", "180"))

# ── 语音识别（复用 wechat-daily-digest 的现成环境，不重复装一遍模型）──────────
SKILL_DIR = Path.home() / ".config" / "alma" / "skills" / "wechat-daily-digest"
VOICE_PY = SKILL_DIR / ".venv" / "bin" / "python"
SENSEVOICE_DIR = Path("/Volumes/Mypssd/ai-models/asr/sensevoice")
ASR_TIMEOUT = int(os.environ.get("WECHAT_ASR_TIMEOUT", "90"))

_SV_RUNNER = """
import sys, wave, array, json
import sherpa_onnx
M, wav = sys.argv[1], sys.argv[2]
rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
    model=M + "/model.int8.onnx", tokens=M + "/tokens.txt",
    language="zh", use_itn=True, num_threads=4)
w = wave.open(wav)
sr, n = w.getframerate(), w.getnframes()
samples = [x / 32768.0 for x in array.array("h", w.readframes(n))]
s = rec.create_stream()
s.accept_waveform(sr, samples)
rec.decode_stream(s)
print(json.dumps({"text": s.result.text}))
"""


def asr_ready() -> bool:
    return (VOICE_PY.exists() and (SENSEVOICE_DIR / "model.int8.onnx").exists())


def transcribe_silk(blob_b64: str) -> str:
    """SILK 语音 → 文字。失败返回空串，由调用方决定怎么标注。"""
    if not asr_ready():
        return ""
    tmp = tempfile.mkdtemp(prefix="wx-asr-")
    silk = os.path.join(tmp, "a.silk")
    wav = os.path.join(tmp, "a.wav")
    try:
        with open(silk, "wb") as fh:
            fh.write(base64.b64decode(blob_b64))
        r = subprocess.run(
            [str(VOICE_PY), "-c",
             f"import pilk; pilk.silk_to_wav({silk!r}, {wav!r}, rate=16000)"],
            capture_output=True, timeout=ASR_TIMEOUT)
        if r.returncode != 0 or not os.path.exists(wav):
            return ""
        r = subprocess.run([str(VOICE_PY), "-c", _SV_RUNNER, str(SENSEVOICE_DIR), wav],
                           capture_output=True, timeout=ASR_TIMEOUT)
        if r.returncode != 0:
            return ""
        line = r.stdout.decode("utf-8", "replace").strip().splitlines()
        if not line:
            return ""
        return (json.loads(line[-1]).get("text") or "").strip()
    except Exception:
        return ""
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def llm_ready() -> bool:
    try:
        req = urllib.request.Request(OLLAMA_URL.rstrip("/") + "/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        names = {m.get("name") for m in tags.get("models", [])}
        return OLLAMA_MODEL in names
    except Exception:
        return False


def _llm(prompt: str) -> str:
    body = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_predict": 700},
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL.rstrip("/") + "/api/generate", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        return (json.loads(resp.read().decode("utf-8")).get("response") or "").strip()


HEADER = """你是凌一的微信助理。下面是微信里若干会话的最近消息，需要你判断并改写成人话。

输出规则（严格遵守）：
每条一行，用竖线分隔共四个字段：
编号|分类|对方要什么|我该做什么

- 分类只能是：待回复、广告、已结束
  · 待回复 = 对方在等凌一回应或做事（含提问、请求、约定、发来待看内容）
  · 广告 = 群发营销、促销、推销、通知类，不需要回
  · 已结束 = 对方只是应了一声（嗯嗯/好的/收到/表情），对话已收尾
- 「对方要什么」≤25 字，说清具体诉求，不要复述原文，不要写"发送了消息"
- 「我该做什么」≤25 字，必须是具体动作（动词开头），例如"洗风扇并检查电机""回复报价"；只有分类为广告或已结束时才写"无需处理"
- [语音] 后附的是转写文字，按文字理解；只有 [语音] 无文字时写"发来语音，未转写"
- [图片] 没有任何内容信息时写"发来图片，需查看"
- 不要编号以外的解释、不要空行、不要 markdown
"""


def classify(items: list[dict]) -> dict[str, dict]:
    """items: [{id, name, kind, lines:[str]}] → {id: {category, want, action}}"""
    if not items:
        return {}
    blocks = []
    for it in items:
        head = "【%s】%s（%s）" % (it["id"], it["name"], "群聊" if it.get("kind") == "group" else "私聊")
        blocks.append(head + "\n" + "\n".join(it.get("lines") or ["（无消息）"]))
    prompt = HEADER + "\n" + "\n\n".join(blocks) + "\n\n现在只输出这些编号对应的行。"

    try:
        raw = _llm(prompt)
    except Exception:
        return {}

    known = {it["id"] for it in items}
    out: dict[str, dict] = {}
    for line in raw.splitlines():
        line = line.strip().lstrip("-•*").strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue
        key, category, want, action = parts[0], parts[1], parts[2], parts[3]
        key = re.sub(r"[^\w]", "", key)
        # 模型经常把 W1 写成 1（前缀字母被吃掉），也可能写成 [W1]。
        # 这里按已知编号回填，而不是原样当 key —— 否则整条判定会被静默丢掉，
        # 页面表现成"没分析"，但真正的原因是解析太严格。
        if key not in known:
            # 只有唯一候选时才回填：W1 和 M1 同时存在时，"1" 到底指谁无法判断，
            # 猜错会把 A 的判定安到 B 头上 —— 那种错比"没分析"更糟。
            cands = [k for k in (("W" + key), ("M" + key)) if k in known]
            if len(cands) == 1:
                key = cands[0]
            else:
                continue
        if category not in ("待回复", "广告", "已结束"):
            continue
        out[key] = {
            "category": category,
            "want": want[:60],
            "action": action[:60],
        }
    return out


BUSINESS_HEADER = """你是凌一的微信经营分析助手。请从以下微信消息证据中识别两类东西：
1. 机会：可能带来企业 AI 咨询、AI 教学/社群、内容合作、产品/项目或其他业务机会。
2. 咨询：对方正在寻求判断、方案、教学或问题解决，不一定代表付费机会。

严格规则：
- 只有消息本身出现明确需求、求助、合作、项目、报价、预算、排期、报名、课程、方案或具体问题时才输出；普通资讯、群发广告、闲聊输出 none。
- 不猜预算、身份、成交概率。没有说就写“未提及/待确认”。
- whyRelated 必须结合凌一的三块业务：企业 AI 落地咨询、AI/副业教学社群、内容创作；无关就写“暂未发现直接关联”。
- nextAction 必须是一个可执行动作，例如“询问现有流程、团队人数和上线时间”。
- 缺少的关键信息写进 missingInfo，不能自行补齐。
- 只输出 JSONL，每行一个 JSON 对象，不要 markdown，不要解释。

JSON 字段：
{"id":"候选编号","type":"opportunity|consultation|both|none","confidence":"high|medium|low","opportunityLevel":"O0|O1|O2|O3","businessFit":"企业AI咨询|教学社群|内容合作|产品项目|其他|未知","consultationType":"诊断|决策|规划|教学|技术|商业|内容|其他|未知","surfaceQuestion":"对方表面问题，没有则空","underlyingNeed":"对方真实需求，没有则空","evidence":"用一句话引用事实，不要编","whyRelated":"为什么和凌一有关","nextAction":"下一步动作","missingInfo":"还缺什么信息","suggestedReply":"适合的简短回复方向，没有则空"}
"""

def _parse_local_json_line(line: str) -> dict | None:
    """容忍本地小模型偶发漏掉空字段冒号，但不放宽到猜字段内容。"""
    line = line.strip().strip("`")
    try:
        value = json.loads(line)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        # qwen 偶尔输出 "missingInfo","suggestedReply":"..."；空字段可以安全补空，
        # 其他结构问题仍然直接降级，不拿正则拼接出伪结论。
        for key in ("missingInfo", "suggestedReply", "surfaceQuestion", "underlyingNeed"):
            line = re.sub(rf'("{key}")\s*(?=,|}})', r'\1:""', line)
        try:
            value = json.loads(line)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def _analyze_business_local(items: list[dict]) -> dict[str, dict]:
    """Local fallback: qwen3:8b. Jev is the primary judgment layer."""
    if not items:
        return {}
    out: dict[str, dict] = {}
    # 18 条候选一次输出很容易超过本地模型的 token 上限；分批也更容易定位失败。
    for offset in range(0, len(items), 6):
        batch = items[offset:offset + 6]
        blocks = []
        for item in batch:
            lines = "\n".join(item.get("lines") or ["（无可读证据）"])
            blocks.append(f"【{item['id']}】{item.get('name') or '候选会话'}（{item.get('kind') or 'unknown'}）\n{lines}")
        try:
            raw = _llm(BUSINESS_HEADER + "\n\n".join(blocks) + "\n\n现在只输出这些编号对应的 JSONL。")
        except Exception:
            continue
        known = {str(x["id"]) for x in batch}
        for line in raw.splitlines():
            line = line.strip().strip("`")
            if not line or not line.startswith("{"):
                continue
            obj = _parse_local_json_line(line)
            if obj is None:
                match = re.search(r"\{.*\}", line)
                obj = _parse_local_json_line(match.group(0)) if match else None
            if obj is None:
                continue
            key = str(obj.get("id") or "")
            if key not in known or obj.get("type") not in {"opportunity", "consultation", "both", "none"}:
                continue
            allowed = {
                "type", "confidence", "opportunityLevel", "businessFit", "consultationType",
                "surfaceQuestion", "underlyingNeed", "evidence", "whyRelated", "nextAction",
                "missingInfo", "suggestedReply",
            }
            out[key] = {k: str(obj.get(k) or "").strip()[:300] for k in allowed}
            out[key]["type"] = obj.get("type")
    return out


JEV_BUSINESS_HEADER = """你是凌一的微信经营分析判断器。请判断下面候选是否属于机会或咨询。
机会包括企业 AI 落地、AI/副业教学社群、内容合作、产品/项目合作；咨询包括诊断、决策、规划、教学、技术、商业或内容问题。
只有有明确证据才输出 opportunity/consultation/both；群发广告、普通资讯、闲聊输出 none。预算、身份、成交概率没有证据就写未知。
输出严格 JSON：{"type":"opportunity|consultation|both|none","confidence":"high|medium|low","opportunityLevel":"O0|O1|O2|O3","businessFit":"企业AI咨询|教学社群|内容合作|产品项目|其他|未知","consultationType":"诊断|决策|规划|教学|技术|商业|内容|其他|未知","surfaceQuestion":"","underlyingNeed":"","evidence":"","whyRelated":"","nextAction":"","missingInfo":"","suggestedReply":""}"""


def _parse_jev_business(data: dict, text: str) -> dict:
    raw = jev_client.extract_text(data) if jev_client else ""
    if not raw:
        raw = text
    raw = raw.strip().strip("`")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return {}
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    if obj.get("type") not in {"opportunity", "consultation", "both", "none"}:
        return {}
    allowed = {"type", "confidence", "opportunityLevel", "businessFit", "consultationType",
               "surfaceQuestion", "underlyingNeed", "evidence", "whyRelated", "nextAction",
               "missingInfo", "suggestedReply"}
    out = {k: str(obj.get(k) or "").strip()[:300] for k in allowed}
    out["type"] = obj.get("type")
    out["judgmentModel"] = "jev"
    return out


def _jev_score_verdict(data: dict) -> dict:
    answers = jev_client.scores(data) if jev_client else {}
    opp = answers.get("opportunity") if isinstance(answers, dict) else None
    con = answers.get("consultation") if isinstance(answers, dict) else None
    if not isinstance(opp, dict) or not isinstance(con, dict):
        return {}
    oscore = float(opp.get("score") or 0)
    cscore = float(con.get("score") or 0)
    otype = "opportunity" if oscore >= 4.5 else None
    ctype = "consultation" if cscore >= 4.5 else None
    if otype and ctype:
        typ = "both"
    elif otype:
        typ = "opportunity"
    elif ctype:
        typ = "consultation"
    else:
        typ = "none"
    # score 0-9 映射为 O0-O3；置信度取 Jev 两个判断里更低的那个。
    confidence = min(float(opp.get("confidence") or 0), float(con.get("confidence") or 0))
    return {
        "type": typ,
        "confidence": "high" if confidence >= .7 else ("medium" if confidence >= .4 else "low"),
        "opportunityLevel": "O3" if oscore >= 7 else ("O2" if oscore >= 5.5 else ("O1" if oscore >= 4.5 else "O0")),
        "opportunityScore": round(oscore, 2),
        "consultationScore": round(cscore, 2),
        "judgmentModel": "jev",
    }


def analyze_business(items: list[dict]) -> dict[str, dict]:
    """Jev scores primary; local qwen supplies narrative fields or full fallback."""
    if not items:
        return {}
    out: dict[str, dict] = {}
    if jev_client and jev_client.available():
        for item in items:
            lines = "\n".join(item.get("lines") or ["（无证据）"])
            prompt = (
                "请用以下证据判断机会和咨询分数。只判断，不要生成长文本。\n"
                "机会 0-9：企业AI咨询、教学社群、内容合作、产品项目；广告/资讯/闲聊低分。\n"
                "咨询 0-9：对方是否寻求判断、方案、教学或问题解决。\n\n"
                f"候选 {item['id']} {item.get('name','')}：\n{lines}"
            )
            ok, data = jev_client.judge(prompt)
            if ok:
                verdict = _jev_score_verdict(data)
                if verdict:
                    out[item["id"]] = verdict
    # qwen 只负责 Jev 已判为有价值候选的人话字段；Jev 不通时才全量降级。
    narrative_items = [item for item in items if out.get(item["id"], {}).get("type") != "none"]
    if narrative_items:
        local = _analyze_business_local(narrative_items)
        if not out:
            # Jev 不可用时，本地模型接管整条判断链，并明确标记降级来源。
            for key, value in local.items():
                value["judgmentModel"] = "qwen3:8b-fallback"
            out.update(local)
        else:
            for key in list(out):
                if key not in local:
                    continue
                value = dict(out[key])
                # Jev 是主判定：本地 qwen 的 type/confidence/level 不能覆盖 Jev 分数。
                # 这里只拿它补“人话字段”，避免出现“机会分数 2.6 却被展示成机会”的错口径。
                for field in ("businessFit", "consultationType", "surfaceQuestion", "underlyingNeed",
                              "evidence", "whyRelated", "nextAction", "missingInfo", "suggestedReply"):
                    if local[key].get(field):
                        value[field] = local[key][field]
                value["judgmentModel"] = out[key].get("judgmentModel")
                out[key] = value
    return out


def describe_attachment(message: dict) -> str:
    """没有正文的附件，给一句人能懂的说明（不是占位符）。"""
    kind = (message.get("type") or "").lower()
    return {
        "image": "发来图片，需查看",
        "sticker": "发来表情",
        "voice": "发来语音，未转写",
        "video": "发来视频，需查看",
        "file": "发来文件，需查看",
    }.get(kind, "发来%s，需查看" % (kind or "内容"))
