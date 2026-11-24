# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-11-24

### 微信「批奏折」统一流、只读回应草稿与白名单群选题
- **微信页「批奏折」视图（V2 发丝线精简风格）**：
  - 在 `#wxSubnav` 新增「批奏折」入口并保留原有「今日行动 / 机会探索 / 咨询分析 / 群提醒」四个子视图，统一归并待办、商机、咨询、群 `@我` 与白名单群选题（自动过滤广告）。
  - 单层白卡 + 宽屏双列发丝线网格 + 单行「类型 × 状态」筛选与批阅进度；按 `会话ID + 最新消息时间` 存 `localStorage`，对方发来更新消息自动重浮并标「新进展」。
- **只读三口吻回应草稿**：
  - 新增 `#wxReplyOv` 回应草稿弹窗与 `#wxRawOv` 原文核对弹窗，支持「专业利落 / 温和体贴 / 简短确认」三口吻切换与现场微调复制；激活 `POST /api/wechat/reply-draft`（本地 Ollama 润色、离线规则降级，严格只读绝不自动发微信）。
- **跨轮次进展 Diff 与白名单群聊选题**：
  - 新增 `scripts/sync/wechat_brief.py` 并挂接至 `scripts/sync/wechat-sync.py`：通过 `~/.config/daily-workbench/wechat-topic-state.json` 比对上轮摘要输出 `prevSummary / latestDelta / hasProgress`；支持可选白名单配置 `~/.config/daily-workbench/wechat-groups.json` 抽取高价值群聊选题 `learningTopics`。
  - 补齐 `scripts/tests/test-wechat-brief.py` 与前端逻辑测试（814/814 双端全绿）。

## [1.1.0] - 2026-11-24

### 工作台系统性重构、安全加固与知识星球简报双视图

- **架构模块化（零外部构建依赖）**：
  - 前端 `workbench.html` 拆分为 `web/` 模块目录（`shell.html` + 7 CSS + 10 HTML + 12 JS），新增纯 Python 拼装与门禁工具 `scripts/build-workbench.py`；交付仍为单文件 `workbench.html`，100% 兼容离线兜底页与 808 条逻辑测试。
  - CSS 门禁升级为作用域感知（media-aware），合并清理全部 32 处同作用域重复覆盖选择器，当前门禁锁死为 `0`。
  - 后端 `app.py` 从 4,299 行精简至 907 行，拆出 `server/{runtime,article,feeds,notion,sync,zsxq,routes_post}.py` 7 个领域模块并保留兼容导出层。
  - `scripts/sync/social-sync.py`（1,922 → 431 行）拆分为 `scripts/sync/social/{common,zsxq,wechat_mp,x_xhs,knowledge}.py` 平台插件包，并补齐根级与子包 `GEMINI.md` 架构地图。
- **知识星球日报 + 7 天周报 & 总览晨间速览**：
  - 新增只读 `scripts/sync/zsxq-daily-sync.py` 与独立大页 `zsxq-daily.html`：单次扫描同时产出 24h 日报与 7 天周报（含精华与高互动 Top 5 精选、分类/星球计数与采集覆盖率如实标注），并接入每日 09:20 `scripts/sync/daily-sync.sh` 与离线兜底页 `#wb-zsxq-daily-seed`。
  - 工作台知识星球页新增「星球简报（今日 24h / 本周 7 天）」切换卡与「刷新简报」按钮（`POST /api/zsxq/daily-sync`），并支持“我的星球 vs 加入星球”范围筛选与短文卡片。
  - 总览页「今天先做这三件事」下方新增可折叠「晨间速览」3 列聚合卡（微信待办 / 选题雷达 / 星球简报，后台静默预取、访客脱敏、专注模式自动隐藏）。
