# -*- coding: utf-8 -*-
"""图 ``task_dynamics``——任务命运：存量 / 流量 / 状态矩阵 / 等待与滞留。

【process 层 · 求解过程 ④】

回答「任务在滚动闭环里怎么变」——既有**存量**、也有**流量**、还有**个体轨迹**，
外加「任务到底在等什么」。由原 ``task_flow`` + ``task_wait`` 合并而来，
``geo_task_lifecycle`` 的空间快照与 ``cycle_metrics(a)`` 的池水位由本图
数值面板覆盖、空间演化见 ``allocation`` 图，面板信息不丢失。

六联面板::

    (a) 周期末任务存量    累计完成 / 延期滞留 / 未到达 / 过期释放 的堆叠构成
    (b) 每周期任务流量    新到 / 选中 / 完成 / 延期 / 过期释放 分组柱
    (c) 任务 × 周期矩阵   每个任务在每个周期处于什么状态（个体轨迹）
    (d) 累计到达 / 完成   阶梯曲线 + 累计完成率（右轴）
    (e) 每任务等待时间    已完成（实测）/ 未完成（右删失 ≥，斜纹）
    (f) 滞留轮次分布      被延期几次才完成 / 仍未完成

数据来源：``metrics.json`` 的 ``cycles[]``（``pool_counts`` / ``selection`` /
``*_ids`` / ``completed_ids`` / ``deferred_ids``）与 ``solution.json`` 的
``tasks[]``（到达 / 完成 / 终态）。到达口径以「首次入池周期」计，与
``pool_counts.n_new`` 一致，避免与 ``arrival_time`` 生成时刻混用导致 (d) 虚高。
"""

from __future__ import annotations

# ── 独立运行自举：支持直接 `python plot/.../xxx.py`（脚本方式）──
# 脚本方式运行时 __package__ 为空，模块内相对导入不可用；此处转以 -m 语义
# 重跑自身（runpy 会正确设置包上下文），再触发文件末尾的 __main__ 守卫出图。
if __name__ == "__main__" and __package__ in (None, ""):
    import sys, runpy
    from pathlib import Path as _P
    _here = _P(__file__).resolve()
    _root = _here.parents[2]   # 实验根 = plot 的上两级（使 `plot` 包可导入）
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    # 同时把项目根（含 task_allocation 包）加入路径：否则 plotting 内
    # `from task_allocation.methods.utils import GeoTIFFDEM` 会因找不到包而
    # 静默回退到 solution.json 的降采样 DEM（115x101px），地图被放大 10~24 倍变糊。
    _proj = _here
    while not (_proj / "task_allocation" / "__init__.py").is_file() and _proj.parent != _proj:
        _proj = _proj.parent
    if (_proj / "task_allocation" / "__init__.py").is_file() and str(_proj) not in sys.path:
        sys.path.insert(0, str(_proj))
    _rel = _here.relative_to(_root / "plot")
    _mod = "plot." + ".".join(_rel.with_suffix("").parts)
    runpy.run_module(_mod, run_name="__main__", alter_sys=True)
    raise SystemExit(0)


from pathlib import Path
from typing import Any, Sequence

import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch

from ..theme import FS, PALETTE, footnote, plt, save_figure, style_axes

