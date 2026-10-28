# -*- coding: utf-8 -*-
"""扩展控制网络上的最短路搜索（Lin2021 §2.3；时空扩展见 design_nofly_zones.md §5）。

职责：以综合代价（cost/composite）为目标搜索 route（虚拟链路序列，即"走廊"）。
动态禁飞场景下搜索状态含时间——状态 = (虚拟节点, 时间层)，时空 Dijkstra 为基线、
时空 A*（h = 距离/v_max）为加速；基础实现在 online/spacetime.py 之上。

不负责：代价定义（cost/）、剪枝（dynamic_prune.py）、时空连接生成（spacetime.py）。
"""