- **性能、存储与同步提速**：
  - `scripts/sync-now.sh` 升级为 4 组 DAG 并行流水线（Notion→资讯、自媒体→微信组内串行防争用、网站监控、选题雷达并行），日志按原固定顺序回放。
  - `social-sync.py` 新增 `--only zsxq|x|xhs|wechat_mp|kb` 单源增量模式；新增 `POST /api/social/sync` 与自媒体页 4 平台卡「快刷」按钮。
  - 为 `make-local.py`（含原子写盘）与 `_rss_write` 补齐备份轮转（各留最近 10 份，清理历史堆积释放 >200 MB）；自动裁剪 `rss-store.json` 中 870 KB 废弃 `channelStates` 缓存；`setType()` 切页不再误重绘隐藏总览页；静默处理 `BrokenPipeError` 并移除重复的 `scripts/token-usage.py`。
- **安全边界与仓库卫生**：
  - 入口页合法 `?token=` 访问自动下发 `HttpOnly; SameSite=Strict` 会话 Cookie，并补齐 `Content-Security-Policy`、`X-Content-Type-Options` 与 `Referrer-Policy` 响应头。
  - 清空前端全部 40+ 处 URL `?token=` 拼接，统一由同源 `fetch` 拦截器携带 Cookie / `Authorization: Bearer`；前端入口自动抹除地址栏 `?token=` 并清除 `localStorage.wb_token` 残留；建立声明式 `data-wb-action` 事件委托机制。
  - 取消 `workbench.local.html` 的 Git 追踪（遵循 `.gitignore`，保留本地离线文件），收口合并旧分支至 `main` 并清理过期分支与失效 worktree。

- 知识星球页面完成最终版式收敛：主时间线优先、右栏辅助，统一双行工具栏并清理重复覆盖规则；不新增功能。
- 重构知识星球子页为 1024px 优先的紧凑时间线布局，拆分趋势、星球清单与低干扰发布记录。

- 知识星球工具栏改为左对齐紧凑排布：搜索 200px + 排序紧挨其后，第二行星球下拉 220px、时间、每页依次排列，不再用 1fr 拉伸或 flex-end 把控件推到两端。
- 紧凑知识星球时间线工具栏：搜索改为小型搜索框，排序按钮显示为 `New / 阅 / 赞 / 评`，时间与分页筛选保持两行紧凑布局；发布记录只读合并已核验历史主题，按 topicId/digest 幂等去重并避免占位 topic 生成死链。
- 最终收紧星球工具栏：重建为独立的搜索/排序上行与星球/时间/每页下行，三个筛选组使用明确的三列 grid，不再共享同一 grid-area 或依赖 absolute/负 margin，避免星球下拉被挤掉。
- 调整时间线标题动作顺序：发新帖紧跟「话题时间线」标题，同步数据移到最右侧。
- 固定时间线标题按钮间距：发新帖紧跟标题，同步数据使用自动左边距推到最右侧。

- 修正 939px 窄桌面知识星球布局：主栏与窄右栏保持双栏，时间线筛选固定为紧凑两行；空发布记录隐藏，避免运营区留下空卡片。

- 收敛知识星球页视觉层级：时间线筛选压缩为两行工具栏，保留原生星球下拉；趋势与发布记录合并为紧凑运营卡，星球清单减少间距。

- 知识星球时间线改用原生星球下拉筛选；标题、正文与轻量元信息分层显示，正文支持自然换行。

### Added

- 知识星球 timeline can be filtered and read four ways: a 星球 chip row (only planets that actually have posts), a 时间范围 switch (7 天 / 30 天 / 全部), and sorts by 最新 / 阅读最高 / 赞最多 / 评论最多. Each row now shows which planet it came from. The filter lives in `zsxqTimelineRows()`, so search, paging and sorting all compose instead of each needing its own path.
- Search covers the excerpt as well as the title — the body is where the content is, so title-only search could not find anything that mattered.
- 发帖趋势 card: six monthly buckets from the timeline itself, so a drop in posting volume is visible instead of hidden in a single total.
- 发布记录 card backed by `GET /api/zsxq/publish-history`: reads the existing `zsxq-publish-log.jsonl`, newest first, with planet name, character count, AI mode and a link to the topic. Read-only.
- The composer keeps an unsent draft in `localStorage` (`wb.zsxq.draft`): text, trigger, selected tags and target planets survive 收起 and a page reload, and are cleared only after a successful publish.

