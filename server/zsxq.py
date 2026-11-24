"""
[INPUT]: 依赖本地 zsxq-cli、Ollama（可选）、scripts/sync/social-sync.py 与 ~/.config/daily-workbench/zsxq-publish.json
[OUTPUT]: 导出知识星球同步、详情读取、标签选项、AI 草稿续写、预览校验与两步发布全部函数
[POS]: server/ 知识星球业务子模块，由 app.py 的 /api/zsxq/* 路由调用并 re-export 兼容旧测试
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""
import datetime
import glob
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOCIAL = os.path.join(DIR, "social.json")

_APP_GLOBALS = None


def bind_app_globals(ns):
    """绑定入口模块全局命名空间，使历史测试对 app.ZSXQ_* 的 monkeypatch 透明生效。"""
    global _APP_GLOBALS
    _APP_GLOBALS = ns


def _cfg_val(name, default):
    if _APP_GLOBALS is not None and name in _APP_GLOBALS:
        return _APP_GLOBALS[name]
    return default

def _find_cli(name: str) -> str:
    """launchd 启动的进程 PATH 很短，shutil.which 找不到 homebrew 里的命令。

    先走 PATH，再按常见安装位置兜底；都没有才返回空（调用方要如实报错）。
    """
    found = shutil.which(name)
    if found:
        return found
    for directory in ("/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")):
        candidate = os.path.join(directory, name)
        if os.access(candidate, os.X_OK):
            return candidate
    return ""


def _node_bin_dir() -> str:
    """node 的 bin 目录。zsxq-cli 是 `#!/usr/bin/env node` 脚本，
    launchd 的 PATH 里没有 nvm/fnm 的目录，直接调会得到 `env: node: No such file`。"""
    found = shutil.which("node")
    if found:
        return os.path.dirname(found)
    candidates = sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin")), reverse=True)
    candidates += [os.path.expanduser("~/Library/Application Support/fnm/aliases/default/bin"),
                   "/opt/homebrew/bin", "/usr/local/bin"]
    for directory in candidates:
        if os.access(os.path.join(directory, "node"), os.X_OK):
            return directory
    return ""


def _cli_env() -> dict:
    env = dict(os.environ)
    extra = [d for d in (_node_bin_dir(), "/opt/homebrew/bin", "/usr/local/bin") if d]
    if extra:
        env["PATH"] = ":".join(extra + [env.get("PATH", "")])
    return env


ZSXQ_CLI = _find_cli("zsxq-cli")
_ZSXQ_DETAIL_CACHE: dict[str, tuple[float, dict]] = {}
_ZSXQ_DETAIL_TTL = 3600


def zsxq_clean_text(text) -> str:
    """剥掉星球富文本标记并解码，返回可读正文（保留换行，全文阅读要段落）。"""
    import re as _re
    import urllib.parse as _up
    t = str(text or "")
    t = _re.sub(r"<e[^>]*/>", "", t)
    t = _re.sub(r"<e[^>]*>.*?</e>", "", t, flags=_re.S)
    t = _up.unquote(t)
    # 保留段落：空行压成一行空行，连续三个以上换行折成两个
    lines = [" ".join(line.split()) for line in t.splitlines()]
    body = "\n".join(lines).strip()
    body = _re.sub(r"\n{3,}", "\n\n", body)
    return body


ZSXQ_PUBLISH_CONFIG = os.path.expanduser("~/.config/daily-workbench/zsxq-publish.json")
ZSXQ_PUBLISH_LOG = os.path.expanduser("~/.config/daily-workbench/zsxq-publish-log.jsonl")
# Read-only, manually verified history from the 2026-11-22 memory record.
# This is deliberately separate from the live publish log and merged at read time.
ZSXQ_HISTORICAL_SEEDS = []  # 公开版默认留空；可在本机 ~/.config/daily-workbench/ 自行配置


def zsxq_publish_config() -> dict:
    """发帖目标只有配置里指定的那一个星球，不支持页面随手选。"""
    try:
        cfg_path = _cfg_val("ZSXQ_PUBLISH_CONFIG", ZSXQ_PUBLISH_CONFIG)
        with open(cfg_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def zsxq_publish_targets(cfg: dict) -> list[dict]:
    """配置里允许发布的星球列表。

    老配置只有单个 ``groupId``，新配置可以写 ``groups: [{groupId, groupName}]``；
    两种都读，单个也当成只有一个目标，行为不变。
    """
    out: list[dict] = []
    for row in (cfg.get("groups") or []):
        if not isinstance(row, dict):
            continue
        gid = str(row.get("groupId") or "").strip()
        if gid and all(x["groupId"] != gid for x in out):
            out.append({"groupId": gid, "groupName": str(row.get("groupName") or gid)})
    gid = str(cfg.get("groupId") or "").strip()
    if gid and all(x["groupId"] != gid for x in out):
        out.insert(0, {"groupId": gid, "groupName": str(cfg.get("groupName") or gid)})
    return out


def zsxq_compose(text: str, tags: list[str], ai_mode: str) -> str:
    """拼出真正要发出去的正文。

    标签放第一行（凌一的既有习惯），已存在就不重复加；AI 内容强制带 bot 标签，
    避免"声明了 aigc 但正文看不出来"。
    """
    body = str(text or "").strip()
    clean_tags = []
    for tag in tags or []:
        t = str(tag or "").strip().lstrip("#")
        if t and t not in clean_tags:
            clean_tags.append(t)
    # 只有"确实要拼标签行"时才补 bot。标签为空说明调用方自己处理首行
    # （发布路由就是这么做的：富文本标签行由 zsxq_tag_line 生成），
    # 这里再补就会出现一条多余的纯文本 #bot（2026-11-22 实测踩到）。
    if clean_tags and ai_mode == "aigc" and "bot" not in clean_tags:
        clean_tags.append("bot")
    if not clean_tags:
        return body
    tag_line = " ".join("#" + t for t in clean_tags)
    first_line = body.splitlines()[0].strip() if body else ""
    if all(("#" + t) in first_line for t in clean_tags):
        return body
    return (tag_line + "\n" + body).strip() if body else tag_line


def zsxq_publish_digest(group_id: str, text: str, ai_mode: str) -> str:
    raw = "%s\n%s\n%s" % (group_id, ai_mode, text)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def zsxq_recent_publishes(minutes: int = 30) -> list[dict]:
    """最近 N 分钟的发布记录：用于防重复发送（内容哈希相同直接拒）。"""
    import time as _time
    cutoff = _time.time() - minutes * 60
    out = []
    try:
        log_path = _cfg_val("ZSXQ_PUBLISH_LOG", ZSXQ_PUBLISH_LOG)
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if float(row.get("at") or 0) >= cutoff:
                    out.append(row)
    except OSError:
        pass
    return out


def zsxq_publish_history(limit: int = 20) -> list[dict]:
    """Return live log plus verified historical seeds, newest first.

    Deduplication is by topicId when available, otherwise by digest. The
    placeholder topic is retained as a record but never becomes a link.
    """
    import time as _time
    rows: list[dict] = []
    try:
        log_path = _cfg_val("ZSXQ_PUBLISH_LOG", ZSXQ_PUBLISH_LOG)
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    row = dict(row)
                    row.setdefault("source", "publish_log")
                    rows.append(row)
    except OSError:
        pass
    rows.extend(dict(seed) for seed in ZSXQ_HISTORICAL_SEEDS)
    unique: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("topicId") or row.get("digest") or "").strip()
        if not key:
            continue
        # Prefer the live log for a topic already captured there.
        if key not in unique or row.get("source") == "publish_log":
            unique[key] = row
    names = {t["groupId"]: t["groupName"] for t in zsxq_publish_targets(zsxq_publish_config())}
    out = []
    for row in unique.values():
        row["at"] = float(row.get("at") or 0)
        row["atText"] = (_time.strftime("%Y-%m-%d %H:%M", _time.localtime(row["at"]))
                         if row["at"] else "")
        row["groupName"] = row.get("groupName") or names.get(str(row.get("groupId") or ""), str(row.get("groupId") or ""))
        out.append(row)
    out.sort(key=lambda r: r.get("at") or 0, reverse=True)
    return out[:max(1, int(limit))]


def zsxq_publish(group_id: str, text: str, ai_mode: str) -> dict:
    """真正发布。调用方必须先走预览 + confirm。"""
    cli_bin = _cfg_val("ZSXQ_CLI", ZSXQ_CLI)
    if not cli_bin:
        return {"ok": False, "error": "本机没有 zsxq-cli"}
    allowed = str(zsxq_publish_config().get("groupId") or "")
    if not allowed or group_id != allowed:
        return {"ok": False, "error": "目标星球不在允许列表里"}
    if ai_mode not in ("aigc", "personal_perspective", "none"):
        return {"ok": False, "error": "ai_mode 取值不合法"}
    cmd = [cli_bin, "topic", "+create", "--group-id", group_id, "--text", text, "--json"]
    if ai_mode != "none":
        cmd += ["--ai-mode", ai_mode]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=_cli_env())
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "发布超时（60s）：可能已提交，禁止自动重试"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "").strip()[:200] or "zsxq-cli 执行失败"}
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        data = {}
    topic = data.get("topic") or {}
    topic_id = str(topic.get("topic_id") or data.get("topic_id") or "")
    ok = bool(data.get("success")) and bool(topic_id)
    return {"ok": ok, "topicId": topic_id or None,
            "error": "" if ok else "接口未返回 topic_id（可能已提交，请到星球核对后再重试）"}


_ZSXQ_HASHTAG_CACHE: dict[str, tuple[float, dict]] = {}


def zsxq_group_hashtags(group_id: str) -> dict:
    """星球已有标签 → hid 映射（10 分钟缓存）。

    正文里的蓝色标签是 `<e type="hashtag" hid="…" />`，hid 必须是真实 id；
    查不到就退 0（平台会按名称匹配），不要让整条发布因为查标签失败而中断。
    """
    import time as _time
    hit = _ZSXQ_HASHTAG_CACHE.get(group_id)
    now = _time.time()
    if hit and now - hit[0] < 600:
        return hit[1]
    mapping: dict[str, str] = {}
    cli_bin = _cfg_val("ZSXQ_CLI", ZSXQ_CLI)
    if cli_bin:
        try:
            params = json.dumps({"group_id": group_id})
            proc = subprocess.run([cli_bin, "api", "call", "get_group_hashtags", "--params", params],
                                  capture_output=True, text=True, timeout=30, env=_cli_env())
            if proc.returncode == 0:
                data = json.loads(proc.stdout or "{}")
                for row in data.get("hashtags") or []:
                    name = str(row.get("title") or "").strip("#").strip()
                    hid = str(row.get("hashtag_id") or "")
                    if name and hid:
                        mapping[name] = hid
        except Exception:
            pass
    _ZSXQ_HASHTAG_CACHE[group_id] = (now, mapping)
    return mapping


_ZSXQ_TAGOPT_CACHE: dict[str, tuple[float, list]] = {}


def sort_tag_options(rows: list[dict]) -> list[dict]:
    """标签按使用次数降序；次数相同按名字排，保证顺序稳定。"""
    clean = [r for r in rows if isinstance(r, dict) and str(r.get("name") or "").strip()]
    return sorted(clean, key=lambda r: (-int(r.get("count") or 0), str(r["name"])))


def zsxq_group_tag_options(group_id: str) -> list[dict]:
    """常用标签候选：名称、hid、使用次数。10 分钟缓存，避免每开一次面板就打接口。"""
    import time as _time
    hit = _ZSXQ_TAGOPT_CACHE.get(group_id)
    now = _time.time()
    if hit and now - hit[0] < 600:
        return hit[1]
    rows: list[dict] = []
    cli_bin = _cfg_val("ZSXQ_CLI", ZSXQ_CLI)
    if cli_bin:
        try:
            params = json.dumps({"group_id": group_id})
            proc = subprocess.run([cli_bin, "api", "call", "get_group_hashtags", "--params", params],
                                  capture_output=True, text=True, timeout=30, env=_cli_env())
            if proc.returncode == 0:
                data = json.loads(proc.stdout or "{}")
                for row in data.get("hashtags") or []:
                    name = str(row.get("title") or "").strip("#").strip()
                    if not name:
                        continue
                    rows.append({"name": name, "hid": str(row.get("hashtag_id") or ""),
                                 "count": int(row.get("topics_count") or 0)})
        except Exception:
            pass
    rows = sort_tag_options(rows)
    _ZSXQ_TAGOPT_CACHE[group_id] = (now, rows)
    return rows


def zsxq_tag_line(tags: list[str], group_id: str) -> str:
    """生成正文首行的标签串：真实 hid，查不到用 0。"""
    import urllib.parse as _up
    hashtags_fn = _cfg_val("zsxq_group_hashtags", zsxq_group_hashtags)
    mapping = hashtags_fn(group_id)
    parts = []
    for tag in tags or []:
        name = str(tag or "").strip().lstrip("#")
        if not name:
            continue
        hid = mapping.get(name, "0")
        parts.append('<e type="hashtag" hid="%s" title="%s" />' % (hid, _up.quote("#%s#" % name)))
    return " ".join(parts)


_ZSXQ_SYNC_LOCK = {"running": False}


def merge_zsxq_snapshot(social: dict, account: dict, posts: list[dict]) -> dict:
    """把新的星球快照并进 social.json，其它平台原样不动。

    只替换「platform 含 知识星球」的账号与帖子；这条路径不碰 X / 公众号 / 小红书，
    所以点一次按钮不会把整条 85 秒的同步链拖起来。
    """
    if not isinstance(social, dict):
        social = {}
    accounts = [a for a in (social.get("accounts") or []) if "知识星球" not in str(a.get("platform") or "")]
    if account:
        accounts.append(account)
    others = [p for p in (social.get("posts") or []) if "知识星球" not in str(p.get("platform") or "")]
    merged = dict(social)
    merged["accounts"] = accounts
    merged["posts"] = others + list(posts or [])
    merged["zsxqSyncedAt"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return merged


def zsxq_only_sync() -> dict:
    """只刷新知识星球：调 social-sync 里的 zsxq_cli_snapshot，再合并回 social.json。"""
    import importlib.util
    module_path = os.path.join(DIR, "scripts", "sync", "social-sync.py")
    spec = importlib.util.spec_from_file_location("social_sync_for_zsxq", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    account, posts = module.zsxq_cli_snapshot()
    if not account or not posts:
        return {"ok": False, "error": "本轮没有取到星球数据（CLI 未返回话题），保留旧快照"}
    module.annotate_sync_status(account, attempted_at=module.now_iso(), status="updated",
                                reason="手动从工作台刷新星球数据。",
                                source=str(account.get("sourceKind") or "知识星球 CLI"), updated=True)
    try:
        with open(SOCIAL, encoding="utf-8") as fh:
            social = json.load(fh)
    except Exception:
        social = {}
    merged = merge_zsxq_snapshot(social, account, posts)
    tmp = SOCIAL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, SOCIAL)
    return {"ok": True, "posts": len(posts), "groups": (account.get("metrics") or {}).get("groups"),
            "at": merged.get("zsxqSyncedAt")}


# ── 知识星球 AI 续写（本地模型，只生成草稿，绝不发布）────────────────────────
ZSXQ_OLLAMA_URL = os.environ.get("ZSXQ_OLLAMA_URL", "http://127.0.0.1:11434")
ZSXQ_DRAFT_MODEL = os.environ.get("ZSXQ_DRAFT_MODEL", "qwen3:8b")
ZSXQ_DRAFT_TIMEOUT = int(os.environ.get("ZSXQ_DRAFT_TIMEOUT", "120"))
ZSXQ_DRAFT_MAX = 600

ZSXQ_STYLE = """你代替凌一写一条知识星球动态。他的写法是这样的：
- 从一件具体的小事或一个具体触发点开头，不写「大家好」「今天想聊」这类开场
- 口语短句，一段一两句，像在跟朋友说话
- 一条只讲一个判断、一个踩坑或一个观察，不铺开讲大道理
- 少包装、少形容词，多个人体验；允许一点不确定（「我还没想清楚」「反正是这么回事」）
- 不要标题、不要小标题、不要 markdown、不要标签、不要 emoji 堆砌
- 不写「希望对你有帮助」「欢迎交流」这类结尾
- 100 到 250 字之间
- 不要编造具体事实：没提到的数字、工具名、人名、地点、版本号一律不写；
  需要细节时用模糊说法（「一个工具」「某个配置」），或干脆不写那一段
