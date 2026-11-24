#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""零外部依赖的 workbench.html 模块拼装器与 CSS 重复选择器门禁。

用法：
    python3 scripts/build-workbench.py              # 从 web/ 模块拼装生成 workbench.html
    python3 scripts/build-workbench.py --check      # 校验 workbench.html 与 web/ 源码是否严格同构
    python3 scripts/build-workbench.py --lint-css   # 扫描 web/styles/*.css 的重复选择器并校验门禁上限
    python3 scripts/build-workbench.py --lint-css --max-dup 230  # 自定义重复选择器门禁上限

设计原则：
  * 纯 Python 标准库，零 npm / Vite 构建负担
  * 交付产物仍是单文件 workbench.html，100% 兼容 file:// 离线兜底页与 4,582 行逻辑测试
  * 模块文件按文件名数字前缀自然排序，新增页面模块只需放入对应目录并重跑本脚本
"""
import argparse
import collections
import hashlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
SHELL = WEB_DIR / "shell.html"
OUT = ROOT / "workbench.html"
VERSION_FILE = ROOT / "VERSION"

# 当前同作用域（顶层 / 同 @media 断点内）CSS 重复选择器门禁：清零后严格锁定为 0
DEFAULT_MAX_DUP_SELECTORS = 0


def _sorted_parts(subdir: str, suffix: str) -> list[Path]:
    d = WEB_DIR / subdir
    if not d.is_dir():
        raise FileNotFoundError(f"缺少源码目录：{d}")
    files = sorted(p for p in d.iterdir() if p.is_file() and p.name.endswith(suffix))
    if not files:
        raise FileNotFoundError(f"目录 {d} 中没有 {suffix} 模块文件")
    return files


def assemble() -> str:
    shell = SHELL.read_text(encoding="utf-8")
    css_text = "".join(p.read_text(encoding="utf-8") for p in _sorted_parts("styles", ".css"))
    body_text = "".join(p.read_text(encoding="utf-8") for p in _sorted_parts("templates", ".html"))
    js_text = "".join(p.read_text(encoding="utf-8") for p in _sorted_parts("js", ".js"))

    for mark in ("<!-- @INCLUDE_CSS -->", "<!-- @INCLUDE_BODY -->", "<!-- @INCLUDE_JS -->"):
        if mark not in shell:
            raise ValueError(f"shell.html 缺少占位符：{mark}")

    version = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "dev"
    return (
        shell
        .replace("<!-- @INCLUDE_CSS -->", css_text, 1)
        .replace("<!-- @INCLUDE_BODY -->", body_text, 1)
        .replace("<!-- @INCLUDE_JS -->", js_text, 1)
        .replace("<!-- @VERSION -->", version)
    )


def _strip_css_comments(text: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", text)


def lint_css_duplicates() -> tuple[dict[str, list[tuple[str, int]]], int]:
    """作用域感知（media-aware）扫描 web/styles/*.css：只报同一作用域（顶层或同一 @media 断点）内的重复选择器冲突。"""
    occurrences: dict[str, list[tuple[str, int]]] = collections.defaultdict(list)
    for path in _sorted_parts("styles", ".css"):
        raw = path.read_text(encoding="utf-8")
        # 保留行号：把注释中的非换行字符替换为空格
        cleaned = re.sub(r"/\*[\s\S]*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), raw)
        depth = 0
        scope_stack: list[tuple[int, str]] = []
        for lineno, line in enumerate(cleaned.splitlines(), 1):
            s = line.strip()
            if not s:
                continue
            if s.startswith("@") and "{" in s:
                hdr = re.sub(r"\s+", " ", s.split("{", 1)[0].strip())
                scope_stack.append((depth, hdr))
                depth += s.count("{") - s.count("}")
                while scope_stack and depth <= scope_stack[-1][0]:
                    scope_stack.pop()
                continue
            cur_scope = " | ".join(h for _, h in scope_stack) if scope_stack else "TOP"
            if "{" in s and not s.startswith("@"):
                sel = re.sub(r"\s+", " ", s.split("{", 1)[0].strip())
                if sel and not sel.startswith("@") and not re.match(r"^(from|to|\d+%)$", sel):
                    key = sel if cur_scope == "TOP" else f"[{cur_scope}] {sel}"
                    occurrences[key].append((path.name, lineno))
            depth += s.count("{") - s.count("}")
            while scope_stack and depth <= scope_stack[-1][0]:
                scope_stack.pop()

    dups = {k: v for k, v in occurrences.items() if len(v) >= 2}
    return dups, len(dups)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="拼装 workbench.html 与 CSS 门禁检查")
    parser.add_argument("--check", action="store_true", help="检查 workbench.html 是否与 web/ 源码一致")
    parser.add_argument("--lint-css", action="store_true", help="检查 web/styles/*.css 重复选择器数量")
    parser.add_argument("--max-dup", type=int, default=DEFAULT_MAX_DUP_SELECTORS,
                        help=f"允许的最大重复选择器数（默认 {DEFAULT_MAX_DUP_SELECTORS}）")
    parser.add_argument("--top", type=int, default=15, help="--lint-css 展示前 N 个高频重复选择器")
    args = parser.parse_args(argv)

    if args.lint_css:
        dups, count = lint_css_duplicates()
        ranked = sorted(dups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        print(f"CSS 重复选择器统计：{count} 个（门禁上限：{args.max_dup}）")
        for sel, locs in ranked[:args.top]:
            loc_str = ", ".join(f"{f}:{ln}" for f, ln in locs)
            print(f"  {len(locs)}x  {sel[:64]:64s}  [{loc_str}]")
        if count > args.max_dup:
            print(f"✗ CSS 重复选择器超标：{count} > {args.max_dup}（请合并已有规则而非继续叠加覆盖）",
                  file=sys.stderr)
            return 3
        print("✓ CSS 重复选择器门禁通过")
        if not args.check:
            return 0

    built = assemble()
    built_sha = hashlib.sha256(built.encode("utf-8")).hexdigest()

    if args.check:
        if not OUT.exists():
            print("✗ 缺少 workbench.html，请先跑 python3 scripts/build-workbench.py", file=sys.stderr)
            return 2
        cur = OUT.read_text(encoding="utf-8")
        cur_sha = hashlib.sha256(cur.encode("utf-8")).hexdigest()
        if cur_sha != built_sha:
            print(
                f"✗ workbench.html 与 web/ 源码不同步！\n"
                f"  当前 SHA: {cur_sha[:16]}\n"
                f"  源码 SHA: {built_sha[:16]}\n"
                f"  请运行：python3 scripts/build-workbench.py",
                file=sys.stderr,
            )
            return 2
        print(f"✓ workbench.html 与 web/ 源码严格同步（SHA-256: {cur_sha[:16]}，{len(built.splitlines())} 行）")
        return 0

    tmp = OUT.with_suffix(".html.tmp")
    tmp.write_text(built, encoding="utf-8")
    os.replace(tmp, OUT)
    print(f"✓ 已拼装 {OUT.name}（{len(built.splitlines())} 行，{len(built.encode('utf-8')):,} bytes，SHA-256: {built_sha[:16]}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
