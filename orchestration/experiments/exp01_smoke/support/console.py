# -*- coding: utf-8 -*-
"""exp01_smoke 控制台呈现：设定 / 过程 / 结果三段式，只做显示不改口径。

设计约定：

* 所有输出走本模块，统一 ``flush=True``，长跑时每一行都能立刻看到；
* 阶段代理只包住分配器与规划器的**打印**，原样转发调用与返回值，
  不碰算法、不改状态、不新增指标；
* 默认只打一屏能扫完的摘要；``run.py --detail`` 才展开逐航次明细表。
"""

from __future__ import annotations

import time
import unicodedata
from datetime import datetime
from typing import Any, Mapping, Sequence

WIDTH = 72
LABEL_COLS = 14


def line(text: str = "") -> None:
    print(text, flush=True)


def banner(title: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line(f"[exp01] {title}  {now}")
    line("=" * WIDTH)


def section(no: int, title: str) -> None:
    line("")
    line(f"{no}. {title}")
    line("-" * WIDTH)


def _display_cols(text: str) -> int:
    """中文在终端占两列；按显示宽度补空格，列才真的对齐。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def kv(label: str, value: Any) -> None:
    pad = max(1, LABEL_COLS - _display_cols(label))
    line(f"  {label}{' ' * pad}{value}")


def stage(index: int, total: int, title: str, detail: str = "") -> None:
    suffix = f"  {detail}" if detail else ""
    line(f"  [{index}/{total}] {title}{suffix}")


def secs(value: float) -> str:
    return f"{float(value):.1f}s"


def compact_ids(ids: Sequence[int], tags: Mapping[int, str], *, limit: int = 8) -> str:
    """任务号串；超过 ``limit`` 个只列前几个并写明还剩多少。"""
    vals = [str(tags.get(int(i), int(i))) for i in ids]
    if len(vals) <= limit:
        return ",".join(vals) or "无"
    return ",".join(vals[:limit]) + f"… 另 {len(vals) - limit} 个"


def assignments_text(assignments: Mapping[int, Sequence[int]],
                     tags: Mapping[int, str]) -> str:
    parts = [
        f"机{int(uid)} {len(tids)}站[{compact_ids(tids, tags, limit=6)}]"
        for uid, tids in sorted(assignments.items()) if tids
    ]
    return "；".join(parts) or "无"


def ticket_status(tickets: Mapping[int, Any]) -> str:
    ok = sum(1 for t in tickets.values() if bool(t.ok))
    return f"{ok}/{len(tickets)} 成"


def failure_lines(tickets: Mapping[int, Any], tags: Mapping[int, str]) -> list[str]:
    out = []
    for uid, t in sorted(tickets.items()):
        if bool(t.ok):
            continue
        detail = str(getattr(t, "detail", "") or "").strip()
        suffix = f"：{detail}" if detail else ""
        out.append(f"       机{uid} {t.failure}{suffix}")
    return out


def failed_tickets(trace: Any, tags: Mapping[int, str]) -> list[str]:
    """整场失败航次清单：周期、机号、失败码与下层 detail（原始文字不改）。"""
    out = []
    for plan in trace.cycles:
        for uid, t in sorted(plan.report.tickets.items()):
            if bool(t.ok):
                continue
            detail = str(getattr(t, "detail", "") or "").strip()
            suffix = f"：{detail}" if detail else ""
            out.append(f"  周期{plan.cycle} 机{uid} {t.failure}{suffix}")
    return out


class AllocatorProgress:
    """任务分配阶段的可视化代理；除打印外完全透明。"""

    def __init__(self, inner: Any, tags: Mapping[int, str]) -> None:
        self._inner = inner
        self._tags = dict(tags)

    def prepare(self, new_tasks: Sequence[Any] | None = None) -> Any:
        n_new = 0 if new_tasks is None else len(new_tasks)
        line(f"  任务分配  冻结周期并求解中（新投放 {n_new} 个）…")
        t0 = time.perf_counter()
        snap = self._inner.prepare(new_tasks)
        line(f"  任务分配  首版排程 {len(snap.executed_ids)} 个任务 / "
             f"{len(snap.orders)} 条航次，用时 {secs(time.perf_counter() - t0)}")
        return snap

    def apply_cost_update(self, update: Any) -> Any:
        if update is None:
            return self._inner.apply_cost_update(update)
        line(f"  事实回灌  写入不可飞边 {len(update.infeasible)} 条、"
             f"实测边 {len(update.facts)} 条")
        return self._inner.apply_cost_update(update)

    def resolve(self) -> Any:
        line("  任务分配  按回灌代价重解中…")
        t0 = time.perf_counter()
        snap = self._inner.resolve()
        line(f"  任务分配  重解后排程 {len(snap.executed_ids)} 个任务 / "
             f"{len(snap.orders)} 条航次，用时 {secs(time.perf_counter() - t0)}")
        return snap

    def reopen(self, task_ids: Sequence[int]) -> Any:
        result = self._inner.reopen(task_ids)
        if result:
            line(f"  回池       {len(result)} 个任务回到池中："
                 f"{compact_ids(result, self._tags)}")
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class PlannerProgress:
    """航迹规划阶段的可视化代理；除打印外完全透明。"""

    def __init__(self, inner: Any, tags: Mapping[int, str]) -> None:
        self._inner = inner
        self._tags = dict(tags)
        self._calls = 0

    def begin_cycle(self) -> None:
        self._calls = 0

    def plan_cycle(self, orders: Sequence[Any]) -> Any:
        self._calls += 1
        label = "首版" if self._calls == 1 else "回灌后终版"
        line(f"  航迹规划  {label}：{len(orders)} 条航次，解算中…")
        t0 = time.perf_counter()
        report = self._inner.plan_cycle(orders)
        line(f"  航迹规划  {label}完成：{ticket_status(report.tickets)}，"
             f"用时 {secs(time.perf_counter() - t0)}")
        for text in failure_lines(report.tickets, self._tags):
            line(text)
        return report

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