- 必须写上面给的这件事，不要换成别的事情或别的场景"""


def zsxq_draft_prompt(seed: str) -> str:
    """拼续写提示词。seed 是凌一给的触发点/想法，原样放进上下文。"""
    seed = (seed or "").strip()
    return (
        ZSXQ_STYLE
        + "\n\n这次要写的内容，起因是：\n"
        + seed
        + "\n\n直接给出正文。只输出正文本身，不要解释你在做什么。"
    )


def zsxq_clean_draft(raw: str, limit: int = ZSXQ_DRAFT_MAX) -> str:
    """把模型输出洗成可直接进正文框的纯文本。

    模型常见噪音：开头一句「好的，以下是正文」、markdown 加粗/标题、
    自己加的首行 #标签、外面套的引号、结尾的自评。全部去掉。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    text = text.replace("**", "").replace("__", "").strip().strip('"').strip("「").strip("」")
    lines = []
    for line in text.splitlines():
        t = line.strip()
        if not t:
            lines.append("")
            continue
        low = t.replace(" ", "")
        if re.fullmatch(r"#\S+(#\S+)*", low):        # 纯标签行
            continue
        if re.match(r"^(标签|题目|标题|正文)[:：]", t):
            continue
        if re.match(r"^(好的|下面是|以下是)(，|,|。)?", t) and len(t) <= 24:
            continue
        if t.startswith("#"):                          # markdown 标题
            t = t.lstrip("#").strip()
            if not t:
                continue
        lines.append(t)
    out = "\n".join(lines).strip()
    out = re.sub(r"\n{3,}", "\n\n", out)
    if len(out) > limit:
        cut = out[:limit]
        for mark in ("。", "！", "？", "\n"):
            idx = cut.rfind(mark)
            if idx >= limit * 0.6:
                cut = cut[:idx + 1]
                break
        out = cut.strip()
    return out


