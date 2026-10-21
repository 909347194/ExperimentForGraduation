# -*- coding: utf-8 -*-
"""baselines — 任务分配对比方法包。

约定见同目录 README.md。

已实现（2026-10-21，exp05_baselines 接入）：

    - ``random``  随机可行分配（性能下界）
    - ``greedy``  收益/代价比贪心插入（构造式）
    - ``vrptw``   Solomon 插入式 VRPTW 启发（硬时间窗，构造式）
"""

from .greedy import GreedySolver
from .random_assign import RandomAssignSolver
from .registry import REGISTRY, get_baseline
from .vrptw import VRPTWInsertionSolver

__all__ = [
    "REGISTRY",
    "get_baseline",
    "GreedySolver",
    "RandomAssignSolver",
    "VRPTWInsertionSolver",
]
