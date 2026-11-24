#!/usr/bin/env python3
"""Build a read-only WeChat snapshot from the local x1a0he WeChat MCP server.

为什么是“快照”而不是直连：工作台是纯本地静态服务，页面每开一次就去敲一遍
MCP 会把 1090 个会话逐个问一遍，打开页面就卡住；而且 MCP 不在时页面会空白。
所以这里按 web-monitor.py 的老规矩——定时拉一次、落成 wechat.json、页面只读快照。

三条口径：
* **只读**。本脚本只调用 MCP 的读取类工具，不发送、不导出、不改任何会话。
* **不臆造未读**。MCP 不提供未读计数，这里也不拿“最后一条不是我”去冒充未读数；
  只有「最后一条是对方发的」才进待回复，并且明确标注这是推断口径。
* **群聊只收 @我**。群里最后一条不是自己发的，不代表在找你。只有正文里出现
  自己的昵称（@凌一）才算需要回应，其余一律不进列表。
* **只按结构过滤，不猜内容**。公众号（`gh_`）和企业微信会话（`@openim`）结构上
  就不是个人对话，直接剔除；但**真人发来的推广不剔除** —— 判断"这条营销不重要"
  需要读正文，那是不该由脚本替他做的判断。真嫌吵应该做成手动忽略，不是猜。

MCP 不在线不抛异常：写一份 available=false 的快照，页面据此显示“未连接”，
同步链路里的其他步骤照常跑完。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wechat_semantic as sem

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "wechat.json"

MCP_URL = os.environ.get("WECHAT_MCP_URL", "http://127.0.0.1:21454/mcp")
MCP_PROTOCOL_VERSIONS = ("2026-07-28", "2025-11-25", "2025-06-18")
MCP_VERSION = MCP_PROTOCOL_VERSIONS[0]

# 扫多少个会话：微信会话列表本身就是按活跃度排的，前 N 个覆盖了真实在聊的人。
# 每个会话一次 messages.query，约 10ms；120 个 ≈ 1.2s，同步时可以接受。
SCAN_LIMIT = int(os.environ.get("WECHAT_SCAN_LIMIT", "120"))
# 待回复只算这个窗口内的，更早的当作已经搁置，不再催
WAIT_WINDOW_HOURS = int(os.environ.get("WECHAT_WAIT_HOURS", "72"))
MENTION_DAYS = int(os.environ.get("WECHAT_MENTION_DAYS", "7"))
# 每个会话取多少条历史当上下文。取多了消息量大，取少了看不懂"嗯嗯"是什么意思。
CONTEXT_COUNT = int(os.environ.get("WECHAT_CONTEXT_COUNT", "8"))
BUSINESS_LOOKBACK_DAYS = int(os.environ.get("WECHAT_BUSINESS_DAYS", "14"))
BUSINESS_MAX_CANDIDATES = int(os.environ.get("WECHAT_BUSINESS_LIMIT", "18"))
BUSINESS_CONTEXT_LIMIT = int(os.environ.get("WECHAT_BUSINESS_CONTEXT_LIMIT", "12"))
BUSINESS_TERMS = (
    "咨询", "需求", "项目", "自动化", "工作流", "知识库", "报价", "预算",
    "合作", "报名", "课程", "陪跑", "怎么做", "多少钱", "方案", "落地",
)

# 系统号与会话 ID 前缀。`gh_` 是公众号/服务号，永远不是"有人在找我"；
# 名字里带这些的是微信自己的系统会话。两类都不进待回复 —— 否则 42 条"待回复"
# 里有 30 条是携程和彩云天气的促销，列表等于没有。
# `gh_` 是公众号；`@openim` 是企业微信/客服接入的会话（客服号、SCRM 群发）。
# 两者都不是"一个会等你回话的人"。
OFFICIAL_SUFFIXES = ("@openim",)
OFFICIAL_PREFIXES = ("gh_",)
SYSTEM_CONVERSATIONS = {"filehelper", "weixin", "weixinreminder", "notifymessage",
                        "medianote", "qqmail", "fmessage", "floatbottle"}
SYSTEM_NAME_HINTS = ("微信团队", "微信支付", "微信收款", "微信运动", "文件传输助手",
                     "服务通知", "订阅号消息", "微信公众平台")


def _is_machine(cid: str, name: str) -> bool:
    """这条会话是不是"机器发的"，而不是一个会等你回话的人。"""
    if cid in SYSTEM_CONVERSATIONS:
        return True
    if any(cid.startswith(prefix) for prefix in OFFICIAL_PREFIXES):
        return True
    if any(cid.endswith(suffix) for suffix in OFFICIAL_SUFFIXES):
        return True
    return any(hint in name for hint in SYSTEM_NAME_HINTS)


class MCP:
    """Minimal JSON-RPC client for the streamable-HTTP MCP endpoint."""

    def __init__(self, url: str = MCP_URL) -> None:
        self.url = url
        self.sid: str | None = None
        self._id = 0
        self.protocol_version = MCP_VERSION
        self.server_info: dict = {}

    def _post(self, payload: dict) -> str:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if self.sid:
            headers["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            self.sid = resp.headers.get("Mcp-Session-Id") or self.sid
            return resp.read().decode("utf-8", errors="replace")

    @staticmethod
    def _last_event(body: str) -> dict:
        events = []
        for line in body.splitlines():
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    continue
        if not events:
            raise RuntimeError("MCP 没有返回可解析的事件")
        return events[-1]

    def initialize(self) -> None:
        """Negotiate the newest protocol supported by both sides."""
        last_error: Exception | None = None
        for version in MCP_PROTOCOL_VERSIONS:
            self.sid = None
            self.protocol_version = version
            try:
                body = self._post({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {
                        "protocolVersion": version,
                        "capabilities": {},
                        "clientInfo": {"name": "daily-workbench", "version": "1"},
                    },
                })
                message = self._last_event(body)
                if "error" in message:
                    raise RuntimeError(str(message["error"])[:240])
                result = message.get("result") or {}
                self.protocol_version = result.get("protocolVersion") or version
                self.server_info = result.get("serverInfo") or {}
                try:
                    self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
                except Exception:
                    pass
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise last_error or RuntimeError("MCP initialize failed")

    # MCP 服务端有并发闸门：同时来几个请求就回 busy，搜索再叠一层 search_busy。
    # 页面同步是个循环，撞上就整轮失败，所以这里统一退避重试 —— 调用方不用各自处理。
    BUSY_RETRIES = 10
    BUSY_BACKOFF = 1.2   # 累计等待 ≈ 66s；实测 busy 峰值持续十几秒，够过

    def call(self, name: str, args: dict | None = None) -> dict:
        last: Exception | None = None
        for attempt in range(self.BUSY_RETRIES):
            try:
                return self._call_once(name, args)
            except RuntimeError as exc:
                text = str(exc)
                if "busy" not in text and "尚未结束" not in text:
                    raise
                last = exc
                time.sleep(self.BUSY_BACKOFF * (attempt + 1))
        raise last if last else RuntimeError("MCP 调用失败")

    def call_raw(self, name: str, args: dict | None = None) -> dict:
        """返回**完整 result**（含 resource 内容块）。

        默认的 call() 只解 text 块并 json.loads —— 那是为业务数据设计的。
        但语音/图片的字节在 `content[].resource.blob` 里，不是 text，
        用 call() 读媒体会拿到空 dict，看起来像"没有音频"，其实是解析层丢了。
        """
        last: Exception | None = None
        for attempt in range(self.BUSY_RETRIES):
            try:
                self._id += 1
                body = self._post({
                    "jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                    "params": {"name": name, "arguments": args or {}},
                })
                msg = self._last_event(body)
                if "error" in msg:
                    raise RuntimeError(str(msg["error"])[:200])
                return msg.get("result", {})
            except RuntimeError as exc:
                text = str(exc)
                if "busy" not in text and "尚未结束" not in text:
                    raise
                last = exc
                time.sleep(self.BUSY_BACKOFF * (attempt + 1))
        raise last if last else RuntimeError("MCP 调用失败")

    def _call_once(self, name: str, args: dict | None = None) -> dict:
        self._id += 1
        body = self._post({
            "jsonrpc": "2.0", "id": self._id, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        })
        msg = self._last_event(body)
        if "error" in msg:
            raise RuntimeError(str(msg["error"])[:200])
        result = msg.get("result", {})
        if result.get("isError"):
            texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
            raise RuntimeError(" ".join(texts)[:200] or "MCP 工具报错")
        for chunk in result.get("content", []):
            if chunk.get("type") == "text":
                try:
                    return json.loads(chunk.get("text") or "{}")
                except json.JSONDecodeError:
                    continue
        return {}


def _fingerprint(text: str) -> str:
    """账号/会话 ID 不落盘：快照只留短指纹，够去重不够复原。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone()


