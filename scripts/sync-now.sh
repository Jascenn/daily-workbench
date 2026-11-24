#!/bin/bash
# 一键全量同步：Notion、类型区、自媒体、微信、网站 → 离线兜底页
#
# 用法：bash scripts/sync-now.sh
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="/opt/homebrew/bin/python3"
cd "$DIR" || exit 1

echo "===== $(date '+%F %T') 开始同步 ====="

# 分阶段计时：页面只显示总耗时，网络慢时看不出到底卡在哪一步。
# 用 Python 取小数秒（macOS 自带 bash 3.2 没有 EPOCHREALTIME），并输出
# 一行机器可读标记，由 app.py 合并到同步日志；人读输出仍保持原样。
clock_now() {
    "$PY" -c 'import time; print(f"{time.time():.3f}")'
}
emit_step() {
    local name="$1" started="$2" ended
    ended=$(clock_now) || return 0
    "$PY" -c 'import sys; print("__WB_STEP__\t%s\t%.3f" % (sys.argv[1], max(0.0, float(sys.argv[3]) - float(sys.argv[2]))))' \
        "$name" "$started" "$ended"
}

# 0. 前置检查：ntn 装了吗、Notion Public API 是否可达
STEP_STARTED=$(clock_now)
if [ ! -x /usr/local/bin/ntn ]; then
    echo "✗ 找不到 /usr/local/bin/ntn（Notion CLI）。"
    echo "  安装：brew install notion-cli  （或见 https://ntn.dev）"
    emit_step "前置检查" "$STEP_STARTED"
    exit 2
fi
CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 8 https://mcp.notion.com/mcp)
if [ "$CODE" != "401" ]; then
    echo "✗ 直连 Notion 返回 $CODE（期望 401）。"
    echo "  先检查网络连通性；脚本不会强行使用某个本机代理端口。"
    emit_step "前置检查" "$STEP_STARTED"
    exit 3
fi
emit_step "前置检查" "$STEP_STARTED"
echo "✓ 前置检查通过（ntn 存在，Notion 通路 401）"

# 1-3. 按资源互斥分组并行：
#   * 组 1（Notion 主链）：Notion 拉取 → 资讯快照（写作区依赖最新 data.json，因此组内串行）
#   * 组 2（本机浏览器/MCP 互斥组）：自媒体快照 → 微信快照（实测争用本机资源，组内严格串行）
#   * 组 3 / 组 4（纯轻量网络 I/O）：网站监控、选题雷达各自独立并行
# 各组输出先写进独立临时日志，汇合后再按固定顺序回放到 stdout，既避免日志穿插，
# 又保持 __WB_STEP__ / __WB_STEP_FAIL__ 顺序与 app.py 同步日志展示完全一致。
RUN_TMP=$(mktemp -d "${TMPDIR:-/tmp}/wb-sync.XXXXXX")
trap 'rm -rf "$RUN_TMP"' EXIT

OPTIONAL_FAILED=0
run_optional() {
    local name="$1" script="$2"
    local started rc
    started=$(clock_now)
    echo "[$name] 开始"
    rc=0
    "$PY" "$script" || rc=$?
    emit_step "$name" "$started"
    if [ "$rc" -ne 0 ]; then
        # 失败必须让 app.py 看见：可选步骤不再用退出码中断整条链路，
        # 若不显式标记，同步日志会把"失败"渲染成 ✔（2026-11-21 修）。
        printf '__WB_STEP_FAIL__\t%s\n' "$name"
        echo "⚠ $name 失败（exit $rc），保留上一份快照"
        touch "$RUN_TMP/opt_failed"
    else
        echo "✓ $name 完成"
    fi
}

# 组 1：Notion 只读拉取 → 重建派生快照（sections.json 必须在 make-local 之前）
(
    STEP_STARTED=$(clock_now)
    if ! "$PY" scripts/sync/notion-sync.py; then
        echo "✗ 同步失败，data.json 未被修改。"
        emit_step "Notion 拉取" "$STEP_STARTED"
        echo 4 > "$RUN_TMP/core_rc"
        exit 0
    fi
    emit_step "Notion 拉取" "$STEP_STARTED"

    STEP_STARTED=$(clock_now)
    if ! "$PY" scripts/sync/sections.py --write; then
        emit_step "资讯快照" "$STEP_STARTED"
        echo 6 > "$RUN_TMP/core_rc"
        exit 0
    fi
    # 只作信息展示，不判死：跨零点那一瞬可能刚好翻一天，不该让整条同步失败。
    "$PY" scripts/sync/sections.py --check || true
    emit_step "资讯快照" "$STEP_STARTED"
    echo 0 > "$RUN_TMP/core_rc"
) > "$RUN_TMP/1_core.log" 2>&1 &
PID_CORE=$!

# 组 2：自媒体快照 → 微信快照（组内串行防争用，但与 Notion/网站/雷达并发）
(
    run_optional "自媒体快照" "scripts/sync/social-sync.py"
    run_optional "微信快照" "scripts/sync/wechat-sync.py"
) > "$RUN_TMP/2_local.log" 2>&1 &
PID_LOCAL=$!

# 组 3：网站监控
(
    run_optional "网站监控" "scripts/sync/web-monitor.py"
) > "$RUN_TMP/3_web.log" 2>&1 &
PID_WEB=$!

# 组 4：选题雷达
(
    run_optional "选题雷达" "scripts/sync/radar-sync.py"
) > "$RUN_TMP/4_radar.log" 2>&1 &
PID_RADAR=$!

wait "$PID_CORE" "$PID_LOCAL" "$PID_WEB" "$PID_RADAR" || true

CORE_RC=$(cat "$RUN_TMP/core_rc" 2>/dev/null || echo 4)
cat "$RUN_TMP/1_core.log"
if [ "$CORE_RC" -ne 0 ]; then
    exit "$CORE_RC"
fi
cat "$RUN_TMP/2_local.log" "$RUN_TMP/3_web.log" "$RUN_TMP/4_radar.log"
[ -f "$RUN_TMP/opt_failed" ] && OPTIONAL_FAILED=1

# 4. 重建离线兜底页，让 file:// 打开也是新数据
STEP_STARTED=$(clock_now)
if ! "$PY" scripts/sync/make-local.py; then
    emit_step "离线兜底页" "$STEP_STARTED"
    exit 5
fi
CHECK_OUTPUT=""
if ! CHECK_OUTPUT=$("$PY" scripts/sync/make-local.py --check 2>&1); then
    echo "✗ 离线兜底页自检失败：$CHECK_OUTPUT"
    emit_step "离线兜底页" "$STEP_STARTED"
    exit 5
fi
emit_step "离线兜底页" "$STEP_STARTED"
if [ "$OPTIONAL_FAILED" -ne 0 ]; then
    echo "⚠ 离线兜底页已更新，但有可选快照失败（旧数据已保留）"
else
    echo "✓ 离线兜底页已更新（含类型区与自媒体快照）"
fi

echo "===== 同步完成 ====="
exit 0
