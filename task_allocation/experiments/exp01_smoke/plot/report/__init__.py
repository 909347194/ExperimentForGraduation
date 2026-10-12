# -*- coding: utf-8 -*-
"""exp01_smoke 图组：**report 层——结论与论文插图**。

放进本层的判断标准：**可以直接放进论文/汇报**，画的是「实验说明什么」，
而不是「实验长什么样（scene）」或「这一轮怎么回事（process）」。

典型形态：多次重复实验的汇总与误差棒、两组配置的对照、参数敏感性扫描结果。
这类图往往要跨多份 ``metrics.json``（多个 seed / 多组配置），因此与只读单份
产物的 scene/process 层拉开层次。

现有图：``seed_summary``（多 seed 复现一致性，仅多 seed 运行时有此图）。
"""

from .seed_summary import plot_seed_summary

__all__ = ["plot_seed_summary"]
