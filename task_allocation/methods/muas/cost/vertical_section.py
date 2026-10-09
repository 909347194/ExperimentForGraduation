# -*- coding: utf-8 -*-
"""vertical_section.py — 基于垂直切面的三维航程代价估算。

参考：environment_dmde/cost_estimator.VerticalSectionCostEstimator
论文思路（赵明等）：
    Step1  取 S、T 连线 L(S,T)
    Step2  过 L 作垂直于 XOY 的切面
    Step3  切面与地形交线映射为二维剖面（水平距–高程）
    Step4  地形跟随 + 飞行高度保持生成估计航迹（忽略复杂飞行动力学限制）
    Step5  航迹长度 L 作为航程；代价 D = L * weight

不在此做完整三维可飞性/禁飞区搜索，仅提供组合优化用的近似代价。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain

DEFAULT_MIN_CLEARANCE = 50.0
DEFAULT_MAX_CLEARANCE = 300.0
DEFAULT_CLIMB_RATE = 0.15  # 相对水平距离的爬升/下降斜率


@dataclass(frozen=True)
class CostEstimationResult:
    distance: float
    cost: float
    profile_xs: np.ndarray
    profile_zs: np.ndarray
    path_xs: np.ndarray
    path_zs: np.ndarray
    flight_altitude: float


class VerticalSectionCostEstimator:
    """垂直切面航程代价估算器。"""

    def __init__(
        self,
        dem_terrain: DEMTerrain | None = None,
        min_clearance: float = DEFAULT_MIN_CLEARANCE,
        max_clearance: float = DEFAULT_MAX_CLEARANCE,
        climb_rate: float = DEFAULT_CLIMB_RATE,
        num_samples: int = 200,
    ) -> None:
        self._dem = dem_terrain
        self._mx = min_clearance
        self._my = max_clearance
        self._climb_rate = climb_rate
        self._num_samples = num_samples

    def estimate(
        self,
        start: tuple[float, float, float],
        end: tuple[float, float, float],
        weight: float = 1.0,
    ) -> CostEstimationResult:
        sx, sy, sz = start
        ex, ey, ez = end

        if self._dem is None:
            # 无 DEM：空间直线长度
            dist = float(np.sqrt((ex - sx) ** 2 + (ey - sy) ** 2 + (ez - sz) ** 2))
            xs = np.array([0.0, dist])
            zs = np.array([sz, ez])
            return CostEstimationResult(
                distance=dist,
                cost=dist * weight,
                profile_xs=xs,
                profile_zs=zs,
                path_xs=xs,
                path_zs=zs,
                flight_altitude=self._mx,
            )

        profile = self._dem.extract_profile(
            start=(sx, sy), end=(ex, ey), num_samples=self._num_samples
        )
        xs_2d = profile.distances
        zs_terrain = self._fill_nan(profile.elevations)

        path_xs, path_zs = self._generate_flight_path(
            xs_2d, zs_terrain, start_z=sz, end_z=ez
        )
        distance = self._compute_path_length(path_xs, path_zs)
        return CostEstimationResult(
            distance=distance,
            cost=float(distance * weight),
            profile_xs=xs_2d,
            profile_zs=zs_terrain,
            path_xs=path_xs,
            path_zs=path_zs,
            flight_altitude=self._mx,
        )

    def _generate_flight_path(
        self,
        xs: np.ndarray,
        terrain_zs: np.ndarray,
        start_z: float,
        end_z: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """地形跟随：过近爬升、适中平飞、过高下降；并保证 ≥ 地形+mx。"""
        n = len(xs)
        path_zs = np.zeros(n)
        path_zs[0] = start_z
        for i in range(1, n):
            clearance = path_zs[i - 1] - terrain_zs[i]
            dx = xs[i] - xs[i - 1]
            if clearance < self._mx:
                path_zs[i] = path_zs[i - 1] + self._climb_rate * dx
            elif clearance > self._my:
                path_zs[i] = path_zs[i - 1] - self._climb_rate * dx
            else:
                path_zs[i] = path_zs[i - 1]
            min_z = terrain_zs[i] + self._mx
            if path_zs[i] < min_z:
                path_zs[i] = min_z
        path_zs[-1] = end_z
        # 终点仍不低于当地最小离地
        path_zs[-1] = max(path_zs[-1], terrain_zs[-1] + self._mx * 0.5)
        return xs.copy(), path_zs

    @staticmethod
    def _compute_path_length(xs: np.ndarray, zs: np.ndarray) -> float:
        dx = np.diff(xs)
        dz = np.diff(zs)
        return float(np.sum(np.sqrt(dx**2 + dz**2)))

    @staticmethod
    def _fill_nan(arr: np.ndarray) -> np.ndarray:
        result = np.asarray(arr, dtype=np.float64).copy()
        nans = np.isnan(result)
        if nans.all():
            return np.zeros_like(result)
        if not nans.any():
            return result
        valid_idx = np.where(~nans)[0]
        nan_idx = np.where(nans)[0]
        result[nan_idx] = np.interp(nan_idx, valid_idx, result[valid_idx])
        return result
