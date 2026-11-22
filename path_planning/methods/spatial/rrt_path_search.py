# -*- coding: utf-8 -*-
"""RRT 初始路径搜索：窗口内采样、SDF 自适应步长、可复现多初值。

职责：在走廊窗口的**窗内 SDF** 上跑 RRT，产出无碰撞折线（纯几何、不带时间）；
      ``plan_many`` 以不同种子提供多初值。
边界：不做候选评估 / 去重 / 选优（``initial_paths.py``）、不做时间映射、
      不建 SDF；端点净空豁免只实现**机制**，数值由调用层给定。
相关：``occupancy_sdf.py``（窗内 ``SdfField``）、``corridor_window.py``
      （窗口与 ``inside_grid``）、``common/plan_params.py``
      （``RRTConfig.from_params`` 的唯一参数来源）。

四条口径（来自参考实现与实测教训，不得"顺手改好"）：

1. **采样在栅格索引空间做**再换回 ENU。斜栅格下若在 ENU 外接矩形里均匀采，
   会有一半点落在带外、被 ``inside_grid`` 白白丢掉——采样效率直接减半。
2. **步长随 SDF 自适应**：``clip(scale·sdf, min_step, step)``。靠近障碍自动
   收缩，空旷处不至于一步跨 30 m 跳过一栋楼。
3. **捷径化预算用尽时接原始节点**。旧做法预算尽直接接"当前点→终点"的未校验
   直线，实测穿楼 11.70 m 仍报 ok；原始边都是生长时校验过的自由边，宁可少省
   几次也不接没校验的边。
4. **端点豁免按半径给**。机巢处实测 SDF 仅 +1.6 / −0.6（离地 6 m 才到 6.7+），
   端点若按全程净空判，每条 OD 都死在起点的自检上。豁免圈内阈值降为
   ``endpoint_clearance_m``，圈外仍是 ``clearance_m``。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from path_planning.methods.spatial.corridor_window import CorridorWindow
from path_planning.methods.spatial.occupancy_sdf import SdfField
from path_planning.methods.spatial.rrt_config import GroundHeightFn, RRTConfig

__all__ = ["GroundHeightFn", "RRTConfig", "RRTResult", "RRT3D"]

#: 长度判零容差（米），只用于"两点是否重合"这类退化判断
_EPS_M = 1e-9
@dataclass(frozen=True)
class RRTResult:
    """一次 RRT 的产物。

    ``nodes`` 是树里留下多少节点；``path_points_before_shortcut`` 是捷径化**前**
    折线的点数。两者不是一回事：节点数与路径点数混成一个字段后，
    "树多大"与"路径多长"再也对不上账。
    """

    path_enu: np.ndarray
    iterations: int
    nodes: int
    path_points_before_shortcut: int


class RRT3D:
    """单次 ``plan`` = 一次 RRT；``plan_many`` = 多初值（不同种子各跑一次）。

    ``alt_range_m`` 是**绝对** z 包络（窗口栅格能覆盖的范围）；给了 ``ground_fn``
    + ``agl_range_m`` 时，采样与扩展还额外受真高带
    ``[ground + agl_min, ground + agl_max]`` 约束，两者取交集。
    """

    def __init__(
        self,
        field: SdfField,
        window: CorridorWindow,
        alt_range_m: tuple[float, float],
        cfg: RRTConfig,
        ground_fn: GroundHeightFn | None = None,
        agl_range_m: tuple[float, float] | None = None,
    ) -> None:
        self.field = field
        self.window = window
        self.alt_range = (float(alt_range_m[0]), float(alt_range_m[1]))
        self.cfg = cfg
        self.ground_fn = ground_fn
        self.agl = None if agl_range_m is None else (float(agl_range_m[0]), float(agl_range_m[1]))
        # 端点豁免需要知道当前 plan 的起终点；plan 内赋值、plan 外恒 None
        self._start_p: np.ndarray | None = None
        self._goal_p: np.ndarray | None = None
        # 索引空间均匀采样（模块说明第 1 条）
        self._last_idx_xy = np.asarray(window.grid.shape[:2], dtype=float) - 1.0
        sz = float(window.grid.spacing[2])
        oz = float(window.grid.origin[2])
        self._k_range = ((self.alt_range[0] - oz) / sz, (self.alt_range[1] - oz) / sz)

    # ---------- 基本判定 -------------------------------------------------

    def _band_ok(self, pts: np.ndarray) -> np.ndarray:
        """真高带判定；无地形信息时全通过（退化成纯绝对包络）。"""
        pts = np.atleast_2d(pts)
        n = len(pts)
        if self.ground_fn is None or self.agl is None:
            return np.ones(n, dtype=bool)
        g = np.asarray(self.ground_fn(pts[:, 0], pts[:, 1]), dtype=np.float64)
        if g.shape != (n,):
            raise ValueError(f"ground_fn 应返回形状 ({n},)，得到 {g.shape}")
        agl = pts[:, 2] - g
        return (agl >= self.agl[0]) & (agl <= self.agl[1])

    def _thresholds(self, pts: np.ndarray) -> np.ndarray:
        """逐点净空阈值：端点豁免圈内放宽（模块说明第 4 条）。"""
        pts = np.atleast_2d(pts)
        thr = np.full(len(pts), self.cfg.clearance_m, dtype=np.float64)
        r = self.cfg.endpoint_exempt_radius_m
        if r <= 0.0 or self._start_p is None or self._goal_p is None:
            return thr
        near = np.minimum(
            np.linalg.norm(pts - self._start_p, axis=1),
            np.linalg.norm(pts - self._goal_p, axis=1),
        )
        thr[near <= r] = self.cfg.effective_endpoint_clearance_m
        return thr

    def _segment_free(self, a: np.ndarray, b: np.ndarray) -> bool:
        """段内采样判定：窗口内 + 真高带 + SDF ≥ 逐点阈值。

        因为阈值逐点不同（端点豁免），这里不复用 ``SdfField.segment_min``。
        两端点以外不判——窗口边界不是墙，带外由 ``inside_grid`` 否决。
        """
        a = np.asarray(a, dtype=np.float64)
        b = np.asarray(b, dtype=np.float64)
        length = float(np.linalg.norm(b - a))
        n = max(2, int(np.ceil(length / self.cfg.sample_step_m)) + 1)
        ts = np.linspace(0.0, 1.0, n)[:, None]
        pts = a[None, :] + ts * (b - a)[None, :]
        if not self.window.inside_grid(pts).all():
            return False
        if not self._band_ok(pts).all():
            return False
        return bool((self.field.at(pts) >= self._thresholds(pts)).all())

    def _sample(self, rng: np.random.Generator) -> np.ndarray:
        """在窗口内均匀采一点（索引空间均匀 → ENU，斜栅格下不采到带外）。"""
        idx_xy = rng.uniform(0.0, self._last_idx_xy)
        k = rng.uniform(*self._k_range)
        q = self.window.grid.index_to_enu(np.array([idx_xy[0], idx_xy[1], k]))[0]
        if self.ground_fn is not None and self.agl is not None:
            g = float(
                np.asarray(self.ground_fn(np.array([q[0]]), np.array([q[1]]))).reshape(-1)[0]
            )
            q[2] = g + rng.uniform(*self.agl)
        q[2] = float(np.clip(q[2], self.alt_range[0], self.alt_range[1]))
        return q

    def _steer(self, near: np.ndarray, q: np.ndarray) -> np.ndarray | None:
        delta = q - near
        dist = float(np.linalg.norm(delta))
        if dist < _EPS_M:
            return None
        step = float(
            np.clip(
                self.cfg.sdf_step_scale * float(self.field.at(near[None, :])[0]),
                self.cfg.min_step_m,
                self.cfg.step_m,
            )
        )
        step = min(step, dist)
        new = near + delta / dist * step
        if new[2] < self.alt_range[0] or new[2] > self.alt_range[1]:
            return None
        if not self._band_ok(new[None, :])[0]:
            return None
        return new

    # ---------- 主循环 ---------------------------------------------------

    def plan(
        self,
        start: Sequence[float],
        goal: Sequence[float],
        *,
        seed: int | None = None,
    ) -> RRTResult | None:
        """跑一次 RRT；``seed=None`` 用配置种子，多初值传 ``cfg.seed + i``。"""
        rng = np.random.default_rng(self.cfg.seed if seed is None else int(seed))
        start_p = np.asarray(start, dtype=np.float64)
        goal_p = np.asarray(goal, dtype=np.float64)
        self._start_p, self._goal_p = start_p, goal_p
        if not self._segment_free(start_p, start_p) or not self._segment_free(goal_p, goal_p):
            return None
        if self._segment_free(start_p, goal_p):
            return RRTResult(
                path_enu=np.vstack([start_p, goal_p]),
                iterations=0,
                nodes=0,
                path_points_before_shortcut=2,
            )

        nodes = [start_p]
        parents = [-1]
        for it in range(1, self.cfg.max_iters + 1):
            q = goal_p if rng.random() < self.cfg.goal_sample_rate else self._sample(rng)
            arr = np.vstack(nodes)
            nearest = int(np.linalg.norm(arr - q, axis=1).argmin())
            new = self._steer(arr[nearest], q)
            if new is None or not self._segment_free(arr[nearest], new):
                continue
            nodes.append(new)
            parents.append(nearest)
            if self._segment_free(new, goal_p):
                nodes.append(goal_p)
                parents.append(len(nodes) - 2)
                path = self._reconstruct(nodes, parents)
                raw = len(path)
                return RRTResult(
                    path_enu=self._shortcut(path),
                    iterations=it,
                    nodes=len(nodes),
                    path_points_before_shortcut=raw,
                )
        return None

    def plan_many(
        self,
        start: Sequence[float],
        goal: Sequence[float],
        *,
        initializations: int,
    ) -> list[RRTResult]:
        """跑 ``initializations`` 次独立 RRT（种子 ``cfg.seed + i``），返回全部成功候选。

        只负责生成候选，不评估、不去重、不选优——那是 ``initial_paths.py`` 的职责。
        """
        n = max(1, int(initializations))
        results: list[RRTResult] = []
        for i in range(n):
            result = self.plan(start, goal, seed=self.cfg.seed + i)
            if result is not None:
                results.append(result)
        return results

    def _reconstruct(self, nodes: list[np.ndarray], parents: list[int]) -> np.ndarray:
        chain: list[np.ndarray] = []
        idx = len(nodes) - 1
        while idx != -1:
            chain.append(nodes[idx])
            idx = parents[idx]
        return np.vstack(chain[::-1])

    def _shortcut(self, path: np.ndarray) -> np.ndarray:
        """贪心捷径：每段都过 ``_segment_free``，**不接任何未校验的边**。

        预算（``shortcut_iters``）用尽时把剩余原始节点原样接上——原始边都是
        RRT 生长时校验过的自由边。预算为 0 时路径原样返回（曾经退化成
        "start→goal 直线穿楼仍 ok"，见模块说明第 3 条）。

        预算是**软上限**：内层必须退到相邻点（``j == i+1``）才停，因为只有
        相邻边保证校验过；单段尝试次数会略超预算，宁可少省几次。
        """
        if len(path) <= 2:
            return path
        kept = [path[0]]
        i = 0
        budget = self.cfg.shortcut_iters
        while i < len(path) - 1 and budget > 0:
            j = len(path) - 1
            while j > i + 1:
                budget -= 1
                if self._segment_free(path[i], path[j]):
                    break
                j -= 1
            kept.append(path[j])  # j == i+1 时是原始相邻边，已校验过
            i = j
        if i < len(path) - 1:
            kept.extend(path[i + 1:])
        return np.vstack(kept)