def _text_preview(message: dict) -> str:
    """把一条消息压成一行可读预览。附件类消息只报类型，不猜内容。"""
    kind = message.get("type") or ""
    raw = (message.get("text") or "").strip()
    if kind == "text":
        return raw[:140]
    if kind == "image":
        return "[图片]"
    if kind == "voice":
        return "[语音]"
    if kind == "sticker":
        return "[表情]"
    if kind == "app":
        # 公众号/小程序分享的正文前面挂着 XML 或 "昵称:" 前缀，去掉再截
        body = raw
        if "<msg>" in body or "<appmsg" in body:
            title = ""
            for tag in ("<title>",):
                start = body.find(tag)
                if start >= 0:
                    end = body.find("</title>", start)
                    if end > start:
                        title = body[start + len(tag):end]
                        break
            return ("[链接] " + title)[:140] if title else "[链接]"
        return body[:140]
    if kind == "system":
        return ""
    return raw[:140] or f"[{kind or '未知'}]"


def _search_messages(client: MCP, base: dict, query: str, start: str, end: str, limit: int = 20) -> list[dict]:
    """用 messages.search 做候选发现；游标和 busy 都在这里收敛处理。"""
    out: list[dict] = []
    cursor = None
    busy_retries = 0
    while len(out) < limit:
        # next_cursor 绑定 page_size，逐页必须一致；否则 MCP 返回 invalid_cursor。
        args = {**base, "query": query, "start_time": start, "end_time": end,
                "page_size": 24}
        if cursor:
            args["cursor"] = cursor
        try:
            page = client.call("messages.search", args)
            busy_retries = 0
        except Exception as exc:
            if "busy" in str(exc) or "search_busy" in str(exc):
                busy_retries += 1
                if busy_retries > 3:
                    break
                time.sleep(1.5 * busy_retries)
                continue
            break
        batch = page.get("messages") or []
        out.extend(batch)
        cursor = page.get("next_cursor")
        if not batch or not page.get("has_more") or not cursor:
            break
    return out[:limit]


