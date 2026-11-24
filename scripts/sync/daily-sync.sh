#!/bin/bash
# 每天一次的自媒体 + 资讯快照刷新。
#
# 为什么不直接跑 sync-now.sh：那条链路还包含 Notion 拉取（2 分钟起）、微信快照、
# 网站监控；这些各有自己的节奏（微信一天三次、网站每 5 分钟）。每天只需要把
# 「变化慢」的两块补齐：自媒体（X/公众号/星球/小红书）与资讯（RSS 快照）。
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
PY=${PYTHON:-/opt/homebrew/bin/python3}
[ -x "$PY" ] || PY=/usr/bin/python3
FAILED=0

run() {
    local name="$1"; shift
    local started rc
    started=$(date +%s)
    if "$@" ; then
        echo "✓ $name ($(( $(date +%s) - started ))s)"
    else
        rc=$?
        echo "✗ $name 失败 (exit $rc)，保留上一份快照"
        FAILED=1
    fi
}

# 两个快照互不依赖，失败各自保留旧文件
run "自媒体快照" "$PY" scripts/sync/social-sync.py
# 订阅源必须先把内容抓下来：服务端的 stale 兜底只管"从没抓过/报错"的源，
# 对"有旧条目但很久没更新"的源不动作（2026-11-22 发现订阅库停在 10-31）。
run "订阅刷新" "$PY" scripts/sync/feeds-refresh.py
run "资讯快照" "$PY" scripts/sync/sections.py --write
# 知识星球 24h 日报 + 7 天周报（只读扫描，失败保留上一份快照）
run "星球简报" "$PY" scripts/sync/zsxq-daily-sync.py
# 离线页内嵌所有快照（含星球简报），最后重建一次，保证 file:// 打开也是新的
run "离线兜底页" "$PY" scripts/sync/make-local.py
exit "$FAILED"
