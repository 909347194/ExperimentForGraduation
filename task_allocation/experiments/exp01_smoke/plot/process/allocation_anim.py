# -*- coding: utf-8 -*-
"""图 ``allocation_anim``——分配过程的**动画演变**（动图）。

【process 层 · 求解过程 ③ 的动态版】

``allocation.png`` 的 (b) 面板是「逐周期空间演化」的多联静态图；本图把它
**变成动图**：在单张地图上回放整个滚动闭环——机巢与地形不动，UAV 航迹随
周期逐段「生长」，任务一旦被航迹扫过即点亮（按其承运 UAV 着色），累计完成
数与里程实时跳动；右栏画出「当前周期」的 DMDE 收敛曲线，把**空间分配**与
**优化搜索**两件事钉在同一时间轴上，最直观地看到「整个过程」。

布局是**左右两栏**：(a) 左栏地图（DEM 满幅铺底 + 航迹回放 + 任务点亮），
顶部一行**状态行**（周期 / 已到达 / **已完成百分比** / 里程，白底随文字自动
贴合，按「哪侧顶部要素少」自动贴左或贴右）；(b) 右栏当前周期 DMDE 收敛曲线，
游标随动画推进。

地图上每座机巢三角形**正下方**实时标注「n 架」= **当前驻巢无人机数**（逐帧
随航迹更新：航次执行期间该 UAV 在空、不计入任何巢，完成航次回到返航巢后计入，
与画面上的无人机图标、归巢空心环严格同步；奇偶巢 badge 左右错开，避免相距
约 3.4 km 的相邻巢 5/6 标号相压；零驻巢置灰）。

「滚动进度」的四次定位调整（都是用户反馈驱动，记录以免重蹈）：右下独立子图 →
地图底部卡片（压 DEM）→ 地图角上的卡片 + 进度条（状态行文字宽 3.9in 放不进
2.4in 的卡片，溢出压住任务标号 = 视觉「文字堆叠」）→ **最终：不做进度条，
只用状态行里的一个百分比数字**（剧情=进度其实已在数字里，占地最小）。

输出：``allocation_anim.gif``（Pillow 合成；无 ffmpeg 时无 mp4）。
``formats`` 不含 ``gif`` / ``mp4`` 时本图直接跳过，避免拖慢默认 PNG 出图。

数据来源：``solution.json`` 的 ``cycles[].tours``（start / start_nest_id /
end_nest_id / task_ids / uav_id / est_cost）+ ``tasks`` / ``nests`` / ``dem``，
叠加 ``cycles[].cost_history``（DMDE 收敛）与 ``metrics`` 的 muas.f2_cost。
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

from ..base.dem import _draw_dem, _dem_view_extent
from ..base.geo import (
    UAV_PALETTE,
    _G_DRONE,
    _G_DOT,
    _G_HOUSE,
    _HALO,
    _IconHandler,
    _IconPatch,
    _arrow,
    _draw_nests,
    _strip_axes,
    _task_size,
    uav_color,
)
from ..theme import (
    FS,
    PALETTE,
    build_gif_palette,
    footnote,
    plt,
    robust_pil_save,
    robust_savefig,
    save_poster,
    style_axes,
    declutter_figure,
    audit_frame,
)
from .allocation import _arrival_cycle_map
from .allocation_status import _stationed_by_cycle

__all__ = ["plot_allocation_anim"]

# GIF 友好参数（位图动图体积极易膨胀，需压 dpi / 帧数）
_GIF_DPI_CAP = 200      # 渲染 dpi 上限（超采样后再缩放到目标宽，保证文字/线条锐利）
_GROW_FRAMES = 12      # 单周期内航迹生长帧数（放慢，使红点→点亮过渡可见）
_HOLD_FRAMES = 5       # 单周期生长结束后的定格帧
_ARRIVE_FRAMES = 4     # 周期起始「到达揭示」：新到达任务先以红点停留若干帧，再开始生长
_INTRO_FRAMES = 2      # 开场（仅底图，等待任务到达）
_OUTRO_FRAMES = 4      # 收尾定格（全部完成 + 总里程）
_FPS = 6              # 播放稍慢，便于看清航迹生长与任务点亮过程
_TARGET_W = 2400       # GIF 输出目标宽度（px）：超采样(13.5in×200dpi≈2700px) → LANCZOS 缩到 2400，左栏地图 DEM 子区≈1400px 近 1:1 铺底、清晰锐利（体积非瓶颈，画质优先）。此前 1500px 时地图仅≈870px 被 LANCZOS 压糊，提至 2400 后 DEM 与 allocation_anim_map 同口径清晰
_C_PENDING = "#ff2d2d"   # 待执行任务：亮红实心（白边），醒目区分于承运 UAV 着色
_C_NEST_CNT = "#0b2545"   # 机巢驻巢数 badge（非零）：与巢标号同色、加粗以示区别
_C_NEST_CNT0 = "#9aa5b1"  # 机巢驻巢数 badge（零）：置灰，弱化但保留巢位信息
# 大画布专用字号（论文 5.9in 口径的 FS 在 12.5in 宽画布上偏小，按画布比例放大）
_F_TITLE = 12.0        # 面板标题
_F_LEGEND = 10.5       # 图例
_F_SUPTITLE = 13.0     # 总标题
_F_STATUS = 10.5       # 顶部状态文字
_F_TASK = 8.0          # 任务标签 T0..T19
_F_NEST_CNT = 8.0       # 机巢驻巢数 badge（与任务标签同档）
_F_NOTE = 8.0          # 收敛/进度面板注释


def _task_by_id(solution: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(t["id"]): t for t in (solution.get("tasks") or [])}


def _nest_by_id(solution: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(n["id"]): n for n in (solution.get("nests") or [])}


def _build_tours(solution: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """把每个周期的 tours 展开成带航迹折线的结构。

    每条航迹：``start_xy → 任务1 → ... → 任务n → 终点巢``，预计算段长与累计
    弧长，供「生长」动画按弧长比例逐段揭示。
    """
    task_by_id = _task_by_id(solution)
    nest_by_id = _nest_by_id(solution)
    out: list[list[dict[str, Any]]] = []
    for cyc in solution.get("cycles") or []:
        tours: list[dict[str, Any]] = []
        for tour in cyc.get("tours") or []:
            if not tour.get("task_ids"):
                continue
            start = tour.get("start") or [0.0, 0.0, 0.0]
            pts: list[tuple[float, float]] = [(float(start[0]), float(start[1]))]
            for tid in tour["task_ids"]:
                t = task_by_id.get(int(tid))
                if t is not None:
                    pts.append((float(t["x"]), float(t["y"])))
            end = nest_by_id.get(int(tour["end_nest_id"])) \
                if tour.get("end_nest_id") is not None else None
            if end is not None:
                pts.append((float(end["x"]), float(end["y"])))
            seg = np.sqrt(np.sum(np.diff(np.asarray(pts, dtype=float), axis=0) ** 2, axis=1))
            cum = np.concatenate([[0.0], np.cumsum(seg)])
            tours.append(
                {
                    "uav_id": int(tour.get("uav_id", -1)),
                    "task_ids": [int(t) for t in tour["task_ids"]],
                    "start_nest_id": tour.get("start_nest_id"),
                    "end_nest_id": tour.get("end_nest_id"),
                    "pts": pts,
                    "cum": cum,
                    "total": float(cum[-1]) if cum.size else 0.0,
                    "est_cost": float(tour.get("est_cost") or 0.0),
                }
            )
        out.append(tours)
    return out


def _owner_map(tours_by_cycle: list[list[dict[str, Any]]]) -> dict[int, int]:
    """任务 -> 承运 UAV（首次出现即记，逐周期累加）。"""
    owner: dict[int, int] = {}
    for tours in tours_by_cycle:
        for tour in tours:
            for tid in tour["task_ids"]:
                owner.setdefault(tid, tour["uav_id"])
    return owner


def _cumulative_cost(tours_by_cycle: list[list[dict[str, Any]]]) -> list[float]:
    """每周期结束时的累计里程（km）：前序周期之和 + 本周期航迹代价之和。"""
    out: list[float] = []
    acc = 0.0
    for tours in tours_by_cycle:
        acc += sum(t["est_cost"] for t in tours) / 1000.0
        out.append(acc)
    return out


def _frame_nest_counts(
    snapshots: list[dict[int, int]],
    tours_cyc: list[dict[str, Any]],
    nest_ids: Sequence[int],
    c: int | None,
    p: float,
) -> dict[int, int]:
    """帧 (c, p) 的各机巢驻巢无人机数（当前该机巢内停着几架）。

    口径与画面**严格一致**——无人机图标在巢上才计入该巢：

    * 周期开始 / p=0（到达揭示帧）：UAV 尚未起飞，头点图标还在起点巢
      → 计在起点巢；
    * 0<p<1（航迹生长）：UAV 在空 → 不计入任何巢；
    * p≥1（定格帧）：航次完成，终点巢处画归巢空心环 → 计在返航巢
      ``end_nest``；
    * 未参飞 UAV 保持上一周期位置不动（空转周期同理）。

    ``snapshots`` 取自 ``allocation_status._stationed_by_cycle``：
    ``snapshots[c]`` 为第 c 周期**开始时**的驻巢分布（``snapshots[0]``=初始
    态），其正确性依赖巢归属链连续（``end_nest(c)=start_nest(c+1)``，
    实测恒成立）。
    """
    loc = dict(snapshots[0]) if c is None else dict(snapshots[c])
    if c is not None:
        for tour in tours_cyc:
            u = int(tour["uav_id"])
            if p >= 1.0 - 1e-9:
                eid = tour.get("end_nest_id")
                if eid is not None:
                    loc[u] = int(eid)
            elif p > 0.0:
                loc.pop(u, None)   # 在空：不计入任何巢
            # p == 0：尚未起飞，仍计在起点巢
    counts = {int(n): 0 for n in nest_ids}
    for _u, nn in loc.items():
        if nn in counts:
            counts[nn] += 1
    return counts


def _draw_nest_counts(ax, nests, counts: dict[int, int], *,
                      fontsize: float) -> None:
    """在每座机巢三角形**正下方**标注「n 架」= 当前驻巢无人机数。

    画在动画叠加层（逐帧重绘）：静态底图的「巢#」标号在上方（奇巢偏左、
    偶巢偏右），本 badge 在下方同样奇偶交替，为相距约 3.4 km 的相邻巢
    5/6 错开位置；零驻巢置灰，保留巢位信息同时降低视觉噪音。
    """
    for n in nests:
        nid = int(n["id"])
        cnt = int(counts.get(nid, 0))
        to_left = nid % 2 == 1
        ax.annotate(
            f"{cnt} 架",
            (float(n["x"]), float(n["y"])),
            textcoords="offset points",
            xytext=(-14 if to_left else 14, -10),
            ha="right" if to_left else "left",
            va="top",
            fontsize=fontsize,
            fontweight="bold",
            color=_C_NEST_CNT0 if cnt == 0 else _C_NEST_CNT,
            zorder=9,
            bbox=_HALO,
        )


def _draw_one_tour(ax, tour: dict[str, Any], p: float, *,
                   alpha: float, lw: float, drone: bool) -> None:
    """按进度 p∈[0,1] 画一条航迹：揭示到弧长比例 p 处，头点放无人机图标。"""
    pts = tour["pts"]
    cum = tour["cum"]
    total = tour["total"]
    if total <= 1e-9 or len(pts) < 2:
        return
    front = p * total
    color = uav_color(tour["uav_id"])

    drawn: list[tuple[float, float]] = [pts[0]]
    for i in range(len(pts) - 1):
        if front >= cum[i + 1]:
            drawn.append(pts[i + 1])
        elif front > cum[i]:
            frac = (front - cum[i]) / (cum[i + 1] - cum[i])
            mx = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * frac
            my = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * frac
            drawn.append((mx, my))
            break
    for a, b in zip(drawn, drawn[1:]):
        _arrow(ax, a, b, color, lw=lw)

    # 无人机头点（终点巢处画小空心环，与 allocation 一致）
    head = drawn[-1]
    if p >= 1.0 - 1e-6 and tour["end_nest_id"] is not None:
        if drone:
            ax.scatter([head[0]], [head[1]], s=150, facecolors="none",
                       edgecolors=color, linewidths=1.6, zorder=4)
    elif drone:
        ax.scatter([head[0]], [head[1]], marker=_G_DRONE, s=95,
                   color=color, edgecolors="white", linewidths=1.1, zorder=6)


def plot_allocation_anim(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("gif",),
    *,
    dpi: int = 200,
) -> list[Path]:
    fmt_set = {str(f).lower().lstrip(".") for f in formats}
    # "poster" = 只出末帧静态海报（不渲 GIF）：长周期动画 GIF 极重，海报刷新
    # 不必重跑整个动画。默认 PNG 出图仍整体跳过动画图（无 gif/mp4/poster）。
    if not (fmt_set & {"gif", "mp4", "poster"}):
        # 非动画格式请求时跳过——不拖慢默认 PNG 出图
        return []

    nests = list(solution.get("nests") or [])
    cycles = list(solution.get("cycles") or [])
    tasks = list(solution.get("tasks") or [])
    if not nests or not cycles or not tasks:
        return []

    tours_by_cycle = _build_tours(solution)
    if not any(tours_by_cycle):
        return []
    owner = _owner_map(tours_by_cycle)
    cum_km = _cumulative_cost(tours_by_cycle)
    task_by_id = _task_by_id(solution)
    arrival_cycle = _arrival_cycle_map(solution)
    # 逐周期起始驻巢快照（供帧态「当前驻巢无人机数」标注，口径见
    # _frame_nest_counts；与 allocation_status 同源，保证两图数字一致）
    snapshots, nest_ids, _capacities = _stationed_by_cycle(solution)
    n_tasks = len(tasks)
    n_cyc = len(cycles)

    # 已完成任务累计（按周期）：completed_after[c] = 前 c+1 个周期执行过的任务
    completed_after: list[set[int]] = []
    acc: set[int] = set()
    for tours in tours_by_cycle:
        for t in tours:
            acc |= set(t["task_ids"])
        completed_after.append(set(acc))

    # ── 帧调度 ───────────────────────────────────────────────────
    # 每帧 = (cycle_idx | None, 进度 p)。intro 空地图；每个周期先以 _ARRIVE_FRAMES
    # 帧(p=0)做「到达揭示」——新到达任务以红点停留、本周期航迹尚未生长，使红点
    # 出现事件清晰可见；随后 _GROW_FRAMES 帧航迹生长点亮任务；末段 _HOLD_FRAMES
    # 帧定格。outro 定格末周期。
    #
    # 空转周期（无航次且无 cost_history——任务池耗尽后的周期，如 exp01 周期
    # 5-8）不做到达揭示/生长动画：左栏本就完全定格（完成率/里程不变），逐帧
    # 重绘只是空转；只保留定格帧，使状态行里的周期号切换仍然可见。
    cycles_raw = list(solution.get("cycles") or [])
    idle_set = {
        c for c in range(n_cyc)
        if not tours_by_cycle[c]
        and not (cycles_raw[c].get("cost_history") or [])
    }
    frames: list[tuple[int | None, float]] = [(None, 0.0)] * _INTRO_FRAMES
    for c in range(n_cyc):
        if c in idle_set:
            frames += [(c, 1.0)] * _HOLD_FRAMES
            continue
        frames += [(c, 0.0)] * _ARRIVE_FRAMES   # 到达揭示：红点先出现并停留
        for g in range(_GROW_FRAMES):
            frames.append((c, (g + 1) / _GROW_FRAMES))
        frames += [(c, 1.0)] * _HOLD_FRAMES
    frames += [(n_cyc - 1, 1.0)] * _OUTRO_FRAMES
    n_frames = len(frames)

    # ── 画布 ─────────────────────────────────────────────────────
    # 左右两栏：左栏地图（DEM 满幅、近 1:1 铺底、清晰）+ 右栏 DMDE 收敛。
    # 进度信息（周期刻度 + 完成率）不再单独成面板，改为**内嵌到左栏地图底部**的
    # 一条扁进度条（见 _draw_progress_strip），随动画同步推进；故右栏只需一个
    # 全高子图，不再 subgridspec 拆分。
    fig = plt.figure(figsize=(13.5, 7.8))
    # width_ratios=1.09: 地图 aspect='equal' 后实际绘制框为正方形（高 5.96in 限定），
    # 1.09 使左槽宽≈5.96in 恰好填满，不留两侧空白；右栏收敛图相应略窄。
    gs = fig.add_gridspec(
        1, 2, width_ratios=[1.09, 1.0], wspace=0.05,
        left=0.04, right=0.985, top=0.90, bottom=0.135,
    )
    ax_map = fig.add_subplot(gs[0])
    ax_conv = fig.add_subplot(gs[1])

    # 静态底图：DEM + 机巢（只画一次）
    _draw_dem(ax_map, solution.get("dem"), solution)
    _draw_nests(ax_map, nests)
    _strip_axes(ax_map)
    # 底图范围 = DEM 视图范围：地形满幅铺底，避免轴比 DEM 大时在四周露出
    # base/dem.py 的橄榄暗色（#77784b）边。DEM 视图已含场景 2.5 km 余量，
    # 任务/机巢均在其内，无需对场景额外 pad。
    extent = _dem_view_extent(solution.get("dem"), solution)
    if extent is not None:
        ax_map.set_xlim(extent[0], extent[1])
        ax_map.set_ylim(extent[2], extent[3])
    else:
        xs = [float(t["x"]) for t in tasks] + [float(n["x"]) for n in nests]
        ys = [float(t["y"]) for t in tasks] + [float(n["y"]) for n in nests]
        pad = (max(xs) - min(xs)) * 0.06 or 1000.0
        ax_map.set_xlim(min(xs) - pad, max(xs) + pad)
        ax_map.set_ylim(min(ys) - pad, max(ys) + pad)
    ax_map.set_facecolor("white")
    ax_map.set_aspect("equal", adjustable="box")

    # 状态行**移出地图**：放在地图轴正下方的边距带（画布级 fig.text）。
    # 为什么不能放图内：状态行宽约 3.9 in，占地图轴（≈5.2 in）的 3/4——放在
    # 顶部无论左右都会横跨大半张地图，而 T10 等任务标号恰贴地图顶缘
    # （逐帧自检 frame#12-68 实测互压 100%），任何图内角落都避不开。
    # 底部边距带（gs.bottom=0.135 与 footnote 之间）是唯一无数据的干净区域。
    _mp = ax_map.get_position()
    status_txt = fig.text(
        _mp.x0 + _mp.width / 2.0, 0.098, "",
        ha="center", va="center", fontsize=_F_STATUS, color="#1f2933",
        zorder=10,
        bbox=dict(boxstyle="round,pad=0.34", fc="white", ec="none", alpha=0.9),
    )
    ax_map.set_title("(a) 滚动闭环分配动画（DEM + 航迹回放）",
                     fontsize=_F_TITLE, loc="left", pad=8)

    # 动画叠加层：与 ax_map 严格重叠、透明
    ax_anim = fig.add_axes(ax_map.get_position(), frameon=False)
    ax_anim.set_zorder(ax_map.get_zorder() + 1)
    ax_anim.set_facecolor("none")
    ax_anim.set_xlim(ax_map.get_xlim())
    ax_anim.set_ylim(ax_map.get_ylim())
    ax_anim.set_aspect("equal", adjustable="box")
    ax_anim.set_xticks([])
    ax_anim.set_yticks([])

    # 图例（机巢 / 无人机 / 待执行），固定位置；大画布上放大字号与图标便于辨识
    handles = [
        _IconPatch(_G_DRONE, fc=uav_color(next(iter(owner.values()))), ec="white",
                   lw=1.0, label="UAV 航迹"),
        _IconPatch(_G_HOUSE, fc=PALETTE["nest"], ec="white", lw=1.0,
                   label="机巢（○=返航巢；下方「n 架」=当前驻巢数）"),
        _IconPatch(_G_DOT, fc=_C_PENDING, ec="white", lw=0.9, label="待执行任务（红）"),
    ]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.72, 0.052),
               handler_map={_IconPatch: _IconHandler()}, ncol=3,
               fontsize=_F_LEGEND, columnspacing=1.6, handletextpad=0.6,
               borderpad=0.5, frameon=False)

    def _frame_state(idx: int) -> tuple[int | None, float]:
        return frames[idx]

    def _update(idx: int) -> None:
        c, p = _frame_state(idx)
        # 当前周期（1-indexed）：intro 帧为 0，表示「尚无任务到达」
        cur_cycle = 0 if c is None else (c + 1)
        ax_anim.clear()
        ax_anim.set_facecolor("none")
        ax_anim.set_xlim(ax_map.get_xlim())
        ax_anim.set_ylim(ax_map.get_ylim())
        ax_anim.set_aspect("equal", adjustable="box")
        ax_anim.set_xticks([])
        ax_anim.set_yticks([])
        ax_conv.clear()

        # 已完成任务集合（含本周期进度 p 之前扫过的）
        done: set[int] = set()
        cur_exec: set[int] = set()
        if c is None:
            done = set()
        else:
            for ci in range(c):
                done |= set(completed_after[ci])
            # 本周期按进度 p 揭示已扫过的任务
            for tour in tours_by_cycle[c]:
                cum = tour["cum"]
                front = p * tour["total"]
                for k, tid in enumerate(tour["task_ids"], start=1):
                    if front >= cum[k]:  # cum[k] = 到达第 k 个任务点的弧长
                        cur_exec.add(tid)
            done |= cur_exec

        # 任务点：未到达周期 → 不绘制；已到达但未执行 → 红点；已执行 → 承运 UAV 着色实心
        for t in tasks:
            tid = int(t["id"])
            x, y = float(t["x"]), float(t["y"])
            arr_c = arrival_cycle.get(tid, 0)
            if arr_c <= 0 or arr_c > cur_cycle:
                continue  # 任务尚未到达，本帧不出现
            if tid in done:
                ax_anim.scatter([x], [y], s=_task_size(t["reward"]),
                                color=uav_color(owner.get(tid, -1)),
                                edgecolors="white", linewidths=0.9, zorder=6)
                label = f"T{tid}"
                if int(tid) in arrival_cycle:
                    label += f"·C{arrival_cycle[int(tid)]}"
                ax_anim.annotate(label, (x, y), textcoords="offset points",
                                 xytext=(0, -14), ha="center", fontsize=_F_TASK,
                                 color="#1f2933", zorder=8, bbox=_HALO)
            else:
                ax_anim.scatter([x], [y], s=_task_size(t["reward"]),
                                color=_C_PENDING, edgecolors="white",
                                linewidths=0.9, zorder=2)

        # 航迹：前序周期画全（淡），当前周期画到 p（实）
        for ci in range(n_cyc):
            if c is None or ci > c:
                continue
            if ci < c:
                for tour in tours_by_cycle[ci]:
                    _draw_one_tour(ax_anim, tour, 1.0, alpha=0.45, lw=1.2, drone=False)
            else:
                for tour in tours_by_cycle[ci]:
                    _draw_one_tour(ax_anim, tour, p, alpha=0.95, lw=1.9, drone=True)

        # 机巢驻巢数（当前帧）：巢形三角形下方「n 架」，随周期与航迹进度
        # 动态更新（在空不计巢、归巢计入返航巢，与上面的图标严格同步）
        counts = _frame_nest_counts(
            snapshots,
            tours_by_cycle[c] if c is not None else [],
            nest_ids, c, p,
        )
        _draw_nest_counts(ax_anim, nests, counts, fontsize=_F_NEST_CNT)

        # 状态行（单行：周期 / 已到达 / 已完成百分比 / 里程），白底随文字自动贴合
        arrived = sum(1 for a in arrival_cycle.values() if a <= cur_cycle)
        rate = (len(done) / n_tasks * 100.0) if n_tasks else 0.0
        if c is None:
            status_text = (f"周期 0/{n_cyc} · 等待任务到达 · "
                           f"已完成 0%（0/{n_tasks}） · 0 km")
        else:
            # 当前周期已揭示部分的里程（按比例累加本周期各航迹）
            cur_km = sum(t["est_cost"] for t in tours_by_cycle[c]) / 1000.0 * p
            total_km = (cum_km[c - 1] if c > 0 else 0.0) + cur_km
            status_text = (
                f"周期 {c + 1}/{n_cyc} · 已到达 {arrived}/{n_tasks} · "
                f"已完成 {rate:.0f}%（{len(done)}/{n_tasks}） · {total_km:.0f} km"
            )
        status_txt.set_text(status_text)

        _draw_conv(ax_conv, solution, c, p)

    # 渲染各帧并保存（手动逐帧抓取 → Pillow 合成，规避本机 matplotlib
    # PillowWriter 不接受 optimize/pil_kwargs 的 grab 限制，且对 GIF 体积更可控）
    written: list[Path] = []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gif_name = "P06_allocation_anim"
    gif_dpi = min(int(dpi), _GIF_DPI_CAP)

    # 总标题与口径脚注必须在抓帧/保存之前添加——否则 GIF 与 poster 都没有
    # 标题和口径说明（曾因顺序错位导致动画产物缺两者，见 2026-10-13 审查）。
    fig.suptitle("exp01_smoke：分配过程动画演变（滚动闭环 · 航迹生长 · DMDE 收敛）",
                 fontsize=_F_SUPTITLE)
    footnote(fig, "口径：航迹按周期逐段生长，任务被航迹扫过即点亮（按承运 UAV 着色，"
                  "标号 T#·Ck，Ck=到达周期）；任务点于其到达周期才出现，未到达前不绘制，"
                  "已到达但未执行者显红。地图下方的状态行给出周期 / 已到达 / 已完成"
                  "百分比 / 累计里程（完成进度只用数字表达，不画进度条；白底随文字"
                  "自动贴合）——放在图外是因为状态行占地图宽 3/4，图内任何角落都会"
                  "压住贴顶的任务标号（逐帧自检实测 T10 互压 100%）；"
                  "右栏 (b) 为「当前周期」DMDE 最优适应度收敛曲线，游标随动画推进。"
                  "任务池耗尽的空转周期无求解数据，右栏显式标注「无 DMDE 求解」，"
                  "不再错接其它周期的曲线。"
                  "各机巢三角形下方的「n 架」= 当前驻巢无人机数（逐帧随航迹更新）："
                  "航次执行期间该 UAV 在空、不计入任何巢，完成航次回到返航巢后计入，"
                  "与画面上的无人机图标、归巢空心环严格同步；零驻巢置灰。"
                  "各机巢驻巢无人机数的逐周期演化已独立成图 allocation_status.gif。"
                  "里程 = 各航迹 est_cost 之和（km）。"
                  "底图地形与机巢为静态层。Ck 标出到达周期，晚到任务（如 T19·C3）"
                  "无法并入早周期航次。")

    try:
        from PIL import Image

        fig.set_dpi(gif_dpi)
        frames_rgb: list = []
        # poster-only 快路径：逐帧抓取只服务 GIF/MP4 编码；只要末帧海报时
        # 直接渲染末帧（docstring 承诺「不渲 GIF」即不逐帧渲染）。
        if fmt_set & {"gif", "mp4"}:
            for idx in range(n_frames):
                _update(idx)
                # 逐帧排版修正 + 自检：状态行文字长度随帧变化，堆叠/溢出只出现在
                # 某些中间帧（poster 末帧正常不代表帧帧正常）。declutter 内部会
                # draw；audit_frame 默认静默，EXP01_AUDIT_FRAMES=1 时逐帧全检。
                declutter_figure(fig)
                audit_frame(fig, idx)
                fig.canvas.draw()
                rgba = np.asarray(fig.canvas.buffer_rgba())
                frames_rgb.append(Image.fromarray(rgba).convert("RGB"))
        else:
            _update(n_frames - 1)
            declutter_figure(fig)
            audit_frame(fig, n_frames - 1)
            fig.canvas.draw()

        if "gif" in fmt_set and frames_rgb:
            gif_path = out_dir / f"{gif_name}.gif"
            # 控制体积：(1) 先统一缩放到目标宽度，避免高分屏 GIF 膨胀；
            # (2) 用「末帧」（内容最全）生成一张全局调色板，所有帧映射到同一
            #     调色板——静态底图（DEM/机巢）逐帧索引完全一致，配合
            #     disposal=2 仅编码变化区域，LZW 大幅压缩。
            W = _TARGET_W
            if frames_rgb[0].width > W:
                h = int(frames_rgb[0].height * W / frames_rgb[0].width)
                frames_rgb = [im.resize((W, h), Image.LANCZOS) for im in frames_rgb]
            # 关键语义色显式入调色板：MEDIANCUT 按像素数分箱，DEM 暖色地形会
            # 把小面积的红点/珊瑚点吞成棕橙（2026-10-13 实测 (165,93,48)）。
            pal_img = build_gif_palette(
                frames_rgb,
                extra_colors=[*UAV_PALETTE, _C_PENDING, "#c3cbd4", "#e6e9ed",
                              _C_NEST_CNT, _C_NEST_CNT0],
            )
            frames_p = [im.quantize(palette=pal_img) for im in frames_rgb]
            robust_pil_save(
                frames_p[0], gif_path, save_all=True, append_images=frames_p[1:],
                duration=int(round(1000.0 / _FPS)), loop=0,
                optimize=True, disposal=2,
            )
            written.append(gif_path)

        if "mp4" in fmt_set:
            try:
                from matplotlib.animation import FFPmpegWriter

                mp4_path = out_dir / f"{gif_name}.mp4"
                writer = FFPmpegWriter(fps=_FPS)
                writer.setup(fig, str(mp4_path), dpi=gif_dpi)
                for idx in range(n_frames):
                    _update(idx)
                    writer.grab_frame()
                writer.finish()
                written.append(mp4_path)
            except Exception as exc:  # 无 ffmpeg 等
                print(f"[exp01] allocation_anim：跳过 mp4（{exc}）")
    except Exception as exc:
        import traceback as _tb

        if exc.__context__ is not None:
            _tb.print_exception(type(exc.__context__), exc.__context__,
                                exc.__context__.__traceback__)
        _tb.print_exc()
        print(f"[exp01] allocation_anim：动画生成失败（{exc}），回退静态海报")
        _update(n_frames - 1)
        if "gif" in fmt_set:
            poster = out_dir / f"{gif_name}.gif"
            save_poster(fig, poster, dpi=gif_dpi)
            written.append(poster)

    # 静态海报 PNG（最终帧），供论文/文档直接引用（高 dpi，印刷级锐利）
    if "png" in fmt_set or "poster" in fmt_set:
        _update(n_frames - 1)
        poster_png = out_dir / f"{gif_name}_poster.png"
        save_poster(fig, poster_png, dpi=300)
        written.append(poster_png)

    if not written:
        plt.close(fig)
    return written


def _last_change_index(hist: Sequence[float]) -> int:
    """best-so-far 序列最后一次发生改进（下降）的代索引；全程不变返回 0。

    与 ``dmde_search.py`` 同一口径，用于把 x 轴截断到「有效改进窗口」。
    """
    last = 0
    prev = hist[0]
    for g, v in enumerate(hist[1:], start=1):
        if v < prev - 1e-12:
            last = g
            prev = v
    return last


def _draw_conv(ax, solution: dict[str, Any], c: int | None, p: float) -> None:
    """右栏上：当前周期 DMDE 收敛曲线（best-so-far），随动画进度 p 渐进揭示。

    呈现约定（与 ``dmde_search.py`` / notes.md §9 一致，只改绘图不改数据）：

    1. 只画「当前周期」自己的曲线——各周期目标量级差可达 1 个数量级，
       跨周期共享 y 轴会把本周期改进压成像素级平线；
    2. x 轴截断到有效改进窗口 ``max(10, 最后改进代×1.3+2)``；
    3. y 轴按当前周期 [min, max] 自适应，使微小改进仍可见；
    4. 初代即最优（候选规模小）的周期不画欺骗性平线，改注释说明。
    """
    cycles = list(solution.get("cycles") or [])
    hists: list[list[float]] = []
    for cyc in cycles:
        hists.append([float(v) for v in (cyc.get("cost_history") or [])])

    style_axes(ax, title="(b) 当前周期 DMDE 收敛（最优适应度）",
               xlabel="代数 g", ylabel="最优适应度")
    ax.set_title(ax.get_title(), fontsize=_F_TITLE)
    ax.tick_params(axis="both", labelsize=8.5)

    if not hists or all(len(h) < 2 for h in hists):
        ax.text(0.5, 0.5, "无 cost_history", transform=ax.transAxes,
                ha="center", va="center", fontsize=FS["base"], color="0.5")
        return

    if c is not None and 0 <= c < len(hists) and len(hists[c]) < 2:
        # 本周期无求解数据（任务池已空、零出动，cost_history 为空）。
        # 绝不再回退到周期 0 的曲线——那会把「左栏周期 N、右栏周期 1 曲线」
        # 钉成跨周期错位（P06 实测：后 4 个空转周期占 51% 播放时长全部
        # 如此，末帧海报亦复现），改为显式占位说明。
        style_axes(ax, title=f"(b) 周期 {c + 1}：无 DMDE 求解",
                   xlabel="代数 g", ylabel="最优适应度")
        ax.set_title(ax.get_title(), fontsize=_F_TITLE)
        ax.tick_params(axis="both", labelsize=8.5)
        ax.text(0.5, 0.5, "任务池已空，本周期无调度决策\n（无 cost_history）",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=FS["base"], color="0.5")
        return

    ci = c if (c is not None and 0 <= c < len(hists)) else 0
    hist = hists[ci]
    if len(hist) < 2:
        ax.text(0.5, 0.5, "无 cost_history", transform=ax.transAxes,
                ha="center", va="center", fontsize=FS["base"], color="0.5")
        return
    ch = _last_change_index(hist)
    n_total = len(hist)
    x_max = n_total - 1
    if ch + 1 < n_total:
        x_max = min(n_total - 1, max(10, int(np.ceil(ch * 1.3)) + 2))
    ax.set_xlim(-0.5, x_max)

    # y 轴按当前周期自适应
    v_min, v_max = min(hist), max(hist)
    span = v_max - v_min
    pad = span * 0.18 if span > 1e-9 else max(abs(v_max) * 2e-6, 1.0)
    ax.set_ylim(v_min - pad, v_max + pad)

    g = np.arange(n_total)
    col = uav_color(ci)
    # 只画到 visible 窗口：超出 xlim 的部分虽被裁掉看不见，但 Line2D 的 extent
    # 仍按完整数据计入 bbox_inches="tight"，会把 poster 撑成异常宽幅
    # （2026-10-13 实测 4.77× 画布宽 → poster 7964px）。
    visible = min(n_total, x_max + 1)
    ax.plot(g[:visible], hist[:visible], "-", drawstyle="steps-post",
            color=col, lw=1.0, alpha=0.25, zorder=2)
    gen_reveal = min(0 if c is None else min(max(int(round(p * x_max)), 0), x_max),
                     visible - 1)
    ax.plot(g[: gen_reveal + 1], hist[: gen_reveal + 1], "-",
            drawstyle="steps-post", color=col, lw=2.0, zorder=4)
    ax.scatter([gen_reveal], [hist[gen_reveal]], s=32, color=col,
               edgecolors="white", linewidths=1.0, zorder=5)
    if ch > 0:
        ax.scatter([ch], [hist[ch]], s=26, facecolors="none", edgecolors=col,
                   linewidths=1.2, zorder=4)

    # 注释：初代即最优 / 总改进幅度
    if abs(span) <= 1e-12:
        ax.text(0.04, 0.06, "初代即达最优\n（候选规模小，此后无改进）",
                transform=ax.transAxes, ha="left", va="bottom",
                fontsize=_F_NOTE, color="0.35",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                          edgecolor="0.85", alpha=0.9))
    else:
        rel = span / abs(hist[-1]) * 100.0 if hist[-1] else 0.0
        ax.text(0.02, 0.95,
                f"展示至第 {x_max} 代；其后 {n_total - 1 - x_max} 代无改进\n"
                f"总改进 {span:.4g}（相对 {rel:.2e}%）",
                transform=ax.transAxes, ha="left", va="top", fontsize=_F_NOTE,
                color="0.35")
    ax.grid(axis="y", alpha=0.2)

if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from task_allocation.experiments.exp01_smoke.plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
