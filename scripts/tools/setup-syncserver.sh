#!/bin/bash
# 加固 / 重装 workbench 同步服务（launchd）。
#
# 为什么需要你手动跑一次：launchctl bootstrap 必须由不受沙箱约束的用户会话执行，
# AI 助手在这个环境里写不进 launchd 域（一律报 Bootstrap failed: 5）。
# 本脚本幂等，可重复执行。
#
# 它做三件事：
#   1) 把 plist 里的 python 从 /usr/bin/python3（Xcode 自带 3.9）换成稳定的
#      managed 3.13 绝对路径，避免 Xcode 升级/卸载后路径失效；
#   2) 补 ThrottleInterval + ProcessType，防止崩溃时被 KeepAlive 高频重启；
#   3) 日志约定到工作区 logs/，并做一次连通性自检。
#
# 用法：
#     bash "/Users/jascen/Development/daily-workbench/scripts/tools/setup-syncserver.sh"

set -u
LABEL="com.lingyi.workbench.syncserver"
DIR="/Users/jascen/Development/daily-workbench"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM=$(id -u)
LOG_DIR="$DIR/logs"
TOKEN_FILE="$HOME/.config/daily-workbench/sync-token"

MANAGED_PY="/opt/homebrew/bin/python3"

echo "=== 1. 前置检查 ==="
if [ -x "$MANAGED_PY" ]; then
    PY="$MANAGED_PY"
    echo "使用 managed python: $PY"
else
    PY="/usr/bin/python3"
    echo "⚠ managed python 不存在，回退到 $PY（Xcode 自带）"
fi
"$PY" -c 'import sys; print("python:", sys.version.split()[0])' || { echo "✗ python 不可用"; exit 1; }
[ -f "$DIR/app.py" ] || { echo "✗ 找不到 $DIR/app.py"; exit 1; }
[ -f "$TOKEN_FILE" ] || { echo "✗ 找不到同步 token：$TOKEN_FILE"; exit 1; }
mkdir -p "$LOG_DIR"
echo "工作目录: $DIR"

echo
echo "=== 2. 备份现有 plist ==="
if [ -f "$PLIST" ]; then
    BAK="$DIR/backups/$LABEL.plist.$(date +%Y%m%d-%H%M%S).bak"
    mkdir -p "$DIR/backups"
    cp "$PLIST" "$BAK"
    echo "已备份 -> $BAK"
else
    echo "（原本没有 plist，将新建）"
fi

echo
echo "=== 3. 写入新 plist ==="
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>$DIR/app.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>ProcessType</key>
    <string>Background</string>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/sync-server.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/sync-server.log</string>
</dict>
</plist>
PLIST_EOF
plutil -lint "$PLIST" || { echo "✗ plist 语法错误"; exit 1; }
echo "✓ plist 语法通过"

echo
echo "=== 4. 重新加载任务 ==="
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null
sleep 1
if launchctl bootstrap "gui/$UID_NUM" "$PLIST"; then
    echo "✓ bootstrap 成功"
else
    echo "✗ bootstrap 失败（退出码 $?）"
    exit 1
fi
sleep 2

echo
echo "=== 5. 加载状态 ==="
launchctl print "gui/$UID_NUM/$LABEL" 2>&1 | grep -E "state|pid|last exit" | head -5

echo
echo "=== 6. 连通性自检 ==="
TOKEN=$(cat "$TOKEN_FILE")
echo -n "端口监听: "; lsof -nP -iTCP:8787 -sTCP:LISTEN 2>/dev/null | tail -1 | awk '{print $1, $2}' || echo "未监听"
echo -n "GET /workbench.html  -> "; curl -s -o /dev/null -w "%{http_code}\n" -m 5 "http://127.0.0.1:8787/workbench.html?token=$TOKEN"
echo -n "GET /data.json       -> "; curl -s -o /dev/null -w "%{http_code}\n" -m 5 "http://127.0.0.1:8787/data.json?token=$TOKEN"
echo -n "GET /data.json 无token -> "; curl -s -o /dev/null -w "%{http_code}\n" -m 5 "http://127.0.0.1:8787/data.json"
echo "（期望：200 / 200 / 401）"

echo
echo "=== 7. 最近日志 ==="
tail -4 "$LOG_DIR/sync-server.log" 2>/dev/null || echo "(暂无日志)"

echo
echo "全部完成。回滚方式：把 backups/ 里最新的 $LABEL.plist.*.bak 覆盖回"
echo "  ~/Library/LaunchAgents/$LABEL.plist  然后重跑本脚本。"
