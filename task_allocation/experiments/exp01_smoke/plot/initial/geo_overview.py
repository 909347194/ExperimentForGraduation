# -*- coding: utf-8 -*-
"""图 ``geo_overview``——初始态势：背景（建筑 / 地形）· 机巢 · 初始机队 · 任务散布

【scene 层 · 环境初始配置】

scene 层回答「实验是怎么摆的」：**一次绘制、与算法无关**——
同一次实验无论怎么求解，这张图都一模一样，它是后面所有过程图的
坐标系与空间参照（看不懂过程图时，先回这张图认路）。

背景层由 ``base/backdrop.py`` 统一裁决（本图不做场景判断）：

    有建筑（城市场景）  建筑轮廓按屋顶高度着色铺底，色条 = 建筑高度 (m)
    只有地形（高原场景）真实 ASTER 瓦片裁剪采样 + 山体阴影晕渲，色条 = 高程 (m)

其余要素：

    机巢           房屋图标，面积 ∝ 容量，标注「巢 {id}」及初始停驻机数
    任务           圆点，面积 ∝ 收益（本次实验的**全部**任务）
    初始无人机     本就停在各自机巢，故按巢计数直接标在巢上，不另撒点

数据来源：``results/solution.json`` 的 dem / nests / tasks / uavs；建筑几何来自
``data/<scenario>/building/*.shp``（按 solution 的 local_frame 投到同一米制平面）。
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

from ..base.backdrop import (
    _backdrop_extent,
    _draw_backdrop,
)
from ..base.buildings import _building_legend_patch
from ..base.geo import (
    _G_DOT,
    _G_HOUSE,
    _IconHandler,
    _IconPatch,
    _draw_nests,
    _set_scene_lim,
    _strip_axes,
    _task_size,
)
from ..theme import PALETTE, plt, save_figure

__all__ = ["plot_geo_overview"]




def plot_geo_overview(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("png",),
    *,
    dpi: int = 300,
) -> list[Path]:
    """初始态势：背景（有建筑画建筑，只有地形画 DEM）+ 机巢 + 全部任务。"""
    from collections import Counter

    nests = list(solution.get("nests") or [])
    tasks = list(solution.get("tasks") or [])
    uavs = list(solution.get("uavs") or [])
    if not nests:
        return []

    # 初始无人机本就停在各自机巢：按 nest_id 计数，直接在巢上标数量
    count_by_nest = Counter(int(u.get("nest_id", -1)) for u in uavs)
    n_initial = len(uavs)
    n_tasks = len(tasks)                       # ← 任务总数

    fig, ax = plt.subplots(figsize=(10.5, 9.0), layout="constrained")
    # 背景层：建筑优先（城市）/ 地形兜底（高原）——判断只在 base/backdrop 里做一次
    bd = _draw_backdrop(ax, solution, nests=nests, tasks=tasks, uavs=uavs)

    # 全部任务（鲜红，作为背景）
    if tasks:
        ax.scatter(
            [float(t["x"]) for t in tasks],
            [float(t["y"]) for t in tasks],
            s=[_task_size(t["reward"]) for t in tasks],
            color="#E53935", edgecolors="white", linewidths=0.8, zorder=6,
            label=f"任务（全部 {n_tasks} 个）",
        )

    _draw_nests(ax, nests, counts=count_by_nest)

    if bd.sm is not None:
        cb = fig.colorbar(bd.sm, ax=ax, fraction=0.035, pad=0.02, shrink=0.75)
        cb.set_label(bd.label, fontsize=9)      # "建筑高度 (m)" / "高程 (m)"
        cb.ax.tick_params(labelsize=8)

    handles = [
        _IconPatch(_G_HOUSE, fc=PALETTE["nest"], ec="white", lw=1.2,
                   label=f"机巢（含初始无人机 {n_initial} 架）"),
        _IconPatch(_G_DOT, fc="#E53935", ec="white", lw=1.0,
                   label=f"任务（全部 {n_tasks} 个）"),
    ]
    # 城市场景把建筑也进图例：否则灰底会被读成"空白"
    if bd.has_buildings:
        handles.insert(0, _building_legend_patch(bd.buildings))
    ax.legend(
        handles=handles, loc="upper right", fontsize=9.5,
        handler_map={_IconPatch: _IconHandler()},
        frameon=True, fancybox=True, framealpha=0.96,
        edgecolor="#e3e7ec", facecolor="white", borderpad=0.7,
    )

    _set_scene_lim(
        ax, nests, tasks, uavs, solution.get("dem"),
        extent=_backdrop_extent(solution, nests, tasks, uavs),
    )
    ax.set_aspect("equal", adjustable="box")
    _strip_axes(ax)

    stem = Path(out_dir) / "I01_geo_overview"
    return save_figure(fig, stem, formats, dpi=dpi)

if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from task_allocation.experiments.exp01_smoke.plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