def _search_global_messages(client: MCP, base: dict, query: str, limit: int = 20) -> list[dict]:
    """Use the newer cross-entity search when available; fall back to messages.search."""
    try:
        page = client.call("search.global", {**base, "query": query, "categories": ["messages"], "page_size": 24})
        results = page.get("results") or page.get("items") or page.get("messages") or []
        messages = [x.get("message", x) if isinstance(x, dict) else x for x in results]
        if messages:
            return messages[:limit]
    except Exception:
        pass
    now = dt.datetime.now().astimezone()
    return _search_messages(client, base, query,
                            (now - dt.timedelta(days=BUSINESS_LOOKBACK_DAYS)).isoformat(timespec="seconds"),
                            now.isoformat(timespec="seconds"), limit)


def _get_message_context(client: MCP, base: dict, message: dict, fallback_count: int = 6) -> list[dict]:
    """Prefer message_ref context; fall back to a bounded query for old MCPs."""
    cid = message.get("conversation_id") or ""
    ref = message.get("message_ref") or ""
    if cid and ref:
        try:
            page = client.call("messages.get_context", {**base, "conversation_id": cid,
                                                        "message_ref": ref, "before": 5, "after": 3})
            context = page.get("messages") or page.get("context") or []
            if context:
                return context
        except Exception:
            pass
    if cid:
        try:
            page = client.call("messages.query", {**base, "conversation_id": cid,
                                                   "recent_count": fallback_count,
                                                   "page_size": fallback_count})
            return page.get("messages") or []
        except Exception:
            pass
    return [message] if message else []


