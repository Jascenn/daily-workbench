#!/usr/bin/env python3
"""每日选题汇总：从 radar.json 里挑最值得写的 3 条，推到提醒事项 + 桌面通知。

为什么不做自动发微信：微信经营层目前是只读的（写接口返回 501），
在用户没明确打开写入能力前，不偷偷往他的聊天窗口塞消息。
提醒事项 + 通知是本地可见、可删、无副作用的方式。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RADAR = ROOT / "radar.json"
LIST_NAME = os.environ.get("RADAR_DIGEST_LIST", "工作台")
CONFIG = Path.home() / ".config" / "daily-workbench" / "radar-digest.json"
STATE = Path.home() / ".config" / "daily-workbench" / "radar-digest-state.json"
TOP_N = int(os.environ.get("RADAR_DIGEST_TOP", "3"))


def score(item: dict) -> float:
    rel = item.get("relevance")
    ang = item.get("angle")
    return (float(rel) * 0.6 if isinstance(rel, (int, float)) else 0.0) + \
           (float(ang) * 0.4 if isinstance(ang, (int, float)) else 0.0)


def pick(radar: dict, n: int = TOP_N) -> list[dict]:
    """只挑"判断过 + 有可写角度"的条目。

    needsHuman 的条目不进汇总：那是"没判断出来"的东西，塞进每日提醒只会变成噪音。
    """
    rows = [it for it in (radar.get("items") or [])
            if it.get("analyzed") and not it.get("needsHuman")
            and (it.get("whyRelated") or (it.get("angles") or []))]
    rows.sort(key=score, reverse=True)
    return rows[:n]


def _pct(value) -> str:
    try:
        return "%d%%" % round(max(0.0, min(9.0, float(value))) / 9 * 100)
    except (TypeError, ValueError):
        return ""


def render(radar: dict, rows: list[dict], when: dt.datetime | None = None) -> str:
    when = when or dt.datetime.now().astimezone()
    # 头部只写日期，不写时分：正文会用来算内容哈希，带分钟等于每次都是"新内容"，
    # 群推送的去重就永远不生效（2026-11-21 测试抓到的真问题）。
    head = "选题雷达 · %s" % when.strftime("%m-%d")
    if not rows:
        cov = radar.get("coverage") or {}
        return "%s\n本轮没有达到可写线的热点（%s/%s 源可用，判断 %s 条）。" % (
            head, cov.get("sourcesOk", 0), cov.get("sourcesTotal", 0), cov.get("itemsJudged", 0))
    lines = [head]
    for i, item in enumerate(rows, start=1):
        bits = [item.get("sourceName") or ""]
        if _pct(item.get("relevance")):
            bits.append("相关 " + _pct(item["relevance"]))
        lines.append("%d. %s" % (i, item.get("title") or "无标题"))
        lines.append("   %s" % " · ".join(b for b in bits if b))
        if item.get("whyRelated"):
            lines.append("   为什么：%s" % item["whyRelated"])
        if item.get("angles"):
            lines.append("   能写：%s" % " / ".join(item["angles"]))
        if item.get("nextAction"):
            lines.append("   下一步：%s" % item["nextAction"])
        if item.get("url"):
            lines.append("   %s" % item["url"])
    return "\n".join(lines)


ITEM_MARKS = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣")


def render_group(radar: dict, rows: list[dict], when: dt.datetime | None = None) -> str:
    """微信群版：内容一条不少，只调排版。

    emoji 只做"路标"（为什么/能写/下一步/链接），不替代文字：
    有的是把手机竖屏的扫读路径拉出来，不是在当装饰。
    """
    when = when or dt.datetime.now().astimezone()
    head = "📡 选题雷达 · %s" % when.strftime("%m-%d")
    if not rows:
        cov = radar.get("coverage") or {}
        return "%s\n今天没有达到可写线的热点（%s/%s 源可用）。" % (
            head, cov.get("sourcesOk", 0), cov.get("sourcesTotal", 0))

    blocks = [head]
    for i, item in enumerate(rows):
        mark = ITEM_MARKS[i] if i < len(ITEM_MARKS) else "%d." % (i + 1)
        lines = ["%s %s" % (mark, item.get("title") or "无标题")]
        meta = [item.get("sourceName") or ""]
        if _pct(item.get("relevance")):
            meta.append("相关 " + _pct(item["relevance"]))
        lines.append("   %s" % " · ".join(m for m in meta if m))
        if item.get("whyRelated"):
            lines.append("   💡 为什么：%s" % item["whyRelated"])
        angles = item.get("angles") or []
        if angles:
            lines.append("   ✍️ 能写：%s" % " / ".join(angles))
        if item.get("nextAction"):
            lines.append("   ✅ 下一步：%s" % item["nextAction"])
        if item.get("url"):
            lines.append("   🔗 %s" % item["url"])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _applescript_str(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def push_reminder(text: str, title: str) -> tuple[bool, str]:
    """在提醒事项里建一条。列表不存在就建，重复标题当天不重复建。

    用 `whose` 过滤而不是 `repeat with r in every reminder`：条目一多，遍历会超过
    25 秒直接 TimeoutExpired（2026-11-22 早上 9 点就是这么失败的）。
    """
    script = '''
tell application "Reminders"
  if not (exists list "%(list)s") then make new list with properties {name:"%(list)s"}
  set targetList to list "%(list)s"
  set todayTitle to "%(title)s"
  set hits to (reminders of targetList whose name is todayTitle and completed is false)
  if (count of hits) > 0 then
    set body of item 1 of hits to "%(body)s"
    return "updated"
  end if
  make new reminder at end of targetList with properties {name:todayTitle, body:"%(body)s"}
  return "created"
end tell
''' % {"list": _applescript_str(LIST_NAME), "title": _applescript_str(title),
       "body": _applescript_str(text)}
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return False, (out.stderr or "").strip()[:160]
        return True, (out.stdout or "").strip()
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:120])


def push_notification(text: str) -> tuple[bool, str]:
    first = (text.splitlines()[1] if len(text.splitlines()) > 1 else text)[:80]
    script = 'display notification "%s" with title "选题雷达" subtitle "%s"' % (
        _applescript_str(first), _applescript_str("今天可写的选题已放进提醒事项"))
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
        return out.returncode == 0, (out.stderr or "").strip()[:120]
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:120])


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, STATE)
    except OSError:
        pass


def digest_hash(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def request_id_for(conversation_id: str, text: str, when: dt.date | None = None) -> str:
    """幂等键必须绑定内容：MCP 同一键换内容会 idempotency_conflict（实测）。"""
    import hashlib
    day = (when or dt.date.today()).isoformat()
    return "wbradar-%s-%s" % (day, hashlib.sha1((conversation_id + text).encode("utf-8")).hexdigest()[:10])


def push_wechat(text: str) -> tuple[bool, str]:
    """发到配置的会话。

    submitted 只代表已提交（delivery=unknown），不代表送达；失败不自动重发。
    """
    conf = (load_config().get("wechat") or {})
    cid = conf.get("conversationId") or os.environ.get("RADAR_WECHAT_CONVERSATION", "")
    if not cid:
        return False, "未配置群会话"

    import importlib.util
    module_path = ROOT / "scripts" / "sync" / "wechat-sync.py"
    spec = importlib.util.spec_from_file_location("wechat_sync_for_digest", module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    client = mod.MCP()
    try:
        client.initialize()
        account = (client.call("accounts.list").get("accounts") or [{}])[0]
        base = {"account_id": account.get("account_id") or "",
                "instance_id": account.get("instance_id") or "",
                "login_generation": account.get("login_generation") or ""}
        res = client.call("messages.send_text",
                          {**base, "conversation_id": cid, "text": text,
                           "client_request_id": request_id_for(cid, text)})
        status = res.get("status") or "unknown"
        return status == "submitted", "operation=%s status=%s delivery=%s" % (
            res.get("operation_id", "?"), status, res.get("delivery", "?"))
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:120])
    finally:
        try:
            client.close()
        except Exception:
            pass


def main(argv: list[str]) -> int:
    if not RADAR.exists():
        print("没有 radar.json，先跑 python3 scripts/sync/radar-sync.py", file=sys.stderr)
        return 2
    radar = json.loads(RADAR.read_text(encoding="utf-8"))
    rows = pick(radar)
    text = render(radar, rows)
    print(text)

    if "--print" in argv:
        return 0

    conf = load_config().get("wechat") or {}
    cid = conf.get("conversationId") or ""
    today = dt.datetime.now().astimezone().strftime("%m-%d")

    if "--dry-run" in argv:
        print("[dry-run] 提醒事项：%s（%d 条候选）"
              % ("会写入 " + LIST_NAME if rows else "会跳过（没有可写条目）", len(rows)))
        print("[dry-run] 通知：会发送")
        print("[dry-run] 微信：%s" % ("会推送到 " + str(conf.get("label", cid)) if (rows and cid)
                                      else "会跳过（%s）" % ("没有可写条目" if not rows else "未配置会话")))
        return 0

    remind_failed = False
    if rows:
        # 标题带日期：每天留一条记录，同一天重复跑只更新自己，不堆积。
        ok, info = push_reminder(text, "选题雷达 · %s · %d 条候选" % (today, len(rows)))
        print("提醒事项：%s %s" % ("已写入" if ok else "失败", info))
        # 提醒写不进去只是少一个入口，不该连带把群推送也掐掉（今天就是这么丢的）。
        remind_failed = not ok

    ok2, info2 = push_notification(text)
    print("通知：%s %s" % ("已发送" if ok2 else "失败", info2))

    # 群推送：只有"有可写条目"且"内容与上次不同"才发，避免每天刷同一条。
    if not cid:
        print("微信：未配置目标会话，跳过")
        return 0
    if not rows:
        print("微信：今天没有可写条目，跳过（不往群里发空结果）")
        return 0
    # 群里发紧凑版（微信不渲染 markdown、窄屏怕长行），提醒事项保留详细版。
    wechat_text = render_group(radar, rows)
    state = load_state()
    h = digest_hash(wechat_text)
    if state.get("lastHash") == h and state.get("lastConversation") == cid:
        print("微信：内容与上次相同（%s 已发过），跳过" % state.get("lastSentAt", "?"))
        return 0

    ok3, info3 = push_wechat(wechat_text)
    print("微信：%s %s" % ("已推送" if ok3 else "失败", info3))
    if ok3:
        save_state({"lastHash": h, "lastConversation": cid,
                    "lastSentAt": dt.datetime.now().astimezone().isoformat(timespec="seconds")})
    if not ok3:
        return 4
    return 3 if remind_failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
