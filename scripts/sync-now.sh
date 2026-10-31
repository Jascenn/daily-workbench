#!/bin/bash
# 一键同步：Notion（经 ntn 直连）→ data.json → 离线兜底页
#
# 用法：bash scripts/sync-now.sh
set -uo pipefail

DIR="/Users/jascen/Development/daily-workbench"
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

# 1. 只读拉取并写入 data.json（经本地同步服务，自动轮转备份 + 骤降保护）
STEP_STARTED=$(clock_now)
if ! "$PY" scripts/sync/notion-sync.py; then
    echo "✗ 同步失败，data.json 未被修改。"
    emit_step "Notion 拉取" "$STEP_STARTED"
    exit 4
fi
emit_step "Notion 拉取" "$STEP_STARTED"

# 2. 重建派生快照（资讯 / 写作 / 复盘）。
#    ⚠️ 顺序：必须在 make-local.py **之前** —— 离线页会把 sections.json 整份内嵌，
#    先建页后建快照就会把上一版嵌进去（页面上看不出任何异常，只是数据旧了）。
#    为什么同步后要重建：写作区的「客户内容」读的是 data.json 里 category=客户服务 的项目，
#    刚同步完那份才是新的；不重建就会停在上一次的状态。
#    （2026-09-30 补：原来这里只有 make-local，sections.json 得靠人记得手跑。）
STEP_STARTED=$(clock_now)
if ! "$PY" scripts/sync/sections.py --write; then
    emit_step "资讯快照" "$STEP_STARTED"
    exit 6
fi
# 只作信息展示，不判死：紧接 --write 之后，跨零点那一瞬可能刚好翻一天
# （days_since 按日历日算，跨零点全体 +1）—— 那种红不是缺陷，不该让整条同步失败。
"$PY" scripts/sync/sections.py --check || true
emit_step "资讯快照" "$STEP_STARTED"

# 3. 刷新自媒体只读快照。X 授权可用时读取公开账号与最近推文，失败时才回落历史报告；
#    知识星球继续读逐条导出。尚未接数据通道的公众号 / 小红书保持“未核验”，绝不写成 0。
STEP_STARTED=$(clock_now)
if ! "$PY" scripts/sync/social-sync.py; then
    emit_step "自媒体快照" "$STEP_STARTED"
    exit 7
fi
emit_step "自媒体快照" "$STEP_STARTED"

# 4. 重建离线兜底页，让 file:// 打开也是新数据
STEP_STARTED=$(clock_now)
"$PY" scripts/sync/web-monitor.py || echo "⚠ 网站监控刷新失败，保留上一份快照"
emit_step "网站监控" "$STEP_STARTED"
STEP_STARTED=$(clock_now)
if ! "$PY" scripts/sync/make-local.py; then
    emit_step "离线兜底页" "$STEP_STARTED"
    exit 5
fi
"$PY" scripts/sync/make-local.py --check >/dev/null 2>&1
emit_step "离线兜底页" "$STEP_STARTED"
echo "✓ 离线兜底页已更新（含类型区与自媒体快照）"

echo "===== 同步完成 ====="
