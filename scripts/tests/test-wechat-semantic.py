#!/usr/bin/env python3
"""微信经营分析语义层的离线测试：不访问 MCP、不调用本地模型。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "wechat_semantic", ROOT / "scripts" / "sync" / "wechat_semantic.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print("PASS", name)


# Jev 的真实返回是 answers.opportunity/consultation 的 score 结构。
score_data={"answers":{"opportunity":{"score":6.2,"confidence":0.8},"consultation":{"score":2.1,"confidence":0.9}}}
score=mod._jev_score_verdict(score_data)
check("Jev score maps to opportunity", score["type"]=="opportunity" and score["opportunityLevel"]=="O2")
check("Jev score keeps model provenance", score["judgmentModel"]=="jev")

items = [
    {"id": "B1", "name": "企业客户", "kind": "direct", "lines": ["peer: 想做内部知识库和自动化"]},
    {"id": "B2", "name": "群聊", "kind": "group", "lines": ["peer: 大家好，今晚直播"]},
]

original = mod._llm
original_jev_available = mod.jev_client.available if mod.jev_client else None
try:
    # 这一段专测本地降级解析，不能被开发机上的真实 Jev 配置干扰。
    if mod.jev_client:
        mod.jev_client.available = lambda: False
    mod._llm = lambda prompt: (
        '{"id":"B1","type":"opportunity","confidence":"medium",'
        '"opportunityLevel":"O1","businessFit":"企业AI咨询",'
        '"consultationType":"未知","surfaceQuestion":"",'
        '"underlyingNeed":"内部知识库和自动化",'
        '"evidence":"对方明确提到内部知识库和自动化",'
        '"whyRelated":"符合企业 AI 落地咨询业务",'
        '"nextAction":"询问现有流程和上线时间",'
        '"missingInfo":"团队人数、预算",'
        '"suggestedReply":"可以，先了解现有流程"}'
        '\n{"id":"B2","type":"none","confidence":"high",'
        '"opportunityLevel":"O0","businessFit":"未知",'
        '"consultationType":"未知","surfaceQuestion":"",'
        '"underlyingNeed":"","evidence":"普通直播通知",'
        '"whyRelated":"暂未发现直接关联","nextAction":"none",'
        '"missingInfo":"","suggestedReply":""}'
    )
    result = mod.analyze_business(items)
    check("parses opportunity JSONL", result["B1"]["type"] == "opportunity")
    check("keeps evidence separate from next action", result["B1"]["evidence"].startswith("对方明确"))
    check("drops none candidates at caller boundary", result["B2"]["type"] == "none")
finally:
    mod._llm = original
    if mod.jev_client and original_jev_available:
        mod.jev_client.available = original_jev_available

check("image attachment is human-readable", mod.describe_attachment({"type": "image"}) == "发来图片，需查看")
check("voice attachment has honest fallback", mod.describe_attachment({"type": "voice"}) == "发来语音，未转写")
# Hybrid path: Jev score is primary; local narrative is only enrichment.
old_available = mod.jev_client.available
old_judge = mod.jev_client.judge
old_local = mod._analyze_business_local
try:
    mod.jev_client.available = lambda: True
    mod.jev_client.judge = lambda prompt: (True, {"answers": {
        "opportunity": {"score": 6.2, "confidence": .8},
        "consultation": {"score": 2.0, "confidence": .9},
    }})
    mod._analyze_business_local = lambda xs: {"B1": {
        "type":"opportunity", "confidence":"medium", "opportunityLevel":"O2",
        "businessFit":"企业AI咨询", "consultationType":"未知", "surfaceQuestion":"",
        "underlyingNeed":"内部自动化", "evidence":"对方说要做客服自动化",
        "whyRelated":"符合企业 AI 落地咨询业务", "nextAction":"询问流程", "missingInfo":"预算",
        "suggestedReply":"先了解流程", "judgmentModel":"qwen3:8b-fallback"}}
    hybrid=mod.analyze_business([items[0]])
    check("Jev stays primary in hybrid analysis", hybrid["B1"]["judgmentModel"]=="jev")
    check("Jev score survives local narrative enrichment", hybrid["B1"]["opportunityScore"]==6.2)
finally:
    mod.jev_client.available = old_available
    mod.jev_client.judge = old_judge
    mod._analyze_business_local = old_local
print("semantic tests passed")
