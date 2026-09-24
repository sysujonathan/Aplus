# -*- coding: utf-8 -*-
"""日志基础设施（红线授权的 logging.basicConfig 合规替代物）。

设计意图（来自 AGENTS.md / .agent/rules/coding-standards.md）：
  禁止各模块自行 ``logging.basicConfig``，统一通过 ``get_logger(__name__)`` 取 logger。

本模块不调用 ``logging.basicConfig``，而是惰性、幂等地为 root logger 配置一个
统一格式的 StreamHandler，避免与散落的 basicConfig 重复添加 handler。
"""
import logging

_DEFAULT_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
_configured = False


def get_logger(name: str) -> logging.Logger:
    """返回统一配置的 logger（按 name 区分来源，全局只配置一次 root handler）。"""
    global _configured
    logger = logging.getLogger(name)
    if not _configured:
        _configure_root()
        _configured = True
    return logger


def _configure_root() -> None:
    root = logging.getLogger()
    if root.handlers:
        return  # 已有 handler，避免重复配置
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
