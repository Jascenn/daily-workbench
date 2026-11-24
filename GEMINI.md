# Daily Workbench - 本地优先的个人经营工作台

Python 3 标准库（ThreadingHTTPServer）+ 原生 HTML/CSS/JS（模块化源码 + 零依赖单文件拼装）+ Notion / Obsidian / 微信 MCP / zsxq-cli 只读与白名单回写管道

<directory>
web/ - 前端模块化源码目录（3 子目录：styles/、templates/、js/，共 29 个模块，经 scripts/build-workbench.py 拼装为单文件 workbench.html）
server/ - 后端领域服务包（7 子模块：runtime、article、feeds、notion、sync、zsxq、routes_post；app.py 精简为 898 行入口与兼容层）
scripts/ - 数据同步管道（含 sync/social/ 5 平台插件包）、零依赖拼装器、工具与回归测试（sync/、tools/、tests/、analysis/、diagnostics/、legacy/）
docs/ - 设计契约与同步管道架构文档（DESIGN.md、sync-pipeline.md）
assets/ - 白名单静态品牌图片
backups/ - 自动轮转的写入前备份（data.json 保留 20 份，rss-store / workbench.local.html 各保留 10 份）
</directory>

<config>
VERSION - 单一版本真源（供 app.py /api/status 与 scripts/build-workbench.py 同步读取）
app.py - 本地服务入口（127.0.0.1:8787，launchd 常驻；HttpOnly Cookie + Bearer + Query 三路鉴权 + CSP/安全头）
workbench.html - 拼装生成的单文件在线入口（勿直接手改，改 web/ 后运行 scripts/build-workbench.py）
workbench.local.html - scripts/sync/make-local.py 生成的 file:// 离线兜底页
zsxq-daily.html - 独立 24h 知识星球日报页（纯只读令牌门，不内嵌真实数据）
scripts/build-workbench.py - 零外部依赖拼装器与作用域感知 CSS 重复选择器门禁（当前门禁上限：0）
scripts/sync-now.sh - 4 组 DAG 并行一键全量同步脚本
</config>
