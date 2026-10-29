# -*- coding: utf-8 -*-
"""vertical_section.py — 基于垂直切面的三维航程代价估算。

参考：environment_dmde/cost_estimator.VerticalSectionCostEstimator
论文思路（赵明等）：
    Step1  取 S、T 连线 L(S,T)
    Step2  过 L 作垂直于 XOY 的切面
    Step3  切面与障碍包络（地形+建筑屋顶）交线映射为二维剖面（水平距–高程）
    Step4  地形跟随 + 飞行高度保持生成估计航迹（忽略复杂飞行动力学限制）
    Step5  航迹长度 L 作为航程；代价 D = L * weight

不在此做完整三维可飞性/禁飞区搜索，仅提供组合优化用的近似代价。

【为什么不用「DEM 网格路径搜索」代替垂直切面近似】(2026-10-13 实测)
    曾实现过 16 邻域 A* + 视线拉直的 DEM 曲面最短路径，在 exp01 真实
    DEM（575×508 网格、20 节点、190 对）上实测：
        垂直切面   0.37 ms/对，190 对 0.07 s，平均 55 279 m
        网格 A*  517.4 ms/对，190 对 98.3 s，平均 56 633 m（差 +2.4%）
    慢 1400 倍、精度只 +2.4%（A* 平均展开 1.25 万节点/对）。原因是 UAV 在
    离地 50 m 巡航面飞行、航程 20~50 km，地形起伏对航迹总长几乎无影响，
    「绕开山峰」几乎不发生。故保留垂直切面近似作为唯一距离口径。

【统一口径】(2026-10-13)
    本估算器的 ``distance`` 是仓内**唯一**的地形距离定义，评价端航迹时间、
    时间窗 ETA、预筛 travel、边际收益插入代价都必须由它派生，不得再用
    ``math.dist`` / 欧氏直线（旧代码里 ETA 用直线 ⇒ 系统性低估飞行时间）。
    由 :func:`make_distance_fn` 生成带记忆化的注入函数。

【建筑包络】(2026-10-29 城市场景)
    城市环境中三维航程的主导竖向结构是建筑。垂直切面剖面高程由「地形」
    扩展为「障碍包络」= 地面高程 + 该点建筑屋顶高度（Height 属性 = 屋顶
    相对当地地面；不在建筑内 = 0），飞行路径仍在包络上做地形跟随 ⇒
    自动「爬楼」。无 DEM 但有建筑时地面按 0 平面；无建筑场景
    （如 lasa_chengguan）行为与既有口径逐位一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from task_allocation.methods.muas.cost.dem_terrain import DEMTerrain
    from task_allocation.methods.utils.building_layer import BuildingLayer

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
        buildings: BuildingLayer | None = None,
    ) -> None:
        self._dem = dem_terrain
        self._mx = min_clearance
        self._my = max_clearance
        self._climb_rate = climb_rate
        self._num_samples = num_samples
        self._buildings = buildings

    def estimate(
        self,
        start: tuple[float, float, float],
        end: tuple[float, float, float],
        weight: float = 1.0,
    ) -> CostEstimationResult:
        sx, sy, sz = start
        ex, ey, ez = end

        if self._dem is None and self._buildings is None:
            # 无 DEM 无建筑：空间直线长度（历史口径逐位不变）
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

        if self._dem is not None:
            profile = self._dem.extract_profile(
                start=(sx, sy), end=(ex, ey), num_samples=self._num_samples
            )
            xs_2d = profile.distances
            zs_ground = self._fill_nan(profile.elevations)
            coords_xy = profile.coords_xy
        else:
            # 无 DEM 但有建筑：地面按 0 平面，建筑构成全部障碍包络
            ts = np.linspace(0.0, 1.0, self._num_samples)
            pxs = sx + (ex - sx) * ts
            pys = sy + (ey - sy) * ts
            dxy = np.sqrt(np.diff(pxs) ** 2 + np.diff(pys) ** 2)
            xs_2d = np.concatenate([[0.0], np.cumsum(dxy)])
            zs_ground = np.zeros(self._num_samples, dtype=np.float64)
            coords_xy = np.column_stack([pxs, pys])

        # 障碍包络 = 地面高程 + 建筑屋顶高度（无建筑处 +0，逐位退化为地形口径）
        zs_obstacle = zs_ground
        if self._buildings is not None:
            zs_obstacle = zs_obstacle + self._buildings.heights_at(
                coords_xy[:, 0], coords_xy[:, 1]
            )

        path_xs, path_zs = self._generate_flight_path(
            xs_2d, zs_obstacle, start_z=sz, end_z=ez
        )
        distance = self._compute_path_length(path_xs, path_zs)
        return CostEstimationResult(
            distance=distance,
            cost=float(distance * weight),
            profile_xs=xs_2d,
            profile_zs=zs_obstacle,
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
        """障碍包络跟随（地形 + 建筑屋顶）：过近爬升、适中平飞、过高下降；并保证 ≥ 包络+mx。"""
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

    def distance(
        self,
        start: tuple[float, float, float],
        end: tuple[float, float, float],
    ) -> float:
        """统一口径的三维航程距离（米），= :meth:`estimate` 的 ``distance``。"""
        return float(self.estimate(start, end).distance)

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


def make_distance_fn(
    estimator: VerticalSectionCostEstimator | None,
    cache: dict[tuple[tuple[float, float, float], tuple[float, float, float]], float]
    | None = None,
):
    """由代价估算器生成**带记忆化**的三维距离函数（统一口径的注入载体）。

    为什么必须缓存：DMDE 单次求解要 evaluate 上万次，每次评价要逐航次逐段
    算飞行时间（时间窗 / 航迹时间）。垂直切面估算 0.37 ms/段，不缓存会把
    单周期求解从秒级拖到分钟级；而一个滚动周期内节点位置固定，段长不会变，
    故按坐标量化（0.1 m）记忆化后命中即退化为字典查找。

    Args:
        estimator: DEM 代价估算器；``None`` 时返回纯欧氏距离函数（无 DEM 场景）。
        cache: 外部缓存字典。跨周期/跨 seed 传同一个可复用已算过的段长
            （任务与机巢位置固定时命中率极高）。

    Returns:
        ``distance_fn(a, b) -> float``，并带 ``.cache`` 属性便于检视/清空。
    """
    memo: dict[tuple[tuple[float, float, float], tuple[float, float, float]], float] = (
        {} if cache is None else cache
    )

    def _key(p) -> tuple[float, float, float]:
        arr = np.asarray(p, dtype=np.float64)
        return (
            round(float(arr[0]), 1),
            round(float(arr[1]), 1),
            round(float(arr[2]) if arr.size > 2 else 0.0, 1),
        )

    if estimator is None:

        def distance_fn(a, b) -> float:  # 无 DEM：三维直线（与旧回退口径一致）
            ka, kb = _key(a), _key(b)
            key = (ka, kb) if ka <= kb else (kb, ka)
            hit = memo.get(key)
            if hit is not None:
                return hit
            pa, pb = np.asarray(ka, dtype=np.float64), np.asarray(kb, dtype=np.float64)
            val = float(np.linalg.norm(pb - pa))
            memo[key] = val
            return val

    else:

        def distance_fn(a, b) -> float:
            ka, kb = _key(a), _key(b)
            key = (ka, kb) if ka <= kb else (kb, ka)
            hit = memo.get(key)
            if hit is not None:
                return hit
            val = float(estimator.estimate(ka, kb).distance)
            memo[key] = val
            return val

    distance_fn.cache = memo  # type: ignore[attr-defined]
    return distance_fn
