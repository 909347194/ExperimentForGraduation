# -*- coding: utf-8 -*-
"""禁飞空域模型：静态永久禁飞 + 动态临时禁飞。

静态（无时间）——等价于静态障碍，离线吸收：
    static_blocked(p, q, margin) 线段是否穿越任一静态禁飞膨胀体

动态（带时间）——**按时窗端点切段 + 每段静态求交**（design §3.2）：
    沿飞行区间 [t_dep, t_arr] 收集所有禁飞时窗端点作为切点，切成若干子区间；
    每个子区间内"哪些禁飞区生效、几何是什么"都是常量，于是该段退化为纯静态求交。
    阶跃模型下这是**精确判定**，且无采样漏判；段数通常 1–3。

膨胀距离分设：静态 d_s_stat（法定间隔）、动态 d_s_dyn（临时管制余量）。

不负责：策略与迭代（online/）；统一对外入口在 query.py。
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
from shapely.geometry import box
from shapely.strtree import STRtree

from path_planning.common.types import DynamicNoFly, Period, StaticNoFly
from path_planning.utils.geometry import segment_prism_hit


class NoFlyQuery:
    """禁飞区查询：无时间入口 + 带时间入口，共用同一几何内核。"""

    def __init__(
        self,
        static: Sequence[StaticNoFly],
        dynamic: Sequence[DynamicNoFly],
        d_s_stat: float = 5.0,
        d_s_dyn: float = 2.0,
        pad: float = 0.0,
    ):
        self.static = [z for z in static]
        self.dynamic = [z for z in dynamic if not z.revoked and z.periods]
        self.d_s_stat = float(d_s_stat)
        self.d_s_dyn = float(d_s_dyn)
        # 搜索期额外余量：把平滑偏移（Dubins 曲线相对直线走廊的偏离）并进膨胀，
        # 降低"搜索期判可行、平滑后翻盘"的比例
        self.pad = float(pad)

        self._stat_polys = [z.poly for z in self.static]
        self._stat_tree = STRtree(self._stat_polys) if self._stat_polys else None

        # 动态：所有时段的几何统一进 2D 索引（判定时再按时段过滤）
        self._dyn_items = []  # (poly, zone_idx, period_idx)
        for zi, z in enumerate(self.dynamic):
            for pi, p in enumerate(z.periods):
                self._dyn_items.append((p.poly, zi, pi))
        self._dyn_tree = (
            STRtree([it[0] for it in self._dyn_items]) if self._dyn_items else None
        )

    # ── 无时间入口：静态禁飞 ──────────────────────────────────────────────

    def _static_margin(self, z: StaticNoFly) -> float:
        return self.d_s_stat if z.buffer_m is None else z.buffer_m

    def static_blocked(self, p: Sequence[float], q: Sequence[float], margin: Optional[float] = None) -> bool:
        if not self.static:
            return False
        m = self.d_s_stat if margin is None else margin
        x0, x1 = min(p[0], q[0]) - m, max(p[0], q[0]) + m
        y0, y1 = min(p[1], q[1]) - m, max(p[1], q[1]) + m
        if self._stat_tree is None:
            return False

        for i in self._stat_tree.query(box(x0, y0, x1, y1)):
            z = self.static[int(i)]
            if segment_prism_hit(p, q, z.poly, z.z0, z.z1, margin=self._static_margin(z)):
                return True
        return False

    def static_blocking_ids(self, p, q) -> list[str]:
        out = []
        if self._stat_tree is None:
            return out

        m = self.d_s_stat
        for i in self._stat_tree.query(
            box(min(p[0], q[0]) - m, min(p[1], q[1]) - m, max(p[0], q[0]) + m, max(p[1], q[1]) + m)
        ):
            z = self.static[int(i)]
            if segment_prism_hit(p, q, z.poly, z.z0, z.z1, margin=self._static_margin(z)):
                out.append(z.zid)
        return out

    # ── 带时间入口：动态禁飞（切段求交） ──────────────────────────────────

    def _active_zones(self, t: float):
        """时刻 t 生效的 (zone_idx, period_idx)。"""
        for zi, z in enumerate(self.dynamic):
            for pi, p in enumerate(z.periods):
                if p.start <= t < p.end:
                    yield zi, pi

    def _split_times(self, t0: float, t1: float) -> list[float]:
        """[t0,t1] 内所有时窗端点（切点），升序、去重、含端点。"""
        pts = {t0, t1}
        for z in self.dynamic:
            for p in z.periods:
                if t0 < p.start < t1:
                    pts.add(p.start)
                if t0 < p.end < t1:
                    pts.add(p.end)
        return sorted(pts)

    def dynamic_blocked(
        self,
        p: Sequence[float],
        q: Sequence[float],
        t_dep: float,
        t_arr: float,
    ) -> bool:
        """整边时空占用检查：沿 [t_dep,t_arr] 是否落入任何生效禁飞体。

        实现：按时窗端点切段 → 每段取该时段几何 → 静态求交。design §3.2。
        """
        if not self.dynamic or t_arr <= t_dep:
            return False
        p = np.asarray(p, dtype=float)
        q = np.asarray(q, dtype=float)
        ts = self._split_times(t_dep, t_arr)

        for a, b in zip(ts[:-1], ts[1:]):
            if b <= a:
                continue
            # 子区间 [a,b) 的起止三维位置（匀速直线假设）
            fa = (a - t_dep) / (t_arr - t_dep)
            fb = (b - t_dep) / (t_arr - t_dep)
            pa = p + fa * (q - p)
            pb = p + fb * (q - p)
            tm = 0.5 * (a + b)
            m = self.d_s_dyn + self.pad
            for k in self._dyn_candidates(pa, pb, m):
                _, zi, pi = self._dyn_items[k]
                per = self.dynamic[zi].periods[pi]
                if not (per.start <= tm < per.end):
                    continue
                if segment_prism_hit(pa, pb, per.poly, per.z0, per.z1, margin=m):
                    return True
        return False

    def dynamic_blocking(
        self,
        p: Sequence[float],
        q: Sequence[float],
        t_dep: float,
        t_arr: float,
    ) -> list[tuple[str, float, float]]:
        """返回冲突详情 [(zid, 冲突段起始时刻, 结束时刻)]（供 L2 诊断与报告）。"""
        out: list[tuple[str, float, float]] = []
        if not self.dynamic or t_arr <= t_dep:
            return out
        p = np.asarray(p, dtype=float)
        q = np.asarray(q, dtype=float)

        ts = self._split_times(t_dep, t_arr)
        for a, b in zip(ts[:-1], ts[1:]):
            if b <= a:
                continue
            fa = (a - t_dep) / (t_arr - t_dep)
            fb = (b - t_dep) / (t_arr - t_dep)
            pa = p + fa * (q - p)
            pb = p + fb * (q - p)
            tm = 0.5 * (a + b)
            m = self.d_s_dyn + self.pad
            for k in self._dyn_candidates(pa, pb, m):
                _, zi, pi = self._dyn_items[k]
                per = self.dynamic[zi].periods[pi]
                if not (per.start <= tm < per.end):
                    continue
                if segment_prism_hit(pa, pb, per.poly, per.z0, per.z1, margin=m):
                    out.append((self.dynamic[zi].zid, a, b))
        return out

    def conservative_blocked(self, p: Sequence[float], q: Sequence[float], t0: float, t1: float) -> bool:
        """L2.5 保守判据：任务时段 [t0,t1] 内**任何时刻**可能冲突即判冲突。

        等价于把每个时段的时间维展平——忽略具体到达时刻，只看几何是否与任一
        时段禁飞体相交且 z 区间重叠。用于"删掉所有可能冲突的边"后重跑。
        """
        if not self.dynamic:
            return False
        m = self.d_s_dyn

        seen = set()
        for k in self._dyn_candidates(p, q, m):
            _, zi, pi = self._dyn_items[k]
            if (zi, pi) in seen:
                continue
            seen.add((zi, pi))
            per = self.dynamic[zi].periods[pi]
            if per.end <= t0 or per.start >= t1:  # 与任务时段不相交
                continue
            if segment_prism_hit(p, q, per.poly, per.z0, per.z1, margin=m):
                return True
        return False

    def _dyn_candidates(self, p, q, m: float) -> list[int]:
        """2D 索引筛选：与线段包围盒（含膨胀）相交的时段几何索引。"""
        if self._dyn_tree is None:
            return []
        bx = box(
            min(p[0], q[0]) - m, min(p[1], q[1]) - m,
            max(p[0], q[0]) + m, max(p[1], q[1]) + m,
        )
        return [int(i) for i in self._dyn_tree.query(bx)]