def _business_candidates(client: MCP, base: dict, listed: list[dict], now: dt.datetime) -> list[dict]:
    """先关键词召回，再按会话去重；不对全量会话逐个 query。"""
    start = (now - dt.timedelta(days=BUSINESS_LOOKBACK_DAYS)).isoformat(timespec="seconds")
    end = now.isoformat(timespec="seconds")
    hits: dict[str, dict] = {}
    for term in BUSINESS_TERMS:
        for msg in _search_global_messages(client, base, term, limit=24):
            cid = msg.get("conversation_id") or ""
            if not cid or cid in SYSTEM_CONVERSATIONS or _is_machine(cid, ""):
                continue
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            row = hits.setdefault(cid, {"messages": [], "lastAt": "", "termHits": []})
            row["messages"].append(msg)
            row["termHits"].append(term)
            at = msg.get("created_at") or ""
            if at > row["lastAt"]:
                row["lastAt"] = at
    if not hits:
        return []
    names = {c.get("conversation_id"): c for c in listed}
    missing = [cid for cid in hits if cid not in names]
    if missing:
        # 只有关键词命中的会话才需要补名字；最多翻一遍列表，避免每个候选单独请求。
        try:
            cursor = None
            for _ in range(20):
                args = {**base, "page_size": 100}
                if cursor:
                    args["cursor"] = cursor
                page = client.call("conversations.list", args)
                for conv in page.get("conversations") or []:
                    names.setdefault(conv.get("conversation_id"), conv)
                cursor = page.get("next_cursor")
                if not page.get("has_more") or not cursor or all(cid in names for cid in missing):
                    break
        except Exception:
            pass
    candidates = []
    for cid, data in hits.items():
        conv = names.get(cid, {})
        messages = sorted(data["messages"], key=lambda m: m.get("created_at") or "")[-6:]
        candidates.append({
            "conversationId": _fingerprint(cid),
            "_cid": cid,
            "name": conv.get("display_name") or conv.get("nickname") or "(未命名)",
            "kind": conv.get("type") or ("group" if cid.endswith("@chatroom") else "direct"),
            "at": data["lastAt"],
            "termHits": sorted(set(data["termHits"])),
            "messages": messages,
        })
    candidates.sort(key=lambda x: x.get("at") or "", reverse=True)
    candidates = candidates[:BUSINESS_MAX_CANDIDATES]
    # 关键词命中只证明“出现过这个词”，不能直接当作需求证据；
    # 对少量候选补最近上下文，避免把群发通知误报成机会。
    for candidate in candidates:
        context = _get_message_context(client, base, (candidate.get("messages") or [{}])[-1], fallback_count=6)
        if context:
            candidate["messages"] = context
        else:
            candidate["contextUnavailable"] = True
    return candidates


def _business_lines(item: dict) -> list[str]:
    lines = []
    for msg in item.get("messages") or []:
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\\s+", " ", text).strip()[:180]
        lines.append(("SELF" if msg.get("is_self") else "peer") + ": " + text)
    if not lines:
        lines = ["peer: 关键词命中，但正文不可读"]
    return lines


def _business_analysis(client: MCP, base: dict, listed: list[dict], now: dt.datetime) -> tuple[list[dict], dict]:
    candidates = _business_candidates(client, base, listed, now)
    if not candidates:
        return [], {"candidates": 0, "analyzed": 0, "lookbackDays": BUSINESS_LOOKBACK_DAYS,
                     "terms": list(BUSINESS_TERMS),
                     "judgmentModel": "jev" if sem.jev_client and sem.jev_client.available() else "qwen3:8b-fallback",
                     "narrativeModel": "qwen3:8b" if sem.llm_ready() else "raw-evidence"}
    items = [{"id": "B%d" % (i + 1), "name": c["name"], "kind": c["kind"], "lines": _business_lines(c)}
             for i, c in enumerate(candidates)]
    # Jev 是主判断，不应被本地 qwen 是否在线短路；qwen 只负责叙述补全/降级。
    verdicts = sem.analyze_business(items) if ((sem.jev_client and sem.jev_client.available()) or sem.llm_ready()) else {}
    opportunities, consultations = [], []
    for i, candidate in enumerate(candidates):
        verdict = verdicts.get("B%d" % (i + 1)) or {}
        typ = verdict.get("type") or "none"
        if typ == "none":
            continue
        base_row = {
            "id": candidate["conversationId"],
            "account": "",
            "contact": candidate["name"],
            "kind": candidate["kind"],
            "at": candidate["at"],
            "evidence": verdict.get("evidence") or "关键词命中：" + "、".join(candidate["termHits"][:4]),
            "sourcePreview": _business_lines(candidate)[-1][:180],
            "whyRelated": verdict.get("whyRelated") or "待核对是否与现有业务相关",
            "nextAction": verdict.get("nextAction") or "补充询问具体需求",
            "missingInfo": verdict.get("missingInfo") or "待确认",
            "confidence": verdict.get("confidence") or "low",
            "needsReview": True,
            "analyzed": bool(verdict),
            "judgmentModel": verdict.get("judgmentModel") or "qwen3:8b-fallback",
            "opportunityScore": verdict.get("opportunityScore"),
            "consultationScore": verdict.get("consultationScore"),
            "termHits": candidate["termHits"][:8],
        }
        if typ in ("opportunity", "both"):
            opportunities.append({**base_row,
                "opportunityLevel": verdict.get("opportunityLevel") or "O1",
                "businessFit": verdict.get("businessFit") or "未知",
            })
        if typ in ("consultation", "both"):
            consultations.append({**base_row,
                "consultationType": verdict.get("consultationType") or "未知",
                "surfaceQuestion": verdict.get("surfaceQuestion") or "",
                "underlyingNeed": verdict.get("underlyingNeed") or "",
                "suggestedReply": verdict.get("suggestedReply") or "",
            })
    return (opportunities + consultations), {
        "candidates": len(candidates), "analyzed": len(verdicts),
        "lookbackDays": BUSINESS_LOOKBACK_DAYS, "terms": list(BUSINESS_TERMS),
        "judgmentModel": "jev" if sem.jev_client and sem.jev_client.available() else "qwen3:8b-fallback",
        "narrativeModel": "qwen3:8b" if sem.llm_ready() else "raw-evidence",
    }


