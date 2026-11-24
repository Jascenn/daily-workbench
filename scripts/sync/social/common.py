"""
[INPUT]: 仅依赖 Python 标准库与本地配置/导出路径常量
[OUTPUT]: 导出路径常量、运行时桥接（bind_entry_globals/scfg）及日期/表格/PDF/命令行公共工具
[POS]: scripts/sync/social/ 公共基础层，供各平台抓手模块共享
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""
from __future__ import annotations

import csv
import datetime as dt
import email.utils
import glob
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

_ENTRY_GLOBALS: dict | None = None


def bind_entry_globals(ns: dict) -> None:
    global _ENTRY_GLOBALS
    _ENTRY_GLOBALS = ns


def scfg(name: str, default=None):
    if _ENTRY_GLOBALS is not None and name in _ENTRY_GLOBALS:
        return _ENTRY_GLOBALS[name]
    return default


ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "social.json"
KNOWLEDGE_VAULT = Path(os.environ.get("WB_OBSIDIAN_ROOT", str(Path.home() / "Documents" / "Obsidian-Tech")))
XREACH_SESSION = Path.home() / ".config" / "xfetch" / "session.json"
OPENCLAW_ENV = Path.home() / ".openclaw" / ".env"
WECHAT_PUSH = Path.home() / ".openclaw" / "workspace" / "articles" / "mp_push.py"
XHS_PIPELINE = Path.home() / ".openclaw" / "workspace" / "skills" / "xiaohongshu-skills" / "scripts" / "fill_draft_defaults.py"
MCPORTER_CONFIG = Path.home() / ".mcporter" / "mcporter.json"

X_PDF = Path(os.environ.get("WB_X_PDF", str(Path.home() / "Downloads" / "x-analytics.pdf")))
ZSXQ_XLSX = Path(os.environ.get("WB_ZSXQ_XLSX", str(Path.home() / "Downloads" / "zsxq-report.xlsx")))
ZSXQ_CSV = Path(os.environ.get("WB_ZSXQ_CSV", str(Path.home() / "Downloads" / "zsxqarticle.csv")))
ZSXQ_TOKEN = Path.home() / ".config" / "daily-workbench" / "zsxq-token"
ZSXQ_GROUPS = [g.strip() for g in os.environ.get("WB_ZSXQ_GROUPS", "").split(",") if g.strip()]
MPDT_BIN = Path.home() / ".venvs" / "mpdt" / "bin" / "mpdt"
MPDT_PROFILE = Path.home() / ".mpdt" / "chrome-profile"

XML_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"



def now_iso() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def annotate_sync_status(
    account: dict[str, object],
    *,
    attempted_at: str,
    status: str,
    reason: str,
    source: str,
    updated: bool = False,
) -> dict[str, object]:
    """Attach honest per-run provenance without changing the data payload.

    ``status`` describes this run, while ``freshness``/``asOf`` describe the
    records themselves.  Keeping both prevents a newly generated file from
    being mistaken for newly fetched platform data.
    """
    account["sync"] = {
        "attemptedAt": attempted_at,
        "status": status,
        "updatedThisRun": bool(updated),
        "source": source,
        "reason": reason,
    }
    return account


def safe_http_url(value: object) -> str:
    text = str(value or "").strip()
    try:
        p = urlparse(text)
    except ValueError:
        return ""
    return text if p.scheme in {"http", "https"} and p.netloc else ""


def column_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref or "")
    n = 0
    for ch in (letters.group(0) if letters else "A"):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_simple_xlsx(path: Path) -> list[dict[str, object]]:
    """Read the first worksheet with stdlib only; enough for this exported table."""
    if not path.exists():
        return []
    with zipfile.ZipFile(path) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{XML_NS}si"):
                shared.append("".join((node.text or "") for node in si.iter(f"{XML_NS}t")))

        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
        first_sheet = workbook.find(f"{XML_NS}sheets/{XML_NS}sheet")
        if first_sheet is None:
            return []
        rel_id = first_sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", "")
        target = rel_map.get(rel_id, "worksheets/sheet1.xml")
        sheet_name = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
        sheet = ET.fromstring(zf.read(sheet_name))

        matrix: list[list[object]] = []
        for row in sheet.findall(f".//{XML_NS}sheetData/{XML_NS}row"):
            values: dict[int, object] = {}
            for cell in row.findall(f"{XML_NS}c"):
                idx = column_index(cell.attrib.get("r", "A1"))
                kind = cell.attrib.get("t", "")
                raw = cell.find(f"{XML_NS}v")
                if kind == "inlineStr":
                    inline = cell.find(f"{XML_NS}is")
                    value = "".join((n.text or "") for n in inline.iter(f"{XML_NS}t")) if inline is not None else ""
                else:
                    value = raw.text if raw is not None else ""
                    if kind == "s" and str(value).isdigit():
                        pos = int(value)
                        value = shared[pos] if pos < len(shared) else ""
                    elif kind == "n":
                        try:
                            number = float(value)
                            value = int(number) if number.is_integer() else number
                        except (TypeError, ValueError):
                            pass
                values[idx] = value
            if values:
                width = max(values) + 1
                matrix.append([values.get(i, "") for i in range(width)])
        if not matrix:
            return []
        headers = [str(x).strip() for x in matrix[0]]
        return [dict(zip(headers, row + [""] * (len(headers) - len(row)))) for row in matrix[1:]]


def parse_date(value: object) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            return email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None


def freshness_fields(
    as_of: object,
    *,
    fresh_days: int = 1,
    stale_after_days: int = 7,
    now: dt.date | None = None,
) -> dict[str, object]:
    """按数据日期计算新鲜度，不信任采集端可能过时的固定标签。

    ``asOf`` 表示这批数据实际覆盖到的最后日期。通道连通不代表数据新鲜，
    页面需要同时呈现“已连接”和“数据过期”这两个事实。
    """
    when = parse_date(as_of)
    if when is None:
        return {"freshness": "missing", "freshnessAgeDays": None}
    if when.tzinfo is not None:
        when = when.astimezone(dt.timezone(dt.timedelta(hours=8)))
    today = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    age = max(0, (today - when.date()).days)
    if age <= fresh_days:
        state = "fresh"
    elif age <= stale_after_days:
        state = "aging"
    else:
        state = "stale"
    return {"freshness": state, "freshnessAgeDays": age}


def file_time(path: Path) -> str | None:
    if not path.exists():
        return None
    return dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def env_flags(path: Path, names: set[str]) -> dict[str, bool]:
    found = {name: False for name in names}
    if not path.exists():
        return found
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in found:
                found[key.strip()] = bool(value.strip())
    except OSError:
        pass
    return found


def env_values(path: Path, names: set[str]) -> dict[str, str]:
    found = {name: "" for name in names}
    if not path.exists():
        return found
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in found:
                found[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return found


def tool_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidates = [
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
        Path.home() / ".local" / "bin" / name,
    ]
    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    if nvm_root.is_dir():
        candidates.extend(sorted(nvm_root.glob(f"*/bin/{name}"), reverse=True))
    return next((str(path) for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


def xreach_ready() -> bool:
    if not tool_binary("xreach") or not XREACH_SESSION.exists():
        return False
    try:
        session = json.loads(XREACH_SESSION.read_text(encoding="utf-8"))
        return bool(session.get("authToken") and session.get("ct0"))
    except (OSError, ValueError):
        return False


def run_json(command: list[str], timeout: int = 45) -> object | None:
    try:
        env = os.environ.copy()
        if command:
            # Keep the symlink's bin directory. NVM CLIs use `#!/usr/bin/env node`,
            # so resolving the symlink into node_modules would hide the sibling
            # `node` executable from launchd's minimal PATH.
            bindir = str(Path(command[0]).parent)
            env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
        result = subprocess.run(command, check=True, text=True, capture_output=True, timeout=timeout, env=env)
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None



def metric_int(value: object) -> int:
    text = str(value or "").replace(",", "").strip()
    try:
        return int(float(text)) if text else 0
    except ValueError:
        return 0



def pdf_text(path: Path) -> str:
    if not path.exists():
        return ""
    # launchd gives this service a minimal PATH, so Homebrew tools are not
    # discoverable there even though they work in an interactive shell.
    binary = shutil.which("pdftotext")
    if not binary:
        for candidate in ("/opt/homebrew/bin/pdftotext", "/usr/local/bin/pdftotext"):
            if Path(candidate).is_file():
                binary = candidate
                break
    if not binary:
        return ""
    try:
        return subprocess.run(
            [binary, "-layout", str(path), "-"],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


