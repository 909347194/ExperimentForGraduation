# -*- coding: utf-8 -*-
"""平滑后二次碰撞校验与偏移修正（README §4.3.4，本章相对基线的新增机制）。

职责：对平滑圆弧逐段调用 CollisionQuery.arc_feasible（建筑 + 静态/动态禁飞），
发生冲突时调整偏移距离迭代修正（回调 dubins_smoother 的偏移量），
直至全部可行或超过迭代上限（标记 SMOOTH_INFEASIBLE）。

不负责：圆弧生成（dubins_smoother.py）。
"""
