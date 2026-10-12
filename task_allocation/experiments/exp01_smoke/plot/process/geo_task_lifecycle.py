# -*- coding: utf-8 -*-
"""图 ``geo_task_lifecycle``——任务生命周期：谁到了、谁在等、谁做完了

【process 层 · 运行过程观测】

process 层回答「哪些目标在什么时候出现、被完成或继续滞留」。
此图**刻意不画航线**——航线是 geo_evolution 的事，这里只看任务本身：

    灰色   尚未到达        橙色   本周期延期（继续滞留）
    绿色   本周期完成      深灰   此前已完成
    空心   已到达但仍待执行

    状态由 metrics 的 completed/deferred id 驱动，而非用最终 task.status
    反推——否则多周期图会把所有任务都误画成终态。

数据来源：``results/solution.json 的 dem / nests / tasks / cycles[]，状态来自 metrics.json 的 cycles[].completed_ids / deferred_ids``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ..base.dem import (
    _dem_view_extent,
    _draw_dem,
)
from ..base.geo import (
    _G_DOT,
    _HALO,
    _IconHandler,
    _IconPatch,
    _draw_nests,
    _label_offset,
    _metric_cycle,
    _panel_title,
    _row_figsize,
    _set_scene_lim,
    _strip_axes,
    _task_size,
)
from ..theme import plt, save_figure

__all__ = ["plot_geo_task_lifecycle"]


# ---------------------------------------------------------------------------
# 本图专用绘制件（其它图不用，故不放 base/geo.py）
# ---------------------------------------------------------------------------

def _draw_task_lifecycle(
    ax,
    tasks: Sequence[dict[str, Any]],
    record: dict[str, Any],
    now: float,
) -> dict[str, int]:
    """绘制一个周期末的任务状态，且只显示当前已到达任务。

    颜色语义固定：灰色=尚未到达，橙色=本周期留待后续，绿色=本周期完成，
    深灰=此前已完成。由 metrics 的 completed/deferred id 驱动，而不是通过最终
    task.status 反推，避免多周期图把所有任务都误画成最终状态。
    """
    completed_now = {int(i) for i in record.get("completed_ids") or []}
    deferred = {int(i) for i in record.get("deferred_ids") or []}
    completed_before = {
        int(t["id"])
        for t in tasks
        if t.get("completed_at") is not None and float(t["completed_at"]) < now - 1e-9
    }
    groups = {
        "future": [t for t in tasks if float(t.get("arrival_time", 0.0)) > now],
        "done_before": [t for t in tasks if int(t["id"]) in completed_before],
        "done_now": [t for t in tasks if int(t["id"]) in completed_now],
        "deferred": [t for t in tasks if int(t["id"]) in deferred],
    }
    # 已到达、但未进入以上集合的任务：例如候选筛选前被排除，仍为待执行。
    known_other = [
        t for t in tasks
        if float(t.get("arrival_time", 0.0)) <= now
        and int(t["id"]) not in (completed_before | completed_now | deferred)
    ]
    groups["pending"] = known_other
    styles = {
        "future": ("#d8dee6", "#c3cbd4", "尚未到达", 0.30),
        "pending": ("none", "#9aa5b1", "待执行", 0.85),
        "deferred": ("#f58518", "white", "本周期延期", 0.96),
        "done_before": ("#7f8c8d", "white", "此前完成", 0.64),
        "done_now": ("#54a24b", "white", "本周期完成", 1.0),
    }
    for name, items in groups.items():
        if not items:
            continue
        face, edge, _, alpha = styles[name]
        ax.scatter(
            [float(t["x"]) for t in items], [float(t["y"]) for t in items],
            s=[_task_size(float(t.get("reward", 1.0))) for t in items],
            facecolors=face, edgecolors=edge, linewidths=1.0, alpha=alpha, zorder=3,
        )
        # 只为本周期相关任务标注，避免未来任务和已完成历史任务淹没主图。
        if name in {"done_now", "deferred", "pending"}:
            for task in items:
                ax.annotate(
                    f"T{task['id']}", (float(task["x"]), float(task["y"])),
                    textcoords="offset points", xytext=_label_offset(int(task["id"])),
                    ha="center", fontsize=6.8, color="#1f2933", zorder=7, bbox=_HALO,
                )
    return {key: len(value) for key, value in groups.items()}



def plot_geo_task_lifecycle(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """按周期展示任务从到达、延期到完成的空间演变。

    此图刻意不画航线：它用于回答“哪些目标在什么时候出现、被完成或继续滞留”，
    与 ``geo_evolution`` 中的航次细节互补。
    """
    nests = list(solution.get("nests") or [])
    tasks = list(solution.get("tasks") or [])
    cycles = list(solution.get("cycles") or [])
    dem = solution.get("dem")
    if not nests or not cycles:
        return []

    # 底图宽高比自适应画幅：地图填满每行，避免两侧大片留白
    extent = _dem_view_extent(dem, solution)
    row_h = 4.8
    fig, axes = plt.subplots(
        len(cycles), 1, figsize=_row_figsize(extent, row_h, len(cycles)),
        squeeze=False, sharex=True, sharey=True, layout="constrained",
    )
    axes = axes.ravel()
    im0 = None
    for index, (ax, cycle) in enumerate(zip(axes, cycles)):
        im = _draw_dem(ax, dem, solution)
        if index == 0:
            im0 = im
        record = _metric_cycle(metrics, int(cycle.get("cycle", -1)))
        counts = _draw_task_lifecycle(
            ax, tasks, record, float(cycle.get("current_time", 0.0))
        )
        _draw_nests(ax, nests)
        # 标题画进地图左上角，不再外置长标题
        _panel_title(
            ax,
            f"周期 {cycle.get('cycle')} @ {float(cycle.get('current_time', 0.0)):.0f}s"
            f"｜待处理 {counts['pending'] + counts['deferred']}"
            f"｜完成 {counts['done_now']}"
            f"｜延期 {counts['deferred']}",
        )
        _strip_axes(ax)
        ax.set_aspect("equal", adjustable="box")

    _set_scene_lim(axes[0], nests, tasks, [], dem, extent=_dem_view_extent(dem, solution))
    if im0 is not None:
        cb = fig.colorbar(im0, ax=axes.tolist(), location="right", shrink=0.5, pad=0.015)
        cb.set_label("高程 (m)", fontsize=9)
        cb.ax.tick_params(labelsize=8)
    handles = [
        _IconPatch(_G_DOT, fc="#d8dee6", ec="#c3cbd4", lw=1.0, label="尚未到达"),
        _IconPatch(_G_DOT, fc="none", ec="#9aa5b1", lw=1.2, label="待执行"),
        _IconPatch(_G_DOT, fc="#f58518", ec="white", lw=1.0, label="本周期延期"),
        _IconPatch(_G_DOT, fc="#54a24b", ec="white", lw=1.0, label="本周期完成"),
        _IconPatch(_G_DOT, fc="#7f8c8d", ec="white", lw=1.0, label="此前完成"),
    ]
    # constrained layout 下用 "outside" 定位，图例才会自动预留空间而不压住 x 轴标签
    fig.legend(handles=handles, loc="outside lower center", ncol=5, fontsize=9,
               handler_map={_IconPatch: _IconHandler()}, frameon=False)
    fig.suptitle("动态任务目标生命周期", fontsize=13, fontweight="bold", color="#1f2933")
    return save_figure(fig, Path(out_dir) / "geo_task_lifecycle", formats, dpi=dpi)
