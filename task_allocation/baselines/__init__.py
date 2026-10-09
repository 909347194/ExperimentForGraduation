# -*- coding: utf-8 -*-
"""baselines — 任务分配对比方法包。

约定见同目录 README.md。
具体算法未实现前，REGISTRY 可为空 dict；实现后在 registry 中注册。
"""

from .registry import REGISTRY, get_baseline

__all__ = ["REGISTRY", "get_baseline"]
