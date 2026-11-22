# -*- coding: utf-8 -*-
"""半开时间/空间区间与区间集合运算。

职责：定义 ``Interval``（半开 ``[start, end)``）与 ``IntervalSet``，提供
      规范化合并、并、交、补、时长、包含判定与"是否覆盖某段"的查询。
边界：纯区间代数，不含轨迹或冲突语义；不依赖 numpy。
相关：``methods/conflict/detector.py``（冲突时段）、``methods/timeline/feasibility.py``
      （悬停时段与飞行时段拼接）、``common/budget.py``（时间累加）。

为什么用半开区间而不是闭区间：

相邻两段 ``[0, 5)`` 与 ``[5, 10)`` 拼接后应恰好是 ``[0, 10)``，不含重叠也不留缝。
闭区间会得到 ``[0,5] ∪ [5,10] = [0,10]``，时长算成 11 而不是 10；而按
``end - start`` 求和又得 10——两套算法对不上，是这类代码最典型的 1 个点误差来源。
半开区间下 ``duration = end - start`` 恒等于真实时长，无需 +1。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

__all__ = [
    "MIN_INTERVAL",
    "Interval",
    "IntervalSet",
]

#: 区间长度下界。短于此视为退化区间（零时长），在规范化时丢弃。
#: 取 1e-9 s：比任何有意义的时间尺度都小，但足以吸收浮点累加误差。
MIN_INTERVAL = 1e-9


@dataclass(frozen=True, order=True)
class Interval:
    """半开区间 ``[start, end)``。

    构造即校验：``end`` 必须严格大于 ``start``。允许长度为 0 的退化区间存在
    于分析中（例如瞬时接触），但不参与规范化保留。
    """

    start: float
    end: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.start) or not math.isfinite(self.end):
            raise ValueError(f"区间端点必须有限：[{self.start}, {self.end})")
        if self.end < self.start:
            raise ValueError(f"区间 end 小于 start：[{self.start}, {self.end})")

    # --- 基本量 ---------------------------------------------------------

    @property
    def duration(self) -> float:
        """时长。半开口径下恒为 ``end - start``，相邻段拼接不产生 1 个点误差。"""
        return self.end - self.start

    @property
    def is_degenerate(self) -> bool:
        return self.duration < MIN_INTERVAL

    @property
    def midpoint(self) -> float:
        return 0.5 * (self.start + self.end)

    # --- 关系判定 -------------------------------------------------------

    def contains(self, t: float) -> bool:
        """``t`` 是否落在 ``[start, end)`` 内。"""
        return self.start <= t < self.end

    def overlaps(self, other: Interval) -> bool:
        """是否有正长度重叠（仅端点接触不算）。"""
        return min(self.end, other.end) - max(self.start, other.start) > MIN_INTERVAL

    def touches(self, other: Interval) -> bool:
        """是否端点接触（拼接关系的判定）。"""
        return (
            abs(self.end - other.start) < MIN_INTERVAL
            or abs(other.end - self.start) < MIN_INTERVAL
        )

    def intersect(self, other: Interval) -> Interval | None:
        lo = max(self.start, other.start)
        hi = min(self.end, other.end)
        if hi - lo < MIN_INTERVAL:
            return None
        return Interval(lo, hi)

    # --- 变换 -----------------------------------------------------------

    def shift(self, dt: float) -> Interval:
        return Interval(self.start + dt, self.end + dt)

    def clamp(self, lo: float, hi: float) -> Interval | None:
        """裁到 ``[lo, hi)``，裁空返回 ``None``。"""
        a = max(self.start, lo)
        b = min(self.end, hi)
        if b - a < MIN_INTERVAL:
            return None
        return Interval(a, b)

    def __bool__(self) -> bool:
        # 显式定义：区间恒为真值。不定义 __len__ 以免 len() 误用（无 __len__ 时
        # len() 自然抛 TypeError，而 bool() 不会被牵连）。
        return True


class IntervalSet:
    """有序、不相交、已合并的区间集合。

    内部不变式：区间按 ``start`` 升序、任意相邻两项既不重叠也不接触
    （否则说明还能合并）。所有构造入口都强制规范化。
    """

    __slots__ = ("_items",)

    def __init__(self, items: Iterable[Interval] = ()) -> None:
        self._items: tuple[Interval, ...] = _normalize(items)

    # --- 构造 -----------------------------------------------------------

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[float, float]]) -> IntervalSet:
        return cls(Interval(a, b) for a, b in pairs)

    @classmethod
    def single(cls, start: float, end: float) -> IntervalSet:
        return cls([Interval(start, end)])

    # --- 只读视图 -------------------------------------------------------

    @property
    def items(self) -> tuple[Interval, ...]:
        return self._items

    @property
    def total_duration(self) -> float:
        return sum(iv.duration for iv in self._items)

    @property
    def is_empty(self) -> bool:
        return not self._items

    @property
    def span(self) -> Interval | None:
        """最小外接区间；空集返回 ``None``。"""
        if not self._items:
            return None
        return Interval(self._items[0].start, self._items[-1].end)

    def __iter__(self) -> Iterator[Interval]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __contains__(self, t: object) -> bool:
        if not isinstance(t, (int, float)):
            return False
        return any(iv.contains(float(t)) for iv in self._items)

    def __repr__(self) -> str:
        parts = ", ".join(f"[{iv.start:.6g}, {iv.end:.6g})" for iv in self._items)
        return f"IntervalSet({parts})"

    # --- 集合运算 -------------------------------------------------------

    def union(self, other: IntervalSet) -> IntervalSet:
        return IntervalSet(self._items + other._items)

    def intersect(self, other: IntervalSet) -> IntervalSet:
        out: list[Interval] = []
        for a in self._items:
            for b in other._items:
                piece = a.intersect(b)
                if piece is not None:
                    out.append(piece)
        return IntervalSet(out)

    def complement(self, within: Interval) -> IntervalSet:
        """在 ``within`` 内取补集。"""
        out: list[Interval] = []
        cursor = within.start
        for iv in self._items:
            piece = iv.intersect(within)
            if piece is None:
                continue
            if piece.start - cursor >= MIN_INTERVAL:
                out.append(Interval(cursor, piece.start))
            cursor = max(cursor, piece.end)
        if within.end - cursor >= MIN_INTERVAL:
            out.append(Interval(cursor, within.end))
        return IntervalSet(out)

    def covers(self, other: IntervalSet) -> bool:
        """``other`` 是否被本集合完全覆盖（逐段判定，允许跨多个区间拼接）。"""
        for src in other._items:
            cursor = src.start
            for iv in self._items:
                if iv.end <= cursor + MIN_INTERVAL:
                    continue
                if iv.start > cursor + MIN_INTERVAL:
                    return False
                cursor = max(cursor, iv.end)
                if cursor >= src.end - MIN_INTERVAL:
                    break
            if cursor < src.end - MIN_INTERVAL:
                return False
        return True

    def clamp(self, lo: float, hi: float) -> IntervalSet:
        out: list[Interval] = []
        for iv in self._items:
            piece = iv.clamp(lo, hi)
            if piece is not None:
                out.append(piece)
        return IntervalSet(out)

    def shift(self, dt: float) -> IntervalSet:
        return IntervalSet(iv.shift(dt) for iv in self._items)

    def total_within(self, within: Interval) -> float:
        return self.intersect(IntervalSet.single(within.start, within.end)).total_duration

    # --- 采样 -----------------------------------------------------------

    def breakpoints(self) -> tuple[float, ...]:
        """全部端点（升序、去重）。供检测器在冲突边界处加密采样。"""
        xs = sorted({iv.start for iv in self._items} | {iv.end for iv in self._items})
        return tuple(xs)

    def gaps(self, within: Interval) -> IntervalSet:
        """``within`` 内未被覆盖的段（即 ``complement`` 的别名，语义更直白）。"""
        return self.complement(within)


# ---------------------------------------------------------------------------
# 规范化
# ---------------------------------------------------------------------------


def _normalize(items: Iterable[Interval]) -> tuple[Interval, ...]:
    """排序、丢弃退化区间、合并重叠与接触的区间。"""
    kept = [iv for iv in items if not iv.is_degenerate]
    if not kept:
        return ()
    kept.sort(key=lambda iv: (iv.start, iv.end))
    merged: list[Interval] = [kept[0]]
    for iv in kept[1:]:
        last = merged[-1]
        # 重叠或端点接触（间隙小于容差）都合并
        if iv.start - last.end <= MIN_INTERVAL:
            merged[-1] = Interval(last.start, max(last.end, iv.end))
        else:
            merged.append(iv)
    return tuple(merged)
