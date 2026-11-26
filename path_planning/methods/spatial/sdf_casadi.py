# -*- coding: utf-8 -*-
"""``SdfField`` → CasADi 三线性插值：NLP 里"净空"的唯一读数来源。

职责：把窗内 SDF 包成 CasADi 可微函数，提供 ENU→连续索引换算与单点取值。
边界：只当"SDF 与求解器之间的转接头"（索引空间、插值器、ENU↔索引），
      不含任何优化建模——换个距离场不应改 NLP 文件。
相关：``occupancy_sdf.py``（``SdfField``）、``common/grid_spec.py``（斜栅格）、
      ``trajectory_optimizer.py``（消费 ``at`` / ``index_of``）。

casadi 是**可选依赖**：没装时几何层（RRT / 走廊 / 建场）照常用，只有 NLP 不可用。
``ca.interpolant`` 是**一个三维输入**的函数（不是三个标量入参），``at`` 里必须 vertcat。
"""

from __future__ import annotations

import itertools

try:  # 可选依赖：瘦装环境只有几何层
    import casadi as ca
except ImportError:  # pragma: no cover
    ca = None

import numpy as np

from path_planning.methods.spatial.occupancy_sdf import SdfField

__all__ = ["SdfOnGrid", "casadi_available", "sdf_cache_key"]


def sdf_cache_key(field) -> tuple:
    """缓存键：场对象身份 + 栅格契约（形状/原点/间距/朝向/偏置）。

    缓存本身挂在 ``TourPlanningContext`` 上，生命周期与一次装配绑定；这里多带
    栅格契约是为防止同一个 id 在长时间进程里被回收复用后命中旧插值器。场数据是
    只读的（``WorldField`` / ``SdfField`` 都是 frozen dataclass），不把 685 MB 的
    SDF 内容做哈希——那会把"省构建"变成"每次全量读一遍"。
    """
    g = field.grid
    return (
        id(field), tuple(int(v) for v in g.shape),
        tuple(float(v) for v in g.origin), tuple(float(v) for v in g.spacing),
        float(g.yaw_rad), float(getattr(field, "sample_bias_m", 0.0)),
    )

#: 同一进程多次求解时给插值器换名字，避免 Function 重名
_INTERPOLANT_SEQ = itertools.count()


def casadi_available() -> bool:
    return ca is not None


class SdfOnGrid:
    """ENU 米制入参、索引空间内插的 SDF 读数器（正 = 自由，单位米）。

    **两种存储布局都要认**：``SdfField`` 是 ``(nx, ny, nz)``（窗口场），
    ``WorldField`` 是 **z-major** ``(nz, nx, ny)``（逐层连续内存，取样快一个量级）。
    早先这里直接 ``field.sdf.ravel(order="F")``，把 z-major 数组按"i 变化最快"喂给
    ``interpolant``，等于整场被转置：同一个 ENU 点上 ``SdfOnGrid`` 读 68.62 m、
    ``WorldField.at`` 读 53.01 m（实测差到 30 m 量级）。局部重规划正是把 WorldField
    直接传进来，于是内层在**错位的场**上求净空：节点被推到 155 m、弦上读到 −60 m，
    IPOPT 只能报 Infeasible。故此处显式归一化到 ``(nx, ny, nz)``，并对形状不符抛错。
    """

    def __init__(self, field) -> None:
        if ca is None:
            raise RuntimeError("未安装 casadi：uv pip install casadi 后重跑")
        grid = field.grid
        self.shape = tuple(int(v) for v in grid.shape)
        nx, ny, nz = self.shape
        arr = np.asarray(field.sdf, dtype=float)
        if arr.shape == (nz, nx, ny):  # WorldField 的 z-major
            arr = np.transpose(arr, (1, 2, 0))
        elif arr.shape != (nx, ny, nz):
            raise ValueError(
                f"sdf 形状 {arr.shape} 既不是 (nx,ny,nz)={self.shape}，"
                f"也不是 z-major (nz,nx,ny)={(nz, nx, ny)}"
            )
        # 窗口场（SdfField）走 window_sdf 时已扣过偏置；WorldField 在这里统一扣，
        # 否则"换了场对象却少扣偏置"会让净空判断静默变乐观。
        if getattr(field, "sample_bias_m", 0.0):
            arr = arr - float(field.sample_bias_m)
        axes = [np.arange(n, dtype=float).tolist() for n in self.shape]
        # i 变化最快（Fortran 序），与 interpolant 的网格轴序 [x,y,z] 对齐
        values = arr.ravel(order="F").tolist()
        self._fun = ca.interpolant(
            f"sdf_linear_{next(_INTERPOLANT_SEQ)}", "linear", axes, values
        )
        self._a0, self._a1 = grid.axes_xy
        self._spacing = tuple(float(v) for v in grid.spacing)
        self._origin = tuple(float(v) for v in grid.origin)

    def index_of(self, x3):
        """``3×N`` 的 MX → 三个连续索引（沿走廊轴、横向、绝对 z）。"""
        sx, sy, sz = self._spacing
        ox, oy, oz = self._origin
        dx, dy = x3[0, :] - ox, x3[1, :] - oy
        ii = (self._a0[0] * dx + self._a0[1] * dy) / sx
        jj = (self._a1[0] * dx + self._a1[1] * dy) / sy
        kk = (x3[2, :] - oz) / sz
        return ii, jj, kk

    def at(self, p):
        """单点（``3×1`` MX）处的 SDF 值（米，正 = 自由）。"""
        ii, jj, kk = self.index_of(p)
        return self._fun(ca.vertcat(ii, jj, kk))
