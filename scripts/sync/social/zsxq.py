"""
[INPUT]: 依赖 zsxq-cli、zsxq-token Cookie 与历史导出文件，经 social.common 读取公共工具
[OUTPUT]: 导出 zsxq_live_snapshot、zsxq_cli_*、zsxq_snapshot 及文本/标签清洗纯函数
[POS]: scripts/sync/social/ 知识星球平台抓手（CLI → Web API → 历史导出三级降级）
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""
from __future__ import annotations

import csv
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from social.common import (
    ZSXQ_CSV, ZSXQ_GROUPS, ZSXQ_TOKEN, ZSXQ_XLSX,
    freshness_fields, parse_date, read_simple_xlsx, safe_http_url, scfg,
)

ZSXQ_GROUP_NAME = ZSXQ_GROUPS[0] if ZSXQ_GROUPS else ""

def zsxq_live_snapshot() -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    """Read the star group through the official web API with a browser cookie.

    The token is the zsxq_access_token cookie copied from wx.zsxq.com; it is
    read-only traffic (group/topic listing) and never posts anything.  A
    missing or expired token degrades to None so the caller can keep serving
    the historical export instead of inventing numbers.
    """
    token = ZSXQ_TOKEN.read_text(encoding="utf-8").strip() if ZSXQ_TOKEN.exists() else ""
    if not token:
        return None, []

    def fetch(url: str) -> dict[str, object]:
        # 知识星球对无浏览器特征的请求偶发 1059 拦截，带 UA 并重试一次可消除绝大多数偶发失败。
        headers = {"Cookie": f"zsxq_access_token={token}",
                   "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"}
        request = urllib.request.Request(url, headers=headers)
        proxy = os.environ.get("ZSXQ_PROXY", "http://127.0.0.1:7890")
        handler = urllib.request.ProxyHandler({} if proxy == "direct" else {"http": proxy, "https": proxy})
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.build_opener(handler).open(request, timeout=20) as response:
                    value = json.loads(response.read().decode("utf-8", errors="replace"))
                    return value if isinstance(value, dict) else {}
            except (OSError, ValueError) as error:
                last_error = error
                time.sleep(1 + attempt * 2)
        raise last_error if last_error else OSError("zsxq fetch failed")

    try:
        groups = fetch("https://api.zsxq.com/v2/groups")
    except (OSError, ValueError):
        return None, []
    if groups.get("succeeded") is not True:
        return None, []
    # token 是账号级凭据，会列出加入的所有星球；只聚合自己创建/管理的星球，绝不误抓别人的。
    group_list = [g for g in (groups.get("resp_data") or {}).get("groups") or [] if isinstance(g, dict)]
    mine = [g for g in group_list if str(g.get("name") or "").strip() in ZSXQ_GROUPS]
    if not mine:
        return {"platform": "知识星球", "account": ZSXQ_GROUP_NAME, "profileUrl": "https://wx.zsxq.com/",
                "dashboardUrl": "https://wx.zsxq.com/", "asOf": None,
                "sourceKind": "知识星球 Web API（登录态只读）", "freshness": "partial", "metrics": {},
                "note": "登录态有效，但星球列表里一个都没匹配上（" + "、".join(ZSXQ_GROUPS) + "）；请确认星球名。"}, []
    posts: list[dict[str, object]] = []
    per_group: dict[str, int] = {}
    failed_groups: list[str] = []
    for group in mine:
        group_name = str(group.get("name") or "").strip()
        topics: dict[str, object] = {}
        if per_group:  # 星球之间留间隔，降低触发 1059 风控的概率
            time.sleep(2)
        for attempt in range(3):
            try:
                topics = fetch(f"https://api.zsxq.com/v2/groups/{group.get('group_id')}/topics?scope=all&count=30")
                break
            except (OSError, ValueError):
                time.sleep(3 + attempt * 4)
        per_group[group_name] = 0
        if topics.get("succeeded") is not True:
            failed_groups.append(group_name)
        for topic in (topics.get("resp_data") or {}).get("topics") or []:
            if not isinstance(topic, dict):
                continue
            talk = topic.get("talk") or {}
            article = talk.get("article") or {}
            title = str(article.get("title") or talk.get("title") or topic.get("title") or "").strip()
            if not title:
                text = str((talk.get("text") or ""))[:60].strip()
                if not text:
                    continue
                title = text
            published = parse_date(topic.get("create_time"))
            likes = topic.get("likes_count")
            comments = topic.get("comments_count")
            readers = topic.get("reading_count")
            ident = hashlib.sha1(str(topic.get("topic_id") or title).encode("utf-8")).hexdigest()[:12]
            posts.append({
                "id": "zsxq-live-" + ident,
                "platform": "知识星球",
                "publishedAt": published.isoformat(timespec="seconds") if published else None,
                "title": title,
                "url": f"https://wx.zsxq.com/topic/{topic.get('topic_id')}" if topic.get("topic_id") else None,
                "metrics": {
                    "reads": readers if isinstance(readers, (int, float)) else None,
                    "likes": likes if isinstance(likes, (int, float)) else None,
                    "comments": comments if isinstance(comments, (int, float)) else None,
                },
            })
            per_group[group_name] += 1
    posts.sort(key=lambda x: x.get("publishedAt") or "", reverse=True)
    newest = max((p["publishedAt"] for p in posts if p.get("publishedAt")), default=None)
    account: dict[str, object] = {
        "platform": "知识星球", "account": "、".join(ZSXQ_GROUPS),
        "profileUrl": "https://wx.zsxq.com/", "dashboardUrl": "https://wx.zsxq.com/",
        "asOf": str(newest)[:10] if newest else None,
        "sourceKind": "知识星球 Web API（登录态只读）",
        **freshness_fields(str(newest)[:10] if newest else None),
        "collectionState": "partial" if failed_groups else ("available" if posts else "partial"),
        "metrics": {"posts": len(posts), "groups": len(mine), "perGroup": per_group},
        "note": ("已聚合 %d 个星球的最近话题（%s）；只读拉取，不发帖不修改。" % (
            len(mine), " · ".join(f"{name} {count} 条" for name, count in per_group.items()))) +
                (f" 本轮 {len(failed_groups)} 个星球被限流未拉到（{'、'.join(failed_groups)}），对应计数为 0 不是没更新。" if failed_groups else ""),
    }
    if not posts:
        account["collectionState"] = "partial"
        account["note"] = "登录态有效但没有拉到话题明细，统计字段保持为空。"
    return account, posts

def _find_cli(name: str) -> str:
    """launchd 的 PATH 里没有 homebrew，shutil.which 会返回空 → 星球通道静默降级。"""
    found = shutil.which(name)
    if found:
        return found
    for directory in ("/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")):
        candidate = os.path.join(directory, name)
        if os.access(candidate, os.X_OK):
            return candidate
    return ""


def _cli_env() -> dict:
    """zsxq-cli 是 `#!/usr/bin/env node`，PATH 里还得有 node。"""
    env = dict(os.environ)
    extra = []
    node = shutil.which("node")
    if node:
        extra.append(os.path.dirname(node))
    extra += sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin")), reverse=True)[:1]
    extra += ["/opt/homebrew/bin", "/usr/local/bin"]
    extra = [d for d in extra if d]
    if extra:
        env["PATH"] = ":".join(extra + [env.get("PATH", "")])
    return env


ZSXQ_CLI = _find_cli("zsxq-cli")


def _zsxq_cli_json(args: list[str], timeout: int = 90) -> dict[str, object]:
    """执行 zsxq-cli 并解析 JSON。失败要抛出真实原因，不能静默当空。"""
    cli_bin = scfg("ZSXQ_CLI", ZSXQ_CLI)
    if not cli_bin:
        raise RuntimeError("zsxq-cli 未安装")
    proc = subprocess.run([cli_bin, *args], capture_output=True, text=True, timeout=timeout,
                          env=_cli_env())
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError((detail[-1] if detail else "")[:200] or "zsxq-cli 执行失败")
    payload = json.loads(proc.stdout or "{}")
    if not isinstance(payload, dict):
        raise RuntimeError("zsxq-cli 返回的不是对象")
    return payload


ZSXQ_DASHBOARD_FIELDS = ",".join([
    "daily.member_active_count",
    "daily.member_increase_count",
    "statistics.yesterday.member_active_count",
    "statistics.yesterday.member_increase_count",
    "statistics.yesterday.income_count",
    "statistics.within_30days_average.member_active_count",
    "statistics.within_30days_average.member_increase_count",
])


def zsxq_cli_dashboard(group_id: str) -> dict[str, object]:
    """读官方运营大盘。

    只有**付费星球**有这东西：免费星球会返回 17208「星球不存在或不是付费星球」。
    这个错误不能当成"拉取失败"糊过去，要原样标成 unsupported，否则会让人以为
    是自己的星球数据坏了。
    """
    params = {
        "method": "GET",
        "path": f"/v2/dashboard/groups/{group_id}/overview",
        "fields": ZSXQ_DASHBOARD_FIELDS,
    }
    try:
        payload = scfg("_zsxq_cli_json", _zsxq_cli_json)(["api", "call", "call_zsxq_api", "--params",
                                  json.dumps(params, ensure_ascii=False)])
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "reason": str(exc)[:160]}
    body = payload.get("body") or {}
    if body.get("succeeded") is not True:
        code = body.get("code")
        reason = str(body.get("error") or "接口未返回数据")[:120]
        return {"status": "unsupported" if code == 17208 else "error",
                "code": code, "reason": reason}
    data = body.get("resp_data") or {}
    daily = data.get("daily") or {}
    stats = data.get("statistics") or {}
    yday = stats.get("yesterday") or {}
    avg = stats.get("within_30days_average") or {}
    return {
        "status": "ok",
        "yesterdayActive": yday.get("member_active_count"),
        "yesterdayIncrease": yday.get("member_increase_count"),
        "yesterdayIncome": yday.get("income_count"),
        "avg30Active": avg.get("member_active_count"),
        "avg30Increase": avg.get("member_increase_count"),
        "todayActive": daily.get("member_active_count"),
        "todayIncrease": daily.get("member_increase_count"),
    }


def _zsxq_plain(text: str) -> str:
    """把星球富文本清成可读标题：剥 <e …/> 标签、URL 解码、去掉话题标签。

    列表接口给的 title 里会带 `<e type="hashtag" hid="…" title="%23…%23" />`
    这种标记，直接展示就是一堆尖括号和百分号（2026-11-22 真实踩到）。
    """
    t = str(text or "")
    t = re.sub(r"<e[^>]*/>", "", t)
    t = re.sub(r"<e[^>]*>.*?</e>", "", t, flags=re.S)
    t = urllib.parse.unquote(t)
    t = re.sub(r"#\S+", "", t)
    t = " ".join(t.split()).strip(" ，,、")
    # 列表接口给的预览常以 "..." / "…" 结尾，那是接口截断，不是作者写的
    return re.sub(r"(?:\.{2,}|…)+$", "", t).strip()


def _zsxq_hashtag_names(raw: str) -> list[str]:
    """从 <e type="hashtag" title="%23AI%23" /> 里取出标签名。

    只认 `<e type="hashtag">`：星球把正文里的链接也塞进 `<e title="https://…">`，
    不过滤就会把 URL 当标签显示（2026-11-23 实测踩到）。
    """
    names = []
    for tag in re.findall(r"<e\b[^>]*/>", str(raw or "")):
        if "type=\"hashtag\"" not in tag and "type='hashtag'" not in tag:
            continue
        m = re.search(r'title="([^"]+)"', tag)
        if not m:
            continue
        name = urllib.parse.unquote(m.group(1)).strip("#").strip()
        if name and not re.search(r"://|\s", name):
            names.append(name)
    return names


def _zsxq_excerpt(text: str, limit: int = 200) -> str:
    """正文摘要：优先停在句号上，别把话切一半。

    列表接口只给约 180 字，还会截在句中（真实踩到 "...不是你的助手。\n\nA"）。
    这里在 limit 内找最后一个句子边界；找不到才硬截并补省略号。
    """
    body = _zsxq_plain(text)
    if len(body) <= limit:
        return body
    window = body[:limit]
    cut = max(window.rfind(ch) for ch in "。！？!?")
    if cut >= int(limit * 0.5):
        return window[:cut + 1]
    return window.rstrip(" ，,、") + "…"


def _zsxq_topic_to_post(topic: dict, group: str = "", owner: str = "") -> dict | None:
    talk = topic.get("talk") or {}
    article = talk.get("article") or {}
    text = str(talk.get("text") or topic.get("content") or "").strip()
    # 先算发布时间：标题兜底分支要用它，放在后面会 UnboundLocalError
    published = parse_date(topic.get("create_time"))
    raw_title = article.get("title") or talk.get("title") or topic.get("title") or ""
    body = _zsxq_plain(text)

    def _first_sentence(src: str, limit: int = 40) -> str:
        parts = [p.strip(" ，,、") for p in re.split(r"[。！？\n]", src)]
        for part in parts:
            if len(part) >= 6:
                return part[:limit]
        # 短帖（"碎屑一条"这种）也该有标题，别整条丢掉
        return " ".join(p for p in parts if p)[:limit]

    # 接口给的 title 本身常被截在句中（"最近把知识星球接上了官方的 A"），
    # 所以正文首句优先；正文为空才退回接口标题。
    # 标签取 annotation（平台标签）优先，正文里的 # 只是普通文字，不当标签。
    tags = _zsxq_hashtag_names(topic.get("annotation")) \
        or _zsxq_hashtag_names(raw_title) or _zsxq_hashtag_names(text)
    title = _first_sentence(body) or _zsxq_plain(raw_title)
    if title and not re.search(r"[\w\u4e00-\u9fff]", title):
        title = ""
    if not title:
        if not tags:
            return None
        title = " / ".join(tags[:3])
    if not title:
        return None
    counts = topic.get("counts") or {}
    ident = hashlib.sha1(str(topic.get("topic_id") or title).encode("utf-8")).hexdigest()[:12]
    return {
        "id": "zsxq-cli-" + ident,
        "platform": "知识星球",
        # 星球归属：时间线要能按星球筛，否则 5 个星球的帖子全糊在一起
        "group": str(group or ""),
        # owner 是星球主人（凌一）时标 mine：前端只对"自己的星球"用短文卡片 + 标签展示。
        "mine": str(owner or "").strip() == "凌一",
        "tags": tags,
        "publishedAt": published.isoformat(timespec="seconds") if published else None,
        "title": title,
        "excerpt": _zsxq_excerpt(text),
        "url": f"https://wx.zsxq.com/topic/{topic.get('topic_id')}" if topic.get("topic_id") else None,
        "metrics": {
            "reads": counts.get("readers"),
            "likes": counts.get("likes"),
            "comments": counts.get("comments"),
        },
    }


def zsxq_cli_map(groups_payload: dict, topics_by_group: dict[str, list[dict]],
                 group_names: list[str] | None = None,
                 dashboards: dict[str, dict] | None = None) -> tuple[dict[str, object] | None, list[dict]]:
    """把 zsxq-cli 的 JSON 映射成自媒体页要的结构（纯函数，便于离线测）。"""
    names = list(group_names or ZSXQ_GROUPS)
    all_groups = [g for g in (groups_payload.get("groups") or []) if isinstance(g, dict)]
    groups = [g for g in all_groups if str(g.get("name") or "").strip() in names]
    if not groups:
        return None, []
    # 星球 owner（主人）用于前端区分"我的星球"和"我加入的别人的星球"。
    owner_of = {str(g.get("name") or "").strip(): str((g.get("owner") or {}).get("name") or "")
                for g in all_groups}
    posts: list[dict] = []
    per_group: dict[str, int] = {}
    for group in groups:
        name = str(group.get("name") or "").strip()
        per_group[name] = 0
        for page in topics_by_group.get(str(group.get("group_id"))) or []:
            for topic in page.get("topics_brief") or []:
                post = _zsxq_topic_to_post(topic if isinstance(topic, dict) else {}, name,
                                           owner_of.get(name, ""))
                if post:
                    posts.append(post)
                    per_group[name] += 1
    posts.sort(key=lambda x: x.get("publishedAt") or "", reverse=True)
    newest = max((p["publishedAt"] for p in posts if p.get("publishedAt")), default=None)
    dashboards = dashboards or {}
    members = {}
    for group in groups:
        count = ((group.get("statistics") or {}).get("members") or {}).get("count")
        if isinstance(count, (int, float)):
            members[str(group.get("name") or "").strip()] = int(count)
    gid_to_name = {str(g.get("group_id")): str(g.get("name") or "").strip() for g in groups}
    dash_ok = [gid_to_name.get(gid, gid) for gid, d in (dashboards or {}).items()
               if (d or {}).get("status") == "ok"]
    dash_off = [gid_to_name.get(gid, gid) for gid, d in (dashboards or {}).items()
                if (d or {}).get("status") == "unsupported"]
    dash_note = ""
    if dashboards:
        dash_note = " 运营大盘：%d 个星球可用%s。" % (
            len(dash_ok),
            ("；%s 为免费星球，官方不提供大盘" % "、".join(dash_off)) if dash_off else "")
    account = {
        "platform": "知识星球",
        "account": "、".join(str(g.get("name") or "").strip() for g in groups),
        "profileUrl": "https://wx.zsxq.com/",
        "dashboardUrl": "https://wx.zsxq.com/",
        "asOf": str(newest)[:10] if newest else None,
        "sourceKind": "知识星球 CLI（zsxq-cli 登录态只读）",
        **freshness_fields(str(newest)[:10] if newest else None),
        "collectionState": "available" if posts else "partial",
        "metrics": {"posts": len(posts), "groups": len(groups),
                    "perGroup": per_group, "members": members,
                    # 星球名 → group_id：页面跳转、多星球发布都要它
                    "groupIds": {str(g.get("name") or "").strip(): str(g.get("group_id") or "")
                                 for g in groups if g.get("group_id")},
                    "dashboard": {str(g.get("name") or "").strip(): dashboards[str(g.get("group_id"))]
                                  for g in groups if str(g.get("group_id")) in dashboards}},
        "note": ("通过 zsxq-cli 只读拉取 %d 个星球共 %d 条话题；不发帖、不评论、不改设置。" % (
            len(groups), len(posts))) + dash_note if posts else "zsxq-cli 可用，但没有返回话题明细。",
    }
    return account, posts


def zsxq_cli_snapshot(pages_per_group: int = 1, all_groups: bool = True) -> tuple[dict[str, object] | None, list[dict]]:
    """用 zsxq-cli 拉知识星球数据。失败返回 (None, [])，交给上层回退历史导出。

    all_groups=True 时拉账号可读的全部星球（时间线要能看到所有星球内容）；
    关掉则退回 ZSXQ_GROUPS 白名单。运营大盘只对"我的星球"拉，别人的星球
    没有我的运营数据，拉了也是无用请求。
    """
    try:
        groups_payload = scfg("_zsxq_cli_json", _zsxq_cli_json)(["group", "+list", "--limit", "200", "--json"])
    except Exception:
        return None, []
    group_list = [g for g in (groups_payload.get("groups") or []) if isinstance(g, dict)]
    if all_groups:
        names = [str(g.get("name") or "").strip() for g in group_list]
    else:
        names = list(ZSXQ_GROUPS)
    groups = [g for g in group_list if str(g.get("name") or "").strip() in names]
    dashboards: dict[str, dict] = {}
    for group in groups:
        gid = str(group.get("group_id") or "")
        if not gid:
            continue
        owner = str((group.get("owner") or {}).get("name") or "")
        if owner != "凌一":
            dashboards[gid] = {"status": "unsupported", "reason": "别人的星球，无我的运营大盘"}
            continue
        if str(group.get("type") or "") != "pay":
            # 免费星球官方就没有大盘接口，先标清楚，不用去打一次 17208。
            dashboards[gid] = {"status": "unsupported",
                               "reason": "免费星球没有官方运营大盘（接口返回 17208）"}
            continue
        dashboards[gid] = zsxq_cli_dashboard(gid)

    topics_by_group: dict[str, list[dict]] = {}
    for group in groups:
        gid = str(group.get("group_id") or "")
        if not gid:
            continue
        pages: list[dict] = []
        end_time = None
        for _ in range(max(1, pages_per_group)):
            args = ["group", "+topics", "--group-id", gid, "--limit", "30", "--json"]
            if end_time:
                args += ["--end-time", end_time]
            try:
                page = scfg("_zsxq_cli_json", _zsxq_cli_json)(args)
            except Exception:
                break
            pages.append(page)
            if not page.get("has_more"):
                break
            end_time = page.get("next_end_time")
            if not end_time:
                break
        topics_by_group[gid] = pages
    return zsxq_cli_map(groups_payload, topics_by_group, names, dashboards)



def zsxq_snapshot() -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    rows = read_simple_xlsx(ZSXQ_XLSX)
    posts: list[dict[str, object]] = []
    for row in rows:
        published = parse_date(row.get("发布时间"))
        link = safe_http_url(row.get("主题链接"))
        title = str(row.get("主题") or "").strip()
        if not title:
            continue
        ident = hashlib.sha1((link or (str(published) + title)).encode("utf-8")).hexdigest()[:12]
        posts.append({
            "id": "zsxq-" + ident,
            "platform": "知识星球",
            "publishedAt": published.isoformat(timespec="seconds") if published else None,
            "title": title,
            "url": link,
            "metrics": {
                "reads": int(row.get("阅读数") or 0),
                "likes": int(row.get("点赞数") or 0),
                "comments": int(row.get("评论数") or 0),
            },
        })
    posts.sort(key=lambda x: x.get("publishedAt") or "", reverse=True)
    dated = [parse_date(p.get("publishedAt")) for p in posts]
    dated = [d for d in dated if d]
    newest = max(dated) if dated else None
    oldest = min(dated) if dated else None
    mature_cutoff = newest - dt.timedelta(days=4) if newest else None
    mature = [p for p in posts if parse_date(p.get("publishedAt")) and parse_date(p.get("publishedAt")) <= mature_cutoff]
    reads = sorted(int(p["metrics"]["reads"]) for p in mature)
    median = reads[len(reads) // 2] if reads else None
    best = sorted(mature, key=lambda p: (p["metrics"]["reads"], p["metrics"]["likes"], p["metrics"]["comments"]), reverse=True)[:3]
    low = sorted(mature, key=lambda p: (p["metrics"]["reads"], -(p["metrics"]["likes"] + p["metrics"]["comments"])))[:2]
    recent = [p for p in posts if mature_cutoff and parse_date(p.get("publishedAt")) > mature_cutoff][:2]
    analysis: list[dict[str, object]] = []
    for p in best:
        analysis.append({"postId": p["id"], "label": "表现较好", "tone": "ok",
                         "reason": f"阅读 {p['metrics']['reads']}，高于成熟样本中位数 {median or '—'}；互动 {p['metrics']['likes'] + p['metrics']['comments']}。"})
    for p in low:
        analysis.append({"postId": p["id"], "label": "需要复盘", "tone": "warn",
                         "reason": f"成熟样本中阅读 {p['metrics']['reads']}，低于中位数 {median or '—'}，且互动偏低；当前导出没有曝光、完读和来源，不能直接归因内容质量。"})
    for p in recent:
        analysis.append({"postId": p["id"], "label": "样本未成熟", "tone": "acc",
                         "reason": "发布距快照不足 4 天，低阅读可能只是累计时间短，先不判好坏。"})

    # The detailed export above has reads and direct links, but only covers 41
    # posts.  A second local export contains the full 2022-2025 posting history.
    # Use it for cadence and total-post context without pretending that its
    # 2025-12-31 endpoint is current in 2026.
    history: list[dict[str, str]] = []
    if ZSXQ_CSV.exists():
        try:
            with ZSXQ_CSV.open(encoding="utf-8-sig", newline="") as fh:
                history = list(csv.DictReader(fh))
        except (OSError, csv.Error):
            history = []
    history_dates = [parse_date(row.get("发布时间")) for row in history]
    history_dates = [value for value in history_dates if value]
    history_newest = max(history_dates) if history_dates else newest
    history_oldest = min(history_dates) if history_dates else oldest
    recent_window = history_newest - dt.timedelta(days=29) if history_newest else None
    recent_count = sum(1 for value in history_dates if recent_window and value >= recent_window)

    def history_int(row: dict[str, str], key: str) -> int:
        try:
            return int(float(row.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    summary = {
        "platform": "知识星球",
        "account": "凌一 · 成长·写作·赚钱",
        "profileUrl": "https://wx.zsxq.com/",
        "dashboardUrl": "https://wx.zsxq.com/",
        "asOf": history_newest.date().isoformat() if history_newest else None,
        "sourceKind": "历史全量 + 近 41 条明细导出",
        **freshness_fields(history_newest.date().isoformat() if history_newest else None),
        "exportAgeDays": (dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
                          - history_newest.date()).days if history_newest else None,
        "metrics": {
            "posts": len(history) or len(posts),
            "detailedPosts": len(posts),
            "readsSample": sum(int(p["metrics"]["reads"]) for p in posts),
            "likes": sum(history_int(row, "赞") for row in history) if history else sum(int(p["metrics"]["likes"]) for p in posts),
            "comments": sum(history_int(row, "评论") for row in history) if history else sum(int(p["metrics"]["comments"]) for p in posts),
            "postsInLast30ExportDays": recent_count if history else None,
            "medianMatureReads": median,
        },
        "range": {
            "start": history_oldest.date().isoformat() if history_oldest else None,
            "end": history_newest.date().isoformat() if history_newest else None,
        },
        "note": "1454 条历史导出用于看长期产量与互动；阅读分析只基于另一个 41 条明细样本。数据截至 2025-12-31，不冒充今天。",
    }
    return summary, posts, analysis


