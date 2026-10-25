#!/bin/bash
# 【诊断用，非关键路径】诊断 Notion 连接器（若你通过第三方桌面 App 的 MCP 连接器走 Notion）为什么不通。
#
# ⚠️ 2026-09-29 起，工作台的 Notion 同步已改走 `ntn` CLI + Surge（scripts/sync-now.sh），
#    **不再依赖这个连接器**。本脚本的作用降级为「了解 WorkBuddy 连接器自身状态」，
#    它掉线不影响任何数据同步。日常要同步请直接跑：bash scripts/sync-now.sh
#
# 用法：bash scripts/check-notion-connector.sh
#
# 判读规则（详见 Obsidian 复盘笔记）：
#   state=ready        凭据有效、工具已注册
#   state=unauthorized 凭据失效 → 重新授权
#   state=error        看 error= 后面是什么：
#                        fetch failed → 网络层，WorkBuddy 直连 mcp.notion.com 太慢
#                        other        → 贴日志给我看
#
# 已决定不开 Surge 增强模式（TUN）：代价是全机网络行为变更，收益只是修好一条不再使用的通道。

set -u
LOG_DIR="$HOME/.workbuddy/logs/$(date +%Y-%m-%d)"
LOG=$(ls "$LOG_DIR"/workbuddyMainThread__*.log 2>/dev/null | head -1)

echo "=========================================="
echo " 1. Notion 连接器运行时状态"
echo "=========================================="
if [ -z "$LOG" ]; then
    echo "✗ 找不到今天的日志（$LOG_DIR）"
else
    grep -o "id=connector:notion state=[a-z]*" "$LOG" 2>/dev/null | sort -u \
        || echo "（今天没有 notion 相关记录 → 连接器没被加载过）"
    echo
    echo "最后一条状态："
    LAST=$(grep "MCP-ControllerState" "$LOG" 2>/dev/null | grep notion | tail -1)
    echo "$LAST" | cut -c1-190

    # 关键判据：ready 之前摔了几次。只看"最后是 ready"会漏掉间歇性故障 ——
    # 2026-09-29 实测 4 次 fetch failed 后第 5 次才 ready（成功率 ~20%），
    # 这种 ready 是碰运气掷中的，网络一抖就复发。
    FAILS=$(grep "MCP-ControllerState" "$LOG" 2>/dev/null | grep -c "connector:notion state=error")
    OKS=$(grep "MCP-ControllerState" "$LOG" 2>/dev/null | grep -c "connector:notion state=ready")
    UNAUTH=$(grep "MCP-ControllerState" "$LOG" 2>/dev/null | grep -c "connector:notion state=unauthorized")
    echo
    echo "本轮累计：ready=$OKS  error=$FAILS  unauthorized=$UNAUTH"
    case "$LAST" in
        *state=ready*)
            if [ "$FAILS" -gt 0 ]; then
                echo "→ ⚠ 终于连上了，但之前摔过 $FAILS 次 —— 属碰运气成功，随时会复发。"
                echo "  不过：工作台同步已改走 ntn + Surge，【这个连接器已不在关键路径上】，"
                echo "  它掉线不影响数据。要手动同步跑：bash scripts/sync-now.sh"
            else
                echo "→ ✓ 一次成功，凭据与链路都正常。"
            fi ;;
        *state=unauthorized*)
            echo "→ ✗ 凭据失效/过期 → 去连接器管理里重新授权，别查网络。"
            echo "  （同样地，这个连接器已不在关键路径上，不影响工作台数据同步。）" ;;
        *fetch\ failed*)
            echo "→ ✗ 网络层：WorkBuddy 直连 mcp.notion.com 太慢，撞上约 10s 拉取超时。"
            echo "  直连失败但下面第 2 段若能拿到 401 → 通路本身没问题，就是慢。"
            echo "  ★ 已决定【不开 Surge 增强模式（TUN）】—— 因为同步已不再走这个连接器。"
            echo "  ★ 正确做法：别管它，直接用 bash scripts/sync-now.sh 同步（走 ntn + Surge）。" ;;
        *state=starting*)
            echo "→ 还在连，稍等再跑一次这个脚本。" ;;
    esac
    echo
    echo "日志最后写入时间：$(stat -f '%Sm' "$LOG")  （现在是 $(date '+%b %d %H:%M:%S')）"