def zsxq_generate_draft(seed: str) -> tuple[bool, dict]:
    """调本地 Ollama 生成草稿。只读、只返回文本；写不写由人来点。"""
    if not (seed or "").strip():
        return False, {"error": "先写一句想法或触发点，再让 AI 续写"}
    body = json.dumps({
        "model": ZSXQ_DRAFT_MODEL,
        "prompt": zsxq_draft_prompt(seed),
        "stream": False,
        "think": False,
        "options": {"temperature": 0.7, "num_predict": 600},
    }).encode("utf-8")
    started = time.time()
    req = urllib.request.Request(
        ZSXQ_OLLAMA_URL.rstrip("/") + "/api/generate", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=ZSXQ_DRAFT_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return False, {"error": "本地模型没响应：%s" % str(exc)[:160],
                       "model": ZSXQ_DRAFT_MODEL}
    text = zsxq_clean_draft(payload.get("response") or "")
    if len(text) < 20:
        return False, {"error": "模型返回内容太短，重试一次或换个触发点",
                       "model": ZSXQ_DRAFT_MODEL}
    return True, {"text": text, "chars": len(text),
                  "model": ZSXQ_DRAFT_MODEL,
                  "ms": int((time.time() - started) * 1000)}


def zsxq_preview_warnings(final: str, tag_names: list[str], ai_mode: str) -> list[str]:
    """预览阶段的提醒。只是提醒，不阻止发布。"""
    warnings = []
    if len(final) < 20:
        warnings.append("正文太短（少于 20 字）")
    if len(final) > 5000:
        warnings.append("正文超过 5000 字，星球可能截断")
    names = [str(t).strip().lstrip("#") for t in tag_names]
    if ai_mode == "aigc" and "bot" not in names:
        warnings.append("AI 声明为 aigc，但标签里没有 bot")
    return warnings


def zsxq_annotation(tags: list[str]) -> str:
    """把标签拼成平台认识的 annotation。

    必须是 `<e type="hashtag" …/>` 富文本：直接往正文写 `#AI` 只是普通文字，
    不会变成可点击的蓝色标签（2026-11-22 真实踩到，还一度把标记写进了正文）。
    """
    import urllib.parse as _up
    parts = []
    seen = []
    for tag in tags or []:
        name = str(tag or "").strip().lstrip("#")
        if not name or name in seen:
            continue
        seen.append(name)
        parts.append('<e type="hashtag" hid="0" title="%s" />' % _up.quote("#%s#" % name))
    return "".join(parts)


def zsxq_set_annotation(topic_id: str, tags: list[str]) -> dict:
    cli_bin = _cfg_val("ZSXQ_CLI", ZSXQ_CLI)
    """PUT /v2/topics/{id} 的 annotation：全量替换该帖标签。"""
    ann = zsxq_annotation(tags)
    if not ann or not ZSXQ_CLI:
        return {"ok": False, "error": "没有标签或没有 CLI"}
    params = json.dumps({"method": "PUT", "path": "/v2/topics/%s" % topic_id,
                         "body": {"req_data": {"annotation": ann}}}, ensure_ascii=False)
    try:
        proc = subprocess.run([cli_bin, "api", "call", "call_zsxq_api", "--params", params],
                              capture_output=True, text=True, timeout=45, env=_cli_env())
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "设置标签超时"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "").strip()[:160]}
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        return {"ok": False, "error": "返回不是 JSON"}
    body = data.get("body") or {}
    topic = (body.get("resp_data") or {}).get("topic") or {}
    return {"ok": bool(topic.get("annotation")), "annotation": topic.get("annotation", "")[:200]}


