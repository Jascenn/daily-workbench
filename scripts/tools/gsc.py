#!/usr/bin/env python3
"""Google Search Console daily snapshot for the WorkBuddy workbench.

Pure standard library: no pip packages needed. Service-account auth is done
with a hand-rolled RS256 JWT (hashlib + pow), all HTTPS via urllib.

One-time setup (done by the user, once):
  1. Google Cloud Console -> new project -> enable "Search Console API".
  2. IAM & Admin -> Service Accounts -> create one -> download the JSON key.
  3. In Google Search Console, add the service-account email to each property
     (Settings > Users and permissions). Viewer is enough for reading.
  4. Save the JSON key as ~/.config/daily-workbench/gsc-service-account.json

Called from web-monitor.py (every 5 min). The actual Google API fetch runs at
most once every REFRESH_HOURS; otherwise the on-disk cache is returned.
This function never raises: on any failure it returns the last good cache
with an error note, or a {"configured": False} hint.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CACHE = ROOT / "gsc-cache.json"
KEY_FILE = Path(os.path.expanduser("~/.config/daily-workbench/gsc-service-account.json"))

TOKEN_URL = "https://oauth2.googleapis.com/token"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GSC_API = "https://www.googleapis.com/webmasters/v3"

# (id, display name, GSC property)
# 2026-10-24：博客从 lingyi.bio 迁至 lingyi.me（内容不变），
# sc-domain:lingyi.me 已完成 DNS 验证并授权服务账号。
SITES = [
    ("tools", "lingyi.tools", "sc-domain:lingyi.tools"),
    ("me", "lingyi.me", "sc-domain:lingyi.me"),
]

REFRESH_HOURS = 20
DAYS = 28          # current window length
DATA_LAG_DAYS = 3  # GSC data is typically 2-3 days behind
TOP_N = 8
HTTP_TIMEOUT = 15

# Google API 在直连下会被干扰（超时 / 假 400），统一走本地代理 —— 与
# web-monitor.py 的 notion_daily_visitors 同一出口，保持一条网络策略。
_PROXY = urllib.request.ProxyHandler({
    "http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"})
_opener = urllib.request.build_opener(_PROXY)

# ---------- minimal DER / RSA helpers (RS256 for the service-account JWT) ---

def _tlv(data: bytes, pos: int):
    tag = data[pos]
    pos += 1
    length = data[pos]
    pos += 1
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    return tag, data[pos:pos + length], pos + length


def _rsa_from_pkcs8_pem(pem: str):
    b64 = "".join(
        line for line in pem.splitlines()
        if line and "BEGIN" not in line and "END" not in line
    )
    der = base64.b64decode(b64)
    _, outer, _ = _tlv(der, 0)
    pos = 0
    _, _, pos = _tlv(outer, pos)   # version
    _, _, pos = _tlv(outer, pos)   # algorithm identifier
    _, octets, _ = _tlv(outer, pos)  # privateKey OCTET STRING
    _, inner, _ = _tlv(octets, 0)
    p2 = 0
    _, _, p2 = _tlv(inner, p2)     # version
    _, n_b, p2 = _tlv(inner, p2)    # n
    _, _, p2 = _tlv(inner, p2)      # e
    _, d_b, _ = _tlv(inner, p2)     # d
    return int.from_bytes(n_b, "big"), int.from_bytes(d_b, "big")


def _rs256_sign(message: bytes, n: int, d: int) -> bytes:
    digest = hashlib.sha256(message).digest()
    # DigestInfo prefix for SHA-256 (EMSA-PKCS1-v1_5)
    t = bytes.fromhex("3031300d060960864801650304020105000420") + digest
    k = (n.bit_length() + 7) // 8
    em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# ---------- Google auth + Search Analytics ----------------------------------

def _access_token(client_email: str, private_key_pem: str) -> str:
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": client_email,
        "scope": GSC_SCOPE,
        "aud": TOKEN_URL,
        "iat": now,
        "exp": now + 3600,
    }
    signing_input = (
        _b64u(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64u(json.dumps(claims, separators=(",", ":")).encode())
    ).encode()
    n, d = _rsa_from_pkcs8_pem(private_key_pem)
    signature = _b64u(_rs256_sign(signing_input, n, d))
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": signing_input.decode() + "." + signature,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with _opener.open(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError("token exchange HTTP %s: %s" % (e.code, detail))
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("token exchange failed: %r" % (payload,))
    return token


def _gsc_query(token: str, site_url: str, start: str, end: str,
               dimensions: list, row_limit: int = 5000) -> list:
    url = GSC_API + "/sites/" + urllib.parse.quote(site_url, safe="") \
        + "/searchAnalytics/query"
    body = json.dumps({
        "startDate": start,
        "endDate": end,
        "dimensions": dimensions,
        "rowLimit": row_limit,
    }).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + token},
    )
    try:
        with _opener.open(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError("GSC API %s: %s" % (e.code, detail))
    return payload.get("rows") or []


def _summarize(rows: list) -> dict:
    clicks = sum(r.get("clicks", 0) for r in rows)
    impr = sum(r.get("impressions", 0) for r in rows)
    pos_num = sum(r.get("position", 0) * r.get("impressions", 0) for r in rows)
    return {
        "clicks": int(clicks),
        "impressions": int(impr),
        "ctr": round(clicks / impr, 4) if impr else 0.0,
        "position": round(pos_num / impr, 1) if impr else 0.0,
    }


def _fetch_site(token: str, site_id: str, name: str, prop: str,
                today: dt.date) -> dict:
    end = today - dt.timedelta(days=DATA_LAG_DAYS)
    start = end - dt.timedelta(days=DAYS - 1)
    prev_end = start - dt.timedelta(days=1)
    prev_start = prev_end - dt.timedelta(days=DAYS - 1)
    iso = lambda d: d.isoformat()

    cur = _gsc_query(token, prop, iso(start), iso(end), ["date"], 5000)
    prev = _gsc_query(token, prop, iso(prev_start), iso(prev_end), ["date"], 5000)
    top_q = _gsc_query(token, prop, iso(start), iso(end), ["query"], TOP_N)
    top_p = _gsc_query(token, prop, iso(start), iso(end), ["page"], TOP_N)

    cur_s, prev_s = _summarize(cur), _summarize(prev)

    def delta(cur_v, prev_v):
        if not prev_v:
            return None
        return round((cur_v - prev_v) / prev_v, 3)

    return {
        "id": site_id,
        "name": name,
        "period": {"start": iso(start), "end": iso(end), "days": DAYS},
        "clicks": cur_s["clicks"],
        "impressions": cur_s["impressions"],
        "ctr": cur_s["ctr"],
        "position": cur_s["position"],
        "deltaClicks": delta(cur_s["clicks"], prev_s["clicks"]),
        "deltaImpressions": delta(cur_s["impressions"], prev_s["impressions"]),
        "topQueries": [
            {"q": (r.get("keys") or [""])[0], "clicks": int(r.get("clicks", 0)),
             "impressions": int(r.get("impressions", 0)),
             "position": round(r.get("position", 0), 1)}
            for r in top_q
        ],
        "topPages": [
            {"p": (r.get("keys") or [""])[0], "clicks": int(r.get("clicks", 0)),
             "impressions": int(r.get("impressions", 0))}
            for r in top_p
        ],
    }


def _refresh() -> dict:
    key = json.loads(KEY_FILE.read_text(encoding="utf-8"))
    token = _access_token(key["client_email"], key["private_key"])
    today = dt.date.today()
    sites = []
    for site_id, name, prop in SITES:
        try:
            sites.append(_fetch_site(token, site_id, name, prop, today))
        except Exception as e:  # one bad property must not kill the other
            sites.append({"id": site_id, "name": name, "error": str(e)[:200]})
    return {
        "version": 1,
        "generatedAt": dt.datetime.now(
            dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
        "sites": sites,
    }


def _read_cache() -> dict | None:
    try:
        value = json.loads(CACHE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _cache_fresh_enough(cached: dict | None) -> bool:
    if not cached:
        return False
    try:
        gen = dt.datetime.fromisoformat(str(cached.get("generatedAt", "")))
        age = dt.datetime.now(dt.timezone.utc).astimezone(gen.tzinfo) - gen
        return age < dt.timedelta(hours=REFRESH_HOURS)
    except (ValueError, TypeError):
        return False


def gsc_snapshot() -> dict | None:
    """Cached GSC summary for web-monitor.py. Never raises."""
    try:
        if not KEY_FILE.exists():
            cached = _read_cache()
            if cached:
                cached = dict(cached)
                cached["notConfigured"] = True
                return cached
            return {"notConfigured": True, "sites": []}
        cached = _read_cache()
        if _cache_fresh_enough(cached):
            return cached
        fresh = _refresh()
        tmp = CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(fresh, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, CACHE)
        return fresh
    except Exception as e:  # noqa: BLE001 - monitor loop must not die
        cached = _read_cache()
        if cached:
            cached = dict(cached)
            cached["error"] = "刷新失败，显示上次成功数据：%s" % str(e)[:160]
            return cached
        return {"error": str(e)[:200], "sites": []}


if __name__ == "__main__":
    snap = gsc_snapshot()
    print(json.dumps(snap, ensure_ascii=False, indent=2))
