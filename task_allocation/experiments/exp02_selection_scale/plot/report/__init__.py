# -*- coding: utf-8 -*-
"""exp02_selection_scale 图组：**report 层——描述性观测图**。

放进本层的判断标准：画的是「规模变化下观测到了什么」，只登记量级与结构。
本实验**不产结论图**：方法/档位优劣、完成约束验收、标定选型、机制归因
都不在本层职责内（见同目录 ``notes.md``）。

数值产物只有一份 ``metrics.json``（``scales`` 列表，N × 档位矩阵）：

    r01_tradeoff       规模—耗时曲线 + 任务去向分账（论文可扩展性主图）
    r02_compression    压缩是否发生、选择数是否随 N 解耦
    r03_time_breakdown 分段计时与现算边数随 N 的变化（不作归因）
    r04_bottleneck     名额族候选规模、选择数封顶与任务去向（结构观察）
    r05_capability     能力档位阶梯的选择规模、墙钟与任务去向
"""

from .r01_tradeoff import plot_r01_tradeoff
from .r02_compression import plot_r02_compression
from .r03_time_breakdown import plot_r03_time_breakdown
from .r04_bottleneck import plot_r04_bottleneck
from .r05_capability import plot_r05_capability

__all__ = [
    "plot_r01_tradeoff",
    "plot_r02_compression",
    "plot_r03_time_breakdown",
    "plot_r04_bottleneck",
    "plot_r05_capability",
]