def zsxq_log_publish(row: dict) -> None:
    import time as _time
    try:
        log_path = _cfg_val("ZSXQ_PUBLISH_LOG", ZSXQ_PUBLISH_LOG)
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({**row, "at": _time.time()}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def zsxq_topic_detail(topic_id: str) -> dict:
    """只读拉一条主题全文。失败给出真实原因，不静默返回空。"""
    import time as _time
    now = _time.time()
    hit = _ZSXQ_DETAIL_CACHE.get(topic_id)
    if hit and now - hit[0] < _ZSXQ_DETAIL_TTL:
        return hit[1]
    cli_bin = _cfg_val("ZSXQ_CLI", ZSXQ_CLI)
    if not cli_bin:
        return {"ok": False, "error": "本机没有 zsxq-cli"}
    try:
        proc = subprocess.run([cli_bin, "topic", "+detail", "--topic-id", str(topic_id), "--json"],
                              capture_output=True, text=True, timeout=45, env=_cli_env())
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "读取超时（45s）"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "").strip()[:200] or "zsxq-cli 执行失败"}
    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError:
        return {"ok": False, "error": "返回不是 JSON"}
    topic = data.get("topic") or (data.get("resp_data") or {}).get("topic") or {}
    text = topic.get("content") or (topic.get("talk") or {}).get("text") or ""
    clean = zsxq_clean_text(text)
    result = {"ok": True, "topicId": str(topic_id), "text": clean, "chars": len(clean),
              "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    _ZSXQ_DETAIL_CACHE[topic_id] = (now, result)
    return result


