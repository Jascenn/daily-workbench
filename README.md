# 每日工作台（Daily Workbench）

本地优先的个人经营工作台：Notion 数据同步 + 白名单回写 + 本地草稿，覆盖收益、项目、客户、时间、资讯、写作、自媒体运营、知识库与复盘。

> **仓库说明**：这是 `daily-workbench` 的开源脱敏公开版（`v1.1.0`）。所有真实数据、客户信息、私有数据库 ID 与本机配置均不入库，可通过环境变量或 `~/.config/daily-workbench/` 本地配置接入。

提交约定见 [CONTRIBUTING.md](CONTRIBUTING.md)；安全问题按
[SECURITY.md](SECURITY.md) 私下报告。

## 架构

- `app.py` + `server/`：本地服务（127.0.0.1:8787，launchd `com.lingyi.workbench.syncserver`）。后端按领域拆分为 `server/{runtime,article,feeds,notion,sync,zsxq,routes_post}.py`；支持 `HttpOnly Cookie` + `Authorization: Bearer` 鉴权与 CSP 安全头，静态文件走白名单。
- `zsxq-daily.html` + `zsxq-daily.json`：知识星球日报（独立新功能，与 `workbench.html` 解耦）。
  - `scripts/sync/zsxq-daily-sync.py` 只读扫描已加入星球过去 24 小时的新主题，生成快照；自然日口径会在跨天时把昨晚内容清零，故用滚动窗口
  - 只覆盖开通 Skill 权限的星球；无权限 / 限流星球在页面顶部如实标注，不计入「没更新」
  - 页面本身无真实数据，数据走 `/zsxq-daily.json`（令牌门）；打开：`/zsxq-daily.html?token=<sync-token>`
- `web/` → `workbench.html`：前端源码按屏幕拆分在 `web/{styles,templates,js}/`，由零外部依赖脚本 `python3 scripts/build-workbench.py` 拼装为单文件 `workbench.html`（内置 CSS 同作用域零重复门禁，100% 兼容离线兜底页与逻辑测试）。数据分多路读取：
  - `/data.json` — Notion 同步产物（收益、项目、客户、跟进、专注记录）；收益只读，项目与客户资料走显式白名单回写
  - `/sections.json` — 资讯 / 写作 / 复盘派生快照（`scripts/sync/sections.py` 生成）
  - `/social.json` — 自媒体只读快照（X / 小红书 / 公众号 / 知识星球，`scripts/sync/social-sync.py` 生成）
  - `/knowledge.json` — Obsidian 全库只读索引（同由 `scripts/sync/social-sync.py` 生成）
  - `/wechat.json` — 微信只读快照（`scripts/sync/wechat-sync.py`，走本机微信 MCP）：待办、机会探索、咨询分析、群里 @ 我、最近活跃会话
    - 语音由 `scripts/sync/wechat_semantic.py` 在本机转写（SILK → WAV → SenseVoice），语义由本地 `qwen3:8b` 改写；模型或 ASR 不可用时降级为原文并标注，不伪造判断
    - 机会/咨询使用 `messages.search` 关键词矩阵召回候选，再补少量上下文；输出 `whyRelated` / `nextAction` / `missingInfo`，仅作待核对的派生判断，不自动建客户、不写 Notion；机会/咨询的主判断由 Jev 结构化评分完成，本地 qwen3:8b 只补人话字段或在 Jev 不可用时降级
    - 访客模式使用内置脱敏 mock 演示待办、机会、咨询和群提醒；进入访客模式不会读取或展示本机微信快照
    - MCP 更新已接入：协议协商、`system.status` 覆盖信息、`search.global` 召回、`messages.get_context` 候选上下文、`groups.members` 群成员核验；媒体保持本地只读，不自动补下载
    - 快照还记录 `eventCoverage`：实时事件条数、保留窗口、缺口和覆盖会话数；“没有发现”与“本轮没覆盖”分开显示
    - 预留接口：`GET /api/wechat/capabilities` 返回覆盖范围与只读能力；`POST /api/wechat/action`、`/api/wechat/reply-draft` 当前明确返回 501，不发送微信、不写 Notion
- `scripts/`：各数据源的同步与快照脚本默认只读；Notion 回写只由本地服务中的显式用户操作触发。
- `scripts/sync/writing_store.py`：Obsidian 正文的稳定 ID、路径边界、哈希冲突检测、备份与原子写入；工作台只接收 `bookId` / `chapterId`，不接收任意本机路径。
- `data.json`、`sections.json`、`social.json`、`knowledge.json`、`web-presence.json` 等本地数据文件不入库（见 `.gitignore`）。

项目回写支持编辑负责人、关联任务和来源记录。关系选择器只读取 Notion 页面标题、状态与日期摘要；保存时仍由 `app.py` 按字段白名单回写，不读取页面正文，也不改动 rollup / 公式字段。

写作的正文唯一真源是 `Obsidian-Tech/projects/` 里的 Markdown。工作台可以直接编辑、预览和保存已存在章节，停止输入 2 秒自动保存，`⌘S` / `Ctrl+S` 立即保存；保存前校验 SHA-256，检测到 Obsidian 外部修改时返回冲突，备份到 `backups/obsidian-writing/` 后再原子替换。Notion 只承载项目、客户、状态等元数据，不作为正文真源，避免双写覆盖。

## 运行

```bash
# 服务（默认已由 launchd 常驻；手动启动）
python3 app.py

# 打开带令牌的工作台链接
bash scripts/tools/open-workbench.sh

# 重新生成派生快照
python3 scripts/sync/sections.py --write
python3 scripts/sync/social-sync.py     # 同时生成 social.json 与 knowledge.json
python3 scripts/sync/web-monitor.py
python3 scripts/sync/wechat-sync.py       # 微信只读快照（需要本机微信 MCP 在 127.0.0.1:21454）

# Notion 只读同步（收益 + 项目）
python3 scripts/sync/notion-sync.py
```

## 设计约定

- 行动优先：首屏先给"今天先做三件事"，指标放后面。
- 未知不写 0：数据缺失时如实说"未接入 / 未核验"，不造数。
- 写入边界：收益、Obsidian 和自媒体平台保持只读；项目资料、客户资料和已完成专注记录只在用户显式点击后，按字段白名单回写 Notion。
- 访客模式：全部示例数据，与真实数据隔离（`GUEST_*` 常量）。
- 设计规范见 [docs/DESIGN.md](docs/DESIGN.md)。