- The 知识星球 target picker is a dropdown instead of a row of chips: the button shows the chosen planet (or `2 个星球：A、B`), and the panel lists every configured planet as a checkbox. It closes on an outside click, keeps at least one target selected, and re-checks the choice against the config each time it opens.
- Publish to more than one 知识星球 at once: `zsxq-publish.json` now accepts `groups: [{groupId, groupName}]` (the old single `groupId` still works), `GET /api/zsxq/publish-config` returns that list, the composer shows a target chip per planet, and `POST /api/zsxq/preview` returns one digest **per target** — tags carry different `hid`s in each planet, so the text differs. Publishing takes `groupIds` plus a `digests` map, writes each target in turn, logs every attempt, and reports per-planet results: all-ok → `200`, some-ok → `207` with `partial: true`, none-ok → `502`. A failed planet never blocks the others and nothing is retried automatically.
- Each row in 星球清单 links to that planet (`https://wx.zsxq.com/group/<group_id>`); the ids come from a new `metrics.groupIds` map written by `social-sync.py`.

- 知识星球 composer can draft for you: `POST /api/zsxq/draft` takes a one-line trigger (`seed`) and asks the local `qwen3:8b` for 100–250 characters in 凌一's own voice, then `zsxq_clean_draft()` strips the model's habits (「好的，以下是正文」openers, markdown headings, self-added hashtag lines, wrapping quotes) and cuts at a sentence boundary. The prompt forbids invented specifics — no tool names, numbers or places that were not given — and forbids drifting to a different story. The button only fills the textarea and ticks `#bot` (so the AI declaration becomes `aigc` automatically); it never previews or publishes by itself.
- Quick tag picking in the 知识星球 composer: `GET /api/zsxq/tags` returns the group's hashtags sorted by usage (`get_group_hashtags`, 10-minute cache), the composer shows the six most-used as one-click chips, and a 更多标签 panel lists all of them with counts and a search box. Chips truncate long names with the full name in `title`, selected tags carry a ✕, and custom comma-separated tags still work and de-duplicate against the picked ones. The list is sorted again in the frontend so a changed API order cannot push cold tags to the front. Group list on that screen collapses zero-post planets into a single muted line, so a growing subscription list does not push the working planets out of view.
- Add a 知识星球-only sync button: `POST /api/zsxq/sync` refreshes just this account through `zsxq_cli_snapshot` and merges the result into `social.json` (`merge_zsxq_snapshot` replaces only 知识星球 accounts/posts, records `zsxqSyncedAt`), taking ~7.5 s instead of the 85 s full sync that also hits X and WeChat. A single-flight guard returns 409 while a refresh is running, and a failed fetch keeps the previous snapshot and says so.
- Publish a 知识星球 topic from the workbench — as a two-step, explicitly confirmed flow: `POST /api/zsxq/preview` composes the final text (tags on the first line, `bot` forced for `aigc`), returns the target group, character count, warnings and a content `digest`, and `POST /api/zsxq/publish` refuses to write unless `confirm: true` and a matching `digest` arrive, the target group is the one configured in `~/.config/daily-workbench/zsxq-publish.json`, the `ai_mode` is valid, and the same content was not published in the last 30 minutes. Every attempt is appended to `zsxq-publish-log.jsonl`; a missing `topic_id` is reported as "may already be submitted, check the planet" rather than retried. The page shows a preview block and keeps 确认发布 disabled until a preview succeeds.
- Read a 知识星球 topic in full inside the workbench: `GET /api/zsxq/topic?id=…` shells out to `topic +detail`, strips the `<e …/>` markup, URL-decodes it, keeps paragraph breaks and caches the result for an hour; the timeline row gets a 展开全文 / 收起 toggle that renders the text inline. Read-only — no post, comment or edit path is exposed. The endpoint resolves `zsxq-cli` and `node` by absolute path because launchd's `PATH` contains neither homebrew nor nvm, which produced `env: node: No such file or directory`.
- Add a 知识星球 sub-page under 自媒体运营 (type `zsxq`, opened from a two-tab sub-menu inside that screen): the six headline figures and the per-group list share the right column (sticky on wide screens) while the topic timeline owns the left, so the two sides no longer differ 3× in height. The timeline shows body excerpts, its own search and newest/most-read sort, and a selectable page size (10 / 20 / 50 / all) instead of a fixed cut. Topic titles come from the post body's first sentence because the API truncates its own title mid-word. Revert with `git revert <sha>`.
- Add a 知识星球 data panel to the media screen: member total, topic count, total/average reads, engagement rate ((likes + comments) / reads), posts in the last 30 days, and the top 5 topics by reads, all computed from the `zsxq-cli` snapshot. Missing metrics render as `—` rather than 0 — `Number(null)` is `0`, so an unchecked null would read as "nobody viewed this". Free groups are called out as having no official dashboard rather than shown as failing.

