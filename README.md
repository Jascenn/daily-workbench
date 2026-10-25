# Daily Workbench（每日工作台）

本地优先的个人经营工作台：Notion 只读同步 + 本地草稿记账，覆盖收益、项目、客户、时间、资讯、写作、自媒体运营与复盘。单文件前端 + 零依赖 Python 标准库后端，所有外部系统只读。

## 特性

- **行动优先首屏**：先给「今天先做三件事」（项目推进 / 数据体检 / 月度目标缺口），经营指标在后
- **Notion 只读同步**：收益、项目、客户库只读拉取；本地「记一笔」草稿显式确认后才推送回 Notion
- **多源自媒体快照**：X / 公众号 / 小红书 / 知识星球，未接入的通道如实标注「未核验」，绝不伪装成 0
- **网站与博客监控**：可用性 5 分钟检测、RSS 更新、GSC 搜索流量、Umami 访问数据
- **收益归并引擎**：`sourceMap` 规则随数据下发，把同一客户的不同备注写法归并成组，笔数/金额守恒有测试守护
- **数据体检**：重复记录、零额记录、断档、项目关联率，每项可跳转到具体记录
- **访客模式**：全部示例数据，与真实数据完全隔离，可直接当 demo 演示
- **AI 增强插件（可选）**：接 TypeSafe Jev 做内容质量分析，不开就按原逻辑走

## 快速开始

```bash
# 1. 启动本地服务（纯标准库，无 pip 依赖）
python3 app.py

# 2. 生成令牌并打开工作台
bash scripts/open-workbench.sh
```

服务监听 `127.0.0.1:8787`，所有数据接口要求令牌（首次启动自动生成到 `~/.config/daily-workbench/sync-token`）。

### 数据接入（全部可选）

工作台没有数据也能跑（访客模式 / 本地记账）。要接真实数据源，配置以下环境变量或直接改脚本顶部常量：

| 变量 | 用途 |
|---|---|
| `WB_NOTION_INCOME_DS` / `WB_NOTION_EXPENSE_DS` / `WB_NOTION_PROJECT_DS` / `WB_NOTION_CLIENT_DS` | Notion 数据源 ID |
| `WB_OBSIDIAN_VAULT` | Obsidian 知识库路径（资讯/写作/复盘快照来源） |
| `WB_X_HANDLE` | X (Twitter) 账号 handle |
| `WB_WECHAT_ACCOUNT` | 公众号名称 |
| `WB_ZSXQ_GROUPS` | 知识星球群名（逗号分隔） |
| `WB_PROXY` | 同步走的代理（默认 `http://127.0.0.1:7890`） |
| `WB_RSS_DATA` | Obsidian RSS 插件数据文件路径 |

Notion 同步还需要 [ntn CLI](https://github.com) 与有效令牌；自媒体通道各自需要对应的登录凭据（X 走 xreach 会话、公众号走 APPID/Secret + IP 白名单、知识星球走网页 cookie token）。没有配置的通道在页面上显示「未接入」，不影响其它功能。

```bash
# 手动刷新全部数据
bash scripts/sync-now.sh
```

## 架构

- `app.py`：本地服务（`ThreadingHTTPServer`）。令牌鉴权、data.json 原子写入（20 份轮转备份 + 骤降护栏）、`/api/sync` 并发锁、订阅源管理、Jev 开关
- `workbench.html`：单文件前端（HTML+CSS+JS 全内联，无构建、无框架）。设计令牌化：颜色 / 字号 / 圆角 / 阴影全部走 CSS 变量，深浅双主题
- `scripts/`：各数据源的同步与快照脚本，全部只读外部系统
- `scripts/tests/`：
  - `test-workbench-logic.mjs` — 499 条逻辑断言（期望值从数据现算，不写死）
  - `smoke-ux.mjs` — 147 条 UX 冒烟
  - `mutate-workbench.py` — 69 条变异测试（每条变异必须让逻辑套件变红）
  - `test-workbench-live.mjs` — 需 Chrome + 本地服务的端到端
- `data.json` 等运行时数据不入库（见 `.gitignore`）

## 设计约定

- **未知不写 0**：数据缺失时如实说「未接入 / 未核验」，不造数
- **只读边界**：Notion、Obsidian、自媒体平台全部只读；写操作只落本地，且显式确认
- **不骗人的 UI**：过期数据标「已过期」，离线标「仅本地缓存」，同步失败显示真实原因
- **删除可撤销**：不打断式撤销条，优于确认弹窗
- 设计规范见 `designs/workbench/DESIGN.md`

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 测试

```bash
node scripts/tests/test-workbench-logic.mjs   # 499 断言
node scripts/tests/smoke-ux.mjs               # 147 冒烟
python3 scripts/tests/mutate-workbench.py     # 69 变异必须全红
```

## License

MIT