def build_account_snapshot(client: MCP, account: dict) -> dict:
    aid = account.get("account_id") or ""
    iid = account.get("instance_id") or ""
    base = {
        "account_id": aid,
        "instance_id": iid,
        "login_generation": account.get("login_generation") or "",
    }

    now = dt.datetime.now().astimezone()
    wait_cutoff = now - dt.timedelta(hours=WAIT_WINDOW_HOURS)

    # ⚠️ page_size 必须**逐页一致**：MCP 的 next_cursor 绑定了第一页的 page_size，
    # 第二页换成别的条数会直接 invalid_cursor（实测：100 → 20 就废了）。
    def list_conversations(limit: int) -> list[dict]:
        """翻会话列表。page_size 必须逐页一致，见下面注释。"""
        out: list[dict] = []
        cursor = None
        while len(out) < limit:
            args = {**base, "page_size": 100}
            if cursor:
                args["cursor"] = cursor
            page = client.call("conversations.list", args)
            batch = page.get("conversations") or []
            out.extend(batch)
            cursor = page.get("next_cursor")
            if not batch or not page.get("has_more") or not cursor:
                break
        return out[:limit]

    event_coverage = {"available": False, "retention": "unknown", "eventGap": False,
                      "returned": 0, "hasMore": False, "conversationCount": 0,
                      "oldestAt": None, "newestAt": None}
    try:
        event_page = client.call("events.read", {**base, "page_size": 500})
        events = event_page.get("events") or []
        times = sorted(e.get("created_at") for e in events if e.get("created_at"))
        event_coverage = {
            "available": True,
            "retention": event_page.get("retention") or "unknown",
            "eventGap": bool(event_page.get("event_gap")),
            "returned": len(events),
            "hasMore": bool(event_page.get("has_more")),
            "conversationCount": len({e.get("conversation_id") for e in events if e.get("conversation_id")}),
            "oldestAt": times[0] if times else None,
            "newestAt": times[-1] if times else None,
        }
    except Exception as exc:
        event_coverage["error"] = type(exc).__name__

    listed: list[dict] = []
    cursor = None
    while len(listed) < SCAN_LIMIT:
        args = {**base, "page_size": 100}
        if cursor:
            args["cursor"] = cursor
        page = client.call("conversations.list", args)
        batch = page.get("conversations") or []
        listed.extend(batch)
        cursor = page.get("next_cursor")
        if not batch or not page.get("has_more") or not cursor:
            break
    listed = listed[:SCAN_LIMIT]

    waiting: list[dict] = []
    active: list[dict] = []
    scanned = 0
    for conv in listed:
        cid = conv.get("conversation_id") or ""
        if not cid:
            continue
        # 一次取 8 条而不是 1 条：调用次数不变，但后面要判断"对方在要什么"
        # 时才有上下文。只取最后一条的话，看到"嗯嗯"根本分不清是收尾还是追问。
        try:
            page = client.call("messages.query", {
                **base, "conversation_id": cid,
                "recent_count": CONTEXT_COUNT, "page_size": CONTEXT_COUNT,
            })
        except Exception:
            continue
        scanned += 1
        messages = page.get("messages") or []
        if not messages:
            continue
        last = messages[0]
        last_at = _parse_iso(last.get("created_at"))
        is_self = bool(last.get("is_self"))
        preview = _text_preview(last)
        if not preview and last.get("type") == "system":
            continue

        row = {
            "name": conv.get("display_name") or conv.get("nickname") or "(未命名)",
            "kind": conv.get("type") or "direct",
            "conversationId": _fingerprint(cid),
            "at": last_at.isoformat() if last_at else "",
            "from": "self" if is_self else "peer",
            "preview": preview,
        }
        active.append(row)

        # 待回复：只有「最后一条是对方发的」+ 在窗口内 + 私聊，或群里 @ 了我。
        # 公众号和系统号先剔掉：它们最后一条永远"不是我发的"，但没人会回携程的促销。
        if is_self or not last_at or last_at < wait_cutoff:
            continue
        if row["kind"] == "direct" and not _is_machine(cid, row["name"]):
            row["reason"] = "私聊最后一条是对方"
            row["hoursAgo"] = round((now - last_at).total_seconds() / 3600, 1)
            row["_cid"] = cid
            row["_messages"] = messages        # 给语义层用，落盘前会清掉
            waiting.append(row)

    # 群里 @ 我：用跨会话搜索拿最近的提及，再按昵称精确过一遍
    mentions: list[dict] = []
    nickname = account.get("display_name") or ""
    if nickname:
        since = (now - dt.timedelta(days=MENTION_DAYS)).isoformat(timespec="seconds")
        until = now.isoformat(timespec="seconds")
        # search_busy 是 MCP 侧的互斥锁：上一个搜索没释放时直接拒绝。
        # 重试三次就够 —— 每次间隔 1.5s，超过说明服务端真的卡住了，跳过提及不影响主流程。
        found = {}
        for attempt in range(3):
            try:
                found = client.call("messages.search", {
                    **base, "query": "@" + nickname,
                    "start_time": since, "end_time": until, "page_size": 40,
                })
                break
            except Exception as exc:
                if "search_busy" not in str(exc):
                    break
                time.sleep(1.5 * (attempt + 1))
        # 群名要按完整会话 ID 反查 —— 快照里只存指纹，这里查的是内存里的原始列表。
        # @ 我的群可能不在前 120 个活跃会话里，所以命中不了就懒加载翻一次全量；
        # 只在真的有提及、且真的查不到时才付这个成本。
        name_by_conv = {c.get("conversation_id"): c.get("display_name") for c in listed}
        missing = {
            m.get("conversation_id") for m in (found.get("messages") or [])
            if m.get("conversation_id") and m.get("conversation_id") not in name_by_conv
        }
        if missing:
            try:
                for conv in list_conversations(1500):
                    cid2 = conv.get("conversation_id")
                    if cid2 in missing:
                        name_by_conv[cid2] = conv.get("display_name")
                        missing.discard(cid2)
                    if not missing:
                        break
            except Exception:
                pass
        for msg in found.get("messages") or []:
            text = msg.get("text") or ""
            if "@" + nickname not in text:
                continue
            at = _parse_iso(msg.get("created_at"))
            mentions.append({
                "name": name_by_conv.get(msg.get("conversation_id")) or "群聊",
                "conversationId": _fingerprint(msg.get("conversation_id") or ""),
                "at": at.isoformat() if at else "",
                "sender": msg.get("sender_name") or msg.get("sender_id") or "",
                "preview": text.replace("\u2005", " ").strip()[:140],
                "isSelf": bool(msg.get("is_self")),
                "_cid": msg.get("conversation_id") or "",
            })

    # 同一个会话在群里提了多次，只留最近一条，避免列表被刷屏
    seen = set()
    deduped = []
    for m in sorted(mentions, key=lambda x: x["at"], reverse=True):
        if m["conversationId"] in seen:
            continue
        seen.add(m["conversationId"])
        deduped.append(m)

    # 新 MCP 可以读取群成员。用它核验“凌一确实在这个群里”，但不把核验失败当成 false：
    # 权限/同步状态不确定时仍保留提醒，并标为待核验。
    for mention in deduped:
        cid = mention.get("_cid") or ""
        if not cid.endswith("@chatroom"):
            mention["mentionVerified"] = None
            mention["mentionBasis"] = "text"
            continue
        try:
            members_page = client.call("groups.members", {**base, "conversation_id": cid, "page_size": 500})
            members = members_page.get("members") or []
            verified = any(
                nickname in {m.get("display_name"), m.get("nickname"), m.get("remark"), m.get("mention_name")}
                for m in members if isinstance(m, dict)
            )
            mention["mentionVerified"] = bool(verified)
            mention["mentionBasis"] = "text+group_member" if verified else "text_only"
        except Exception:
            mention["mentionVerified"] = None
            mention["mentionBasis"] = "text_only"
        mention.pop("_cid", None)

    # ── 语义层：把原始消息转成「对方要什么 / 我该做什么」────────────────────
    # 这里做三件事，每一件都可能失败，失败时降级但不撒谎：
    #   1. 语音转文字（SILK → SenseVoice）；转不出就标 asr="failed"
    #   2. 批量喂本地模型判断分类并改写成人话
    #   3. 模型没起来就保留原文，标 analyzed=false，页面说"未解析"
    analyzed_ids: dict[str, str] = {}
    sem_items: list[dict] = []
    pending_voice = [r for r in waiting
                     if any((m.get("type") or "").lower() == "voice" for m in r.get("_messages") or [])]
    if pending_voice and sem.asr_ready():
        for row in pending_voice:
            for msg in row.get("_messages") or []:
                if (msg.get("type") or "").lower() != "voice":
                    continue
                att = (msg.get("attachments") or [{}])[0]
                ref = att.get("media_ref")
                if not ref:
                    continue
                try:
                    got = client.call_raw("messages.read_media", {
                        **base, "conversation_id": row["_cid"], "media_ref": ref,
                        "representation": "source", "allow_download": False,
                        "byte_offset": 0, "byte_length": 400000,
                    })
                    content = (got.get("content") or [])
                    blob = next((c["resource"].get("blob") for c in content
                                 if c.get("type") == "resource" and c.get("resource")), None)
                    text = sem.transcribe_silk(blob) if blob else ""
                except Exception:
                    text = ""
                msg["_asr"] = text
                if text:
                    msg["_asr_ok"] = True

    def context_lines(row: dict) -> list[str]:
        """把会话最近几条压成给模型看的行。已转写的语音直接用文字。"""
        out = []
        for msg in reversed(row.get("_messages") or []):
            who = "SELF" if msg.get("is_self") else "peer"
            body = (msg.get("text") or "").strip()
            kind = (msg.get("type") or "").lower()
            if kind == "voice":
                body = ("[语音] " + msg["_asr"]) if msg.get("_asr_ok") else "[语音]"
            elif kind in ("image", "sticker", "video", "file"):
                body = sem.describe_attachment(msg)
            elif not body:
                body = sem.describe_attachment(msg)
            else:
                body = re.sub(r"<[^>]+>", " ", body)          # 去 XML 标签
                body = re.sub(r"\s+", " ", body).strip()[:160]
            out.append("%s: %s" % (who, body))
        return out

    for idx, row in enumerate(waiting, start=1):
        key = "W%d" % idx
        analyzed_ids[row["conversationId"]] = key
        sem_items.append({"id": key, "name": row["name"], "kind": row["kind"],
                          "lines": context_lines(row)})
    for idx, item in enumerate(deduped, start=1):
        key = "M%d" % idx
        analyzed_ids[item["conversationId"]] = key
        sem_items.append({"id": key, "name": item["name"], "kind": "group",
                          "lines": ["peer: " + item["preview"]]})

    verdicts: dict[str, dict] = {}
    if sem_items and sem.llm_ready():
        verdicts = sem.classify(sem_items)

    for api_key, row in [(analyzed_ids[r["conversationId"]], r) for r in waiting]:
        v = verdicts.get(api_key) or {}
        row["category"] = v.get("category") or "待回复"
        row["want"] = v.get("want") or ""
        row["action"] = v.get("action") or ""
        row["analyzed"] = bool(v)
        # 语音转写的原话留一份，页面可以展开看
        # _messages 是新 → 旧，所以 said[0] 才是最近一条语音；取 [-1] 会拿到最旧的。
        said = [m["_asr"] for m in (row.get("_messages") or []) if m.get("_asr_ok")]
        if said:
            row["transcript"] = said[0][:300]
        if any((m.get("type") or "").lower() == "voice" for m in row.get("_messages") or []) \
                and not row.get("transcript"):
            row["asrFailed"] = True
        # 内部字段不落盘
        row.pop("_messages", None)
        row.pop("_cid", None)

    for item in deduped:
        v = verdicts.get(analyzed_ids.get(item["conversationId"], "")) or {}
        item["want"] = v.get("want") or ""
        item["action"] = v.get("action") or ""
        item["analyzed"] = bool(v)

    for r in active:
        r.pop("_messages", None)
        r.pop("_cid", None)

    waiting.sort(key=lambda x: x["hoursAgo"])
    active.sort(key=lambda x: x["at"], reverse=True)

    # 机会 / 咨询：先 search 召回，再对少量候选补上下文；与待办分开，
    # 因为“值得跟进”不等于“现在要回复”。
    business_rows, business_meta = _business_analysis(client, base, listed, now)
    for row in business_rows:
        row["account"] = account.get("display_name") or "(未命名账号)"
    opportunities = [r for r in business_rows if "opportunityLevel" in r]
    consultations = [r for r in business_rows if "consultationType" in r]

    # 待办 = 模型判为待回复的。广告和已结束留在快照里但不进待办清单。
    todos = [r for r in waiting if r.get("category") == "待回复"]

    return {
        "account": {
            "id": _fingerprint(aid),
            "name": nickname or "(未命名账号)",
            "ready": bool(account.get("ready")),
            "wechatVersion": account.get("wechat_version") or "",
            "pluginVersion": account.get("plugin_version") or "",
        },
        "waiting": waiting,
        "mentions": deduped,
        "todos": todos,
        "opportunities": opportunities,
        "consultations": consultations,
        "businessAnalysis": business_meta,
        "eventCoverage": event_coverage,
        "active": active[:40],
        "scanned": scanned,
        "semantic": {
            "analyzed": len(verdicts) + int(business_meta.get("analyzed", 0)),
            "llm": sem.OLLAMA_MODEL if sem.llm_ready() else "",
            "judgmentModel": business_meta.get("judgmentModel", "qwen3:8b-fallback"),
            "asr": bool(sem.asr_ready()),
        },
    }


