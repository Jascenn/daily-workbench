#!/usr/bin/env python3
"""选题雷达：把热点源抓成只读快照，再由 Jev 判断"和我有什么关系"。

为什么要这一层：热点站点是给人看的，不是给工作台用的。直接把标题列表贴进页面，
等于又造一个 RSS 阅读器。这里只做三件事：

1. **抓**：按来源抓标题+链接，抓不到就如实标 blocked/error，绝不编造条目。
2. **判**：Jev 对每条打两个分——与凌一三块业务的相关度、可写价值（0–9）。
3. **写**：只给留下来的条目生成"为什么与你有关 / 可以写什么 / 下一步"。

失败策略：单个来源挂了不影响其他来源；Jev 不可用时条目保留但标 analyzed=false，
页面显示原文标题而不是假装分析过。
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_client
import wechat_semantic as sem

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "radar.json"
CACHE = ROOT / "radar-cache.json"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36")
TIMEOUT = int(os.environ.get("RADAR_TIMEOUT", "25"))
MAX_ITEMS = int(os.environ.get("RADAR_MAX_ITEMS", "36"))
JUDGE_LIMIT = int(os.environ.get("RADAR_JUDGE_LIMIT", "12"))
NARRATIVE_LIMIT = int(os.environ.get("RADAR_NARRATIVE_LIMIT", "6"))
MIN_RELEVANCE = float(os.environ.get("RADAR_MIN_RELEVANCE", "5.0"))
# 榜单每 2 小时刷新一次，同一批标题会被反复看到。缓存 24 小时，避免重复烧 Jev 调用。
CACHE_TTL = int(os.environ.get("RADAR_CACHE_TTL", "86400"))

# 榜单 ID 来自 tophub 首页 /n/<id>；掘金榜是 JS 渲染，解析不到，先不接。
TOPUB_BOARDS = [
    ("36kr", "36氪 24 小时热榜", "Q1Vd5Ko85R"),
    ("ithome", "IT之家", "74Kvx59dkx"),
    ("zhihu", "知乎热榜", "mproPpoq6O"),
]


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def parse_tophub(body: str) -> list[dict]:
    """从榜单页 HTML 取标题+链接。拆出来是为了能用固定 HTML 做离线回归。"""
    rows = re.findall(
        r'<td[^>]*class="al"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', body, re.S)
    items = []
    for rank, (url, title) in enumerate(rows, start=1):
        title = _clean(title)
        if not title or not url.startswith("http"):
            continue
        items.append({"title": title[:160], "url": url, "rank": rank})
    return items


def fetch_tophub(board_id: str) -> list[dict]:
    return parse_tophub(_get("https://tophub.today/n/%s" % board_id))


def fetch_hn() -> list[dict]:
    body = _get("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=15")
    data = json.loads(body)
    items = []
    for rank, hit in enumerate(data.get("hits") or [], start=1):
        title = (hit.get("title") or "").strip()
        url = hit.get("url") or ("https://news.ycombinator.com/item?id=%s" % hit.get("objectID", ""))
        if not title:
            continue
        items.append({"title": title[:160], "url": url, "rank": rank,
                      "points": hit.get("points"), "at": hit.get("created_at")})
    return items


SOURCES = [
    {"id": "36kr", "name": "36氪 · 24 小时热榜", "kind": "tophub", "board": "Q1Vd5Ko85R",
     "url": "https://tophub.today/n/Q1Vd5Ko85R"},
    {"id": "ithome", "name": "IT之家", "kind": "tophub", "board": "74Kvx59dkx",
     "url": "https://tophub.today/n/74Kvx59dkx"},
    {"id": "zhihu", "name": "知乎热榜", "kind": "tophub", "board": "mproPpoq6O",
     "url": "https://tophub.today/n/mproPpoq6O"},
    # 微博热搜页是 JS 渲染（条目由前端模板拼出），服务端 HTML 里没有条目；
    # 先如实标 unsupported，不猜、不空跑。
    {"id": "weibo", "name": "微博热搜", "kind": "unsupported",
     "url": "https://tophub.today/n/KqndgxeLl9",
     "reason": "页面为 JS 渲染，服务端 HTML 不含条目"},
    {"id": "hn", "name": "Hacker News", "kind": "hn", "url": "https://news.ycombinator.com/"},
]


def collect() -> tuple[list[dict], list[dict]]:
    items: list[dict] = []
    statuses: list[dict] = []
    for src in SOURCES:
        started = dt.datetime.now().astimezone()
        if src["kind"] == "unsupported":
            statuses.append({"id": src["id"], "name": src["name"], "url": src["url"],
                             "status": "unsupported", "itemCount": 0,
                             "fetchedAt": started.isoformat(timespec="seconds"),
                             "error": src.get("reason", "")})
            continue
        try:
            if src["kind"] == "tophub":
                rows = fetch_tophub(src["board"])
            else:
                rows = fetch_hn()
            for row in rows:
                items.append({**row, "source": src["id"], "sourceName": src["name"]})
            statuses.append({"id": src["id"], "name": src["name"], "url": src["url"],
                             "status": "ok" if rows else "empty", "itemCount": len(rows),
                             "fetchedAt": started.isoformat(timespec="seconds"), "error": ""})
        except Exception as exc:  # noqa: BLE001
            statuses.append({"id": src["id"], "name": src["name"], "url": src["url"],
                             "status": "error", "itemCount": 0,
                             "fetchedAt": started.isoformat(timespec="seconds"),
                             "error": "%s: %s" % (type(exc).__name__, str(exc)[:120])})
    # 去重：同标题只留第一次出现（榜单之间经常互相转录）
    seen, unique = set(), []
    for row in items:
        key = row["title"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique[:MAX_ITEMS], statuses


JEV_QUESTIONS = {
    "relevance": {
        "type": "score",
        "instructions": ("与凌一三块业务的相关度 0-9。他的业务是：企业 AI 落地咨询、"
                         "AI/副业教学社群、内容创作（公众号/小红书）。纯娱乐八卦、体育、"
                         "社会新闻给低分；AI 工具、自动化、出海、副业、内容平台规则给高分。"),
        "criteria": ["完全无关", "几乎无关", "弱相关", "有点沾边", "可参考", "相关",
                     "明确相关", "高相关", "直接可用", "必看"],
    },
    "angle": {
        "type": "score",
        "instructions": ("可写价值 0-9。能不能产出公众号 / 小红书 / 社群分享的具体角度。"
                         "只有结论没有细节的新闻给低分；有数据、有对比、有踩坑过程的给高分。"),
        "criteria": ["写不出", "几乎写不出", "很薄", "勉强", "可做短评", "能写一篇",
                     "有结构可写", "角度清晰", "强选题", "马上能写"],
    },
}


def _cache_key(title: str) -> str:
    return " ".join((title or "").lower().split())


def load_cache() -> dict:
    try:
        with open(CACHE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_cache(cache: dict) -> None:
    now = dt.datetime.now().astimezone()
    keep_after = now.timestamp() - 7 * 86400
    pruned = {}
    for key, val in cache.items():
        try:
            at = float(val.get("at") or 0)
        except (TypeError, ValueError):
            continue
        if at >= keep_after:
            pruned[key] = val
    try:
        tmp = CACHE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(pruned, fh, ensure_ascii=False)
        os.replace(tmp, CACHE)
    except OSError:
        pass


def _cached_verdict(cache: dict, title: str) -> dict | None:
    entry = cache.get(_cache_key(title))
    if not entry:
        return None
    try:
        if dt.datetime.now().astimezone().timestamp() - float(entry.get("at") or 0) > CACHE_TTL:
            return None
    except (TypeError, ValueError):
        return None
    return {"relevance": entry.get("relevance"), "angle": entry.get("angle"),
            "judgmentModel": entry.get("judgmentModel") or "jev",
            "analyzed": True, "fromCache": True}


def judge(items: list[dict], cache: dict | None = None) -> list[dict]:
    cache = cache if cache is not None else {}
    out = []
    for row in items[:JUDGE_LIMIT]:
        hit = _cached_verdict(cache, row["title"])
        if hit:
            out.append({**row, **hit})
            continue
        prompt = ("判断这条热点对凌一的价值。只判断，不要写内容。\n"
                  "标题：%s\n来源：%s\n" % (row["title"], row["sourceName"]))
        ok, data = jev_client.judge(prompt, questions=JEV_QUESTIONS)
        # 先写成"未解析"，成功再覆盖。缺字段会让下游只能靠"取不到"猜状态。
        verdict = {"analyzed": False, "judgmentModel": ""}
        if ok:
            answers = jev_client.scores(data)
            rel = (answers.get("relevance") or {}).get("score")
            ang = (answers.get("angle") or {}).get("score")
            if rel is not None:
                verdict = {"relevance": round(float(rel), 2),
                           "angle": round(float(ang), 2) if ang is not None else None,
                           "judgmentModel": "jev", "analyzed": True}
                cache[_cache_key(row["title"])] = {
                    "relevance": verdict["relevance"], "angle": verdict["angle"],
                    "judgmentModel": "jev",
                    "at": dt.datetime.now().astimezone().timestamp(),
                }
        out.append({**row, **verdict})
    # Jev 没跑完的部分不丢，但明确标未解析
    for row in items[JUDGE_LIMIT:]:
        out.append({**row, "analyzed": False, "judgmentModel": ""})
    return out


NARRATIVE_HEADER = """你是凌一的内容选题助手。他的三块业务：企业 AI 落地咨询、AI/副业教学社群、内容创作（公众号/小红书）。
只输出 JSON：
{"whyRelated":"这条热点具体能怎么用到他的业务上（≤40字）","angles":["可直接开写的选题角度（≤24字）"],"nextAction":"24 小时内能做完的一步（≤24字）"}

