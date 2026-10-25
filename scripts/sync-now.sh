#!/bin/bash
# 一键同步：Notion（经 ntn CLI + Surge）→ data.json → 离线兜底页
#
# 为什么不用 WorkBuddy MCP：
#   WorkBuddy 的 Electron 出口不读系统代理，直连 mcp.notion.com 延迟在
#   0.18s~12s 之间剧烈波动（探针成功率约 1/5），连接器反复掉线。
#   ntn 是 Rust/reqwest，尊重 HTTPS_PROXY，强制走 Surge 后稳定 0.28~0.9s。
#
# 用法：bash scripts/sync-now.sh
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${WB_PYTHON:-$(command -v python3)}"
cd "$DIR" || exit 1

echo "===== $(date '+%F %T') 开始同步 ====="

# 0. 前置检查：ntn 装了吗、Surge 通吗
NTN="${NTN_BIN:-/usr/local/bin/ntn}"
if [ ! -x "$NTN" ]; then
    echo "✗ 找不到 $NTN（Notion CLI）。"
    echo "  安装后用 NTN_BIN=/path/to/ntn 指定位置，或放 /usr/local/bin/ntn。"
    exit 2
fi
PROXY="${WB_PROXY:-http://127.0.0.1:7890}"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 8 -x "$PROXY" https://mcp.notion.com/mcp)
if [ "$CODE" != "401" ]; then
    echo "✗ 经代理 $PROXY 访问 Notion 返回 $CODE（期望 401）。"
    echo "  代理没通，先检查 Surge 是否在运行。"
    exit 3
fi
echo "✓ 前置检查通过（ntn 存在，代理通路 401）"

# 1. 只读拉取并写入 data.json（经本地同步服务，自动轮转备份 + 骤降保护）
if ! "$PY" scripts/notion-sync.py; then
    echo "✗ 同步失败，data.json 未被修改。"
    exit 4
fi

# 2. 重建派生快照（资讯 / 写作 / 复盘）。
#    ⚠️ 顺序：必须在 make-local.py **之前** —— 离线页会把 sections.json 整份内嵌，
#    先建页后建快照就会把上一版嵌进去（页面上看不出任何异常，只是数据旧了）。
#    为什么同步后要重建：写作区的「客户内容」读的是 data.json 里 category=客户服务 的项目，
#    刚同步完那份才是新的；不重建就会停在上一次的状态。
#    （2026-09-30 补：原来这里只有 make-local，sections.json 得靠人记得手跑。）
"$PY" scripts/sections.py --write || exit 6
# 只作信息展示，不判死：紧接 --write 之后，跨零点那一瞬可能刚好翻一天
# （days_since 按日历日算，跨零点全体 +1）—— 那种红不是缺陷，不该让整条同步失败。
"$PY" scripts/sections.py --check || true

# 3. 刷新自媒体只读快照。X 授权可用时读取公开账号与最近推文，失败时才回落历史报告；
#    知识星球继续读逐条导出。尚未接数据通道的公众号 / 小红书保持“未核验”，绝不写成 0。
"$PY" scripts/social-sync.py || exit 7

# 4. 重建离线兜底页，让 file:// 打开也是新数据
"$PY" scripts/web-monitor.py || echo "⚠ 网站监控刷新失败，保留上一份快照"
"$PY" scripts/make-local.py || exit 5
"$PY" scripts/make-local.py --check >/dev/null 2>&1
echo "✓ 离线兜底页已更新（含类型区与自媒体快照）"

echo "===== 同步完成 ====="
