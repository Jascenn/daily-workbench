#!/usr/bin/env python3
"""
微信批奏折（v1.2.0）后端单元测试：
1. wechat_brief.apply_progress_diff 跨轮次增量进展对比；
2. wechat_brief.collect_learning_topics 白名单群聊选题提炼；
3. server.routes_post 只读回应草稿兜底与路由契约。
"""
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "sync"))

import wechat_brief
from server import routes_post


class _FakeMCP:
    def __init__(self, messages_by_cid):
        self.messages_by_cid = messages_by_cid

    def call(self, method, params):
        assert method == "messages.list"
        return {"messages": self.messages_by_cid.get(params["conversation_id"], [])}


class TestWechatBrief(unittest.TestCase):
    def test_progress_diff_first_run_then_new_message(self):
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "topic-state.json"
            round1 = [{
                "account": {"id": "acc1"},
                "waiting": [{
                    "conversationId": "c1",
                    "name": "陈总",
                    "at": "2026-11-23T10:00:00+08:00",
                    "want": "询问私有化部署方案",
                }],
            }]
            p1 = wechat_brief.apply_progress_diff(round1, state_path=state_file)
            self.assertEqual(p1, 0)
            self.assertNotIn("hasProgress", round1[0]["waiting"][0])

            # 同时间同摘要重复同步：不误报新进展
            round1_dup = json.loads(json.dumps(round1))
            p_dup = wechat_brief.apply_progress_diff(round1_dup, state_path=state_file)
            self.assertEqual(p_dup, 0)
            self.assertNotIn("hasProgress", round1_dup[0]["waiting"][0])

            # 出现更新消息且摘要变化：写入 prevSummary / latestDelta / hasProgress
            round2 = [{
                "account": {"id": "acc1"},
                "waiting": [{
                    "conversationId": "c1",
                    "name": "陈总",
                    "at": "2026-11-24T15:00:00+08:00",
                    "want": "确认 2 台 4090 服务器并约周四看 demo",
                }],
            }]
            p2 = wechat_brief.apply_progress_diff(round2, state_path=state_file)
            self.assertEqual(p2, 1)
            item = round2[0]["waiting"][0]
            self.assertTrue(item["hasProgress"])
            self.assertEqual(item["prevSummary"], "询问私有化部署方案")
            self.assertEqual(item["latestDelta"], "确认 2 台 4090 服务器并约周四看 demo")

    def test_whitelist_groups_collect_and_empty_config(self):
        now = dt.datetime(2026, 11, 24, 21, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
        with tempfile.TemporaryDirectory() as td:
            # 配置文件不存在时直接返回空，不报错
            empty = wechat_brief.collect_learning_topics(
                _FakeMCP({}), {}, [], now,
                text_preview_fn=lambda m: m.get("text", ""),
                fingerprint_fn=lambda s: "fp_" + s,
                config_path=Path(td) / "missing.json",
            )
            self.assertEqual(empty, [])

            cfg_path = Path(td) / "wechat-groups.json"
            cfg_path.write_text(json.dumps({"groups": ["AI 独立开发者"], "lookbackHours": 24}), encoding="utf-8")
            listed = [
                {"conversation_id": "g1@chatroom", "name": "AI 独立开发者核心群", "kind": "group"},
                {"conversation_id": "g2@chatroom", "name": "闲聊灌水群", "kind": "group"},
            ]
            fake_mcp = _FakeMCP({
                "g1@chatroom": [
                    {"created_at": "2026-11-24T20:10:00+08:00", "sender_name": "阿K", "text": "只读草稿+三口吻复制最稳妥"},
                    {"created_at": "2026-11-24T19:40:00+08:00", "sender_name": "老周", "text": "必须按 conversationId + lastAt 自动重浮"},
                    {"created_at": "2026-11-20T10:00:00+08:00", "sender_name": "过期", "text": "超过 24 小时不该计入"},
                ],
                "g2@chatroom": [
                    {"created_at": "2026-11-24T20:10:00+08:00", "sender_name": "路人", "text": "非白名单群不抓"},
                ],
            })
            topics = wechat_brief.collect_learning_topics(
                fake_mcp, {"account_id": "a1"}, listed, now,
                text_preview_fn=lambda m: m.get("text", ""),
                fingerprint_fn=lambda s: "fp_" + s,
                config_path=cfg_path,
                llm_model="",
            )
            self.assertEqual(len(topics), 1)
            self.assertEqual(topics[0]["name"], "AI 独立开发者核心群")
            self.assertEqual(topics[0]["messageCount"], 2)
            self.assertIn("阿K：只读草稿", topics[0]["sourcePreview"])

    def test_reply_draft_fallback_readonly(self):
        old_flag = os.environ.get("WECHAT_REPLY_LLM_DISABLE")
        os.environ["WECHAT_REPLY_LLM_DISABLE"] = "1"
        try:
            drafts, source, _ = routes_post._generate_wechat_reply_drafts({
                "contact": "陈总",
                "want": "确认周四演示时间",
                "action": "回复确认周四 15:00",
            })
            self.assertEqual(source, "fallback")
            self.assertTrue(drafts["pro"] and drafts["warm"] and drafts["short"])
            self.assertIn("周四", drafts["pro"])
        finally:
            if old_flag is None:
                os.environ.pop("WECHAT_REPLY_LLM_DISABLE", None)
            else:
                os.environ["WECHAT_REPLY_LLM_DISABLE"] = old_flag


if __name__ == "__main__":
    unittest.main()
