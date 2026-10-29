# -*- coding: utf-8 -*-
"""领域核心类型（L0，纯数据结构，无算法、无 IO）。

职责：定义全模块共享的名词，与论文术语一一对应：

    route       控制网络上的连续虚拟链路序列（可行"走廊"）  —— Lin2021 Fig.1(a)
    path        路径平滑后的三维空间几何曲线                —— Lin2021 Fig.1(b)
    trajectory  带时间维的时空轨迹（位置/速度/加速度）       —— Lin2021 Fig.1(c)

已定义类型：

    MotionState   运动状态 0..6（0=悬停；1..6=六方向最高速）  —— Lin2021 Fig.8(a)
    Building      建筑（底面多边形 + 竖向区间 [z0,z1]）
    StaticNoFly / Period / DynamicNoFly   禁飞区模型（design §15）
    ControlPoint / Link / VirtualLink / ExpandedNetwork
    Route / Path / Trajectory
    ODRequest / PlanResult / InfeasibilityReason / CostUpdate

不负责：参数默认值（config.py）、事件词汇表（events.py）、文件读写（scenario.py）。
映射：Lin2021 §2.1；design_nofly_zones.md §2、§15。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional

import numpy as np
from shapely.geometry.base import BaseGeometry

Vec3 = np.ndarray  # shape (3,)，局部米制 [x, y, z]

N_STATES = 7  # Lin2021 Fig.8(a)


class MotionState(IntEnum):
    """0 悬停；1..6 六个面方向的最高速度飞越。"""

    HOLD = 0
    PX = 1  # +x
    NX = 2  # -x
    PY = 3  # +y
    NY = 4  # -y
    PZ = 5  # +z（爬升）
    NZ = 6  # -z（下降）

    @staticmethod
    def from_dir(d: Vec3) -> "MotionState":
        """链路方向 → 主导轴状态 1..6；零向量退化为 HOLD。"""
        ax = int(np.argmax(np.abs(d)))
        sign = 1 if d[ax] >= 0 else -1
        table = {(0, 1): 1, (0, -1): 2, (1, 1): 3, (1, -1): 4, (2, 1): 5, (2, -1): 6}
        return MotionState(table[(ax, sign)])


def node_id(cp: int, state: int) -> int:
    return cp * N_STATES + int(state)


def node_cp(node: int) -> int:
    return node // N_STATES


def node_state(node: int) -> int:
    return node % N_STATES


# ── 环境对象 ──────────────────────────────────────────────────────────────


@dataclass
class Building:
    """建筑（长方体化）：底面多边形 + 竖向区间（局部米制）。"""

    bid: int
    poly: BaseGeometry
    z0: float = 0.0
    z1: float = 0.0
    bclass: str = ""
    height: float = 0.0


@dataclass
class StaticNoFly:
    """静态永久禁飞区：与时间无关，等价于静态障碍（离线吸收）。design §2.3。"""

    zid: str
    poly: BaseGeometry
    z0: float = 0.0
    z1: float = 1e9
    name: str = ""
    source: str = "unknown"
    buffer_m: Optional[float] = None  # None → 用全局 d_s_stat


@dataclass
class Period:
    """动态禁飞的一个时段（阶跃模型）：[start,end) 内几何固定。design §2.2。"""

    start: float  # 仿真相对秒
    end: float
    poly: BaseGeometry
    z0: float = 0.0
    z1: float = 1e9


@dataclass
class DynamicNoFly:
    """动态临时禁飞区：时段列表，几何可随项变化（完整形态）。"""

    zid: str
    periods: list[Period]
    name: str = ""
    version: int = 1
    buffer_m: Optional[float] = None  # None → 用全局 d_s_dyn
    revoked: bool = False

    def live_periods(self, t0: float, t1: float) -> list[tuple[float, float, Period]]:
        """与 [t0,t1] 相交的时段切片 [(ts, te, period)]，已裁剪到区间内。"""
        out = []
        if self.revoked:
            return out
        for p in self.periods:
            if p.end <= t0 or p.start >= t1:
                continue
            out.append((max(p.start, t0), min(p.end, t1), p))
        return out


@dataclass
class CityEnvironment:
    """场景内存形态：建筑 + 禁飞区 + 区域范围（全部局部米制）。"""

    buildings: list[Building] = field(default_factory=list)
    static_nofly: list[StaticNoFly] = field(default_factory=list)
    dynamic_nofly: list[DynamicNoFly] = field(default_factory=list)
    region: Optional[BaseGeometry] = None
    dem: Optional[Any] = None  # 预留：地面高程采样器（广州 DEM 暂缺，按平地）
    meta: dict = field(default_factory=dict)

    def summary(self) -> dict:
        hs = [b.height for b in self.buildings if b.height > 0]
        return {
            "n_buildings": len(self.buildings),
            "n_static_nofly": len(self.static_nofly),
            "n_dynamic_nofly": len(self.dynamic_nofly),
            "height_max": float(max(hs)) if hs else 0.0,
            "height_mean": float(np.mean(hs)) if hs else 0.0,
        }


# ── 网络对象 ──────────────────────────────────────────────────────────────


@dataclass
class ControlPoint:
    idx: int
    p: Vec3
    level: int = 0
    src: str = "building"  # building / top / free


@dataclass
class Link:
    """原始（未扩展）链路：控制点对 + 三维长度。"""

    a: int
    b: int
    dist: float


@dataclass
class VirtualLink:
    """扩展网络中的有向虚拟链路：(状态 sa 的 a) → (状态 sb 的 b)。"""

    u: int  # 虚拟节点 id = a*7 + sa
    v: int
    time: float  # 飞行时间（Lin2021 Eq.1-4）
    dist: float


@dataclass
class ExpandedNetwork:
    """扩展控制网络（7 状态 × 控制点）。

    `adj` 以虚拟节点 id（= cp*7 + state）为索引，元素 (v, time, dist)。
    """

    points: np.ndarray
    adj: list[list[tuple[int, float, float]]]
    links: list[VirtualLink] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def n_points(self) -> int:
        return int(self.points.shape[0])

    @property
    def n_nodes(self) -> int:
        return len(self.adj)

    def point_of(self, node: int) -> Vec3:
        return self.points[node // N_STATES]


# ── 结果对象 ──────────────────────────────────────────────────────────────


@dataclass
class Route:
    nodes: list[int]  # 虚拟节点 id 序列
    points: list[int]  # 控制点序号序列
    time: float = 0.0
    dist: float = 0.0


@dataclass
class Path:
    pts: np.ndarray  # (M,3) 采样点
    arc_length: float = 0.0
    deviations: list[float] = field(default_factory=list)


@dataclass
class Trajectory:
    pts: np.ndarray
    times: np.ndarray
    speeds: np.ndarray

    @property
    def total_time(self) -> float:
        return float(self.times[-1]) if len(self.times) else 0.0


class InfeasibilityReason(IntEnum):
    NO_ROUTE = 1
    STATIC_BLOCKED = 2
    DYNAMIC_BLOCKED = 3
    ORIGIN_UNREACHABLE = 4
    DEST_UNREACHABLE = 5


@dataclass
class ODRequest:
    origin: Vec3
    dest: Vec3
    t_dep: float = 0.0
    uav: Any = None


@dataclass
class PlanResult:
    route: Optional[Route] = None
    path: Optional[Path] = None
    trajectory: Optional[Trajectory] = None
    total_time: float = float("inf")
    total_tpr: float = 0.0  # λ_r = 0（槽位保留）
    feasible: bool = False
    infeasible_reason: Optional[InfeasibilityReason] = None
    level: str = ""  # L1 / L2 / L2.5 / L4
    events: list[Any] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


@dataclass
class CostUpdate:
    """闭环交换物：(i,j,k) → 真实综合代价或 ∞+原因。"""

    entries: list[tuple[int, int, int, float, Optional[InfeasibilityReason]]] = field(
        default_factory=list
    )
