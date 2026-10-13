# -*- coding: utf-8 -*-
"""图 ``allocation_gantt``——单周期分配甘特（从 ``allocation`` 拆出，独立成图）。

回答「谁做了哪些任务、按什么顺序、降哪个巢」。原 ``allocation`` 把甘特与
逐周期空间演化拼在一张图里；拆开后甘特独占整张画布，行更高、标签更清楚，
适合论文正文单独引用。

数据来源：``solution.json`` 的 ``cycles[].tours`` + ``tasks`` / ``nests``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ..base.geo import (
    _G_DRONE,
    _G_HOUSE,
    _IconHandler,
    _IconPatch,
    uav_color,
)
from ..theme import FS, PALETTE, footnote, plt, save_figure

from .allocation import _collect_rows, _draw_gantt, _arrival_cycle_map

__all__ = ["plot_allocation_gantt"]


def plot_allocation_gantt(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    nests = list(solution.get("nests") or [])
    cycles = list(solution.get("cycles") or [])
    rows = _collect_rows(solution)
    if not nests or not cycles or not rows:
        return []

    task_by_id = {int(t["id"]): t for t in (solution.get("tasks") or [])}
    nest_by_id = {int(n["id"]): n for n in nests}
    arrival_cycle = _arrival_cycle_map(solution)
    n_rows = len(rows)
    gantt_h = max(3.0, 0.52 * n_rows + 1.7)

    fig = plt.figure(figsize=(12.6, gantt_h + 1.35))
    gs = fig.add_gridspec(1, 1, left=0.11, right=0.965, top=0.93, bottom=0.13)
    ax = fig.add_subplot(gs[0])
    _draw_gantt(ax, rows, task_by_id, nest_by_id, arrival_cycle,
                title="分配航迹条：线长 ∝ 真实航程 · 方块=任务 · 两端=起/降巢")
    # 图例：UAV 配色 + 机巢符号（与地图一致）
    uavs_used = sorted({int(r["uav_id"]) for r in rows})
    handles = [
        _IconPatch(_G_DRONE, fc=uav_color(u), ec="white", lw=1.0,
                   label=f"UAV {u}") for u in uavs_used
    ]
    handles += [
        _IconPatch(_G_HOUSE, fc=PALETTE["nest"], ec="white", lw=1.0,
                   label="机巢（○=返航巢，琥珀=异巢终止）"),
    ]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.028),
               handler_map={_IconHandler: _IconHandler()},
               ncol=min(8, len(handles)), fontsize=FS["legend"],
               columnspacing=1.1, frameon=False)

    fig.suptitle("exp01_smoke：单周期分配航迹条", fontsize=FS["suptitle"])
    footnote(fig, "口径：行 = 航次（周期×UAV）；横向 UAV 色航线长度 ∝ 真实飞行距离"
                  "（起飞巢→任务→返航巢；段间距按三维几何比例、总量按 DEM 航程"
                  "est_cost 标定，km）；方框=任务"
                  "（上标号 T#·Ck，Ck=到达周期；下收益 R_j）；左端实心屋=起飞巢、"
                  "右端空心圈=返航巢；琥珀色=异巢终止（起飞巢≠返航巢）。"
                  "Ck 标出任务到达周期，区分「到达」与「执行」。")
    return save_figure(fig, Path(out_dir) / "P04_allocation_gantt", formats, dpi=dpi)
