#!/usr/bin/env python3
"""把当前 data.json 内嵌进 workbench.html，生成不依赖服务的离线兜底版。

为什么需要它：内置预览连不到沙箱里的 127.0.0.1:8787，file:// 直接打开在线版
会退回空 localStorage 而显示空白。离线版把真实数据种进 localStorage，任何
打开方式都能看到数据。

**三份种子**：
    #wb-seed            记账：data.json          -> localStorage["wb"]
    #wb-sections-seed   类型区：sections.json    -> localStorage["wb_sections"]
    #wb-social-seed     自媒体：social.json      -> localStorage["wb_social"]
    #wb-web-seed        网站：web-presence.json -> localStorage["wb_web_presence"]
分三个 localStorage 键而不是合成一个：来源与更新节奏都不同。
⚠️ 三份种子都要参与 --check 比对，否则漏掉一份就会把过期离线页当成最新。

用法：
    python3 scripts/make-local.py            # 用 data.json + sections.json 重新生成
    python3 scripts/make-local.py --check    # 只比对，不写文件

约定：workbench.html 是唯一模板，本脚本只做"注入数据"这一件事，绝不改模板。
每次 data.json / sections.json 变化后应重跑本脚本（每日自动化会调用）。
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(DIR, "workbench.html")
DATA = os.path.join(DIR, "data.json")
SECTIONS = os.path.join(DIR, "sections.json")
SOCIAL = os.path.join(DIR, "social.json")
WEB_PRESENCE = os.path.join(DIR, "web-presence.json")
OUT = os.path.join(DIR, "workbench.local.html")
BACKUP_DIR = os.path.join(DIR, "backups")

SEED_MARK = 'id="wb-seed"'
SEED_TMPL = (
    '<script id="wb-seed">/* 离线种子：内嵌 data.json 快照，由 scripts/make-local.py 生成 */\n'
    'try{localStorage.setItem("wb",%s);}catch(e){}\n'
    '</script>\n'
)
SEC_MARK = 'id="wb-sections-seed"'
SEC_TMPL = (
    '<script id="wb-sections-seed">/* 离线种子：内嵌 sections.json 快照（资讯/写作/复盘），同由 make-local.py 生成 */\n'
    'try{localStorage.setItem("wb_sections",%s);}catch(e){}\n'
    '</script>\n'
)
SOC_MARK = 'id="wb-social-seed"'
SOC_TMPL = (
    '<script id="wb-social-seed">/* 离线种子：内嵌 social.json 自媒体只读快照 */\n'
    'try{localStorage.setItem("wb_social",%s);}catch(e){}\n'
    '</script>\n'
)
WEB_MARK = 'id="wb-web-seed"'
WEB_TMPL = (
    '<script id="wb-web-seed">/* 离线种子：网站健康与博客更新快照 */\n'
    'try{localStorage.setItem("wb_web_presence",%s);}catch(e){}\n'
    '</script>\n'
)


def _grab(html, sid, key):
    m = re.search(r'<script id="%s">.*?localStorage\.setItem\("%s",(.*?)\);' % (sid, key), html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def embedded_snapshot(html):
    """取出离线文件里已内嵌的那份记账数据（用于 --check 与避免重复注入）。"""
    return _grab(html, "wb-seed", "wb")


def embedded_sections(html):
    """取出离线文件里已内嵌的那份类型区数据。见文件头：漏掉它就会把过期页当最新。"""
    return _grab(html, "wb-sections-seed", "wb_sections")


def embedded_social(html):
    return _grab(html, "wb-social-seed", "wb_social")


def embedded_web(html):
    return _grab(html, "wb-web-seed", "wb_web_presence")


def strip_seed(html):
    html = re.sub(r'<script id="wb-seed">.*?</script>\n?', '', html, flags=re.S)
    html = re.sub(r'<script id="wb-sections-seed">.*?</script>\n?', '', html, flags=re.S)
    html = re.sub(r'<script id="wb-social-seed">.*?</script>\n?', '', html, flags=re.S)
    return re.sub(r'<script id="wb-web-seed">.*?</script>\n?', '', html, flags=re.S)


def build(template, data, sections, social, web):
    body = strip_seed(template)
    seed = SEED_TMPL % json.dumps(data, ensure_ascii=False)
    if sections is not None:
        seed += SEC_TMPL % json.dumps(sections, ensure_ascii=False)
    if social is not None:
        seed += SOC_TMPL % json.dumps(social, ensure_ascii=False)
    if web is not None:
        seed += WEB_TMPL % json.dumps(web, ensure_ascii=False)
    m = re.search(r'<body[^>]*>\s*\n?', body)
    if not m:
        raise SystemExit("模板里找不到 <body>，无法注入")
    return body[:m.end()] + seed + body[m.end():]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对，不写文件")
    ap.add_argument("--no-sections", action="store_true", help="不注入类型区种子（调试用）")
    ap.add_argument("--no-social", action="store_true", help="不注入自媒体种子（调试用）")
    ap.add_argument("--no-web", action="store_true", help="不注入网站监控种子（调试用）")
    args = ap.parse_args()

    for p in (SRC, DATA):
        if not os.path.exists(p):
            raise SystemExit("缺少文件：%s" % p)

    with open(SRC, encoding="utf-8") as f:
        template = f.read()
    with open(DATA, encoding="utf-8") as f:
        data = json.load(f)
    sections = None
    if not args.no_sections:
        if not os.path.exists(SECTIONS):
            raise SystemExit("缺少 %s —— 先跑 python3 scripts/sections.py --write" % SECTIONS)
        with open(SECTIONS, encoding="utf-8") as f:
            sections = json.load(f)
    social = None
    if not args.no_social:
        if not os.path.exists(SOCIAL):
            raise SystemExit("缺少 %s —— 先跑 python3 scripts/social-sync.py" % SOCIAL)
        with open(SOCIAL, encoding="utf-8") as f:
            social = json.load(f)
    web = None
    if not args.no_web:
        if not os.path.exists(WEB_PRESENCE):
            raise SystemExit("缺少 %s —— 先跑 python3 scripts/web-monitor.py" % WEB_PRESENCE)
        with open(WEB_PRESENCE, encoding="utf-8") as f:
            web = json.load(f)

    out = build(template, data, sections, social, web)

    if args.check:
        if not os.path.exists(OUT):
            print("离线版不存在，需要生成")
            return 1
        with open(OUT, encoding="utf-8") as f:
            current = f.read()
        if current == out:
            print("已是最新（%d 条收益 / %d 个项目 / %d 个类型区 / %d 条自媒体内容）" % (
                len(data.get("earnings", [])), len(data.get("projects", [])),
                len((sections or {}).get("sections", [])), len((social or {}).get("posts", []))))
            return 0
        # 差异定位：**条数相同也会过期** —— lastNotionSync 一变整份产物就不同，
        # 模板（workbench.html）被改动同理。原来这里只打印条数，于是
        # "内嵌 257 条 / data.json 257 条"看起来像不该重建，实际必须重建
        # （2026-09-29 因此把过期离线页当成了最新，退出码 2 被 `| tail` 吃掉更隐蔽）。
        # 三份种子必须分别报，否则"哪一份过期"看不出来。
        diff = []
        now_d, now_s, now_social = embedded_snapshot(current), embedded_sections(current), embedded_social(current)
        now_web = embedded_web(current)
        if now_d != data:
            if isinstance(now_d, dict):
                keys = [k for k in sorted(set(now_d) | set(data)) if now_d.get(k) != data.get(k)]
                diff.append("记账数据差在 %s" % "、".join(keys))
            else:
                diff.append("离线版里找不到记账数据快照")
        if sections is not None and now_s != sections:
            if isinstance(now_s, dict):
                old_ids = {s.get("id") for s in (now_s.get("sections") or [])}
                new_ids = {s.get("id") for s in sections.get("sections", [])}
                if old_ids != new_ids:
                    diff.append("类型区集合变了（%s → %s）" % (
                        "、".join(sorted(old_ids)) or "空", "、".join(sorted(new_ids))))
                else:
                    diff.append("类型区数据有更新（生成时间 %s → %s）" % (
                        now_s.get("generatedAt"), sections.get("generatedAt")))
            else:
                diff.append("离线版里找不到类型区快照")
        if social is not None and now_social != social:
            if isinstance(now_social, dict):
                diff.append("自媒体数据有更新（生成时间 %s → %s）" % (
                    now_social.get("generatedAt"), social.get("generatedAt")))
            else:
                diff.append("离线版里找不到自媒体快照")
        if web is not None and now_web != web:
            if isinstance(now_web, dict):
                diff.append("网站监控有更新（检测时间 %s → %s）" % (
                    now_web.get("generatedAt"), web.get("generatedAt")))
            else:
                diff.append("离线版里找不到网站监控快照")
        if diff:
            print("需重新生成：" + "；".join(diff))
        else:
            print("需重新生成：数据一致，差在模板 —— workbench.html 已被改动")
        return 2

    if os.path.exists(OUT):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        dst = os.path.join(BACKUP_DIR, "workbench.local.html.%s.bak" % time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(OUT, dst)
        print("旧版已备份 -> %s" % os.path.relpath(dst, DIR))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print("已生成 %s（内嵌 %d 条收益 / %d 个项目 / %d 个类型区 / %d 条自媒体内容，lastNotionSync=%s）" % (
        os.path.relpath(OUT, DIR), len(data.get("earnings", [])), len(data.get("projects", [])),
        len((sections or {}).get("sections", [])), len((social or {}).get("posts", [])), data.get("lastNotionSync")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
