#!/usr/bin/env python3
"""Build a small, read-only website/blog monitor snapshot.

Availability is checked independently from traffic analytics: HTTP 200 proves a
site answered, not that visits were collected.  The page polls this snapshot;
launchd refreshes it every five minutes.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET


def _gsc_section() -> dict | None:
    """Google Search Console 每日快照（服务账号，只读）。

    数据由 scripts/gsc.py 拉取并缓存（最多 20 小时刷新一次）；密钥配好之前
    返回 notConfigured 提示，页面据此显示接入指引。永不抛异常。"""
    try:
        from gsc import gsc_snapshot
        return gsc_snapshot()
    except Exception as exc:  # noqa: BLE001 - 监控主循环不能死
        return {"error": "GSC 模块加载失败：%s" % str(exc)[:160], "sites": []}


def _words_section() -> dict | None:
    """新词雷达每周新词快照。

    数据由 scripts/sync/words.py 拉取雷达站 words.json 并缓存（最多 6 小时刷新
    一次）；用 words-seen.json 记录已提醒的周，有新周才报一次。永不抛异常。"""
    try:
        from words import words_snapshot
        return words_snapshot()
    except Exception as exc:  # noqa: BLE001 - 监控主循环不能死
        return {"error": "新词模块加载失败：%s" % str(exc)[:160], "isNew": False}


def _ai_usage_section() -> dict | None:
    """AI 用量快照：本机 Codex 日志统计 + Jev 模型连通性探测。

    数据由 scripts/ai-usage.py 拉取并缓存（Jev 6h / Codex 扫描 12h 一次）。
    永不抛异常。"""
    try:
        from ai_usage import snapshot
        return snapshot()
    except Exception as exc:  # noqa: BLE001 - 监控主循环不能死
        return {"error": "AI 用量模块加载失败：%s" % str(exc)[:160]}


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web-presence.json"
BLOG_REPO = Path(os.environ.get("WB_BLOG_REPO", "~/blog").expanduser())
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X) WorkbenchMonitor/1.0"
SITES = [
    ("tools", "工具网站", "https://lingyi.tools/"),
    ("blog", "博客主站", "https://lingyi.me/"),
    ("blog-www", "博客 www", "https://www.lingyi.me/"),
]
RSS_URL = "https://lingyi.me/rss.xml"
ANALYTICS_DS = "991b158c-798e-47e2-ab9b-42ffb4d0da77"  # Notion「每日访问数据」
NTN = "/usr/local/bin/ntn"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def previous_snapshot() -> dict[str, object]:
    try:
        value = json.loads(OUT.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def curl_get(url: str, timeout: int = 20, body_limit: int | None = None,
             proxy: str | None = None) -> tuple[dict[str, object], bytes]:
    curl = shutil.which("curl") or "/usr/bin/curl"
    with tempfile.TemporaryDirectory(prefix="wb-web-monitor-") as temp:
        body_path = Path(temp) / "body"
        command = [
            curl, "-L", "-sS", "--compressed", "--connect-timeout", "8",
            # 本机网络对 Cloudflare 边缘的单次 connect/TLS 握手偶发超时
            #（2026-10-07 实测：同 URL 直连 5/6 成功 1-3.5s，超时那次的 conn=0），
            # 给站点探活加一次快速重试，避免把"本机网络抖动"记成"站点宕机"。
            "--retry", "1", "--retry-connrefused",
            "--max-time", str(timeout), "-A", USER_AGENT,
            "-o", str(body_path), "-w",
            "__WB_META__%{http_code}\t%{time_total}\t%{url_effective}\t%{content_type}",
        ]
        if body_limit:
            command.extend(["--range", f"0-{body_limit - 1}"])
        if proxy:
            command.extend(["-x", proxy])
        command.append(url)
        try:
            run = subprocess.run(command, capture_output=True, timeout=timeout + 3)
            stdout = run.stdout.decode("utf-8", errors="replace")
            stderr = run.stderr.decode("utf-8", errors="replace").strip()
            match = re.search(r"__WB_META__(\d{3})\t([\d.]+)\t([^\t]*)\t(.*)$", stdout)
            code = int(match.group(1)) if match else 0
            elapsed = round(float(match.group(2)) * 1000) if match else None
            final_url = match.group(3) if match else url
            content_type = match.group(4).strip() if match else ""
            body = body_path.read_bytes() if body_path.exists() else b""
            ok = 200 <= code < 400
            state = "ok" if ok and run.returncode == 0 and (elapsed or 0) < 5000 else "slow" if ok else "down"
            error = ""
            if run.returncode != 0:
                if code:
                    error = "响应超时或未完整读取"
                elif "curl: (60)" in stderr:
                    error = "TLS 证书校验失败（可能命中错误 DNS）"
                elif "curl: (28)" in stderr:
                    error = "连接超时"
                else:
                    curl_line = next((line for line in stderr.splitlines() if line.startswith("curl:")), "")
                    error = curl_line or "连接失败"
            return ({
                "url": url, "finalUrl": final_url, "status": code or None,
                "latencyMs": elapsed, "contentType": content_type,
                "state": state, "error": error,
            }, body)
        except (OSError, subprocess.TimeoutExpired) as error:
            return ({
                "url": url, "finalUrl": url, "status": None, "latencyMs": None,
                "contentType": "", "state": "down", "error": type(error).__name__,
            }, b"")


def parse_rss(body: bytes) -> tuple[dict[str, object] | None, int]:
    if not body:
        return None, 0
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None, 0
    items = root.findall("./channel/item")
    if not items:
        return None, 0
    item = items[0]
    published_raw = (item.findtext("pubDate") or "").strip()
    try:
        published = parsedate_to_datetime(published_raw).astimezone(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        published = published_raw or None
    return ({
        "title": (item.findtext("title") or "未命名文章").strip(),
        "url": (item.findtext("link") or "").strip(),
        "publishedAt": published,
    }, len(items))


def analytics_config() -> dict[str, object]:
    config = BLOG_REPO / "vercel.json"
    schedule = None
    try:
        value = json.loads(config.read_text(encoding="utf-8"))
        cron = next((x for x in value.get("crons", []) if x.get("path") == "/api/cron/analytics"), None)
        schedule = cron.get("schedule") if cron else None
    except (OSError, ValueError, AttributeError):
        pass
    return {
        "provider": "Umami",
        "state": "verified-pipeline" if schedule else "missing",
        "schedule": schedule,
        "cadenceLabel": "每日 09:00 汇总" if schedule == "0 1 * * *" else "未识别",
        "valuesAvailable": False,
        "lastVerifiedAt": "2026-10-06T11:55:08+08:00",
        "verifiedEvidence": "生产 Cron 手动运行 HTTP 200，日志确认真实调用 Umami 统计接口并查询 Notion 数据源。",
        "note": "同步链路已在生产验证（2026-10-06 手动运行成功），当日数值已入库但工作台尚未直接回读 Notion，故不展示具体数字。",
    }


def notion_daily_visitors(days: int = 7) -> dict[str, object] | None:
    """Read the latest rows from the Notion「每日访问数据」source (read-only).

    Values arrive from the verified daily Umami→Notion cron; this only reads
    what production already wrote, so it stays consistent with that pipeline.
    """
    env = dict(os.environ, HTTPS_PROXY="http://127.0.0.1:7890", HTTP_PROXY="http://127.0.0.1:7890")
    try:
        proc = subprocess.run(
            [NTN, "api", f"/v1/data_sources/{ANALYTICS_DS}/query", "-X", "POST", "-d", json.dumps({
                "page_size": days,
                "sorts": [{"property": "日期", "direction": "descending"}],
            })],
            capture_output=True, text=True, timeout=30, env=env,
        )
        payload = json.loads((proc.stdout or "").strip() or "{}")
        rows = [r for r in payload.get("results", []) if isinstance(r, dict)]
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    daily = []
    for row in rows:
        props = row.get("properties", {})

        def num(key: str) -> int | None:
            value = props.get(key, {}).get("number")
            return int(value) if isinstance(value, (int, float)) else None

        date = (props.get("日期", {}).get("date") or {}).get("start")
        if not date:
            continue
        daily.append({
            "date": date,
            "pageviews": num("浏览量"),
            "visitors": num("访客"),
            "visits": num("会话"),
            "topPage": (props.get("热门页面", {}).get("rich_text") or [{}])[0].get("plain_text"),
        })
    if not daily:
        return None
    last7 = daily[:7]
    total = sum(int(r["pageviews"] or 0) for r in last7)
    return {
        "latest": daily[0],
        "last7Pageviews": total,
        "days": last7,
        "asOf": daily[0]["date"],
        "source": "Notion 每日访问数据（生产 Umami Cron 每日 09:00 写入）",
    }

def build() -> dict[str, object]:
    old = previous_snapshot()
    old_sites = {str(site.get("id")): site for site in old.get("sites", []) if isinstance(site, dict)}
    sites: list[dict[str, object]] = []
    for ident, name, url in SITES:
        check, _ = curl_get(url, timeout=20, body_limit=65536)
        prior = old_sites.get(ident, {})
        transport_failure = check["state"] == "down" and not check.get("status")
        failures = int(prior.get("consecutiveFailures") or 0) + 1 if transport_failure else 0
        # A single TLS/connect timeout is common on this network and is not
        # enough evidence to call the site down.  Definite HTTP errors (such as
        # 525) still surface immediately.
        if transport_failure and failures < 2:
            check["state"] = "checking"
        # 2026-10-25 实测：本机直连出口对 Cloudflare 整段超时（ipify/cloudflare 全 000），
        # 同 URL 走 Surge 1.4s 返回 200 —— 探活失败经常是出口问题而非站点问题。
        # 直连判 down 时用 Surge 出口复检一次，两边都不通才判 down。
        if check["state"] == "down" and not check.get("status"):
            proxy_check, _ = curl_get(url, timeout=15, body_limit=65536, proxy="http://127.0.0.1:7890")
            if proxy_check["state"] in {"ok", "slow"}:
                check = {**proxy_check, "id": ident, "name": name, "checkedAt": now_iso(),
                         "consecutiveFailures": 0, "viaProxy": True}
                failures = 0
        check["consecutiveFailures"] = failures
        check.update({"id": ident, "name": name, "checkedAt": now_iso()})
        sites.append(check)

    rss_check, rss_body = curl_get(RSS_URL, timeout=25)
    latest, item_count = parse_rss(rss_body)
    previous_blog = old.get("blog") if isinstance(old.get("blog"), dict) else {}
    previous_latest = previous_blog.get("latest") if isinstance(previous_blog.get("latest"), dict) else None
    used_previous = latest is None and previous_latest is not None
    if used_previous:
        latest = previous_latest
    previous_url = str((previous_latest or {}).get("url") or "")
    latest_url = str((latest or {}).get("url") or "")
    blog = {
        "feedUrl": RSS_URL,
        "state": "ok" if rss_check["state"] == "ok" and latest else "slow" if latest else "down",
        "checkedAt": now_iso(),
        "latest": latest,
        "itemCount": item_count or int(previous_blog.get("itemCount") or 0),
        "newSinceLastCheck": bool(previous_url and latest_url and previous_url != latest_url),
        "usingPrevious": used_previous,
        "error": rss_check.get("error") or ("RSS 内容无法解析" if not latest else ""),
    }

    alerts: list[dict[str, str]] = []
    for site in sites:
        # 「偏慢」不立即告警：本机到 Cloudflare 的延迟抖动很常见（实测 1s~8s 波动），
        # 连续两轮仍偏慢才值得提醒，避免监控面板常驻误报。
        was_slow = str((old_sites.get(str(site.get("id"))) or {}).get("state")) == "slow"
        if site["state"] == "slow" and was_slow:
            alerts.append({"tone": "warn", "title": f"{site['name']}：持续偏慢",
                           "detail": f"连续两轮检测超过 5s（本轮 {site.get('latencyMs')}ms）"})
        elif site["state"] not in {"ok", "checking", "slow"}:
            status = f"HTTP {site['status']}" if site.get("status") else "无响应"
            alerts.append({"tone": "warn", "title": f"{site['name']}：需要检查", "detail": f"{status} · {site.get('error') or '响应偏慢'}"})
    if blog["newSinceLastCheck"] and latest:
        alerts.insert(0, {"tone": "ok", "title": "博客检测到新文章", "detail": str(latest.get("title") or "")})
    if blog["state"] != "ok":
        alerts.append({"tone": "warn", "title": "博客更新检测不稳定", "detail": str(blog.get("error") or "RSS 暂时不可用")})

    search = _gsc_section()
    if isinstance(search, dict):
        for gsite in search.get("sites", []):
            if not isinstance(gsite, dict):
                continue
            dclicks = gsite.get("deltaClicks")
            if isinstance(dclicks, (int, float)) and dclicks <= -0.3:
                alerts.append({
                    "tone": "warn",
                    "title": "搜索流量下跌：%s" % gsite.get("name"),
                    "detail": "近 28 天点击 %s，较上期 %s" % (
                        gsite.get("clicks"), format(dclicks, ".0%")),
                })

    words = _words_section()
    if isinstance(words, dict):
        latest = words.get("latestWeek") or {}
        if words.get("isNew") and latest.get("words"):
            names = "、".join(w.get("term", "") for w in latest["words"][:6])
            if len(latest["words"]) > 6:
                names += "等"
            alerts.insert(0, {
                "tone": "ok",
                "title": "新词雷达：本周捕获 %d 个新词" % len(latest["words"]),
                "detail": names,
            })
            try:
                from words import mark_seen
                mark_seen(str(latest.get("id") or ""))
            except Exception:
                pass

    return {
        "version": 1,
        "generatedAt": now_iso(),
        "pollEverySeconds": 300,
        "sites": sites,
        "blog": blog,
        "analytics": analytics_config(),
        "alerts": alerts,
        "visitors": notion_daily_visitors(),
        "search": search,
        "words": words,
        "aiUsage": _ai_usage_section(),
        "dataPolicy": "站点可用性每 5 分钟检测；博客更新来自 RSS；搜索流量每日更新（GSC）；新词雷达每周更新；HTTP 正常不等于流量指标正常。",
    }


def main() -> int:
    value = build()
    temp = OUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, OUT)
    print(f"wrote {OUT.name}: {len(value['sites'])} sites, {len(value['alerts'])} alerts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
