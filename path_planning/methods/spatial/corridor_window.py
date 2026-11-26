# -*- coding: utf-8 -*-
"""走廊窗口：把"起终点连线 ± 半宽"变成沿走廊轴向的斜栅格。

职责：按 ``CorridorRule`` 解析半宽、构造 **斜栅格**（轴 0 = 起终点水平方向），
      并提供"点在窗内""侧向距离""ENU 外接盒"三个查询。
边界：不建 SDF、不做路径搜索；局部域收缩在 ``coordination`` 层。
相关：``common/grid_spec.py``（斜栅格契约）、``world_field.py``（窗口取样）、
      ``rrt_path_search.py``（在窗内采样）。

为什么要让栅格转：窗口的格数正比于走廊带面积 ``L × 2(half_width + margin)``，而
ENU 外接矩形在斜向走廊下会多算 1.6–3.4 倍。5 km 级场景里那是几千万格，CasADi 插值器
根本建不起来。

两点口径，别混：

* 栅格在 ENU 里是**斜的**，但坐标仍是 ENU 米制——只在 ``GridSpec.index_to_enu`` /
  ``enu_to_index`` 里旋转，别处不做局部系换算。
* 走廊 = **计算域**，也是**解域**：带外没算过 SDF，所以带外一律不可用。这不是可选
  开关；窄走廊会把"可解"变"无解"，由 ``RRT_NO_PATH`` 如实报出，不静默放行。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

import numpy as np

from path_planning.common.grid_spec import GridSpec

__all__ = [
    "CorridorRule",
    "CorridorWindow",
    "corridor_window",
    "required_pad_m",
    "window_area_m2",
    "field_z_envelope",
]

_MIN_HORIZONTAL_M = 1e-6
#: 索引空间的浮点容差（单位：格）。斜栅格 index→ENU→index 往返有约 1e-13 格误差；
#: margin=0 时起点正好落在格 0 上，会被判成"窗外"，整条航段白报无解。
_EPS_INDEX = 1e-6


@dataclass(frozen=True)
class CorridorRule:
    """走廊几何规则。

    Attributes:
        width_ratio:  走廊**总宽** / 水平起终点距离；半宽 = 0.5·ratio·L
        half_width_m: 直接给半宽（优先于 ``width_ratio``）
        half_width_min_m: 比例口径的**绝对下限**（米）。纯比例口径在短航段上会退化成
                     一条直线（30 m 航段 0.5·0.1·L = 1.5 m，横向自由度为零），给地板
                     保住横向自由度；显式 ``half_width_m`` 不受它影响
        margin_m:     走廊外扩余量，轴向与横向各加一圈（建场取窗用，不是交付约束）
    """

    width_ratio: float | None = None
    half_width_m: float | None = None
    half_width_min_m: float = 0.0
    margin_m: float = 0.0

    def half_width_for(self, horizontal_distance_m: float) -> float:
        """解析半宽（米）：显式 ``half_width_m`` 优先，否则 max(0.5·ratio·L, 下限)。"""
        if self.half_width_m is not None:
            return float(self.half_width_m)
        if self.width_ratio is not None:
            return max(0.5 * float(self.width_ratio) * float(horizontal_distance_m),
                       float(self.half_width_min_m))
        return float(self.half_width_min_m)


@dataclass(frozen=True)
class CorridorWindow:
    """绕水平起终点线段的走廊窗口（斜栅格）。

    Attributes:
        grid:         窗口栅格（``yaw_rad`` = 起终点方位角）
        start_xy/goal_xy: 水平端点
        u:            起→终 水平单位向量（= 栅格轴 0 方向）
        normal:       水平单位法向（= 栅格轴 1 方向）
        half_width_m: 用户要求的半宽
        lateral_m:    栅格实际半宽 = half_width + margin（且不小于两格）
    """

    grid: GridSpec
    start_xy: tuple[float, float]
    goal_xy: tuple[float, float]
    u: np.ndarray
    normal: np.ndarray
    half_width_m: float
    lateral_m: float

    @property
    def horizontal_length_m(self) -> float:
        dx = self.goal_xy[0] - self.start_xy[0]
        dy = self.goal_xy[1] - self.start_xy[1]
        return math.hypot(dx, dy)

    def lateral_of(self, pts: np.ndarray) -> np.ndarray:
        """点到走廊中心轴的**水平**侧向距离（绝对值）。"""
        d = np.asarray(pts, dtype=np.float64)[..., :2] - np.asarray(
            self.start_xy, dtype=np.float64
        )
        return np.abs(d @ self.normal)

    def inside_grid(self, pts: np.ndarray) -> np.ndarray:
        """是否落在窗口栅格内（ENU 入参；内部走 ``enu_to_index``，斜栅格同样成立）。

        窗口外一律不采、不判——没算过 SDF 的地方不能当自由空间。
        """
        idx = self.grid.enu_to_index(np.asarray(pts, dtype=np.float64))
        upper = np.asarray(self.grid.shape, dtype=np.float64) - 1.0
        return np.all((idx >= -_EPS_INDEX) & (idx <= upper + _EPS_INDEX), axis=-1)

    @property
    def enu_bounds(self) -> tuple[float, float, float, float]:
        """窗口四个水平角点的 ENU 外接矩形（只用于建筑粗筛）。

        斜栅格下它的面积**大于**栅格本身，所以按它筛出的是超集；真正挡路的由逐格
        栅格化决定。
        """
        nx, ny = self.grid.shape[0], self.grid.shape[1]
        corners = self.grid.index_to_enu(
            np.array(
                [[0.0, 0.0, 0.0], [nx - 1.0, 0.0, 0.0], [0.0, ny - 1.0, 0.0],
                 [nx - 1.0, ny - 1.0, 0.0]],
                dtype=np.float64,
            )
        )
        return (
            float(corners[:, 0].min()),
            float(corners[:, 1].min()),
            float(corners[:, 0].max()),
            float(corners[:, 1].max()),
        )


def corridor_window(
    start_enu: tuple[float, float, float],
    goal_enu: tuple[float, float, float],
    rule: CorridorRule,
    spacing: tuple[float, float, float],
    alt_min_m: float,
    alt_max_m: float,
    *,
    lateral_floor_cells: float = 2.0,
) -> CorridorWindow:
    """把走廊（+margin）转成沿走廊轴向的窗口栅格。

    轴向：轴 0 = 起终点水平方向，轴 1 = 其法向，轴 2 = 绝对 z。
    沿轴范围 ``[-margin, L+margin]``，横向 ``±lateral``，竖向覆盖整个飞行带。

    Args:
        start_enu / goal_enu: 端点（米，绝对 z）
        rule:     走廊规则
        spacing:  体素间距
        alt_min_m / alt_max_m: 飞行带绝对 z 包络
        lateral_floor_cells: 横向半宽至少几格（避免半宽过小导致只有 1 列格点）

    Raises:
        ValueError: 起终点水平重合，或飞行带退化。
    """
    sx, sy, sz = (float(v) for v in spacing)
    if alt_max_m <= alt_min_m:
        raise ValueError(f"飞行带退化：alt_min={alt_min_m}, alt_max={alt_max_m}")

    dx = float(goal_enu[0]) - float(start_enu[0])
    dy = float(goal_enu[1]) - float(start_enu[1])
    length = math.hypot(dx, dy)
    if length < _MIN_HORIZONTAL_M:
        raise ValueError("起终点水平重合，无法构造走廊（应在请求准入阶段先拦截）")
    u = np.array([dx / length, dy / length], dtype=np.float64)
    normal = np.array([-u[1], u[0]], dtype=np.float64)

    margin = float(rule.margin_m)
    half_width = rule.half_width_for(length)
    lateral = max(half_width + margin, lateral_floor_cells * max(sx, sy))
    start_xy = (float(start_enu[0]), float(start_enu[1]))
    goal_xy = (float(goal_enu[0]), float(goal_enu[1]))

    n_along = math.ceil((length + 2.0 * margin) / sx) + 1
    n_lateral = math.ceil(2.0 * lateral / sy) + 1
    # 竖向必须 ceil 而不是 round：不整除时 round 会向下取整，窗口顶低于带给定的
    # 天花板，带顶附近的合法点被判成窗外或取到边缘 SDF
    n_z = math.ceil((alt_max_m - alt_min_m) / sz) + 1

    # 斜栅格原点 = 起点 - margin·u - lateral·normal（轴 0/1 就是 u/normal）
    corner = np.asarray(start_xy, dtype=np.float64) - margin * u - lateral * normal
    grid = GridSpec(
        origin=(float(corner[0]), float(corner[1]), float(alt_min_m)),
        spacing=(sx, sy, sz),
        shape=(n_along, n_lateral, n_z),
        yaw_rad=math.atan2(float(u[1]), float(u[0])),
    )
    return CorridorWindow(
        grid=grid,
        start_xy=start_xy,
        goal_xy=goal_xy,
        u=u,
        normal=normal,
        half_width_m=half_width,
        lateral_m=lateral,
    )


def window_area_m2(window: CorridorWindow) -> float:
    """窗口水平面积（= 走廊带面积，不是 ENU 外接矩形）。"""
    nx, ny = window.grid.shape[0], window.grid.shape[1]
    sx, sy = window.grid.spacing[0], window.grid.spacing[1]
    return (nx - 1) * sx * (ny - 1) * sy


def field_z_envelope(
    ground_min_m: float, alt_max_m: float
) -> tuple[float, float]:
    """飞行绝对高度带 → 建场竖直包络（航段与试跑脚本共用这一处口径）。

    下界取最低地面，上界先取飞行绝对上界；建场时再按最高楼顶兜底抬高。
    """
    return float(ground_min_m), float(alt_max_m)


def required_pad_m(
    rule: CorridorRule,
    spacing: tuple[float, float, float],
    max_od_length_m: float,
    *,
    extra_m: float,
    min_pad_m: float = 0.0,
) -> float:
    """任意合法 OD 的走廊窗口不出场所需的**水平外扩**（米）。

    ``build_world_field(pad_m=...)`` 与请求准入共用这一处，不许写第二份。三处容易算错：

    * 用**最终 lateral**（``max(half_width + margin, 2·格距)``），不是 ``0.5·ratio·L``：
      显式给定半宽会整个绕过 ratio，按 ratio 算会漏掉 margin 与显式半宽。
    * 窗口是**斜**的：角点相对端点的单轴最大偏移是 ``hypot(轴向, 横向)``，不是两者
      之和，也不是只看 lateral。
    * 结果必须是两条水平间距的**公倍数**：``build_world_field`` 用同一个 pad 外扩 x/y，
      pad 不是某轴整数倍时该轴全域格点整体平移，所有窗口的取样值改变、历史基线不可复现。
    """
    sx, sy, _ = (float(v) for v in spacing)
    lateral = max(
        rule.half_width_for(max_od_length_m) + rule.margin_m, 2.0 * max(sx, sy)
    )
    reach = math.hypot(rule.margin_m + sx, lateral + sy) + float(extra_m)
    step = _aligned_step(sx, sy)
    return max(math.ceil(reach / step), math.ceil(float(min_pad_m) / step)) * step


def _aligned_step(sx: float, sy: float) -> float:
    """两条水平轴**同时**对齐所需的最小步长 = lcm(sx, sy)（按有理数算）。

    只取 ``min(sx, sy)`` 是错的：``(6,4,2)`` 时 min=4，pad 不是 6 的倍数，x 轴格点
    整体平移，所有取样位置改变，历史基线不可复现。
    """
    fa = Fraction(str(float(sx))).limit_denominator(10_000)
    fb = Fraction(str(float(sy))).limit_denominator(10_000)
    step = float(
        Fraction(
            math.lcm(fa.numerator, fb.numerator),
            math.gcd(fa.denominator, fb.denominator),
        )
    )
    if not math.isfinite(step) or step <= 0.0 or step > 1e4:
        raise ValueError(
            f"水平格距 ({sx}, {sy}) 无法对齐：请让两者可公度，或设成相同值"
        )
    return step
