# 数据同步管道

所有外部系统（Notion、Obsidian、自媒体平台、网站）对工作台都是**只读**的。写操作只落本地，且显式确认后才推送。

## 数据流

```mermaid
flowchart LR
    subgraph external[外部数据源（全部只读）]
        N[Notion<br/>收益/支出/项目/客户]
        O[Obsidian<br/>资讯 RSS/写作/复盘]
        S[X / 公众号 / 小红书 / 知识星球]
        W[网站与博客<br/>HTTP / GSC / Umami]
    end

    subgraph pipeline[sync-now.sh 一键管道]
        A[notion-sync.py] --> B[sections.py]
        B --> C[social-sync.py]
        C --> D[web-monitor.py]
        D --> E[make-local.py]
    end

    subgraph data[本地快照（gitignore，绝不入库）]
        J[data.json<br/>记账事实]
        K[sections.json<br/>类型区派生]
        L[social.json<br/>自媒体快照]
        M[web-presence.json<br/>网站监控]
    end

    N --> A
    O --> B
    S --> C
    W --> D
    A --> J
    B --> K
    C --> L
    D --> M
    E --> F[workbench.local.html<br/>离线兜底页]

    J & K & L & M --> G[app.py<br/>127.0.0.1:8787]
    G --> H[workbench.html<br/>单文件前端]
```

## 步骤说明

`scripts/sync-now.sh` 按固定顺序执行，每步失败码独立：

| 步 | 脚本 | 产物 | 失败码 |
|---|---|---|---|
| 0 | 前置检查（`ntn` 存在 + 代理通路 401） | — | 2 / 3 |
| 1 | `scripts/sync/notion-sync.py` | `data.json`（原子写 + 轮转备份 + 骤降护栏） | 4 |
| 2 | `scripts/sync/sections.py --write` | `sections.json`（必须在 make-local 之前） | 6 |
| 3 | `scripts/sync/social-sync.py` | `social.json` | 7 |
| 4 | `scripts/sync/web-monitor.py` | `web-presence.json`（失败只告警） | — |
| 4b | `scripts/sync/make-local.py` | `workbench.local.html` 离线兜底页 | 5 |

**顺序为什么不能变**：`make-local.py` 会把 `sections.json` 整份内嵌进离线页，先建页后建快照会把上一版数据嵌进去，页面上看不出任何异常，只是数据旧了。

## 关键不变式

- **data.json 是唯一的记账事实**，`sections.json` / `social.json` 都是可重算的派生快照
- 页面对四份快照**只读**，唯一的写路径是「记一笔」→ `data.json` → 草稿显式推送 Notion
- 每条同步写入 `sync-log.json`（最近 20 条），前端「同步日志」展示启动日志式步骤明细
- 未接入的通道在页面上显示「未接入 / 未核验」，绝不伪装成 0

## 相关脚本

| 目录 | 用途 |
|---|---|
| `scripts/sync/` | 管道步骤（上表） |
| `scripts/tools/` | 独立工具：打开链接、装 launchd 服务、GSC 快照、AI 用量、Notion API 小工具 |
| `scripts/diagnostics/` | 诊断探针：Notion data source 结构、旧连接器状态（非关键路径） |
| `scripts/tests/` | 499 逻辑断言 + 147 冒烟 + 69 变异 + live 端到端 |
