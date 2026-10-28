# -*- coding: utf-8 -*-
"""扩展控制网络上的最短路搜索（Lin2021 §2.3）。

职责：以综合代价（cost/composite）为目标跑 Dijkstra，输出 route
（虚拟链路序列，即论文定义的"走廊"）。

不负责：代价定义（cost/）、剪枝（dynamic_prune.py）。
"""
