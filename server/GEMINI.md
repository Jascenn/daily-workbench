# server/
> L2 | Parent: /GEMINI.md

后端模块化服务包，承接从 `app.py` 拆出的全部独立领域子系统。`app.py` 作为精简启动入口（898 行，原 4,299 行）与 Facade 兼容层保留既有符号导出，确保 launchd 配置与全部历史单元测试零改动兼容。

## 成员清单

- `__init__.py`：包初始化标记
- `runtime.py`：公共运行时桥接层（`bind_app_globals` / `cfg`），打破循环依赖并透明兼容单元测试 monkeypatch
- `article.py`：站内阅读文章抓取、公网 URL SSRF 防护、`_SafeArticleRedirectHandler` 与 `_ArticleHTMLParser` 正文清洗器
- `feeds.py`：RSS 订阅源读写、自动轮转与废弃 `channelStates` 裁剪、探查、解析与陈旧源刷新
- `notion.py`：项目/客户/专注记录显式白名单映射、关系搜索缓存与本地原子回写
- `sync.py`：一键同步进程组执行与超时回收、分阶段耗时/失败标记解析、同步日志与 Jev 代理调用
- `zsxq.py`：知识星球 CLI 路径解析、话题详情缓存、标签选项、AI 草稿续写、预览校验、两步发布与单源同步
- `routes_post.py`：承载全部 `POST /api/*` 写操作路由分发（`handle_post`），含锁保护、防重与白名单校验

## 架构约定

- 新增后端能力优先在 `server/` 下建立独立领域模块，`app.py` 仅做配置声明、GET 只读分发、POST 委托与兼容导出
- 模块内访问可被测试覆盖的配置或函数时统一经 `server.runtime.cfg()` 读取，严禁反向导入 `app`

[PROTOCOL]: Update this header on change, then check GEMINI.md