硬规则：
- 禁止写"与凌一相关""影响其业务"这类空话，必须说清是哪块业务、怎么用。
- 角度禁止复述标题，要写成"对比/清单/复盘/避坑/成本测算"这种可执行的选题形态。
- 不知道就写"待确认"，不要编造数字或结论。"""

# 角度如果只是把标题换个说法，等于没写。用字符重合率挡掉这种输出。
def _repeats_title(text: str, title: str, threshold: float = 0.6) -> bool:
    a = {c for c in (text or "") if "\u4e00" <= c <= "\u9fff"}
    b = {c for c in (title or "") if "\u4e00" <= c <= "\u9fff"}
    if not a or not b:
        return False
    return len(a & b) / len(a) >= threshold


def _cached_narrative(cache: dict, title: str) -> dict | None:
    entry = cache.get(_cache_key(title))
    if not entry or not (entry.get("whyRelated") or entry.get("angles")):
        return None
    try:
        if dt.datetime.now().astimezone().timestamp() - float(entry.get("at") or 0) > CACHE_TTL:
            return None
    except (TypeError, ValueError):
        return None
    return {"whyRelated": entry.get("whyRelated") or "",
            "angles": list(entry.get("angles") or []),
            "nextAction": entry.get("nextAction") or ""}


def enrich(rows: list[dict], cache: dict | None = None) -> list[dict]:
    cache = cache if cache is not None else {}
    picked = [r for r in rows
              if r.get("analyzed") and (r.get("relevance") or 0) >= MIN_RELEVANCE]
    picked.sort(key=lambda r: ((r.get("relevance") or 0) * 0.6 + (r.get("angle") or 0) * 0.4), reverse=True)
    for row in picked[:NARRATIVE_LIMIT]:
        hit = _cached_narrative(cache, row["title"])
        if hit:
            row.update(hit)
            row["fromCache"] = True
            row["needsHuman"] = not (hit["whyRelated"] or hit["angles"])
            continue
        try:
            raw = sem._llm(NARRATIVE_HEADER + "\n标题：%s\n来源：%s" % (row["title"], row["sourceName"]))
            match = re.search(r"\{.*\}", raw, re.S)
            obj = json.loads(match.group(0)) if match else {}
        except Exception:
            obj = {}
        why = str(obj.get("whyRelated") or "").strip()[:80]
        angles = [str(a).strip()[:40] for a in (obj.get("angles") or []) if str(a).strip()]
        # 复述标题的角度不要；宁可空着让页面显示"需人工判断"
        angles = [a for a in angles if not _repeats_title(a, row["title"])][:2]
        if _repeats_title(why, row["title"], 0.75):
            why = ""
        row["whyRelated"] = why
        row["angles"] = angles
        row["nextAction"] = str(obj.get("nextAction") or "").strip()[:60]
        row["needsHuman"] = not (why or angles)
        if why or angles:
            entry = cache.setdefault(_cache_key(row["title"]), {})
            entry.update({"whyRelated": why, "angles": angles,
                          "nextAction": row["nextAction"],
                          "at": dt.datetime.now().astimezone().timestamp()})
    return rows


def write(snapshot: dict) -> None:
    import shutil
    tmp = OUT.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=1)
    if OUT.exists():
        try:
            shutil.copy2(OUT, OUT.with_suffix(".json.bak"))
        except OSError:
            pass
    os.replace(tmp, OUT)


def main() -> int:
    items, statuses = collect()
    cache = load_cache()
    rows = judge(items, cache)
    rows = enrich(rows, cache)
    save_cache(cache)
    ok_sources = [s for s in statuses if s["status"] == "ok"]
    snapshot = {
        "version": 1,
        "generatedAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataPolicy": ("只读快照。抓不到的来源标 error，不补条目；Jev 只做相关度/可写价值判断，"
                       "未解析的条目如实标记；角度由本地模型生成，需人工确认。"),
        "sources": statuses,
        "items": rows,
        "coverage": {
            "sourcesTotal": len(statuses),
            "sourcesOk": len(ok_sources),
            "sourcesFailed": len(statuses) - len(ok_sources),
            "itemsFetched": len(items),
            "itemsJudged": sum(1 for r in rows if r.get("analyzed")),
            "keptForWriting": sum(1 for r in rows if r.get("whyRelated") or r.get("angles")),
            "judgmentsFromCache": sum(1 for r in rows if r.get("fromCache")),
            "judgmentModel": "jev" if jev_client.available() else "unavailable",
        },
    }
    write(snapshot)
    print("✓ 选题雷达：%d 源可用 / %d 源失败 · 抓取 %d 条 · Jev 判断 %d 条 · 可写 %d 条"
          % (snapshot["coverage"]["sourcesOk"], snapshot["coverage"]["sourcesFailed"],
             len(items), snapshot["coverage"]["itemsJudged"], snapshot["coverage"]["keptForWriting"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
