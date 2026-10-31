# Contributing

欢迎 issue 与 PR。这个项目刻意保持「零依赖、单文件前端」，提交前请先读下面的约定。

## 开发环境

```bash
git clone https://github.com/Jascenn/daily-workbench.git
cd daily-workbench
python3 app.py                 # 无 pip 依赖，Python 3.10+
bash scripts/tools/open-workbench.sh   # 打印带令牌的本地链接
```

## 必须遵守的约定

1. **期望值不写死**：测试断言的数字从 data.json 现算（见 `scripts/tests/test-workbench-logic.mjs` 顶部的 `calcInc()` / `calcByRule()`）。写死真实数据的数字会让别人的 CI 假红。
2. **不骗人的 UI**：数据缺失显示「未接入 / 未核验」，绝不显示 0 或假数据；同步失败必须显示真实原因。
3. **未知不造数**：新增数据通道时，未授权/未配置的分支要有独立的诚实状态，而不是静默降级。
4. **设计令牌**：CSS 里的颜色、字号、圆角、阴影全部走 `--pri-*` / `--fs-*` / `--radius-*` / `--shadow-*` 变量（`workbench.html` 顶部是唯一真源）。变异测试会抓绕过令牌的硬编码。
5. **只读边界**：外部系统一律只读。任何写回外部系统的操作都要显式确认。
6. **normalize() 透传红线**：`earningsReadonly` / `earningsSource` / `projectsSource` / `notionMeta` / `sourceMap` / `goals` 六个顶层字段必须原样透传，漏一个 = 保存一次静默抹掉（logic 测试第 20 节守护）。

## 提交前

```bash
node scripts/tests/test-workbench-logic.mjs   # 必须全绿
node scripts/tests/smoke-ux.mjs               # 必须全绿
python3 scripts/tests/mutate-workbench.py     # 70 条变异必须全红
python3 scripts/tests/test-sync-timings.py    # 分阶段耗时解析必须全绿
python3 scripts/tests/test-sync-timeout.py    # 超时清理与日志留痕必须全绿
python3 -m py_compile app.py scripts/sync/*.py scripts/tools/*.py scripts/tests/*.py
```

## 提交规范

- 一次 PR 只做一件事
- commit message 用英文祈使句（`Add ...` / `Fix ...`）
- 用户可见文案用中文，与现有语气一致（说人话，不用「赋能/闭环/颗粒度」）
- 更新 `CHANGELOG.md` 的 `[Unreleased]` 区块

## 隐私

不要在 issue / PR / 测试夹具里贴真实的收益数据、客户名或平台凭据。示例数据用 `wb-guest` 前缀的常量（见 `workbench.html` 的 `guestSample()`）。
