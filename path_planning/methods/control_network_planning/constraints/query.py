# -*- coding: utf-8 -*-
"""统一约束查询门面（离线/在线共用的唯一判定口径）。

拟接口（CollisionQuery）：

    link_feasible(p, q, *, include_dynamic: bool) -> bool
        链路是否穿越建筑或禁飞区；离线剪枝传 include_dynamic=False，
        在线剪枝传 True，判定函数本体共享。
    point_feasible(p) -> bool
    arc_feasible(arc, *, include_dynamic: bool) -> bool
        平滑圆弧二次校验 —— README §4.3.4

不负责：剪枝策略与修正迭代（offline/link_builder.py、online/dynamic_prune.py、online/post_check.py）。
"""
