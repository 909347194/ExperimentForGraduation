# -*- coding: utf-8 -*-
"""图 ``allocation_gantt``——单周期分配甘特（从 ``allocation`` 拆出，独立成图）。

回答「谁做了哪些任务、按什么顺序、降哪个巢」。原 ``allocation`` 把甘特与
逐周期空间演化拼在一张图里；拆开后甘特独占整张画布，行更高、标签更清楚，
适合论文正文单独引用。

数据来源：``solution.json`` 的 ``cycles[].tours`` + ``tasks`` / ``nests``。
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

if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from task_allocation.experiments.exp01_smoke.plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