### Changed

- Pin `AI` / `碎屑` / `bot` to the front of the 知识星球 tag chips, in that fixed order, ahead of the usage-sorted rest; `bot` shows even when the group's tag list does not return it, because the AI declaration is derived from that tag. The 更多标签 panel uses the same ordering.
- Stop making the user pick the 知识星球 AI declaration every time. It is derived from the content convention already in use — AI-written posts carry `#bot` — so the mode is `aigc` when that tag is selected and 不声明 otherwise, shown as a single inline line (`AI 声明：不声明 [改]`). The `<select>` only appears after clicking 改, and once the user overrides it by hand the choice stops following the tags.
- Render the 知识星球 preview the way the post will look: tags become pill chips (`#AI #碎屑 #bot`) above the body instead of a wall of `<e …/>` markup, which is now folded into a 查看源码 disclosure. Also stop `zsxq_compose` from appending a literal `#bot` line when it receives no tags — the publish route builds the rich tag line itself, so the preview had been showing a duplicate plain-text `#bot` as the body's first line.
- Keep the 知识星球 composer inline. A fixed-overlay modal was tried and reverted: it looked heavy and disconnected from the timeline, so the panel stays in place in the page flow and is instead kept compact — a 4-row textarea (120–240px), tag chips on one horizontally scrolling line with `white-space: nowrap` so a name never breaks mid-word, and the action row on a single line. Collapse it with 收起 when it is in the way.
- Make the 知识星球 page-size control readable: `.seg` defaults to a `--bg` fill that is invisible on a card, so the 10/20/50/全部 buttons looked like loose numbers. They now sit on a card-coloured pill with a strong border, the active size uses the accent fill with white text, a "每页" label says what the numbers mean, and timeline rows tighten from 12px to 8px vertical padding.
- Schedule the slow-moving snapshots once a day (`com.lingyi.workbench.dailysync`, 09:20): social snapshot, feed refresh, news snapshot, then rebuild the offline page. `sync-now.sh` had never been scheduled at all, so these only ever updated when run by hand — the WeChat snapshot was 22 hours old and the news list 22 days old.
- Refresh RSS subscriptions before rebuilding the news snapshot: the server's stale-feed fallback only retries sources that have no entries or a recorded error, so nine healthy-but-idle feeds sat at 2026-10-31 while every snapshot faithfully reported them as expired. Pulling each subscription through `/api/feeds/refresh` moved the list from 0 items in 21 days to 20, newest 2026-11-22.
- Turn the weekly 知识星球 content pass into a scheduled job (`com.lingyi.workbench.zsxqtasks`, Mondays 09:10): take the top-N posts by reads inside a 60-day window, skip any topic that already produced a task (state keyed by `topicId`), and write one `二次创作：…（星球 N 阅读）` task per new topic, linked on **both** sides of the project↔task relation. Names come from the topic body (the list API only returns a 60-character preview) with `<e …/>` tags and hashtags stripped, and clipping stops at punctuation rather than mid-phrase. An empty week produces nothing instead of recycling old posts.
- Fix the daily digest losing the whole run when Reminders was slow: the AppleScript looped over every reminder and hit the 25-second timeout, and a reminder failure returned before the group push. It now filters with `whose` (0.9 s), allows 60 s, and treats a failed reminder as a warning (exit 3) while still sending the notification and the group message.
- Show the related-task count on project cards (`5 个关联任务`). The project's 关联任务 relation and the task's 所属项目 relation are two independent Notion relations, so linking a task from the task side does not populate the project side — the count reflects the project-side relation only, and zero is hidden rather than rendered as `0 个任务`.
- Stop hard-coding the project count in the project-board assertion: it compared against a literal 29, so adding four real projects in Notion turned a rendering test into a data test. It now compares the rendered cards against the loaded projects only.
- Add 知识星球 official dashboard data where it exists: for paid groups, `api call /v2/dashboard/groups/{id}/overview` is projected onto `metrics.dashboard` (yesterday active / new members / income, 30-day averages, today's running totals). Free groups are marked `unsupported` with the API's own reason (17208, "星球不存在或不是付费星球") instead of being called and reported as a failure — the main 242-member group is free, so it has no dashboard by design.
- Read 知识星球 through `zsxq-cli` (the authenticated `zsxq` skill channel) instead of the cookie Web API: one `group +list` plus paginated `group +topics` per configured group, mapped to the same account/post shape the media screen already renders. Real data now replaces the fallback archive — 5 groups, 95 topics, with readers/likes/comments and per-group member counts. Channel order is `zsxq-cli` → cookie Web API → historical export, and `sync.source` records whichever channel actually answered so a CLI failure is not reported as an API failure.
- Format the WeChat digest for a phone screen without dropping information: items are separated by a blank line, numbered with 1️⃣2️⃣3️⃣, and every field keeps its label plus a marker (💡 why / ✍️ angles / ✅ next step / 🔗 link) with the URL intact. Emoji are wayfinding, not decoration — each one still carries the field name next to it. No Markdown and no title truncation.
- Record per-run provenance for every social account (`sync.status` = updated / partial / history / blocked / not_connected, plus `updatedThisRun`, `source`, `reason`) and a `syncRun` rollup, so a freshly written `social.json` is no longer mistaken for freshly fetched platform data. Snapshot version → 2; `--check` validates before writing.
- Make the three派生快照 steps (自媒体 / 微信 / 网站) non-fatal in `sync-now.sh`: they no longer abort the chain, but failures now emit `__WB_STEP_FAIL__` so the sync log renders ✗ instead of a false ✔. `app.py` parses the marker; step names in tests are derived from the script instead of hard-coded counts.
- Show WeChat opportunity/consultation confidence as an integer percentage derived from Jev's 0–9 score, and stop rendering two-decimal raw scores: repeated calls on the same text vary by ~0.1, so the decimals implied precision the model does not have. Missing scores stay blank instead of becoming 0%.
- Make the client-status assertion data-independent (inject a synthetic status-less client) because a real Notion sync can fill every status and turn a behaviour test into a data test.
- Update the local WeChat MCP integration for server `0.2.0`: negotiate supported protocol versions with fallback, record `system.status` limits and event coverage, prefer `search.global` and `messages.get_context` with legacy fallbacks, verify group mentions through `groups.members`, and keep media reads local-only. Message sending remains disabled.

### Changed

- Push the daily radar digest to a configured WeChat group (default `2755945645@chatroom`): sends only when there are writable items **and** the text changed since the last send, so the group does not receive the same digest daily. The idempotency key is derived from `conversation_id + text` because the MCP rejects a reused key with different content (`idempotency_conflict`). The digest header carries the date only — an hour/minute stamp would make every run look like new content and defeat the dedupe. `submitted` means accepted, not delivered; failures are never auto-retried. `--dry-run` shows what would be sent without touching Reminders, notifications or WeChat.
- Add a daily radar digest at 09:00 (`com.lingyi.workbench.radardigest`): pick the top 3 judged-and-writable items and write them to a dated Reminder in the 工作台 list plus a desktop notification. Items marked `needsHuman` or un-analyzed never enter the digest, an empty day says so instead of inventing picks, and re-running the same day updates that day's reminder instead of stacking duplicates. WeChat delivery deliberately stays off — the WeChat layer is read-only (write endpoints return 501) until that is explicitly enabled.
- Cache radar judgments and angles for 24 hours (`radar-cache.json`, pruned at 7 days) and schedule the radar every 2 hours via `com.lingyi.workbench.radar`. A run now costs ~14 s when the hot list has not changed instead of re-asking Jev for the same 12 titles every time — measured 31 s → 14 s. Cache hits are reported as `coverage.judgmentsFromCache` so a cached verdict is never mistaken for a fresh one.
- Add 选题雷达 (`radar.json`): fetch 36氪 / IT之家 / 知乎热榜 / Hacker News, score each item with Jev for **business relevance** and **write value** (0–9), then draft "why it matters / what angle / next step" with the local model. Sources report `ok` / `empty` / `unsupported` / `error` individually — 微博热搜 is JS-rendered so it is marked unsupported rather than silently returning 0 items. Items Jev could not judge keep `analyzed: false` instead of looking scored, and angles that merely restate the headline are dropped by a character-overlap guard. Served at `/radar.json` (same bearer token) and shown as a card on the news screen; `sync-now.sh` runs it as a non-fatal step.
- Add a read-only WeChat screen backed by the local WeChat MCP snapshot (`wechat.json`): to-dos sorted by idle time, group @-mentions restricted to the account nickname, and recent active conversations. The snapshot never fabricates an unread count — pending replies are labelled as an inference — and service accounts (`gh_*`, `*@openim`) are excluded structurally rather than by guessing at message content. Per-conversation "ignore" is a local display preference, not data.
- Add a local semantic layer (`scripts/sync/wechat_semantic.py`): voice messages are transcribed (SILK → WAV → SenseVoice, on-device) and every pending conversation is rewritten by a local model into "what they want / what you should do", with a 待回复 / 广告 / 已结束 grouping. Classification only groups — nothing is dropped; the original text stays one click away. When the model is unavailable the entries are marked `analyzed=false` and show raw text instead of pretending to be interpreted. The default direction when a category is missing is "to-do", never "advertisement".
- Add opportunity exploration and consultation analysis inside the existing WeChat tab. `messages.search` performs a bounded keyword-matrix recall before a small local-model pass; results keep evidence separate from `whyRelated`, `nextAction`, and `missingInfo`, and remain reviewable rather than becoming CRM/Notion writes. Jev 负责机会/咨询主判断与评分，本地 qwen3:8b 只补叙述或在 Jev 不可用时降级。
- Add a fully redacted guest-mode WeChat mock with sample to-dos, opportunities, consultations, and group mentions. Toggling guest mode swaps the snapshot in memory only and restores the real snapshot on exit; no local WeChat data is read or written.
- Reserve WeChat business-action contracts (`/api/wechat/capabilities`, `/api/wechat/action`, `/api/wechat/reply-draft`) without enabling external writes; the UI keeps status, follow-up date, and reply drafts local-only for now.
- Adopt the updated WeChat MCP surface: negotiate supported protocol versions, record `system.status` limits, prefer `search.global` and `messages.get_context` for business recall/context, verify group mentions with `groups.members`, and keep media reads local-only.
- Record per-account event coverage (`retention`, `eventGap`, oldest/newest event and covered conversations) so the WeChat UI can explain partial real-time coverage instead of implying a complete inbox.
- Add the WeChat tab subviews `今日行动 / 机会探索 / 咨询分析 / 群提醒`. Opportunity and consultation cards keep source evidence visible, mark model output as review-required, and include coverage/unknown copy instead of treating an empty search as proof that no opportunity exists.

- Move the feed source of truth to a workspace-owned `rss-store.json` with atomic writes, timestamped backups, and an explicit `--import-qiaomu` one-time seed; the runtime never touches the Obsidian plugin file.
- Derive item source names from the subscription name so plugin-side name drift can no longer detach feeds from their entries.
- Add selectable feed-health sorting (stale-first, freshest, item count, group) persisted per browser, with a non-squeezing card header.
- Add server-side feed refresh with a proxy fallback for sources the plugin cannot reach.
- Preserve `<details>` folds from fetched articles and render them as native collapsible sections with per-whitelist recursion.
- Add block-level copy buttons and click-to-zoom for reader images.
- Add news search, source/group/unread filters, one-click reset, recent/dead-feed overviews, and clickable source history.
- Keep feed probing and subscription management reachable from the compact mobile news summary.
- Add writing-page next-chapter focus, goal progress, collapsible source materials, and collapsible chapter/client archives.
- Add a writing-first action panel, direct current-chapter resume link, remaining-word target, highlighted next chapter, and a default-collapsed client card.
- Add in-workbench Obsidian Markdown editing with live safe preview, autosave/manual save, SHA-256 conflict detection, backup rotation, and atomic writes.
- Add Obsidian wikilink and local-image preview support with a token-protected, path-bounded image endpoint.
- Add safe Obsidian deep links for writing outlines, written chapters, source documents, drafts, and review notes.
- Add media overview summaries plus default-collapsed account, website, performance, brief, signal, and knowledge sections.
- Add review search and type filters with a recent-record focus, collapsible history, and compact prediction, weekly-report, and note-history tools.
- Add a focused overview with collapsed daily timeline, progress-first monthly goals, and on-demand goal settings plus six-month achievement history.
- Add a focused project page with actionable metrics first and default-collapsed closure, priority, delivery, and hours evidence.
- Add a focused customer page with follow-up risk first, a compact health-check summary, and one-click filter reset.
- Split the pomodoro panel into a focus workspace and a world-time pane with Shanghai as the primary clock and two configurable candidate cities numbered 1-2.
- Add candidate selectors to the world-clock card so the compact time pane stays display-only while clock choices remain editable below.
- Collapse project focus details and the full world-clock workspace behind summaries while keeping the primary timer and candidate clocks visible.
- Separate candidate clocks into distinct cards with larger gaps, clearer hierarchy, and roomier time labels.
- Collapse the empty focus-trend chart into a compact summary while keeping the full chart available after real sessions exist.
- Add per-stage sync timings and highlight the slowest stage in the workbench sync log.
- Add schema-driven project writeback for project fields, tags, dates, hours, parent relations, and notes.
- Add searchable project owner, related-task, and source-record editors backed by read-only Notion summaries and whitelist-only writeback.
- Add project type, sprint, owner, child-count, relation, and related-link presentation on project cards.
- Add explicit `已延迟` project status handling and keyboard navigation for workbench page switching.
- Add a whitelist-only project writeback test suite with local rollup preservation checks.
- Add a pull-request template with privacy and read-only-boundary checks.
- Add baseline repository hygiene files and monthly GitHub Actions update checks.
- Add a security policy, issue forms, and a CODEOWNERS map.

### Changed

- Align the in-app reader title, h2, and h3 sizes, weights, and margins with the lingyi.me article baseline.
- Trap keyboard focus inside open dialogs, focus the first available control on open, restore the trigger on close, and lock background scrolling.
- Add explicit accessible names to the overview note, goal amount, and earnings search/filter controls instead of relying on placeholders.
- Prioritize actionable content on the news, writing, media, and review pages, while keeping complete data available through disclosure controls.
- Increase every primary media disclosure row to a 44px touch target and let performance titles use two lines on mobile instead of truncating the comparison away.
- Make review tool disclosures and note-history controls 44px tall, expand inline Obsidian link hit areas without changing row density, and preserve project names with a two-line mobile clamp.
- Hide the review clear-filter action until a filter is active, matching the news and customer toolbars and reducing default toolbar noise.
- Prioritize today's actions and goal progress on the overview page while keeping notes, timeline, goal settings, and full business evidence available in place.
- Prioritize current and at-risk projects, keep the project board at its content height, and separate completed from cancelled scopes.
- Prioritize customer follow-up gaps on the first screen, keep secondary health checks collapsed, and reduce the empty follow-up state to one action row.
- Reduce the time page's repeated empty-state action, project breakdown, and full city controls into compact summaries with on-demand expansion.
- Reduce the news page height from about 2447px to 1140px on desktop and from 3599px to 1810px on mobile.
- Reduce the writing page height from 1422px to 959px on desktop and from 1905px to 1416px on mobile.
- Reduce the media page height from 2961px to 1213px on desktop and from 5724px to 1593px on mobile.
- Reduce the review page height from 2825px to 1021px on desktop and from 5334px to 2017px on mobile.
- Reuse the latest synchronized Notion project schema instead of fetching it again for every save.
- Remove WorkBuddy branding and obsolete WorkBuddy-specific runtime assumptions from the independent workbench.
- Align project and customer writeback subprocesses with the macOS system proxy and launchd path environment.

### Fixed

- Keep verified 知识星球 publish history visible in `workbench.local.html` and `file://` previews: the local page now seeds token-free history, uses the live API first, falls back on unavailable/unauthorized requests, merges duplicate records, and does not create links for placeholder topic IDs.

- The 最新 / 阅读最高 buttons never changed appearance: the sort did re-order the timeline, but only the page-size buttons synced their `.on` state. Both sort buttons now have ids and are synced in `renderZsxqScreen`.

- Stop the 知识星球 preview from always warning "aigc but no #bot tag": the check looked for the literal `#bot` inside the first line, which is rich markup (`title="%23bot%23"`), so it never matched. It now checks the tag names list, and the whole rule is a testable `zsxq_preview_warnings()`.

- Resolve `zsxq-cli` and `node` by absolute path in `social-sync.py` too. Under launchd its `PATH` contains neither homebrew nor nvm, so the scheduled 09:20 sync would have silently fallen back to the cookie API and then to the old historical export — the same bug already fixed in `app.py`, found again when the new sync button returned "no topics" in 0.04 s.

- Publish 知识星球 topics with native blue tags: the first line of the body must be `<e type="hashtag" hid="real-id" title="%23…%23" />` markup, which the platform renders as tag chips — plain `#AI #bot` stays grey text. The publish path now looks up each tag's `hid` from `get_group_hashtags` (falling back to `hid="0"`), and still sets the same tags as `annotation`. Verified against two previously published topics whose content uses exactly this shape.

- Clean 知识星球 topic titles before they reach the media list: strip `<e type="hashtag" …/>` markup, URL-decode, drop `#tag` noise, treat `...` placeholders as empty, fall back to tag names, trim API-added trailing ellipses, and skip topics that have neither text nor tags. Also hoist `published` above the fallback branch that referenced it — that raised `UnboundLocalError` on any tag-less empty topic.

- Raise the full sync timeout from 180s to 300s so normal Notion, social, and website-monitoring latency does not produce a false 504.
- Record timed-out syncs in sync history and terminate the entire sync process group so background children cannot keep running.
- Fix duplicate source-card IDs that wrote the source count into the overview card while leaving the data-source dialog header blank.
- Retry transient Notion proxy failures without retrying validation, authentication, or permission errors.
- Fix project-page horizontal overflow at tablet and narrow-desktop widths by stacking the page header and allowing source text to wrap.
- Keep the media page's hero metrics readable in dark mode by using the deck foreground token instead of the card background token.
- Preserve Notion rollup progress and derived project fields during local cache updates after writeback.

### Security

- Restrict generated Obsidian links to the `Obsidian-Tech` vault and reject paths outside its root.
- Keep real earnings, customer names, platform credentials and local data snapshots out of Git.

### Changed

- Reworked the 知识星球 timeline controls into readable header, sorting, search, and stacked filter rows; added responsive single-column behavior and compacted trend/publish history into an Operations card.

- Rebuilt the knowledge planet timeline toolbar into a clear two-row responsive grid and moved publishing into the timeline header.

- Add the WeChat Today Actions task queue with normalized tasks, account/search filters, local-only status and snooze state, review-required markers, and clipboard actions.
