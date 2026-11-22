# -*- coding: utf-8 -*-
"""局部域：一次冲突对应的"改哪里"（空间圆域 + 时间窗 + 过渡权重）。

职责：由冲突对的 CPA 时刻与双方位置定出局部域中心、半径与时间窗，并给出
      让路位移的过渡权重（中心全量、边界为零）。
边界：不生成候选几何、不重规划、不判定；只提供域与权重。
相关：``conflict/conflict_detector.py``（冲突对来源）、``detour_candidates.py``
      （消费者）、``docs/conflict_model.md`` §10.2（"只改几何"的自由度）。

**为什么要有过渡权重**：若只把冲突处的几个折点平移到新位置、其余点不动，折线上
会出现一个尖角（节点间位移突变）。那不是"让路"，是"拐折"——内层 NLP 会用极大的
加速度去追它，要么解不出、要么解出一个加速度贴上限的轨迹。用余弦钟形把位移从
中心按距离衰减到边界 0，折线保持 C⁰ 平滑，位移在域边界自然闭合。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from path_planning.common.plan_types import Trajectory
from path_planning.methods.timeline.splines import position_at

__all__ = ["LocalRegion", "region_from_conflict", "transition_weight"]


@dataclass(frozen=True)
class LocalRegion:
    """局部重规划域：水平圆域 ``center ± radius``，附冲突时段。

    Attributes:
        center_xy: 域中心（水平 ENU，米）
        radius_m:  水平半径（米）；0 视为非法
        t_cpa:     冲突时刻（诊断与日志用，不改变冻结的 t0）
        time_pad_s: 冲突时段松弛量（秒）
    """

    center_xy: tuple[float, float]
    radius_m: float
    t_cpa: float
    time_pad_s: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.radius_m) or self.radius_m <= 0.0:
            raise ValueError(f"radius_m 必须为正有限值：{self.radius_m}")
        if self.time_pad_s < 0.0:
            raise ValueError(f"time_pad_s 不能为负：{self.time_pad_s}")

    @property
    def time_window(self) -> tuple[float, float]:
        return (self.t_cpa - self.time_pad_s, self.t_cpa + self.time_pad_s)

    def distance_xy(self, xy) -> float:
        """点到域中心的水平距离。"""
        dx = float(xy[0]) - self.center_xy[0]
        dy = float(xy[1]) - self.center_xy[1]
        return math.hypot(dx, dy)

    def contains_xy(self, xy) -> bool:
        return self.distance_xy(xy) <= self.radius_m

    def weight(self, xy) -> float:
        """过渡权重：中心 1、边界 0（余弦钟形，``cos`` 保证一阶光滑）。"""
        return transition_weight(self.distance_xy(xy), self.radius_m)


def transition_weight(distance_m: float, radius_m: float) -> float:
    """余弦钟形权重：``d >= r`` 为 0，``d = 0`` 为 1。"""
    if radius_m <= 0.0:
        raise ValueError(f"radius_m 必须为正：{radius_m}")
    if distance_m <= 0.0:
        return 1.0
    if distance_m >= radius_m:
        return 0.0
    return 0.5 * (1.0 + math.cos(math.pi * distance_m / radius_m))


def region_from_conflict(
    traj_a: Trajectory,
    traj_b: Trajectory,
    cpa_time: float | None,
    *,
    margin_m: float,
    time_pad_s: float = 10.0,
) -> LocalRegion:
    """由一对冲突航迹与 CPA 时刻构出局部域。

    中心取两机在 CPA 时刻位置的**中点**（谁的航迹可动都能覆盖到）：若取可动侧的
    位置，可动侧一旦已经偏离原航迹，域就会偏到被让路的一方那一侧，改不到真正
    需要挪动的段。

    ``cpa_time`` 为 ``None``（几何上逼近但没有时间重叠的判定）时退化为"两轨迹
    共同时段中点"——这种情形本不该判冲突，但作为兜底不能让域无中心。
    """
    if cpa_time is None:
        t = 0.5 * (max(traj_a.t0, traj_b.t0) + min(traj_a.t_end, traj_b.t_end))
    else:
        t = float(cpa_time)
    pa = position_at(traj_a, t)
    pb = position_at(traj_b, t)
    center = (0.5 * (pa[0] + pb[0]), 0.5 * (pa[1] + pb[1]))
    return LocalRegion(
        center_xy=center, radius_m=float(margin_m), t_cpa=t, time_pad_s=float(time_pad_s)
    )
