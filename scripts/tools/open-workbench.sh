#!/bin/bash
# 打印（并可自动打开）工作台的正确访问链接。
#
# 为什么需要它：本地同步服务对 /data.json 和 /api/sync 都要求 token。
# 裸开 http://127.0.0.1:8787/workbench.html 会让页面**静默退回浏览器缓存**，
# 并且点「同步一次」只会得到 HTTP 401 —— 2026-09-29 实际踩过这个坑。
#
# 用法：
#     bash scripts/tools/open-workbench.sh          # 只打印链接
#     bash scripts/tools/open-workbench.sh --open   # 打印并用默认浏览器打开
#     bash scripts/tools/open-workbench.sh --qrcode # 打印链接 + 终端二维码（手机同局域网可扫）

set -u
DIR="/Users/jascen/Development/daily-workbench"
TOKEN_FILE="$HOME/.config/daily-workbench/sync-token"
PORT=8787

if [ ! -f "$TOKEN_FILE" ]; then
    echo "✗ 找不到同步令牌：$TOKEN_FILE"
    echo "  服务首次启动时会自动生成；也可先跑 scripts/tools/setup-syncserver.sh。"
    exit 2
fi
TOKEN=$(cat "$TOKEN_FILE")

URL="http://127.0.0.1:${PORT}/workbench.html?token=${TOKEN}"

echo
echo "工作台链接（复制到浏览器打开）："
echo
echo "  $URL"
echo

# 服务是否在跑
if curl -s -o /dev/null -m 3 "http://127.0.0.1:${PORT}/api/status"; then
    echo "✓ 本地服务在运行（127.0.0.1:${PORT}）"
else
    echo "⚠ 本地服务没响应。先在 Terminal 跑一次："
    echo "    bash \"$DIR/scripts/tools/setup-syncserver.sh\""
fi

# 顺带自检：带 token 应该 200，裸链接应该 401
code_ok=$(curl -s -o /dev/null -w "%{http_code}" -m 5 "$URL")
code_bare=$(curl -s -o /dev/null -w "%{http_code}" -m 5 "http://127.0.0.1:${PORT}/data.json")
# 注意：变量后面紧跟全角字符时必须写成 ${var}。UTF-8 locale 下 bash 会把全角字符
# 也算进变量名，`$code_ok（` 会被解析成变量 `code_ok（`，set -u 下直接报未绑定。
echo "  自检：带 token 打开页面 -> ${code_ok}（期望 200）；裸链接读数据 -> ${code_bare}（期望 401，说明令牌确实在生效）"
echo
echo "提示：本浏览器成功打开过一次带令牌的链接后，会把它记在 localStorage，"
echo "      之后即使打开裸链接也能正常读取数据与同步。"

case "${1:-}" in
    --open)
        echo
        echo "正在用默认浏览器打开…"
        open "$URL"
        ;;
    --qrcode)
        echo
        if command -v qrencode >/dev/null 2>&1; then
            qrencode -t ANSIUTF8 "$URL"
        else
            echo "（未装 qrencode，跳过二维码：brew install qrencode）"
        fi
        ;;
    ""|--help|-h)
        [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ] && {
            echo "用法：bash scripts/tools/open-workbench.sh [--open|--qrcode]"
        }
        ;;
    *)
        echo "未知参数：${1}（可用：--open / --qrcode）"
        exit 1
        ;;
esac
