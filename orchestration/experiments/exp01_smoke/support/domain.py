# -*- coding: utf-8 -*-
"""任务产生域：数据域矩形与最近巢标注。

职责：把"任务点从哪儿来"单独收口——域是场景数据矩形，与机巢位置、机队能力无关。
边界：只做几何定义，不做任何过滤；点的取舍在 ``support/tasks.py``（只按能否进入
      算法计算判：真高带 / 绝对带 / SDF / 棱柱净空）。
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


def data_bounds(scenario) -> tuple[float, float, float, float]:
    """场景数据矩形（不含 ``bbox_margin_m`` 的 5 m 外扩）。"""
    return tuple(float(v) for v in scenario.data_bounds_xy)


def nearest_nest(x: float, y: float, nests: Sequence) -> tuple[int, float]:
    """平面最近巢：返回 ``(nest_id, 水平距离 m)``；只作诊断标注，不解算。"""
    best_id, best_d = -1, float("inf")
    for n in nests:
        d = float(np.hypot(x - n.x, y - n.y))
        if d < best_d:
            best_id, best_d = int(n.id), d
    if best_id < 0:
        raise ValueError("没有可用机巢")
    return best_id, best_d
