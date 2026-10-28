# -*- coding: utf-8 -*-
"""链路连接与剪枝。

职责：候选控制点对 → d* 最大距离截断 → CollisionQuery.link_feasible 剪掉
穿越建筑（Lin2021 Fig.6）或穿越静态永久禁飞区的链路（README §4.3.1），
产出原始控制网络（节点 + 有向链路）。

不负责：虚拟节点扩展（virtual_network.py）。
"""