def main() -> int:
    now = dt.datetime.now().astimezone()
    snapshot: dict = {
        "version": 1,
        "generatedAt": now.isoformat(timespec="seconds"),
        "source": "wechat-mcp",
        "dataPolicy": (
            "只读快照。MCP 不提供未读计数，待回复按「最后一条是对方发的」推断，"
            "群聊只收正文里 @ 到你的消息；账号与会话 ID 只落指纹。"
        ),
    }

    try:
        client = MCP()
        client.initialize()
        try:
            status = client.call("system.status")
        except Exception:
            status = {}
        limits = status.get("limits") if isinstance(status, dict) else {}
        snapshot["mcp"] = {
            "server": client.server_info,
            "protocolVersion": client.protocol_version,
            "protocolVersions": status.get("protocol_versions", []) if isinstance(status, dict) else [],
            "transport": status.get("transport") if isinstance(status, dict) else "streamable_http",
            "eventRetentionSeconds": limits.get("event_seconds") if isinstance(limits, dict) else None,
            "eventLimit": limits.get("events") if isinstance(limits, dict) else None,
            "pageItems": limits.get("page_items") if isinstance(limits, dict) else None,
            "mediaRequestsPerInstance": limits.get("media_requests_per_instance") if isinstance(limits, dict) else None,
        }
        accounts = (client.call("accounts.list") or {}).get("accounts") or []
    except Exception as exc:  # MCP 没开：写一份"未连接"快照，不中断同步
        snapshot.update({
            "available": False,
            "reason": f"{type(exc).__name__}: {str(exc)[:120]}",
            "accounts": [],
        })
        _write(snapshot)
        print(f"⚠ 微信 MCP 不可用（{type(exc).__name__}），已写入未连接快照")
        return 0

    ready = [a for a in accounts if a.get("ready")]
    snapshot["available"] = bool(ready)
    if not ready:
        snapshot["reason"] = "MCP 在线但没有已登录且就绪的微信账号"
        snapshot["accounts"] = []
        _write(snapshot)
        print("⚠ 微信 MCP 在线，但没有就绪账号")
        return 0

    results = []
    for index, account in enumerate(ready):
        if index:
            time.sleep(2.0)   # 账号之间让 MCP 喘一口气，否则第二个账号必撞 busy
        try:
            results.append(build_account_snapshot(client, account))
        except Exception as exc:
            results.append({
                "account": {
                    "id": _fingerprint(account.get("account_id") or ""),
                    "name": account.get("display_name") or "(未命名账号)",
                    "ready": True,
                },
                "waiting": [], "mentions": [], "active": [], "scanned": 0,
                "error": f"{type(exc).__name__}: {str(exc)[:120]}",
            })

    snapshot["accounts"] = results
    snapshot["summary"] = {
        "waiting": sum(len(r.get("waiting") or []) for r in results),
        "mentions": sum(len(r.get("mentions") or []) for r in results),
        "opportunities": sum(len(r.get("opportunities") or []) for r in results),
        "consultations": sum(len(r.get("consultations") or []) for r in results),
        "scanned": sum(r.get("scanned") or 0 for r in results),
        "windowHours": WAIT_WINDOW_HOURS,
        "mentionDays": MENTION_DAYS,
        "businessLookbackDays": BUSINESS_LOOKBACK_DAYS,
    }
    _write(snapshot)
    s = snapshot["summary"]
    print(f"✓ 微信快照：{s['scanned']} 个会话 · 待回复 {s['waiting']} · 群里 @ 我 {s['mentions']}")
    return 0


def _write(snapshot: dict) -> None:
    """原子写 + 留一份上一版，页面读取时不会撞到半截 JSON。"""
    tmp = OUT.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=1)
    if OUT.exists():
        try:
            shutil.copy2(OUT, OUT.with_suffix(".json.bak"))
        except OSError:
            pass
    os.replace(tmp, OUT)


if __name__ == "__main__":
    sys.exit(main())
