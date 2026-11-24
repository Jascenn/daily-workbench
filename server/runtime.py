"""
[INPUT]: 无外部依赖，由入口 app.py 启动时调用 bind_app_globals(globals()) 注入命名空间
[OUTPUT]: 导出 bind_app_globals 与 cfg，支持测试对 app.<NAME> 的 monkeypatch 透明穿透至 server/* 子模块
[POS]: server/ 公共运行时桥接层，打破循环依赖并保持历史测试 100% 兼容
[PROTOCOL]: Update this header on change, then check GEMINI.md
"""

_APP_GLOBALS = None


def bind_app_globals(ns: dict) -> None:
    """绑定入口模块全局命名空间。"""
    global _APP_GLOBALS
    _APP_GLOBALS = ns


def cfg(name: str, default=None):
    """优先从入口模块读取最新配置/函数（兼容单元测试 monkeypatch），否则回退模块默认值。"""
    if _APP_GLOBALS is not None and name in _APP_GLOBALS:
        return _APP_GLOBALS[name]
    return default
