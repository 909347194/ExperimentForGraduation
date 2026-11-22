# -*- coding: utf-8 -*-
"""内层 NLP 的全部旋钮（`:class:`NlpConfig`）。

职责：把 ``PlanParams`` + ``VehicleSpec`` 派生出的数值集中成一个不可变配置对象，
      供求解器（``trajectory_optimizer.py``）、协调层（``coordination/local_replan``）
      与测试共用。
边界：不含任何求解逻辑、不校验数值本身（正定性由 ``PlanParams`` 把关）。
相关：``trajectory_optimizer.py``（消费者）、``common/plan_params.py``（数值来源）、
      ``docs/time_mapping.md`` §3（口径 B 的三条速度约束）。
"""

from __future__ import annotations

from dataclasses import dataclass

from path_planning.common.plan_params import PlanParams
from path_planning.common.vehicle_spec import GRAVITY_MPS2, VehicleSpec

__all__ = ["NlpConfig"]


@dataclass(frozen=True)
class NlpConfig:
    """NLP 全部旋钮（数值来自 ``PlanParams`` + ``VehicleSpec``，这里只搬运）。"""

    n_nodes: int
    segment_samples: int
    dt_min_s: float
    dt_max_s: float
    max_iter: int
    solver_tol: float
    v_max_plan_mps: float
    v_max_mps: float
    v_up_mps: float
    v_dn_mps: float
    a_max_mps2: float
    thrust_max_per_mass: float
    thrust_min_per_mass: float
    clearance_m: float
    retreat_node_ladder: tuple[int, ...]
    solve_budget_multiplier: float
    gravity_mps2: float = GRAVITY_MPS2
    half_width_m: float | None = None
    corridor_normal_xy: tuple[float, float] | None = None
    corridor_pivot_xy: tuple[float, float] | None = None
    max_time_s: float | None = None
    endpoint_exempt_segments: int = 1
    #: 几何引导管半径（米）。位置在 NLP 里是自由变量、目标只有 min ΣΔt，
    #: 不给管子时求解器会把节点拉回直线段——局部让路的位移只在初值里存在，
    #: 一解就消失。协调层必须显式给值；初规划保持 None（RRT 折线本来就该被拉直）。
    guide_radius_m: float | None = None
    #: 绝对 z 飞行带（米）。RRT 折线受真高带约束，但 NLP 的节点是自由变量：
    #: 不给上下界时，min ΣΔt 会**爬高越过障碍**（实测爬到 208 m AGL，远超 120 m
    #: 建场带），既超出机体使用口径，也让冲突层按"不同高度层"判成安全。
    alt_min_m: float | None = None
    alt_max_m: float | None = None

    @classmethod
    def from_params(
        cls,
        params: PlanParams,
        vehicle: VehicleSpec,
        *,
        clearance_m: float,
        half_width_m: float | None = None,
        corridor_normal_xy: tuple[float, float] | None = None,
        corridor_pivot_xy: tuple[float, float] | None = None,
        max_time_s: float | None = None,
        endpoint_exempt_segments: int = 1,
        guide_radius_m: float | None = None,
        alt_range_m: tuple[float, float] | None = None,
    ) -> NlpConfig:
        """从 ``PlanParams`` + ``VehicleSpec`` 派生（不重复定义默认值）。"""
        if alt_range_m is not None and float(alt_range_m[1]) <= float(alt_range_m[0]):
            raise ValueError(f"alt_range_m 退化：{alt_range_m}")
        return cls(
            n_nodes=int(params.n_nodes),
            segment_samples=int(params.clearance_samples_per_segment),
            dt_min_s=float(params.dt_min_s),
            dt_max_s=float(params.dt_max_s),
            max_iter=int(params.max_iter),
            solver_tol=float(params.solver_tol),
            v_max_plan_mps=vehicle.v_max_plan_mps,
            v_max_mps=vehicle.v_max_mps,
            v_up_mps=vehicle.v_up_mps,
            v_dn_mps=vehicle.v_dn_mps,
            a_max_mps2=vehicle.a_max_mps2,
            thrust_max_per_mass=vehicle.thrust_max_per_mass,
            thrust_min_per_mass=vehicle.thrust_min_per_mass,
            clearance_m=float(clearance_m),
            retreat_node_ladder=tuple(int(n) for n in params.retreat_node_ladder),
            solve_budget_multiplier=float(params.solve_budget_multiplier),
            half_width_m=None if half_width_m is None else float(half_width_m),
            corridor_normal_xy=corridor_normal_xy,
            corridor_pivot_xy=corridor_pivot_xy,
            max_time_s=None if max_time_s is None else float(max_time_s),
            endpoint_exempt_segments=int(endpoint_exempt_segments),
            guide_radius_m=None if guide_radius_m is None else float(guide_radius_m),
            alt_min_m=None if alt_range_m is None else float(alt_range_m[0]),
            alt_max_m=None if alt_range_m is None else float(alt_range_m[1]),
        )
