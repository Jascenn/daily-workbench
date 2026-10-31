# 每日工作台（Daily Workbench）

本地优先的个人经营工作台：Notion 数据同步 + 白名单回写 + 本地草稿，覆盖收益、项目、客户、时间、资讯、写作、自媒体运营、知识库与复盘。

> **仓库说明**：这个仓库是 `daily-workbench` 的私有开发镜像，用于真实数据管道和个人历史记录；公开版本在 [Jascenn/daily-workbench](https://github.com/Jascenn/daily-workbench)。真实数据、客户信息和本机配置不得进入 Git。

提交约定见 [CONTRIBUTING.md](CONTRIBUTING.md)；安全问题按
[SECURITY.md](SECURITY.md) 私下报告。

## 架构

- `app.py`：本地服务（127.0.0.1:8787，launchd `com.lingyi.workbench.syncserver`）。所有数据接口都要求令牌（`~/.config/daily-workbench/sync-token`），静态文件走白名单。
- `workbench.html`：单文件前端。数据分四路读取：
  - `/data.json` — Notion 同步产物（收益、项目、客户、跟进、专注记录）；收益只读，项目与客户资料走显式白名单回写
  - `/sections.json` — 资讯 / 写作 / 复盘派生快照（`scripts/sync/sections.py` 生成）
  - `/social.json` — 自媒体只读快照（X / 小红书 / 公众号 / 知识星球，`scripts/sync/social-sync.py` 生成）
  - `/knowledge.json` — Obsidian 全库只读索引（同由 `scripts/sync/social-sync.py` 生成）
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

# Notion 只读同步（收益 + 项目）
python3 scripts/sync/notion-sync.py
```

## 设计约定

- 行动优先：首屏先给"今天先做三件事"，指标放后面。
- 未知不写 0：数据缺失时如实说"未接入 / 未核验"，不造数。
- 写入边界：收益、Obsidian 和自媒体平台保持只读；项目资料、客户资料和已完成专注记录只在用户显式点击后，按字段白名单回写 Notion。
- 访客模式：全部示例数据，与真实数据隔离（`GUEST_*` 常量）。
- 设计规范见 [docs/DESIGN.md](docs/DESIGN.md)。
