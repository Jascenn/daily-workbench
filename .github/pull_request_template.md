## 背景

<!-- 说明用户问题、数据边界或关联的 issue。 -->

Closes #

## 改了什么

-

## 隐私与数据边界

- [ ] 未提交真实收益、客户名、平台凭据或本机路径
- [ ] 示例数据使用 `wb-guest` 前缀，与真实数据隔离
- [ ] 外部系统仍为只读；任何写回都需显式确认
- [ ] 未把 `CHANGELOG.md` 的开发记录混进网站可见内容

## 怎么验证

- [ ] `python3 -m py_compile app.py scripts/sync/*.py scripts/tools/*.py scripts/tests/*.py`
- [ ] `bash -n scripts/sync-now.sh scripts/tools/*.sh scripts/diagnostics/*.sh`
- [ ] 相关 logic / smoke / mutation 测试通过，或说明为何只能本机执行
- [ ] 涉及界面时附访客模式截图

## 回滚

<!-- 说明如何恢复数据或代码。 -->
