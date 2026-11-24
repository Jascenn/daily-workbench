# scripts/sync/social/
> L2 | Parent: /GEMINI.md

自媒体与知识库只读抓取插件包，由 `scripts/sync/social-sync.py` 统一编排全量刷新与 `--only` 单平台增量刷新。

## 成员清单

- `common.py`：路径常量、`bind_entry_globals/scfg` 运行时桥接及日期/XLSX/PDF/CLI 公共工具
- `zsxq.py`：知识星球三级降级抓手（`zsxq-cli` → Web API Cookie → 历史导出）与正文/标签清洗
- `wechat_mp.py`：微信公众号官方 API 与本机 `mpdt`（Chrome 登录态）抓手
- `x_xhs.py`：X（`xreach` + PDF 历史兜底）与小红书公开笔记抓手
- `knowledge.py`：Obsidian-Tech 只读知识库索引、自动化健康状态与周复盘汇总

[PROTOCOL]: Update this header on change, then check GEMINI.md
