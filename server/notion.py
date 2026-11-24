"""
[INPUT]: 依赖 /usr/local/bin/ntn CLI、data.json 与 server.runtime 运行时配置
[OUTPUT]: 导出项目/客户/专注记录白名单映射、关系搜索缓存与本地原子回写函数
[POS]: server/ Notion 显式白名单回写子模块，绝不写收益、绝不读页面正文
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""
import datetime
import json
import math
import os
import re
import re as _re
import subprocess
import threading
import time

from server.runtime import cfg as _cfg

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(DIR, "data.json")
NTN = "/usr/local/bin/ntn"
DS_FOCUS = "00000000-0000-4000-8000-000000000001"
DS_PROJECT = "00000000-0000-4000-8000-000000000002"
DS_TASKS = "00000000-0000-4000-8000-000000000003"
DS_RECORDS = "00000000-0000-4000-8000-000000000004"
NOTION_SUBPROCESS_ENV = dict(os.environ)

_CLIENT_SELECT_PROPS = {
    "customerType": "客户类型",
    "status": "客户状态",
    "relationSource": "关系来源",
}
_CLIENT_DATE_PROPS = {
    "firstContact": "首次接触",
    "lastContact": "上次联系",
    "nextFollowUp": "下次跟进",
}
_CLIENT_TEXT_PROPS = {
    "note": "备注",
    "wechatId": "微信ID",
    "coordinate": "坐标",
    "todo": "待办",
    "benefits": "已送福利",
    "address": "快递地址",
    "channel": "渠道",
    "knownChannel": "认识渠道",
}
_CLIENT_WRITABLE = set(_CLIENT_SELECT_PROPS) | set(_CLIENT_DATE_PROPS) | set(_CLIENT_TEXT_PROPS) | {"name"}
_CLIENT_LOCAL_KEYS = {key: key for key in _CLIENT_WRITABLE}

_PROJECT_WRITABLE = {
    "name", "status", "priority", "projectType", "tags",
    "start", "due", "doneDate", "sprint", "sprintStart", "sprintEnd",
    "estHours", "actHours", "note", "relatedLink",
    "parentNotionId", "relatedTaskIds", "sourceRecordIds", "ownerIds",
}
_PROJECT_LOCAL_KEYS = {
    "name": "name",
    "status": "status",
    "priority": "priority",
    "projectType": "projectType",
    "tags": "tags",
    "start": "start",
    "due": "due",
    "doneDate": "doneDate",
    "sprint": "sprint",
    "sprintStart": "sprintStart",
    "sprintEnd": "sprintEnd",
    "estHours": "estHours",
    "actHours": "actHours",
    "note": "note",
    "relatedLink": "relatedLink",
    "parentNotionId": "parentNotionId",
    "relatedTaskIds": "relatedTaskIds",
    "sourceRecordIds": "sourceRecordIds",
    "ownerIds": "ownerIds",
}
_PROJECT_SCHEMA_CACHE = {"at": 0.0, "schema": None}
_PROJECT_RELATION_CACHE = {}
_PROJECT_RELATION_CACHE_TTL = 300


def _rich_text_value(value):
    text = "" if value is None else str(value).strip()
    if not text:
        return []
    if len(text) > 8000:
        raise ValueError("单个文本字段最多 8000 字")
    return [{"type": "text", "text": {"content": text[i:i + 2000]}}
            for i in range(0, len(text), 2000)]


def _ntn_api_call(cmd, body=None, timeout=45, attempts=3):
    """执行 ntn 公开 API 调用，并重试代理侧的瞬时失败。

    Surge/上游偶发返回 ``Failed to execute public API request``。该错误在
    同一请求稍后重试即可恢复；认证、参数和权限错误不重试，避免掩盖真问题。
    Notion 的 PATCH 是设置属性而不是追加记录，重复发送相同属性结果一致。
    """
    last_error = None
    for attempt in range(max(1, int(attempts))):
        try:
            proc = subprocess.run(
                cmd,
                input=body,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_cfg("NOTION_SUBPROCESS_ENV", NOTION_SUBPROCESS_ENV),
            )
        except subprocess.TimeoutExpired:
            # 超时不自动重试：一次已经等了完整超时，再等会让页面长期无响应。
            raise
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode == 0 and out:
            return out
        message = err or out or "ntn API 请求失败"
        last_error = RuntimeError(message[-1200:])
        transient = "failed to execute public api request" in message.lower()
        if not transient or attempt + 1 >= attempts:
            raise last_error
        time.sleep(1.0 * (attempt + 1))
    raise last_error or RuntimeError("ntn API 请求失败")


def _build_client_properties(fields):
    """把白名单字段转换成 Notion page properties。

    页面只能改客户库里的资料字段，不允许借这个接口改收益、项目或任意其它页面。
    """
    if not isinstance(fields, dict):
        raise ValueError("fields 必须是对象")
    unknown = sorted(set(fields) - _CLIENT_WRITABLE)
    if unknown:
        raise ValueError("不支持回写的字段：" + ", ".join(unknown))
    props = {}
    if "name" in fields:
        name = str(fields.get("name") or "").strip()
        if not name:
            raise ValueError("客户名称不能为空")
        if len(name) > 200:
            raise ValueError("客户名称最多 200 字")
        props["Name"] = {"title": _rich_text_value(name)}
    for key, prop_name in _CLIENT_SELECT_PROPS.items():
        if key not in fields:
            continue
        value = str(fields.get(key) or "").strip()
        if len(value) > 100:
            raise ValueError("%s 选项过长" % prop_name)
        props[prop_name] = {"select": {"name": value} if value else None}
    for key, prop_name in _CLIENT_DATE_PROPS.items():
        if key not in fields:
            continue
        value = str(fields.get(key) or "").strip()
        if value:
            try:
                datetime.date.fromisoformat(value)
            except ValueError:
                raise ValueError("%s 必须是 YYYY-MM-DD" % prop_name)
        props[prop_name] = {"date": {"start": value} if value else None}
    for key, prop_name in _CLIENT_TEXT_PROPS.items():
        if key in fields:
            props[prop_name] = {"rich_text": _rich_text_value(fields.get(key))}
    if not props:
        raise ValueError("没有可写入的字段")
    return props


def _notion_update_page(page_id, properties):
    """经 ntn CLI 更新 Notion 页面；令牌仍由 ntn 自己从系统钥匙串读取。"""
    cmd = [_cfg("NTN", NTN), "api", "/v1/pages/%s" % page_id, "-X", "PATCH"]
    out = _cfg("_ntn_api_call", _ntn_api_call)(
        cmd,
        json.dumps({"properties": properties}, ensure_ascii=False),
        timeout=45,
    )
    return json.loads(out)


def _project_schema_from_current():
    """用最近一次 Notion 同步保存的选项构造一套最小 schema。

    回写时不必再额外 GET 一次数据库 schema；同步脚本已经把真实选项写进
    ``notionMeta.projectSchema``。属性和类型是工作台白名单的一部分，选项则
    永远以最近同步结果为准。
    """
    data = _cfg("_read_current")() or {}
    meta = data.get("notionMeta") or {}
    options = meta.get("projectSchema") or {}
    required = ("status", "priority", "type", "sprint", "tags")
    if not all(isinstance(options.get(key), list) for key in required):
        return None

    def option_prop(typ, key):
        return {
            "type": typ,
            typ: {"options": [{"name": str(name)} for name in options.get(key, [])
                               if str(name or "").strip()]},
        }

    def typed_prop(typ):
        return {"type": typ, typ: {}}

    return {"properties": {
        "项目名称": typed_prop("title"),
        "状态": option_prop("status", "status"),
        "优先级": option_prop("select", "priority"),
        "类型": option_prop("select", "type"),
        "冲刺": option_prop("select", "sprint"),
        "标签": option_prop("multi_select", "tags"),
        "开始日期": typed_prop("date"),
        "截止日期": typed_prop("date"),
        "完成日期": typed_prop("date"),
        "冲刺开始日期": typed_prop("date"),
        "冲刺结束日期": typed_prop("date"),
        "预计工时": typed_prop("number"),
        "实际工时": typed_prop("number"),
        "备注": typed_prop("rich_text"),
        "相关链接": typed_prop("url"),
        "上级 项目": typed_prop("relation"),
        "关联任务": typed_prop("relation"),
        "来源记录": typed_prop("relation"),
        "负责人": typed_prop("people"),
    }}


def _notion_project_schema():
    """读取项目管理 schema：优先用同步快照，缺失时再现场拉取并短时缓存。"""
    now = time.time()
    cached = _PROJECT_SCHEMA_CACHE.get("schema")
    if cached and now - float(_PROJECT_SCHEMA_CACHE.get("at") or 0) < 300:
        return cached
    schema = _project_schema_from_current()
    if schema:
        _PROJECT_SCHEMA_CACHE["at"] = now
        _PROJECT_SCHEMA_CACHE["schema"] = schema
        return schema
    cmd = [_cfg("NTN", NTN), "api", "/v1/data_sources/%s" % DS_PROJECT, "-X", "GET"]
    out = _cfg("_ntn_api_call", _ntn_api_call)(cmd, timeout=45)
    try:
        schema = json.loads(out)
    except ValueError:
        raise RuntimeError("项目管理 schema 返回的不是 JSON")
    if not isinstance(schema.get("properties"), dict):
        raise RuntimeError("项目管理 schema 缺少 properties")
    _PROJECT_SCHEMA_CACHE["at"] = now
    _PROJECT_SCHEMA_CACHE["schema"] = schema
    return schema


def _project_relation_kind(kind):
    """返回关系选择器允许查询的 Notion 数据源配置。"""
    cfg = {
        "task": {"data_source": DS_TASKS, "title": "任务名称", "label": "关联任务"},
        "record": {"data_source": DS_RECORDS, "title": "记录标题", "label": "来源记录"},
    }.get(str(kind or "").strip().lower())
    if not cfg:
        raise ValueError("关系类型必须是 task 或 record")
    return cfg


def _relation_option_from_page(page):
    """把 Notion 页面压缩成关系选择器需要的安全摘要。"""
    if not isinstance(page, dict):
        return None
    page_id = str(page.get("id") or "").strip().lower()
    if not page_id:
        return None
    title = ""
    status = ""
    date = ""
    for prop in (page.get("properties") or {}).values():
        if not isinstance(prop, dict):
            continue
        typ = prop.get("type")
        if typ == "title" and not title:
            title = "".join(str(x.get("plain_text") or "")
                            for x in (prop.get("title") or []))
        elif typ == "status" and not status:
            status = str((prop.get("status") or {}).get("name") or "")
        elif typ == "select" and not status:
            status = str((prop.get("select") or {}).get("name") or "")
        elif typ == "date" and not date:
            date = str((prop.get("date") or {}).get("start") or "")
    return {
        "id": page_id,
        "title": title.strip() or "未命名页面",
        "status": status,
        "date": date,
    }


def _project_relation_lookup(kind, ids):
    """按页面 ID 读取关系标签，并缓存五分钟避免重复请求。"""
    cfg = _project_relation_kind(kind)
    now = time.time()
    out = []
    for raw_id in ids or []:
        page_id = str(raw_id or "").strip().lower()
        if not _re.fullmatch(
                r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}",
                page_id):
            raise ValueError("%s 含无效页面 ID" % cfg["label"])
        cache_key = (kind, page_id)
        cached = _PROJECT_RELATION_CACHE.get(cache_key)
        if cached and now - float(cached.get("at") or 0) < _PROJECT_RELATION_CACHE_TTL:
            out.append(cached["item"])
            continue
        cmd = [_cfg("NTN", NTN), "api", "/v1/pages/%s" % page_id, "-X", "GET"]
        page = json.loads(_cfg("_ntn_api_call", _ntn_api_call)(cmd, timeout=45))
        item = _relation_option_from_page(page) or {
            "id": page_id, "title": "未命名页面", "status": "", "date": "",
        }
        _PROJECT_RELATION_CACHE[cache_key] = {"at": now, "item": item}
        out.append(item)
    return out


def _project_relation_search(kind, query="", limit=20):
    """按标题搜索 Notion 关系数据源；返回摘要而不是整页原始属性。"""
    cfg = _project_relation_kind(kind)
    q = str(query or "").strip()
    if len(q) > 100:
        raise ValueError("搜索词最多 100 字")
    try:
        size = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        size = 20
    body = {
        "page_size": size,
        "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}],
    }
    if q:
        body["filter"] = {
            "property": cfg["title"],
            "title": {"contains": q},
        }
    cmd = [_cfg("NTN", NTN), "api", "/v1/data_sources/%s/query" % cfg["data_source"],
           "-X", "POST"]
    page = json.loads(_cfg("_ntn_api_call", _ntn_api_call)(
        cmd, json.dumps(body, ensure_ascii=False), timeout=45))
    return [item for item in (
        _relation_option_from_page(row) for row in (page.get("results") or [])
    ) if item]


def _project_option_names(schema, prop_name, expected_type):
    prop = (schema.get("properties") or {}).get(prop_name)
    if not isinstance(prop, dict) or prop.get("type") != expected_type:
        raise ValueError("Notion 项目库缺少可写属性：%s" % prop_name)
    section = prop.get(expected_type) or {}
    return [str(o.get("name") or "") for o in (section.get("options") or [])
            if o.get("name")]


def _project_relation_ids(value, label):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("%s 必须是页面 ID 数组" % label)
    out = []
    for item in value:
        page_id = str(item or "").strip().lower()
        if not _re.fullmatch(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", page_id):
            raise ValueError("%s 含无效页面 ID" % label)
        out.append(page_id)
    return out


def _build_project_properties(fields, schema=None):
    """把项目白名单字段转换成 Notion page properties。

    只允许写 Notion 中真实存在且可编辑的属性。完成百分比、子任务数量、
    子级项目等 rollup/派生值永远不接受前端写入。
    """
    if not isinstance(fields, dict):
        raise ValueError("fields 必须是对象")
    unknown = sorted(set(fields) - _PROJECT_WRITABLE)
    if unknown:
        raise ValueError("不支持回写的字段：" + ", ".join(unknown))
    schema = schema or _notion_project_schema()
    props = schema.get("properties") or {}
    out = {}

    def require(name, typ):
        prop = props.get(name)
        if not isinstance(prop, dict) or prop.get("type") != typ:
            raise ValueError("Notion 项目库缺少可写属性：%s" % name)
        return prop

    if "name" in fields:
        require("项目名称", "title")
        name = str(fields.get("name") or "").strip()
        if not name:
            raise ValueError("项目名称不能为空")
        if len(name) > 200:
            raise ValueError("项目名称最多 200 字")
        out["项目名称"] = {"title": _rich_text_value(name)}

    select_fields = {
        "status": ("状态", "status"),
        "priority": ("优先级", "select"),
        "projectType": ("类型", "select"),
        "sprint": ("冲刺", "select"),
    }
    for key, (prop_name, typ) in select_fields.items():
        if key not in fields:
            continue
        value = str(fields.get(key) or "").strip()
        require(prop_name, typ)
        allowed = _project_option_names(schema, prop_name, typ)
        if value and value not in allowed:
            raise ValueError("%s 不是 Notion 中的有效选项：%s" % (prop_name, value))
        out[prop_name] = {typ: {"name": value} if value else None}

    if "tags" in fields:
        require("标签", "multi_select")
        raw = fields.get("tags")
        if raw is None:
            values = []
        elif isinstance(raw, list):
            values = [str(x or "").strip() for x in raw]
        else:
            raise ValueError("标签必须是数组")
        values = [x for x in values if x]
        if len(values) > 20:
            raise ValueError("标签最多 20 个")
        if any(len(x) > 100 for x in values):
            raise ValueError("单个标签最多 100 字")
        out["标签"] = {"multi_select": [{"name": x} for x in values]}

    date_fields = {
        "start": "开始日期",
        "due": "截止日期",
        "doneDate": "完成日期",
        "sprintStart": "冲刺开始日期",
        "sprintEnd": "冲刺结束日期",
    }
    for key, prop_name in date_fields.items():
        if key not in fields:
            continue
        require(prop_name, "date")
        value = str(fields.get(key) or "").strip()
        if value:
            try:
                datetime.date.fromisoformat(value)
            except ValueError:
                raise ValueError("%s 必须是 YYYY-MM-DD" % prop_name)
        out[prop_name] = {"date": {"start": value} if value else None}

    for key, prop_name in (("estHours", "预计工时"), ("actHours", "实际工时")):
        if key not in fields:
            continue
        require(prop_name, "number")
        raw = fields.get(key)
        if raw in (None, ""):
            value = None
        else:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError("%s 必须是数字" % prop_name)
            if not math.isfinite(value) or value < 0 or value > 100000:
                raise ValueError("%s 必须在 0–100000 之间" % prop_name)
        out[prop_name] = {"number": value}

    if "note" in fields:
        require("备注", "rich_text")
        out["备注"] = {"rich_text": _rich_text_value(fields.get("note"))}

    if "relatedLink" in fields:
        require("相关链接", "url")
        value = str(fields.get("relatedLink") or "").strip()
        if value and not _re.match(r"^https?://", value, _re.I):
            raise ValueError("相关链接必须是 http(s) URL")
        out["相关链接"] = {"url": value or None}

    if "parentNotionId" in fields:
        require("上级 项目", "relation")
        parent = str(fields.get("parentNotionId") or "").strip().lower()
        parent_ids = _project_relation_ids([parent] if parent else [], "上级项目")
        out["上级 项目"] = {"relation": [{"id": x} for x in parent_ids]}

    for key, prop_name in (("relatedTaskIds", "关联任务"),
                           ("sourceRecordIds", "来源记录"),
                           ("ownerIds", "负责人")):
        if key not in fields:
            continue
        expected = "people" if key == "ownerIds" else "relation"
        require(prop_name, expected)
        ids = _project_relation_ids(fields.get(key), prop_name)
        out[prop_name] = {expected: [{"id": x} for x in ids]}

    if not out:
        raise ValueError("没有可写入的字段")
    return out


def _build_focus_properties(session):
    """把一条已完成的本地专注记录映射到「今日番茄追踪表」白名单字段。

    只接受工作台自己生成的结构，不接受前端任意 Notion 属性。放弃/未完成的
    计时不会回写，避免把中断记录伪装成完成番茄。
    """
    if not isinstance(session, dict):
        raise ValueError("专注记录格式不正确")
    if session.get("status") != "completed":
        raise ValueError("只有已完成的专注记录可以回写")
    project = str(session.get("projectName") or "自由专注").strip()[:200]
    date = str(session.get("date") or "").strip()
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise ValueError("专注日期必须是 YYYY-MM-DD")
    try:
        minutes = int(round(float(session.get("elapsedSeconds") or 0) / 60))
    except (TypeError, ValueError):
        minutes = 0
    if minutes <= 0 or minutes > 240:
        raise ValueError("专注时长必须在 1–240 分钟之间")
    title = ("%s · %d 分钟专注" % (project, minutes))[:200]
    note = str(session.get("noteLine") or "").strip()
    if not note:
        note = "%s · %d 分钟 · 工作台专注记录" % (project, minutes)
    return {
        "任务名称": {"title": _rich_text_value(title)},
        "备注": {"rich_text": _rich_text_value(note[:2000])},
        "完成": {"checkbox": True},
        "实际番茄": {"number": 1},
        "干扰记录": {"rich_text": _rich_text_value("无")},
        "所属项目": {"rich_text": _rich_text_value(project)},
        "日期": {"date": {"start": date}},
        "状态": {"status": {"name": "完成"}},
        "预估番茄": {"number": 1},
    }


def _notion_create_focus_page(properties):
    """在 Notion「今日番茄追踪表」创建一条页面；凭据仍由 ntn 钥匙串读取。"""
    cmd = [_cfg("NTN", NTN), "api", "/v1/pages", "-X", "POST"]
    payload = {
        "parent": {"type": "data_source_id", "data_source_id": DS_FOCUS},
        "properties": properties,
    }
    out = _cfg("_ntn_api_call", _ntn_api_call)(
        cmd,
        json.dumps(payload, ensure_ascii=False),
        timeout=45,
    )
    return json.loads(out)


def _write_focus_update(session_id, notion_page):
    """Notion 创建成功后把页面标识写回对应本地专注记录。"""
    with _cfg("_data_write_lock"):
        data = _cfg("_read_current")()
        if not data:
            raise RuntimeError("data.json 不可读")
        session = next((s for s in (data.get("focusSessions") or [])
                        if str(s.get("id") or "") == session_id), None)
        if not session:
            raise LookupError("本地专注记录不存在")
        now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        session["notionPageId"] = notion_page.get("id")
        session["notionUrl"] = notion_page.get("url") or ""
        session["notionPushedAt"] = now
        meta = data.get("notionMeta")
        if isinstance(meta, dict):
            meta["focusDs"] = DS_FOCUS
            meta["focusDsName"] = "今日番茄追踪表"
            meta["lastFocusWritebackAt"] = now
        _cfg("_rotate_backup")()
        data_path = _cfg("DATA", DATA)
        tmp = "%s.tmp.%d" % (data_path, threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, data_path)
        return session


def _client_money_note(client):
    profit = client.get("profit")
    if isinstance(profit, (int, float)) and profit != 0:
        return "利润 ¥%s" % format(round(profit, 2), ",.2f").rstrip("0").rstrip(".")
    income = client.get("income")
    if isinstance(income, (int, float)) and income != 0:
        return "收入 ¥%s" % format(round(income, 2), ",.2f").rstrip("0").rstrip(".")
    return ""


def _write_client_update(page_id, fields):
    """Notion 写入成功后同步本地 data.json，页面不用等下一次全量同步。"""
    with _cfg("_data_write_lock"):
        data = _cfg("_read_current")()
        if not data:
            raise RuntimeError("data.json 不可读")
        client = next((c for c in (data.get("clients") or [])
                       if str(c.get("notionId") or "") == page_id), None)
        if not client:
            raise LookupError("本地客户缓存里找不到这个页面")
        for src, dst in _CLIENT_LOCAL_KEYS.items():
            if src == "note" or src not in fields:
                continue
            client[dst] = "" if fields.get(src) is None else str(fields.get(src))
        if "note" in fields:
            memo = "" if fields.get("note") is None else str(fields.get("note")).strip()
            client["memo"] = memo
            client["note"] = memo or _client_money_note(client)
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        client["updatedAt"] = now
        client["writable"] = True
        meta = data.get("notionMeta")
        if isinstance(meta, dict):
            meta["lastWritebackAt"] = now
        _cfg("_rotate_backup")()
        data_path = _cfg("DATA", DATA)
        tmp = "%s.tmp.%d" % (data_path, threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, data_path)
        return client


def _write_project_update(page_id, fields):
    """Notion 写入成功后同步本地项目卡，避免页面等下一次全量同步。"""
    with _cfg("_data_write_lock"):
        data = _cfg("_read_current")()
        if not data:
            raise RuntimeError("data.json 不可读")
        projects = data.get("projects") or []
        project = next((p for p in projects
                        if str(p.get("notionId") or "").lower() == page_id), None)
        if not project:
            raise LookupError("本地项目缓存里找不到这个页面")
        for src, dst in _PROJECT_LOCAL_KEYS.items():
            if src not in fields or src == "parentNotionId":
                continue
            value = fields.get(src)
            if src == "tags":
                project["tags"] = [str(x) for x in (value or []) if str(x or "").strip()]
            elif src in ("estHours", "actHours"):
                project[dst] = None if value in (None, "") else float(value)
            elif src == "doneDate":
                project[dst] = str(value or "").strip() or None
            elif src in ("ownerIds", "relatedTaskIds", "sourceRecordIds"):
                project[dst] = [str(x) for x in (value or []) if str(x or "").strip()]
            else:
                project[dst] = "" if value is None else str(value).strip()
        if "parentNotionId" in fields:
            parent_nid = str(fields.get("parentNotionId") or "").strip().lower()
            parent = next((p for p in projects
                           if str(p.get("notionId") or "").lower() == parent_nid), None) if parent_nid else None
            if parent_nid and not parent:
                raise LookupError("上级项目不在当前本地缓存里")
            project["parent"] = parent.get("id") if parent else None
            project["parentNotionId"] = parent_nid or None
        project["category"] = ((project.get("tags") or [None])[0]
                               or project.get("projectType") or "")
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        project["updatedAt"] = now
        project["writable"] = True
        meta = data.get("notionMeta")
        if isinstance(meta, dict):
            meta["lastProjectWritebackAt"] = now
        _cfg("_rotate_backup")()
        data_path = _cfg("DATA", DATA)
        tmp = "%s.tmp.%d" % (data_path, threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, data_path)
        return project


