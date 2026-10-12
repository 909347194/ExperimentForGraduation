# -*- coding: utf-8 -*-
"""图 ``task_flow``——任务状态随周期变化：存量 / 流量 / 个体轨迹。

【process 层 · 运行过程观测】

回答「任务在滚动闭环里怎么变」——既有**存量**、也有**流量**，还有**个体轨迹**：

    (a) 周期末任务存量   —— 累计完成 / 延期滞留 / 未到达 / 过期释放 的堆叠构成
    (b) 每周期任务流量   —— 新到 / 选中 / 完成 / 延期 / 过期释放 分组柱
    (c) 任务 × 周期矩阵  —— 每个任务在每个周期处于什么状态（个体轨迹）
    (d) 累计进度         —— 累计到达 vs 累计完成（阶梯）与累计完成率

与 ``cycle_metrics`` 的分工：后者看**求解侧**（水位、双目标、泊位、开销），
本图看**任务侧**（每个任务的来龙去脉），二者同源但视角互补。

数据来源：``metrics.json`` 的 ``cycles[]``（``pool_counts`` / ``selection`` /
``*_ids``）与 ``solution.json`` 的 ``tasks[]``（到达时刻、完成时刻、终态）。

口径提示：**到达**以「该任务首次进入待办池的周期」计（即首次出现在某周期
``executed_ids ∪ deferred_ids`` 中），而非 ``solution.json`` 的 ``arrival_time``
——后者是任务生成时刻，处在周期边界上的任务会顺延到下个周期入池，
两者直接混用会让 (d) 的累计到达曲线虚高。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch

from ..theme import PALETTE, plt, save_figure, style_axes

__all__ = ["plot_task_flow"]

#: 任务状态编码（矩阵图的离散色标，顺序即图例顺序）
_STATES = (
    ("未到达", "#eceff3"),
    ("池内待处理（本周期未执行）", "#f58518"),
    ("本周期完成", "#54a24b"),
    ("此前已完成", "#b7d7b0"),
    ("过期 / 释放", "#e45756"),
    ("已到达·当期未入池", "#9aa5b1"),
)
_NOT_ARRIVED, _IN_POOL, _DONE_NOW, _DONE_BEFORE, _EXPIRED, _OFF_POOL = range(6)

#: (b) 面板的流量序列：(图例名, 取数函数, 颜色)
_FLOW_SERIES = (
    ("新到 new", lambda c: (c.get("pool_counts") or {}).get("n_new"), PALETTE["new"]),
    ("选中 T_sel", lambda c: (c.get("selection") or {}).get("n_selected"),
     PALETTE["selected"]),
    ("完成", lambda c: c.get("n_completed"), PALETTE["executed"]),
    ("延期滞留", lambda c: c.get("n_deferred"), PALETTE["deferred"]),
    ("过期 / 释放",
     lambda c: len(c.get("expired_ids") or []) + len(c.get("released_ids") or []),
     PALETTE["release"]),
)


def _num(value: Any) -> float:
    return float(value or 0.0)


def _cumsum(values: Sequence[float]) -> list[float]:
    out: list[float] = []
    acc = 0.0
    for v in values:
        acc += float(v)
        out.append(acc)
    return out


def _arrival_cycles(tasks: Sequence[dict[str, Any]],
                    cycles: Sequence[dict[str, Any]]) -> list[int]:
    """每个任务的**入池周期**（0 基），与 ``pool_counts.n_new`` 口径一致。

    ``solution.json`` 的 ``arrival_time`` 是任务生成时刻，与「第几个周期进入
    待办池」并不等价（边界上的任务会顺延到下个周期）。因此这里以**池内成员资格**
    为准：首次出现在某周期 ``executed_ids ∪ deferred_ids`` 里的周期即其入池周期；
    从未出现过（例如被事件剔除）才退回 ``arrival_time`` 阈值判定。
    """
    appear: dict[int, int] = {}
    for k, cyc in enumerate(cycles):
        ids = {int(i) for i in (cyc.get("executed_ids") or [])}
        ids |= {int(i) for i in (cyc.get("deferred_ids") or [])}
        for tid in ids:
            appear.setdefault(tid, k)

    out: list[int] = []
    for task in tasks:
        tid = int(task.get("id", -1))
        if tid in appear:
            out.append(appear[tid])
            continue
        arr = _num(task.get("arrival_time"))
        out.append(
            next(
                (k for k, c in enumerate(cycles)
                 if _num(c.get("current_time")) >= arr - 1e-9),
                len(cycles),
            )
        )
    return out


def _state_grid(tasks: Sequence[dict[str, Any]],
                cycles: Sequence[dict[str, Any]],
                arrival: Sequence[int]) -> np.ndarray:
    """任务 × 周期 状态矩阵；行序与 ``tasks`` 一致，列序与 ``cycles`` 一致。"""
    grid = np.full((len(tasks), len(cycles)), _NOT_ARRIVED, dtype=int)
    done_at: dict[int, float] = {}
    for t in tasks:
        if str(t.get("status")) == "done" and t.get("completed_at") is not None:
            done_at[int(t.get("id", -1))] = float(t["completed_at"])

    gone: set[int] = set()
    for k, cyc in enumerate(cycles):
        t_k = _num(cyc.get("current_time"))
        executed = {int(i) for i in (cyc.get("executed_ids") or [])}
        deferred = {int(i) for i in (cyc.get("deferred_ids") or [])}
        gone |= {int(i) for i in (cyc.get("expired_ids") or [])}
        gone |= {int(i) for i in (cyc.get("released_ids") or [])}

        for r, task in enumerate(tasks):
            tid = int(task.get("id", -1))
            if k < arrival[r]:
                grid[r, k] = _NOT_ARRIVED
            elif tid in executed:
                grid[r, k] = _DONE_NOW
            elif tid in deferred:
                grid[r, k] = _IN_POOL
            elif tid in gone:
                grid[r, k] = _EXPIRED
            elif tid in done_at and done_at[tid] <= t_k + 1e-9:
                grid[r, k] = _DONE_BEFORE
            else:
                grid[r, k] = _OFF_POOL
    return grid


def plot_task_flow(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    cycles = list(metrics.get("cycles") or [])
    if not cycles:
        return []

    tasks = list(solution.get("tasks") or [])
    labels = [f"周期 {c.get('cycle')}" for c in cycles]
    x = np.arange(len(cycles))

    # ── 累计序列（到达口径与 (c) 矩阵一致：以入池周期计）──────────
    if tasks:
        arrival = _arrival_cycles(tasks, cycles)
        arrived_cum = [
            float(sum(1 for a in arrival if a <= k)) for k in range(len(cycles))
        ]
        n_total = float(len(tasks))
    else:  # 无任务表时退回「每周期新到」的累计（口径一致，只是少了个体信息）
        arrival = []
        arrived_cum = _cumsum(
            [_num((c.get("pool_counts") or {}).get("n_new")) for c in cycles]
        )
        n_total = max(arrived_cum) if arrived_cum else 0.0
    n_total = max(n_total, max(arrived_cum) if arrived_cum else 0.0)

    done_cum = _cumsum([_num(c.get("n_completed")) for c in cycles])
    gone_cum = _cumsum(
        [len(c.get("expired_ids") or []) + len(c.get("released_ids") or [])
         for c in cycles]
    )
    deferred = [_num(c.get("n_deferred")) for c in cycles]
    unarrived = [max(n_total - a, 0.0) for a in arrived_cum]

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 7.8))
    ax_stock, ax_flow, ax_grid, ax_prog = axes.ravel()

    # ── (a) 周期末任务存量 ──────────────────────────────────────
    layers = (
        ("累计完成", done_cum, PALETTE["executed"]),
        ("延期滞留", deferred, PALETTE["deferred"]),
        ("未到达", unarrived, "#c8cfd6"),
        ("过期 / 释放", gone_cum, PALETTE["release"]),
    )
    bottom = np.zeros(len(cycles))
    for name, values, color in layers:
        vals = np.asarray(values, dtype=float)
        # 全 0 的层不进图例，避免图例里出现无意义的条目
        ax_stock.bar(x, vals, 0.55, bottom=bottom, color=color,
                     label=name if vals.max() > 0 else None)
        bottom = bottom + vals
    style_axes(
        ax_stock,
        title=f"(a) 周期末任务存量构成（全集 {n_total:.0f} 个，柱高恒定）",
        ylabel="任务数",
    )
    ax_stock.set_xticks(x, labels)
    ax_stock.set_ylim(0, max(bottom.max(), 1.0) * 1.16)
    ax_stock.legend(ncol=2, loc="upper right")

    # ── (b) 每周期任务流量 ──────────────────────────────────────
    series = [(name, [_num(getter(c)) for c in cycles], color)
              for name, getter, color in _FLOW_SERIES]
    series = [(n, v, c) for n, v, c in series if max(v, default=0.0) > 0]
    width = 0.8 / max(len(series), 1)
    for idx, (name, values, color) in enumerate(series):
        offset = (idx - (len(series) - 1) / 2) * width
        ax_flow.bar(x + offset, values, width * 0.92, color=color, label=name)
    style_axes(ax_flow, title="(b) 每周期任务流量", ylabel="任务数")
    ax_flow.set_xticks(x, labels)
    ax_flow.set_ylim(0, max(max(v, default=0.0) for _, v, _ in series) * 1.45 + 0.5
                     if series else 1.0)
    ax_flow.legend(ncol=3, loc="upper right")

    # ── (c) 任务 × 周期 状态矩阵 ────────────────────────────────
    if tasks:
        grid = _state_grid(tasks, cycles, arrival)
        cmap = ListedColormap([c for _, c in _STATES])
        norm = BoundaryNorm(np.arange(-0.5, len(_STATES) + 0.5), cmap.N)
        ax_grid.imshow(grid, cmap=cmap, norm=norm, aspect="auto",
                       interpolation="nearest")
        ax_grid.set_xticks(x, labels)
        step = 1 if len(tasks) <= 24 else max(1, len(tasks) // 24)
        rows = np.arange(len(tasks))
        ax_grid.set_yticks(rows[::step], [str(t.get("id")) for t in tasks][::step])
        ax_grid.grid(False)
        ax_grid.set_ylim(len(tasks) - 0.5, -0.5)
        # 图例只列矩阵里真实出现过的状态
        handles = [
            Patch(facecolor=color, edgecolor="0.75", linewidth=0.6, label=name)
            for idx, (name, color) in enumerate(_STATES)
            if np.any(grid == idx)
        ]
        style_axes(ax_grid, title="(c) 任务 × 周期 状态矩阵",
                   xlabel="滚动周期", ylabel="任务 id")
        ax_grid.legend(handles=handles, loc="upper left",
                       bbox_to_anchor=(1.01, 1.0), fontsize=8)
    else:
        ax_grid.axis("off")
        ax_grid.text(0.5, 0.5, "无任务级数据（solution.json 缺 tasks）",
                     ha="center", va="center", fontsize=10, color="0.45",
                     transform=ax_grid.transAxes)

    # ── (d) 累计进度 ────────────────────────────────────────────
    ax_prog.fill_between(x, done_cum, arrived_cum, step="post",
                         color="#9aa5b1", alpha=0.25, label="已到达未完成")
    ax_prog.plot(x, arrived_cum, drawstyle="steps-post", marker="o",
                 color=PALETTE["new"], label="累计到达")
    ax_prog.plot(x, done_cum, drawstyle="steps-post", marker="s",
                 color=PALETTE["executed"], label="累计完成")
    style_axes(ax_prog, title="(d) 累计到达 / 完成进度", ylabel="任务数")
    ax_prog.set_xticks(x, labels)
    ax_prog.set_ylim(0, max(arrived_cum) * 1.15 + 1)

    ax_rate = ax_prog.twinx()
    rate = [(d / a * 100.0) if a > 0 else 0.0 for d, a in zip(done_cum, arrived_cum)]
    ax_rate.plot(x, rate, "o--", color=PALETTE["cost"], label="累计完成率")
    ax_rate.set_ylabel("累计完成率 (%)")
    ax_rate.set_ylim(0, 105)
    ax_rate.grid(False)

    h1, l1 = ax_prog.get_legend_handles_labels()
    h2, l2 = ax_rate.get_legend_handles_labels()
    ax_prog.legend(h1 + h2, l1 + l2, loc="upper left")

    fig.suptitle("exp01_smoke：任务状态随周期变化", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))

    stem = Path(out_dir) / "task_flow"
    return save_figure(fig, stem, formats, dpi=dpi)
