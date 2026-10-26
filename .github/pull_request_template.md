## 背景

<!-- 说明用户问题、数据边界或关联的 issue。 -->

Closes #

## 改了什么

-

## 数据与隐私

- [ ] 未提交真实收益、客户名、平台凭据或本机路径
- [ ] 示例数据使用 `wb-guest` 前缀，与真实数据隔离
- [ ] 外部系统仍为只读；任何写回都需显式确认
- [ ] 缺失数据保持「未接入 / 未核验 / 已过期」，没有伪装成 0

## 怎么验证

- [ ] `python3 -m py_compile app.py scripts/sync/*.py scripts/tools/*.py scripts/tests/*.py`
- [ ] `bash -n scripts/sync-now.sh scripts/tools/*.sh scripts/diagnostics/*.sh`
- [ ] `node scripts/tests/test-workbench-logic.mjs`
- [ ] 涉及界面时附浅色、深色和移动端截图

## 回滚

<!-- 说明如何恢复数据或代码。 -->
