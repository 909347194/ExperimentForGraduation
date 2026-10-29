# -*- coding: utf-8 -*-
"""统一约束查询门面：离线/在线共用**同一几何内核**，两个入口。

接口（CollisionQuery）：

    link_feasible(p, q, margin) -> bool
        无时间入口：建筑 + 静态禁飞（离线剪枝、平滑后复核均走这里）
    edge_occupancy_free(p, q, t_dep, t_arr) -> bool
        **带时间入口**：动态禁飞的整边时空占用检查
        实现 = 按时窗端点切段 + 每段静态求交（design_nofly_zones.md §3.2-3.3；
        阶跃模型下精确，无采样漏判；段数通常 1–3；每段取该时段对应的几何 Ω_jm，
        故禁飞区"几何随时间变化"同样精确）
    point_feasible(p) / polyline_feasible(pts)
    conservative_blocked(p, q, t0, t1)   L2.5 保守判据

不负责：剪枝策略与修正迭代（offline/link_builder.py、online/*）。
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from path_planning.common.types import CityEnvironment
from path_planning.methods.control_network_planning.constraints.buildings import BuildingQuery
from path_planning.methods.control_network_planning.constraints.nofly_zones import NoFlyQuery


class CollisionQuery:
    """建筑 + 禁飞区的统一判定门面。"""

    def __init__(
        self,
        env: CityEnvironment,
        d_s_stat: float = 5.0,
        d_s_dyn: float = 2.0,
        building_margin: float = 0.0,
        dynamic_pad: float = 0.0,
    ):
        self.env = env
        self.buildings = BuildingQuery(env.buildings)
        self.nofly = NoFlyQuery(
            env.static_nofly, env.dynamic_nofly, d_s_stat, d_s_dyn, pad=dynamic_pad
        )
        self.building_margin = float(building_margin)  # 平滑偏移余量可并入膨胀

    # ── 无时间入口 ────────────────────────────────────────────────────────

    def link_feasible(self, p, q, margin: Optional[float] = None) -> bool:
        """线段不与建筑/静态禁飞冲突（margin 缺省用 building_margin）。"""
        m = self.building_margin if margin is None else margin
        if not self.buildings.link_feasible(p, q, margin=m):
            return False
        return not self.nofly.static_blocked(p, q)

    def link_blocking(self, p, q, margin: Optional[float] = None) -> list[str]:
        m = self.building_margin if margin is None else margin
        out = [f"building:{b}" for b in self.buildings.blocking(p, q, margin=m)]
        out += [f"static_nofly:{z}" for z in self.nofly.static_blocking_ids(p, q)]
        return out

    def point_feasible(self, p, margin: Optional[float] = None) -> bool:
        m = self.building_margin if margin is None else margin
        if not self.buildings.point_feasible(p, margin=m):
            return False
        return not self.nofly.static_blocked(p, p)

    def polyline_feasible(self, pts: np.ndarray, margin: Optional[float] = None) -> bool:
        pts = np.asarray(pts, dtype=float)
        for a, b in zip(pts[:-1], pts[1:]):
            if not self.link_feasible(a, b, margin):
                return False
        return True

    # ── 带时间入口 ────────────────────────────────────────────────────────

    def edge_occupancy_free(self, p, q, t_dep: float, t_arr: float) -> bool:
        """整边时空占用检查：∀τ∈[t_dep,t_arr] 均不落入生效禁飞体。"""
        return not self.nofly.dynamic_blocked(p, q, t_dep, t_arr)

    def edge_blocking(self, p, q, t_dep: float, t_arr: float) -> list[tuple[str, float, float]]:
        return self.nofly.dynamic_blocking(p, q, t_dep, t_arr)

    def conservative_blocked(self, p, q, t0: float, t1: float) -> bool:
        """L2.5：任务时段内任何时刻可能冲突即判冲突（用于保守全剪）。"""
        return self.nofly.conservative_blocked(p, q, t0, t1)

    def polyline_occupancy_free(self, pts: np.ndarray, times: np.ndarray) -> bool:
        """平滑后路径的时空复核（时刻由路径累加得到）。"""
        pts = np.asarray(pts, dtype=float)
        times = np.asarray(times, dtype=float)
        for (a, b), (t0, t1) in zip(zip(pts[:-1], pts[1:]), zip(times[:-1], times[1:])):
            if not self.edge_occupancy_free(a, b, t0, t1):
                return False
        return True
