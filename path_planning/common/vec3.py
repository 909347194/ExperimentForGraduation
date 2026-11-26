# -*- coding: utf-8 -*-
"""``Vec3`` 与三维量的自洽校验（被段、航迹、请求三处共用）。

职责：定义三维点的类型别名与"必须是 3 维且有限"的校验。
边界：纯类型与校验，不含任何几何计算。
相关：``segment.py``、``trajectory.py``、``plan_types.py``。

为什么单独成文件：这三个模块都要校验三维量，把校验放在任一处都会让另两处反向
import 它，形成不必要耦合；放这里则依赖是单向的（都指向本模块）。
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["Vec3", "check_vec3"]

#: 三维点/向量。单位跟随上下文（位置为 m，速度为 m/s，加速度为 m/s²）。
Vec3 = tuple[float, float, float]

#: 自洽校验容差。位置用 1e-6 m、时间用 1e-6 s，足够吸收浮点累加误差。
_TOL = 1e-6


def check_vec3(v: Any, name: str) -> None:
    """校验 ``v`` 是 3 维有限实数序列，否则抛 ``ValueError``。"""
    if len(v) != 3:
        raise ValueError(f"{name} 必须是 3 维：{v}")
    if not all(math.isfinite(float(c)) for c in v):
        raise ValueError(f"{name} 含非有限值：{v}")
