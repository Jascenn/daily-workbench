# web/
> L2 | Parent: /README.md

前端源码模块目录。所有样式、页面模板与脚本按领域拆分于此，由 `scripts/build-workbench.py` 零依赖拼装为单文件 `workbench.html`（确保离线兜底页 `workbench.local.html` 与 4,582 行 `test-workbench-logic.mjs` 零改动兼容）。

## 成员清单

- `shell.html`：HTML 外壳，声明 `<!-- @INCLUDE_CSS -->` / `<!-- @INCLUDE_BODY -->` / `<!-- @INCLUDE_JS -->` 拼装锚点
- `styles/00-tokens-base.css`：设计令牌、暗色主题变量、全局基础样式与目标带
- `styles/10-reader-banners.css`：站内阅读抽屉、提示条、撤销条与交互能力查询
- `styles/20-sections-writing.css`：类型导航、资讯/写作编辑器与复盘阅读样式
- `styles/30-project-client-time.css`：项目、客户、时间与世界时钟样式
- `styles/40-media-focus.css`：自媒体运营页与 v3 默认减量/专注模式样式
- `styles/50-wechat.css`：微信 MCP 待办与经营分析样式
- `styles/60-zsxq.css`：知识星球大盘、独立屏、今日日报与二级菜单样式
- `templates/00-topbar.html`：顶栏、状态播报与类型导航条
- `templates/10-screen-ledger.html` ~ `80-screen-content.html`：各业务主屏与知识星球子屏 HTML 结构
- `templates/90-modals.html`：写作编辑器、项目/记账/RSS/客户/阅读器与命令面板浮层
- `js/00-config-state.js`：令牌读取与 URL 清洗、接口常量、工具函数与本地状态读写
- `js/10-ledger.js`：总览 KPI、目标带、图表、来源排名、数据体检与同步日志
- `js/20-sections-core.js`：快照按需加载、页签切换、复盘阅读器与访客常量
- `js/30-project-client-wechat-time.js`：项目、客户、微信主视图逻辑
- `js/35-time-worldclock-media.js`：时间轴、世界时钟与自媒体概览趋势
- `js/40-zsxq.js`：知识星球大盘、独立屏、两步发布与今日日报卡片
- `js/50-media-health-radar.js`：运营健康分、选题雷达、资讯列表与站内阅读器
- `js/60-writing.js`：Obsidian Markdown 写作编辑器、实时预览与冲突保存
- `js/70-review-weekly.js`：复盘、自动周报、发布预测、AI 用量与 `renderAll`
- `js/80-editing-feeds.js`：编辑弹窗、RSS 探查管理与数据来源弹窗
- `js/90-notes-theme-cmd.js`：备忘、主题、访客模式、导入导出与命令面板
- `js/99-boot.js`：在线轮询、全局快捷键与启动引导

## 开发约束

- 修改前端源码后执行 `python3 scripts/build-workbench.py` 重新生成 `workbench.html`
- 提交前执行 `python3 scripts/build-workbench.py --check --lint-css` 确保源码同构且 CSS 重复选择器不新增

[PROTOCOL]: Update this header on change, then check GEMINI.md
