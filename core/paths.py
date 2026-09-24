# -*- coding: utf-8 -*-
"""项目路径基础设施（红线授权的 sys.path 唯一合规替代物）。

设计意图（来自 AGENTS.md / .agent/rules/coding-standards.md）：
  禁止各模块自行 ``sys.path.insert``，统一从这里取路径或调用 ``ensure_importable()``。

本模块自身是 ``sys.path.insert`` 的**唯一授权注入点**（位于 ``ensure_importable``），
门禁 quality_gate.py 已将其列入豁免白名单。其它文件不得再出现 sys.path.insert。
"""
from pathlib import Path
import sys

# core/ 的父目录即项目根（本文件位于 <root>/core/paths.py）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

CORE_DIR = PROJECT_ROOT / 'core'
TOOLS_DIR = PROJECT_ROOT / 'tools'
DATA_DIR = PROJECT_ROOT / 'data'
CONFIG_DIR = PROJECT_ROOT / 'config'
DOCS_DIR = PROJECT_ROOT / 'docs'
AGENT_DIR = PROJECT_ROOT / '.agent'


def ensure_importable() -> None:
    """将项目根加入 ``sys.path``（幂等），供 CLI 入口在 ``__main__`` 时调用一次。

    示例（tools/xxx.py 顶部）::

        if __name__ == '__main__':
            from core.paths import ensure_importable
            ensure_importable()
            # 之后即可 import core / tools 下模块

    这是全项目 sys.path.insert 的唯一合法位置。
    """
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
