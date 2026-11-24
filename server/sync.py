"""
[INPUT]: 依赖 scripts/sync-now.sh、sync-log.json、web-presence/wechat/radar 快照与 Jev 配置
[OUTPUT]: 导出 _jev_*、_sync_log_append、_run_sync_process、_sync_timeout_entry、_parse_sync_step_*、_build_sync_steps
[POS]: server/ 同步执行、超时进程组回收、日志解析与 Jev 代理子模块
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""
import datetime
import json
import math
import os
import re
import signal
import subprocess
import time
import urllib.error
import urllib.request

from server.runtime import cfg as _cfg

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JEV_SETTINGS = os.path.expanduser("~/.config/daily-workbench/jev-settings.json")
SYNC_SCRIPT = os.path.join(DIR, "scripts", "sync-now.sh")
SYNC_TIMEOUT = 300
SYNC_LOG = os.path.join(DIR, "sync-log.json")
SYNC_LOG_KEEP = 20
WEB_PRESENCE = os.path.join(DIR, "web-presence.json")
WECHAT = os.path.join(DIR, "wechat.json")
RADAR = os.path.join(DIR, "radar.json")

def _jev_settings_read():
    try:
        with open(JEV_SETTINGS, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _jev_settings_write(d):
    os.makedirs(os.path.dirname(JEV_SETTINGS), exist_ok=True)
    tmp = JEV_SETTINGS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, JEV_SETTINGS)


def _jev_call(text):
    """调 TypeSafe Jev 对一段文本做质量判断。

    只读 ``~/.config/jev-jarvis/env``，使用直连；不再硬编码某个本机代理端口，
    避免代理未启动或端口变化时把可用的网络链路一起拖垮。
    """
    cfg = {}
    env_path = os.path.expanduser("~/.config/jev-jarvis/env")
    try:
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    except OSError:
        return False, {"error": "未找到 ~/.config/jev-jarvis/env"}
    key = cfg.get("TYPESAFE_API_KEY", "")
    if not key:
        return False, {"error": "未配置 TYPESAFE_API_KEY"}
    base = (cfg.get("TYPESAFE_BASE_URL") or "https://api.typesafe.ai").rstrip("/")
    model = cfg.get("TYPESAFE_MODEL") or "jev-latest"
    payload = {
        "model": model,
        "state": "以下是一段要发布的内容，请从「对读者是否有实际用处」的角度给出评分与理由。",
        "questions": {
            "quality": {
                "type": "score",
                "instructions": "内容质量 0-10 分：有用、有观点、说人话。",
                # score 的 criteria 是 list（0-9 档文字说明），与 jev-jarvis
                # 内置 RISK_LEVELS 同构 —— 上限 10 档，从无价值到非常好。
                "criteria": [
                    "完全没有用", "几乎没有信息量", "有点用但很空", "信息量少",
                    "一般，缺观点", "有用但平淡", "有用，有一点观点",
                    "较好，有用有观点", "很好，有用有观点说人话", "优秀，看完就想收藏",
                ],
            }
        },
        # 附带待评文本：TypeSafe 的 state 是判断上下文，直接拼接
    }
    body = dict(payload)
    body["state"] = payload["state"] + "\n\n" + text[:2000]
    req = urllib.request.Request(
        base + "/v1/systemone",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return True, json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None)
        detail = ""
        if code is not None:
            try:
                detail = exc.read().decode("utf-8")[:160]  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
        return False, {"error": "HTTP %s %s" % (code or "?", detail or str(exc)[:120])}
# 订阅真源独立化：只读写工作台自己的 rss-store.json，运行时完全不碰 Obsidian
# 插件文件（2026-10-31 插件更新丢过一次 data.json）。全新环境需要种子数据时，
# 显式执行一次 python3 scripts/sync/sections.py --import-qiaomu。

def _sync_log_append(entry):
    """每次同步结束追加一条记录，只留最近 _cfg("_cfg("SYNC_LOG", SYNC_LOG)_KEEP", _cfg("SYNC_LOG", SYNC_LOG)_KEEP) 条。
    日志是本地诊断信息，不属于需备份的用户数据，写入失败不影响同步结果本身。"""
    try:
        logs = []
        if os.path.exists(_cfg("SYNC_LOG", SYNC_LOG)):
            try:
                with open(_cfg("SYNC_LOG", SYNC_LOG), encoding="utf-8") as f:
                    logs = json.load(f)
                if not isinstance(logs, list):
                    logs = []
            except Exception:
                logs = []
        logs.append(entry)
        tmp = _cfg("SYNC_LOG", SYNC_LOG) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(logs[-_cfg("SYNC_LOG_KEEP", SYNC_LOG_KEEP):], f, ensure_ascii=False, indent=2)
        os.replace(tmp, _cfg("SYNC_LOG", SYNC_LOG))
    except OSError:
        pass


def _run_sync_process(timeout=None):
    if timeout is None:
        timeout = _cfg("SYNC_TIMEOUT", SYNC_TIMEOUT)
    """运行同步脚本，并在超时后清理整个进程组。

    同步脚本会再拉起 Python 同步任务；只杀掉 bash 本身会让 Notion 拉取等
    后代进程继续占用网络和文件。独立进程组保证超时不会留下后台残骸。
    """
    proc = subprocess.Popen(
        ["bash", _cfg("SYNC_SCRIPT", SYNC_SCRIPT)],
        cwd=DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        raise subprocess.TimeoutExpired(
            proc.args,
            timeout,
            output=stdout,
            stderr=stderr,
        )
    return subprocess.CompletedProcess(
        proc.args,
        proc.returncode,
        stdout,
        stderr,
    )


def _sync_timeout_entry(started, trigger):
    """构造同步超时记录；独立函数便于验证超时一定会在历史里留痕。"""
    state = _cfg("_read_current")() or {}
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ok": False,
        "code": None,
        "elapsed": round(time.time() - started, 1),
        "earnings": len(state.get("earnings", [])),
        "projects": len(state.get("projects", [])),
        "error": "同步超时（%ds），已终止全部子进程" % SYNC_TIMEOUT,
        "trigger": trigger if trigger in ("manual", "auto") else "manual",
        "steps": [],
    }


def _parse_sync_step_timings(out):
    """解析 sync-now.sh 的分阶段耗时标记。

    标记只用于本机诊断，格式固定为 ``__WB_STEP__\\t步骤名\\t秒``。
    同名标记以最后一次为准；旧脚本没有标记时返回空字典，不伪造耗时。
    """
    import re as _re

    timings = {}
    for match in _re.finditer(
            r"__WB_STEP__\t([^\t\r\n]+)\t([0-9]+(?:\.[0-9]+)?)", out or ""):
        name, raw = match.groups()
        try:
            elapsed = round(float(raw), 1)
        except ValueError:
            continue
        if math.isfinite(elapsed) and elapsed >= 0:
            timings[name] = elapsed
    return timings


def _strip_sync_step_timings(out):
    """从给用户看的脚本输出里移除机器标记，避免失败提示出现内部协议行。"""
    return "\n".join(
        line for line in (out or "").splitlines()
        if not line.startswith("__WB_STEP__\t")
        and not line.startswith("__WB_STEP_FAIL__\t")
    ).strip()


def _parse_sync_step_failures(out):
    """可选步骤的失败名单。可选步骤不中断链路，只能靠显式标记上报。"""
    names = set()
    for line in (out or "").splitlines():
        if line.startswith("__WB_STEP_FAIL__\t"):
            name = line.split("\t", 1)[1].strip()
            if name:
                names.add(name)
    return names


def _sync_error_line(out, code=None):
    """从脚本输出中挑出真正可诊断的错误行。

    同步脚本的第一行固定是“===== 开始同步 =====”，不能把它当错误写进历史。
    优先取 Traceback / Error / 失败行；没有明确错误时保留最后一条有效输出，
    再退到退出码，保证日志至少能告诉用户失败发生在哪一层。
    """
    lines = [
        line.strip() for line in (out or "").splitlines()
        if line.strip()
        and not line.startswith("__WB_STEP__\t")
        and not line.lstrip().startswith("=====")
        and not line.lstrip().startswith("✓")
    ]

    def score(line):
        low = line.lower()
        if "traceback" in low:
            return 2
        if "error" in low or "exception" in low:
            return 5
        if line.startswith("✗"):
            return 4
        if "失败" in line or "错误" in line:
            return 2
        return 1

    if lines:
        # 同分取最后一条：Python traceback 的末行通常才是真正的异常摘要。
        best = max(enumerate(lines), key=lambda item: (score(item[1]), item[0]))[1]
        return best[:160]
    if code is not None:
        return "同步脚本退出，code %s" % code
    return ""


def _build_sync_steps(code, out, state):
    """把一次同步拆成 Linux 启动日志式的步骤行（✓/✗ + 一句话结果）。
    步骤顺序与 sync-now.sh 一致；失败时只列到挂掉的那一步 —— 后面的没跑。"""
    import re as _re

    timings = _parse_sync_step_timings(out)

    def finish(rows):
        for row in rows:
            if row["name"] in timings:
                row["elapsed"] = timings[row["name"]]
        return rows

    def rss_subs():
        try:
            d = _cfg("_rss_read")()
            subs = d.get("subscriptions") if isinstance(d, dict) else None
            return len(subs) if isinstance(subs, list) else None
        except Exception:
            return None

    def wechat_line():
        """微信快照成功与否不影响整条同步，所以这里只描述状态，不参与 ok 判定。"""
        try:
            with open(_cfg("WECHAT", WECHAT), encoding="utf-8") as f:
                wx = json.load(f)
            if not isinstance(wx, dict):
                return None
            if not wx.get("available"):
                return "未连接（%s）" % (wx.get("reason") or "MCP 不在线")
            s2 = wx.get("summary") or {}
            return "待办 %s · 机会 %s · 咨询 %s · 群里 @ 我 %s · 扫描 %s 个会话" % (
                sum(len(a.get("todos") or []) for a in (wx.get("accounts") or []) if isinstance(a, dict)),
                s2.get("opportunities", 0), s2.get("consultations", 0),
                s2.get("mentions", 0), s2.get("scanned", 0))
        except Exception:
            return None

    def web_line():
        try:
            with open(_cfg("WEB_PRESENCE", WEB_PRESENCE), encoding="utf-8") as f:
                wp = json.load(f)
            sites = wp.get("sites") if isinstance(wp, dict) else None
            if not isinstance(sites, list) or not sites:
                return None
            bad = [s for s in sites if isinstance(s, dict)
                   and s.get("state") not in ("ok", "slow", "checking")]
            total = len(sites)
            slow = [s for s in sites if isinstance(s, dict) and s.get("state") == "slow"]
            line = "%d 个站点正常" % (total - len(bad)) if not bad \
                else "%d 个站点中 %d 个异常" % (total, len(bad))
            if slow:
                line += " · %d 个偏慢（%s）" % (
                    len(slow), "、".join(str(s.get("name") or s.get("id") or "?") for s in slow))
            return line
        except Exception:
            return None

    def radar_line():
        try:
            with open(_cfg("RADAR", RADAR), encoding="utf-8") as f:
                rd = json.load(f)
            cov = rd.get("coverage") or {}
            if not cov:
                return None
            return "%d/%d 源可用 · 抓取 %d 条 · 可写 %d 条" % (
                cov.get("sourcesOk", 0), cov.get("sourcesTotal", 0),
                cov.get("itemsFetched", 0), cov.get("keptForWriting", 0))
        except Exception:
            return None

    fail_at = {2: "ntn", 3: "ntn", 4: "notion", 6: "sections", 7: "social", 5: "local"}
    fail_step = fail_at.get(code) if code else None
    ok = code == 0
    steps = []
    steps.append({"name": "前置检查", "ok": fail_step not in ("ntn",),
                  "detail": "ntn 存在 · Surge 通路 401" if fail_step != "ntn" else "失败，见错误信息"})
    if fail_step == "ntn":
        return finish(steps)

    diff = ""
    m = _re.search(r"原 \((\d+), (\d+)\) → 新 \((\d+), (\d+)\)", out)
    if m:
        oe, op, ne, np_ = map(int, m.groups())
        parts = []
        if ne != oe:
            parts.append("收益 %+d 条" % (ne - oe))
        if np_ != op:
            parts.append("项目 %+d 条" % (np_ - op))
        diff = (" · 变化：" + " · ".join(parts)) if parts else " · 数据无变化"
    drafts = len([e for e in (state.get("earnings") or [])
                  if isinstance(e, dict) and e.get("source") == "local"]) \
        if isinstance(state, dict) else None
    draft_txt = " · 本地草稿 %d 篇待整理" % drafts if drafts else ""
    ne_count = len(state.get("earnings") or []) if isinstance(state, dict) else 0
    np_count = len(state.get("projects") or []) if isinstance(state, dict) else 0
    steps.append({"name": "Notion 拉取", "ok": fail_step != "notion",
                  "detail": "收益 %d 条 / 项目 %d 条%s%s" % (
                      ne_count, np_count, diff, draft_txt)
                  if fail_step != "notion" else "失败，data.json 未修改"})
    if fail_step == "notion":
        return finish(steps)

    n = rss_subs()
    steps.append({"name": "资讯快照", "ok": fail_step != "sections",
                  "detail": ("订阅源 %d 个 · 已重建" % n) if n else "已重建"})
    if fail_step == "sections":
        return finish(steps)

    step_failures = _parse_sync_step_failures(out)

    social_ok = fail_step != "social" and "自媒体快照" not in step_failures
    steps.append({"name": "自媒体快照", "ok": social_ok,
                  "detail": "已刷新" if social_ok else "失败，保留上一份快照"})
    if fail_step == "social":
        return finish(steps)

    wx = wechat_line()
    wx_ok = "微信快照" not in step_failures
    steps.append({"name": "微信快照", "ok": wx_ok,
                  "detail": (wx or "已刷新（未连接微信 MCP）") if wx_ok else "失败，保留上一份快照"})
    w = web_line()
    web_ok = "网站监控" not in step_failures
    steps.append({"name": "网站监控", "ok": web_ok,
                  "detail": (w or "已刷新（快照缺失）") if web_ok else "失败，保留上一份快照"})
    rd = radar_line()
    radar_ok = "选题雷达" not in step_failures
    steps.append({"name": "选题雷达", "ok": radar_ok,
                  "detail": (rd or "已刷新（无快照）") if radar_ok else "失败，保留上一份快照"})
    steps.append({"name": "离线兜底页", "ok": ok,
                  "detail": "已重建" if ok else "失败"})
    return finish(steps)



