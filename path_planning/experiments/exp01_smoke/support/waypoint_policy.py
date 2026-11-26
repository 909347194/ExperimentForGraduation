# -*- coding: utf-8 -*-
"""任务产生侧：语义点位 → 合规的绝对 ENU 三维航点。

职责（只做这一件事，三段串起来）：

1. **基线**：``nest`` 取巢址所在表面（地面与屋顶取大——巢在楼顶就按楼顶，不能按地面），
   ``task`` 取屋顶（无建筑即地面）；
2. **加高**：``nest`` 加 ``nest_hover_agl_m``（离巢悬停间隙，不建模起降），
   ``task`` 加 ``task_clearance_agl_m``（作业面上方间隙）；
3. **净空阶梯**：若该点 SDF 或棱柱净空不足``clearance_m``，按竖直格距一格一格抬，
   直到达标；抬到绝对上界仍不达标就报错要求换点——**不静默兜底**。

边界（用户裁定 2026-11-23）：点位高不高、选得对不对是**调用方（任务产生）**的职责，
不在 ``path_planning`` 库、也不在 ``methods/`` 里做。``methods`` 只接收已合规的三维点并
做入口校验（``tour_planner._validate_endpoints``：绝对带 + 逐点真高带），算法内不做兜底。
本模块不建场、不跑规划：净空由装配层注入的 :class:`SiteClearance` 查询。

数值全部来自实验配置（``config.yaml`` 的 ``mission_waypoints`` / ``planning.clearance_m`` /
``field.spacing_m[2]``），本模块不藏默认值。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Final

if TYPE_CHECKING:  # pragma: no cover
    from path_planning.common.scenario_types import Scenario

__all__ = [
    "POINT_TYPE_NEST",
    "POINT_TYPE_TASK",
    "POINT_TYPES",
    "SiteClearance",
    "WaypointPolicyConfig",
    "resolve_waypoint",
]

POINT_TYPE_NEST: Final[str] = "nest"
POINT_TYPE_TASK: Final[str] = "task"
POINT_TYPES: Final[tuple[str, ...]] = (POINT_TYPE_NEST, POINT_TYPE_TASK)


@dataclass(frozen=True)
class SiteClearance:
    """净空查询端口：由实验装配层注入（场 + 棱柱），本模块不 import 规划实现。

    ``clearance_m`` 是**生产侧必须达到**的净空 = 求解阈值（交付阈值 + SDF 量化容差）：
    端点本身要落在 NLP 的可行域里，否则求解器第一步就无解。交付/审计仍按较小的
    交付阈值判，两个数不混用。
    ``z_ceiling_m`` 是飞行绝对高度上界（运行时按 ``ground_min + alt_agl_max`` 派生），
    净空阶梯抬到它就算到头了。
    """

    sdf_at: Callable[[float, float, float], float]
    prism_clearance_at: Callable[[float, float, float], float]
    z_ceiling_m: float
    clearance_m: float


@dataclass(frozen=True)
class WaypointPolicyConfig:
    """高度解析规则的数值（实验配置显式给出，本模块不藏默认值）。"""

    nest_hover_agl_m: float
    task_clearance_agl_m: float
    lift_step_m: float

    def __post_init__(self) -> None:
        for name in ("nest_hover_agl_m", "task_clearance_agl_m", "lift_step_m"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} 必须为正有限值：{value}")


def resolve_waypoint(
    x: float,
    y: float,
    point_type: str,
    cfg: WaypointPolicyConfig,
    scenario: "Scenario",
    site: SiteClearance,
) -> tuple[float, float, float]:
    """``x, y, 语义类型`` → 已通过净空与高度检查的绝对 ENU 三维点。

    Raises:
        ValueError: 点类型未知、坐标/高程非有限、或抬到上界仍无净空（要求换点）。
    """
    px, py = float(x), float(y)
    if not math.isfinite(px) or not math.isfinite(py):
        raise ValueError(f"航点平面坐标必须有限：{(px, py)}")

    if point_type == POINT_TYPE_NEST:
        # 巢址所在表面：楼顶巢不能被当成"地面 + 间隙"，否则点直接埋进楼体。
        base = max(float(scenario.ground_height_at(px, py)),
                   float(scenario.roof_height_at(px, py)))
        offset = float(cfg.nest_hover_agl_m)
        base_name = "巢址表面（地面/屋顶取高）"
    elif point_type == POINT_TYPE_TASK:
        base = float(scenario.roof_height_at(px, py))
        offset = float(cfg.task_clearance_agl_m)
        base_name = "作业面（屋顶，无建筑即地面）"
    else:
        raise ValueError(f"未知航点类型 {point_type!r}；允许值：{list(POINT_TYPES)}")

    z0 = base + offset
    if not math.isfinite(z0):
        raise ValueError(f"航点高程不是有限值：point_type={point_type}, x={px}, y={py}")

    clearance = float(site.clearance_m)
    step = float(cfg.lift_step_m)
    z, lifted = z0, 0.0
    while True:
        if z > float(site.z_ceiling_m) + 1e-9:
            raise ValueError(
                f"点位不合规：({px:.1f}, {py:.1f}) 为 {point_type!r}，{base_name} {base:.1f} m"
                f" + 间隙 {offset:g} m = {z0:.1f} m，净空阶梯抬 {lifted:g} m 后到 {z:.1f} m，"
                f"仍拿不到 {clearance:g} m 净空或已越过飞行上界 {site.z_ceiling_m:.1f} m。"
                f"请在 uav_config.json 换点，不得把越界点交给规划器兜底。"
            )
        sdf = float(site.sdf_at(px, py, z))
        prism = float(site.prism_clearance_at(px, py, z))
        if min(sdf, prism) >= clearance - 1e-6:
            return (px, py, z)
        z += step
        lifted += step
