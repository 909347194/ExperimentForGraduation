# -*- coding: utf-8 -*-
"""exp02_selection_scale 图组：**report 层——结论与论文插图**。

放进本层的判断标准：**可以直接放进论文/汇报**，画的是「实验说明什么」，
而不是「实验长什么样」或「这一轮怎么回事」。

本实验的全部图件都是 report 层：单周期变体扫描（N × variant 矩阵），
数值产物只有一份 ``metrics.json``（``scales`` 列表），图回答四个问题：

    r01_tradeoff       吞吐—耗时—质量的全局权衡（核心图）
    r02_compression    规模压缩是否发生、与 N 是否解耦（成功标准 1）
    r03_time_breakdown 耗时分解与惰性边数：O(n²) 瓶颈是否已除（成功标准 2）
    r04_bottleneck     瓶颈定位：选择名额 max_ratio·K 还是 α
    r05_capability     能力驱动标定：k≈cap(t) 的平衡点（成功标准 3/4）
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
