# -*- coding: utf-8 -*-
"""baselines — 任务分配对比方法包。

约定见同目录 README.md。

已实现（exp05_baselines 接入）：

    - ``random``  随机可行分配（性能下界）
    - ``greedy``  收益/代价比贪心插入（构造式）
    - ``vrptw``   Solomon 插入式 VRPTW 启发（硬时间窗，构造式）

已实现（exp12_literature_baselines 接入，演化类文献基线）：

    - ``genetic`` 经典 GA（锦标赛 + 保序交叉 + 变异 + 精英保留）
    - ``pmx_de``  置换编码 DE（PMX 交叉 + DE/rand/1 贪婪替换）
    - ``sort_de`` 随机键排序解码 DE（标准 DE/rand/1/bin）
"""

from .genetic import GeneticSolver
from .greedy import GreedySolver
from .pmx_de import PmxDESolver
from .random_assign import RandomAssignSolver
from .registry import REGISTRY, get_baseline
from .sort_de import SortDESolver
from .vrptw import VRPTWInsertionSolver

__all__ = [
    "REGISTRY",
    "get_baseline",
    "GeneticSolver",
    "GreedySolver",
    "PmxDESolver",
    "RandomAssignSolver",
    "SortDESolver",
    "VRPTWInsertionSolver",
]
