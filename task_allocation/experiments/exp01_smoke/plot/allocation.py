# -*- coding: utf-8 -*-
"""exp01_smoke 图 3：UAV—任务 分配甘特图 ``allocation_gantt``。

把「分配」这件事直接摊开看，不依赖地理直觉：

    行       一架 UAV 在一个滚动周期里的航次（只画真被分配到任务的）
    列       航次内的任务执行顺序 1, 2, 3, …
    单元格   该顺位上的任务：格内写「T{任务 id}」与「R=收益」，底色 ∝ 收益
    右侧三列 起止机巢（异巢终止标橙色）· 航次航程 · 航次总收益

回答三个问题：谁做了哪些任务、按什么顺序、最后降在哪个巢。
与 ``assignment_map`` 互补：本图看分配结构，那张图看空间分布。

版式要点：右侧三列画在**坐标轴内部**的留白区，色条画在轴外，
两者不重叠（早期版本把标注放轴外、色条贴轴右缘，导致起始机巢被色条遮住）。

数据来源：``results/solution.json`` 的 ``cycles[].tours`` 与 ``tasks``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .theme import plt, save_figure, style_axes

__all__ = ["plot_allocation_gantt"]

#: 异巢终止的提示色（与普通文字区分）
_RELOCATED_COLOR = "#c2410c"
_NORMAL_COLOR = "#2d3748"


def _collect_rows(solution: dict[str, Any]) -> list[dict[str, Any]]:
    """把各周期航次摊平成甘特图的行（保持周期顺序，组内按 UAV id 排）。"""
    rows: list[dict[str, Any]] = []
    for cyc in solution.get("cycles") or []:
        tours = [t for t in (cyc.get("tours") or []) if t.get("task_ids")]
        for tour in sorted(tours, key=lambda t: int(t.get("uav_id", -1))):
            rows.append(
                {
                    "cycle": int(cyc.get("cycle", 0)),
                    "uav_id": int(tour.get("uav_id", -1)),
                    "task_ids": [int(t) for t in tour["task_ids"]],
                    "start_nest_id": tour.get("start_nest_id"),
                    "end_nest_id": tour.get("end_nest_id"),
                    "est_cost": float(tour.get("est_cost", 0.0)),
                    "est_reward": float(tour.get("est_reward", 0.0)),
                }
            )
    return rows


def plot_allocation_gantt(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png", "pdf"),
    *,
    dpi: int = 200,
) -> list[Path]:
    rows = _collect_rows(solution)
    if not rows:
        return []

    tasks = {int(t["id"]): t for t in (solution.get("tasks") or [])}
    rewards = [
        float(tasks.get(tid, {}).get("reward", 0.0))
        for r in rows
        for tid in r["task_ids"]
    ]
    vmin = min(rewards) if rewards else 0.0
    vmax = max(rewards) if rewards else 1.0
    if abs(vmax - vmin) < 1e-9:
        vmax = vmin + 1.0
    mid = (vmin + vmax) / 2.0

    cmap = plt.get_cmap("viridis")
    norm = plt.Normalize(vmin=vmin, vmax=vmax)

    max_len = max(len(r["task_ids"]) for r in rows)
    n = len(rows)

    # 右侧三列的 x 位置（画在坐标轴内的留白区）
    x_nest = max_len + 1.3
    x_cost_right = max_len + 6.6
    x_reward_right = max_len + 9.2
    x_max = max_len + 9.6
    x_min = 0.35

    fig, ax = plt.subplots(
        figsize=(11.8, max(3.8, 0.68 * n + 2.2)), layout="constrained"
    )
    ys = np.arange(n)[::-1]  # 第一行画在最上面

    for y, row in zip(ys, rows):
        # 航次底纹：把整条航次串成一行
        ax.barh(
            y, len(row["task_ids"]), left=0.5, height=0.74,
            color="#eef2f6", edgecolor="#d0d5da", linewidth=0.8, zorder=1,
        )
        for pos, tid in enumerate(row["task_ids"], start=1):
            reward = float(tasks.get(tid, {}).get("reward", 0.0))
            ax.barh(
                y, 0.86, left=pos - 0.43, height=0.68,
                color=cmap(norm(reward)), edgecolor="white",
                linewidth=1.1, zorder=2,
            )
            ink = "white" if reward < mid else "#132238"
            ax.text(pos, y + 0.10, f"T{tid}", ha="center", va="center",
                    fontsize=9, fontweight="bold", color=ink, zorder=3)
            ax.text(pos, y - 0.17, f"R={reward:.1f}", ha="center", va="center",
                    fontsize=6.8, color=ink, alpha=0.92, zorder=3)

        s, e = row["start_nest_id"], row["end_nest_id"]
        relocated = s is not None and e is not None and s != e
        ax.text(
            x_nest, y, f"巢{s} → 巢{e}", ha="left", va="center",
            fontsize=8.6, color=_RELOCATED_COLOR if relocated else _NORMAL_COLOR,
            fontweight="bold" if relocated else "normal", zorder=3,
        )
        if relocated:
            ax.text(
                x_nest, y - 0.34, "异巢终止", ha="left", va="center",
                fontsize=6.8, color=_RELOCATED_COLOR, zorder=3,
            )
        ax.text(
            x_cost_right, y, f"{row['est_cost'] / 1000.0:.1f} km",
            ha="right", va="center", fontsize=8.2, color="#4a5568", zorder=3,
        )
        ax.text(
            x_reward_right, y, f"ΣR={row['est_reward']:.1f}",
            ha="right", va="center", fontsize=8.2, color="#4a5568", zorder=3,
        )

    # 周期之间画分隔线
    for i in range(n - 1):
        if rows[i]["cycle"] != rows[i + 1]["cycle"]:
            ax.axhline((ys[i] + ys[i + 1]) / 2.0, color="#9aa5b1",
                       linewidth=1.0, zorder=0)

    # 任务区 / 明细列 的分界
    ax.axvline(max_len + 0.65, color="#d0d5da", linewidth=1.0, zorder=0)

    # 列头（写在最上方，自解释，不需要额外图例）
    header_y = n - 0.30
    ax.text(1.0, header_y, "航次内的任务执行顺序", ha="left", va="bottom",
            fontsize=8, color="#7b8794", zorder=3)
    ax.text(x_nest, header_y, "起止机巢", ha="left", va="bottom",
            fontsize=8, color="#7b8794", zorder=3)
    ax.text(x_cost_right, header_y, "航程", ha="right", va="bottom",
            fontsize=8, color="#7b8794", zorder=3)
    ax.text(x_reward_right, header_y, "航次收益", ha="right", va="bottom",
            fontsize=8, color="#7b8794", zorder=3)

    ax.set_yticks(ys, [f"周期 {r['cycle']}     UAV {r['uav_id']}" for r in rows],
                  fontsize=8.8)
    ax.set_xticks(np.arange(1, max_len + 1))
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(-0.85, n + 0.35)

    style_axes(ax, title="UAV—任务 分配甘特图", xlabel="")
    ax.grid(axis="x", alpha=0.18)
    ax.grid(axis="y", visible=False)
    # x 轴标签只对齐任务区（明细列不画刻度）
    span = x_max - x_min
    ax.xaxis.set_label_coords(((x_min + max_len + 0.5) / 2.0 - x_min) / span, -0.10)
    ax.set_xlabel("任务顺位")

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    fig.colorbar(sm, ax=ax, fraction=0.026, pad=0.015, label="任务收益 R_j")

    fig.suptitle("exp01_smoke：任务分配结构（谁做什么 · 什么顺序 · 降哪个巢）",
                 fontsize=13)

    stem = Path(out_dir) / "allocation_gantt"
    return save_figure(fig, stem, formats, dpi=dpi)