fi

echo
echo "=========================================="
echo " 2. 出口网络：直连 vs 走 Surge"
echo "=========================================="
printf "  直连(绕过代理)   "
curl -s -o /dev/null -w "%{http_code}  %{time_total}s\n" -m 12 --noproxy '*' https://mcp.notion.com/mcp
printf "  走 Surge 7890    "
curl -s -o /dev/null -w "%{http_code}  %{time_total}s\n" -m 12 -x http://127.0.0.1:7890 https://mcp.notion.com/mcp
echo "  （401 = 未认证的正常响应，能返回 401 就说明通路没问题；000 = 超时）"
echo "  （判定：直连 total 明显大于 1s → 就是 WorkBuddy 被这个拖垮）"

echo
echo "=========================================="
echo " 3. WorkBuddy 是否走了 Surge"
echo "=========================================="
PID=""
# 主进程 PID 直接从日志里取最可靠（日志每行都带 [pid=NNNN]，且新进程会往同一个
# MainThread 日志追加，所以"最后一行"就是当前主进程）。
# 用 pgrep 匹配 "MacOS/Electron" 会误抓到 sidecar-entry.js，不能这么写。
if [ -n "$LOG" ]; then
    PID=$(grep -o 'pid=[0-9]*' "$LOG" 2>/dev/null | tail -1 | cut -d= -f2)
fi
# 兜底：日志里取到的 PID 若已不在运行（刚重启、新进程还没写日志），
# 退回用 ps 找真正的主进程（排除 sidecar / helper）。
if [ -n "$PID" ] && ! lsof -nP -a -p "$PID" -iTCP >/dev/null 2>&1; then
    ALT=$(ps -Ao pid,command 2>/dev/null \
          | grep "WorkBuddy.app/Contents/MacOS/WorkBuddy" \
          | grep -v "sidecar\|helper\|grep" | awk '{print $1}' | head -1)
    if [ -n "$ALT" ]; then
        echo "  （日志里的 pid=$PID 已不在运行，改用 ps 找到的 pid=$ALT）"
        PID="$ALT"
    fi
fi
if [ -z "$PID" ]; then
    echo "（取不到 WorkBuddy 主进程 PID，跳过）"
elif ! lsof -nP -a -p "$PID" -iTCP >/dev/null 2>&1; then
    echo "（主进程 pid=$PID 已不在运行）"
else
    N=$(lsof -nP -a -p "$PID" -iTCP 2>/dev/null | grep -c ESTABLISHED)
    S=$(lsof -nP -a -p "$PID" -iTCP 2>/dev/null | grep -c "127.0.0.1:7890")
    echo "  WorkBuddy 主进程 pid=$PID"
    echo "    已建立连接 = $N    其中走 Surge = $S"
    if [ "$S" -eq 0 ] && [ "$N" -gt 0 ]; then
        echo "    → ✗ 一条都没走 Surge，全部直连（这就是 fetch failed 的根因）"
    elif [ "$S" -gt 0 ]; then
        echo "    → ✓ 有流量走 Surge"
    fi
    echo "    （注：本段只统计当前快照。连接多为短连接，采到 0 条走 Surge 属正常；"
    echo "      要看结论请以第 2 段的直连 vs Surge 延迟对比为准）"
fi

echo
echo "=========================================="
echo " 4. Surge 是否全局接管（TUN）"
echo "=========================================="
netstat -rn -f inet 2>/dev/null | awk '$1=="default"{print "  默认路由: "$0}'
echo "  （若默认路由是 en0/192.168.1.1 → Surge 只做系统代理，未 TUN 全局接管）"