__all__ = ["plot_task_dynamics"]

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
    ("选中 n_sel", lambda c: (c.get("selection") or {}).get("n_selected"),
     PALETTE["selected"]),
    ("完成", lambda c: c.get("n_completed"), PALETTE["executed"]),
    ("延期滞留", lambda c: c.get("n_deferred"), PALETTE["deferred"]),
    ("超期滞留 / 释放",
     lambda c: len(c.get("overdue_ids") or []) + len(c.get("released_ids") or []),
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
    待办池」并不等价（边界上的任务会顺延到下个周期）。因此以**池内成员资格**
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
        # 2026-10-24 无超期淘汰：过期任务滞留池中、不出池，不计入 gone；
        # gone 仅含 released（事件释放/剔除，真正离开池）。
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


def _defer_rounds(metrics: dict[str, Any]) -> dict[int, int]:
    """每个任务被延期的轮次总数（出现在几个周期的 deferred_ids）。"""
    rounds: dict[int, int] = {}
    for c in metrics.get("cycles") or []:
        for tid in c.get("deferred_ids") or []:
            rounds[int(tid)] = rounds.get(int(tid), 0) + 1
    return rounds


def plot_task_dynamics(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    cycles = list(metrics.get("cycles") or [])
    if not cycles:
        return []

    tasks = list(solution.get("tasks") or [])
    labels = [f"周期 {c.get('cycle')}" for c in cycles]
    x = np.arange(len(cycles))

    # ── 累计序列（到达口径与矩阵一致：以入池周期计）──────────────
    if tasks:
        arrival = _arrival_cycles(tasks, cycles)
        arrived_cum = [
            float(sum(1 for a in arrival if a <= k)) for k in range(len(cycles))
        ]
        n_total = float(len(tasks))
    else:
        arrival = []
        arrived_cum = _cumsum(
            [_num((c.get("pool_counts") or {}).get("n_new")) for c in cycles]
        )
        n_total = max(arrived_cum) if arrived_cum else 0.0
    n_total = max(n_total, max(arrived_cum) if arrived_cum else 0.0)

    done_cum = _cumsum([_num(c.get("n_completed")) for c in cycles])
    gone_cum = _cumsum(
        [len(c.get("released_ids") or [])   # 离开池仅 released（过期不出池）
         for c in cycles]
    )
    deferred = [_num(c.get("n_deferred")) for c in cycles]
    unarrived = [max(n_total - a, 0.0) for a in arrived_cum]

    fig, axes = plt.subplots(2, 3, figsize=(11.8, 7.4))
    ax_stock, ax_flow, ax_grid, ax_prog, ax_wait, ax_rounds = axes.ravel()

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
        ax_stock.bar(x, vals, 0.55, bottom=bottom, color=color,
                     label=name if vals.max() > 0 else None)
        bottom = bottom + vals
    style_axes(
        ax_stock,
        title="(a) 周期末任务存量构成（全集 %.0f 个，柱高恒定）" % n_total,
        ylabel="任务数",
    )
    ax_stock.set_xticks(x, labels)
    ax_stock.set_ylim(0, max(bottom.max(), 1.0) * 1.30)
    ax_stock.legend(ncol=2, loc="upper left", fontsize=FS["legend"])

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
    ax_flow.set_ylim(0, max(max(v, default=0.0) for _, v, _ in series) * 1.42 + 0.5
                     if series else 1.0)
    ax_flow.legend(ncol=2, loc="upper left", fontsize=FS["legend"],
                   columnspacing=0.9, handlelength=1.2, labelspacing=0.35)

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
        handles = [
            Patch(facecolor=color, edgecolor="0.75", linewidth=0.6, label=name)
            for idx, (name, color) in enumerate(_STATES)
            if np.any(grid == idx)
        ]
        style_axes(ax_grid, title="(c) 任务 × 周期 状态矩阵",
                   xlabel="滚动周期", ylabel="任务 id")
        ax_grid.legend(handles=handles, loc="upper left",
                       bbox_to_anchor=(1.01, 1.0), fontsize=FS["legend"])
    else:
        ax_grid.axis("off")
        ax_grid.text(0.5, 0.5, "无任务级数据（solution.json 缺 tasks）",
                     ha="center", va="center", fontsize=FS["base"], color="0.45",
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
    ax_prog.legend(h1 + h2, l1 + l2, loc="upper left", ncol=2,
                   fontsize=FS["legend"], columnspacing=1.0,
                   handlelength=1.4, labelspacing=0.35)

    # ── (e) 每任务等待时间（右删失） ─────────────────────────────
    wait_tasks = [t for t in tasks
                  if t.get("arrival_time") is not None and t.get("completed_at") is not None
                  or (t.get("arrival_time") is not None and t.get("status") != "done")]
    horizon = max(
        [float(c.get("current_time") or 0.0) for c in cycles]
        + [float(t.get("completed_at") or 0.0)
           for t in tasks if t.get("completed_at") is not None],
        default=0.0,
    )
    if wait_tasks:
        wids = [int(t["id"]) for t in wait_tasks]
        wdone = [str(t.get("status")) == "done" for t in wait_tasks]
        wend = [float(t["completed_at"]) if t.get("completed_at") is not None
                else horizon for t in wait_tasks]
        wait = [e - float(t["arrival_time"]) for e, t in zip(wend, wait_tasks)]
        wx = np.arange(len(wait_tasks))
        wcolors = [PALETTE["executed"] if d else PALETTE["deferred"] for d in wdone]
        bars = ax_wait.bar(wx, wait, 0.62, color=wcolors, edgecolor="white",
                           linewidth=0.6)
        for b, d in zip(bars, wdone):
            if not d:
                b.set_hatch("//")
                b.set_alpha(0.75)
        done_waits = [w for w, d in zip(wait, wdone) if d]
        if done_waits:
            mean_w = float(np.mean(done_waits))
            ax_wait.axhline(mean_w, color="0.4", linewidth=0.9, linestyle="--", zorder=0)
        ax_wait.set_xticks(wx, [str(i) for i in wids], fontsize=FS["tick"])
        ax_wait.set_ylim(0, max(wait) * 1.22 + horizon * 0.05)
        style_axes(ax_wait,
                   title="(e) 每任务等待时间（斜纹 = 未完成，右删失下界）",
                   xlabel="任务 id", ylabel="等待 (s)")
        if done_waits:
            ax_wait.text(0.98, 0.97, f"已完成者均值 {mean_w:.0f}s（虚线）",
                         transform=ax_wait.transAxes, ha="right", va="top",
                         fontsize=FS["footnote"], color="0.4",
                         bbox=dict(boxstyle="round,pad=0.25", fc="white",
                                   ec="none", alpha=0.85))
    else:
        ax_wait.axis("off")
        ax_wait.text(0.5, 0.5, "无任务级数据", ha="center", va="center",
                     fontsize=FS["base"], color="0.45", transform=ax_wait.transAxes)

    # ── (f) 滞留轮次分布 ──────────────────────────────────────────
    rounds = _defer_rounds(metrics)
    if tasks:
        fids = [int(t["id"]) for t in tasks]
        fdone = [str(t.get("status")) == "done" for t in tasks]
        r_vals = [rounds.get(i, 0) for i in fids]
        counts: dict[int, dict[str, int]] = {}
        for r, d in zip(r_vals, fdone):
            counts.setdefault(r, {"done": 0, "pending": 0})
            counts[r]["done" if d else "pending"] += 1
        rr = sorted(counts)
        ax_rounds.bar([r - 0.19 for r in rr], [counts[r]["done"] for r in rr], 0.36,
                      color=PALETTE["executed"], edgecolor="white", linewidth=0.6,
                      label="最终完成")
        ax_rounds.bar([r + 0.19 for r in rr], [counts[r]["pending"] for r in rr], 0.36,
                      color=PALETTE["deferred"], edgecolor="white", linewidth=0.6,
                      hatch="//", alpha=0.8, label="仍未完成")
        for r in rr:
            tot = counts[r]["done"] + counts[r]["pending"]
            ax_rounds.text(r, tot + 0.06, f"{tot}", ha="center", fontsize=FS["tick"],
                          color="0.3")
        ax_rounds.set_xticks(rr)
        ax_rounds.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
        ax_rounds.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
        ax_rounds.set_ylim(0, max(sum(counts[r].values()) for r in rr) * 1.2 + 0.4)
        style_axes(ax_rounds, title="(f) 滞留轮次分布（被延期几次）",
                  xlabel="滞留轮次", ylabel="任务数")
        ax_rounds.legend(loc="upper left", fontsize=FS["legend"],
                         labelspacing=0.35, handlelength=1.2)
    else:
        ax_rounds.axis("off")
        ax_rounds.text(0.5, 0.5, "无任务级数据", ha="center", va="center",
                      fontsize=FS["base"], color="0.45", transform=ax_rounds.transAxes)

    fig.suptitle("exp01_smoke：任务命运（存量 · 流量 · 状态矩阵 · 等待与滞留）",
                 fontsize=FS["suptitle"])
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    footnote(fig, "口径：到达以「首次入池周期」计（与 pool_counts.n_new 一致），"
                  "非 arrival_time 生成时刻；(e) 未完成任务的等待为右删失下界 ≥；"
                  "空间生命周期快照见 allocation 图的 DEM 演化面板。")
    return save_figure(fig, Path(out_dir) / "P09_task_dynamics", formats, dpi=dpi)

if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from task_allocation.experiments.exp01_smoke.plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
