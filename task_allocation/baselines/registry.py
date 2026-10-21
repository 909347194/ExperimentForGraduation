# -*- coding: utf-8 -*-
"""registry.py — baseline 名称 → 求解器类。

约定：
    - key 与 experiments 配置中的 method name 一致（小写+下划线）
    - 只注册 baselines 内的对比方法；主算法 dmde 不在此注册
    - 获取方式：get_baseline(name) -> type[BaseOptimizer]
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .greedy import GreedySolver
from .random_assign import RandomAssignSolver
from .vrptw import VRPTWInsertionSolver

if TYPE_CHECKING:
    from task_allocation.methods.muas.base import BaseOptimizer

REGISTRY: dict[str, type] = {
    "random": RandomAssignSolver,
    "greedy": GreedySolver,
    "vrptw": VRPTWInsertionSolver,
}


def get_baseline(name: str) -> type:
    """按名称返回 baseline 求解器类。

    Raises:
        KeyError: 名称未注册时，提示可用列表。
    """
    if name not in REGISTRY:
        known = ", ".join(sorted(REGISTRY)) or "(empty)"
        raise KeyError(
            f"baseline '{name}' not registered. Known: {known}. "
            "See task_allocation/baselines/README.md."
        )
    return REGISTRY[name]
