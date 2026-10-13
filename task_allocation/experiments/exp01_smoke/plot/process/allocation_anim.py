# -*- coding: utf-8 -*-
"""图 ``allocation_anim``——分配过程的**动画演变**（动图）。

【process 层 · 求解过程 ③ 的动态版】

``allocation.png`` 的 (b) 面板是「逐周期空间演化」的多联静态图；本图把它
**变成动图**：在单张地图上回放整个滚动闭环——机巢与地形不动，UAV 航迹随
周期逐段「生长」，任务一旦被航迹扫过即点亮（按其承运 UAV 着色），累计完成
数与里程实时跳动；右栏同步画出「当前周期」的 DMDE 收敛曲线与进度刻度，把
**空间分配**与**优化搜索**两件事钉在同一时间轴上，最直观地看到「整个过程」。

输出：``allocation_anim.gif``（Pillow 合成；无 ffmpeg 时无 mp4）。
``formats`` 不含 ``gif`` / ``mp4`` 时本图直接跳过，避免拖慢默认 PNG 出图。

数据来源：``solution.json`` 的 ``cycles[].tours``（start / start_nest_id /
end_nest_id / task_ids / uav_id / est_cost）+ ``tasks`` / ``nests`` / ``dem``，
叠加 ``cycles[].cost_history``（DMDE 收敛）与 ``metrics`` 的 muas.f2_cost。
"""

from __future__ import annotations

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
    style_axes,
)
from .allocation import _arrival_cycle_map

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
# 大画布专用字号（论文 5.9in 口径的 FS 在 12.5in 宽画布上偏小，按画布比例放大）
_F_TITLE = 12.0        # 面板标题
_F_LEGEND = 10.5       # 图例
_F_SUPTITLE = 13.0     # 总标题
_F_STATUS = 10.5       # 顶部状态文字
_F_TASK = 8.0          # 任务标签 T0..T19
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
    if not (fmt_set & {"gif", "mp4"}):
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
    frames: list[tuple[int | None, float]] = [(None, 0.0)] * _INTRO_FRAMES
    for c in range(n_cyc):
        frames += [(c, 0.0)] * _ARRIVE_FRAMES   # 到达揭示：红点先出现并停留
        for g in range(_GROW_FRAMES):
            frames.append((c, (g + 1) / _GROW_FRAMES))
        frames += [(c, 1.0)] * _HOLD_FRAMES
    frames += [(n_cyc - 1, 1.0)] * _OUTRO_FRAMES
    n_frames = len(frames)

    # ── 画布 ─────────────────────────────────────────────────────
    # 横向大画布：左列地图（DEM 满幅、近 1:1 铺底、清晰）+ 右列 DMDE 收敛。
    # 旧的竖向 12.5×9.5 布局把地图压成左列竖条（aspect='equal' 后 DEM 左右
    # 留白、有效像素被 GIF 全局 255 色版量化压糊）——本图只保留地图与收敛，
    # 机巢驻巢数演化已独立成 allocation_status.gif，故地图可占 ~60% 宽、满高。
    fig = plt.figure(figsize=(13.5, 7.8))
    gs = fig.add_gridspec(
        1, 2, width_ratios=[1.6, 1.0], wspace=0.05,
        left=0.04, right=0.985, top=0.90, bottom=0.135,
    )
    gs_r = gs[1].subgridspec(2, 1, height_ratios=[1.0, 0.62], hspace=0.32)
    ax_map = fig.add_subplot(gs[0])
    ax_conv = fig.add_subplot(gs_r[0])
    ax_status = fig.add_subplot(gs_r[1])

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
                   label="机巢（○=返航巢）"),
        _IconPatch(_G_DOT, fc=_C_PENDING, ec="white", lw=0.9, label="待执行任务（红）"),
    ]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.34, 0.052),
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
        ax_status.clear()

        title_txt = ax_anim.text(
            0.03, 0.95, "", transform=ax_anim.transAxes, ha="left", va="top",
            fontsize=_F_STATUS, color="#1f2933", zorder=10,
            bbox=dict(boxstyle="round,pad=0.34", fc="white", ec="none", alpha=0.85),
        )

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

        # 顶部状态文字
        arrived = sum(1 for a in arrival_cycle.values() if a <= cur_cycle)
        if c is None:
            title_txt.set_text(f"周期 0/{n_cyc} · 等待任务到达 · 0 km")
        else:
            # 当前周期已揭示部分的里程（按比例累加本周期各航迹）
            cur_km = sum(t["est_cost"] for t in tours_by_cycle[c]) / 1000.0 * p
            total_km = (cum_km[c - 1] if c > 0 else 0.0) + cur_km
            title_txt.set_text(
                f"周期 {c + 1}/{n_cyc} · 已到达 {arrived}/{n_tasks} · "
                f"已执行 {len(done)}/{n_tasks} 任务 · {total_km:.0f} km"
            )

        # 收尾定格（末周期已画满）时，进度刻度全部转为「已完成」
        status_c = n_cyc if (c is not None and c == n_cyc - 1 and p >= 1.0) else c
        _draw_status(ax_status, status_c, n_cyc, done, n_tasks)
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
                  "已到达但未执行者显红。右栏上为「当前周期」DMDE 最优适应度收敛曲线，"
                  "游标随动画推进；右下 (b) 为滚动进度——周期刻度（绿●已完成 / 青环=当前 / "
                  "灰○未到）+ 完成率横条。各机巢驻巢无人机数的逐周期演化已独立成图 "
                  "allocation_status.gif。里程 = 各航迹 est_cost 之和（km）。"
                  "底图地形与机巢为静态层。Ck 标出到达周期，晚到任务（如 T19·C3）"
                  "无法并入早周期航次。")

    try:
        from PIL import Image

        fig.set_dpi(gif_dpi)
        frames_rgb: list = []
        for idx in range(n_frames):
            _update(idx)
            fig.canvas.draw()
            rgba = np.asarray(fig.canvas.buffer_rgba())
            frames_rgb.append(Image.fromarray(rgba).convert("RGB"))

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
                extra_colors=[*UAV_PALETTE, _C_PENDING, "#c3cbd4", "#e6e9ed"],
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
            robust_savefig(fig, poster, dpi=gif_dpi, bbox_inches="tight", facecolor="white")
            written.append(poster)

    # 静态海报 PNG（最终帧），供论文/文档直接引用（高 dpi，印刷级锐利）
    if "png" in fmt_set:
        _update(n_frames - 1)
        poster_png = out_dir / f"{gif_name}_poster.png"
        robust_savefig(fig, poster_png, dpi=300, bbox_inches="tight",
                        facecolor="white")
        written.append(poster_png)

    if not written:
        plt.close(fig)
    return written


def _draw_status(ax, c: int | None, n_cyc: int,
                 done: set[int], n_tasks: int) -> None:
    """右栏下：(b) 滚动进度 = 周期刻度（C1..Cn）+ 完成率横条。

    绿●已完成 / 青环=当前 / 灰○未到；完成率随动画推进实时增长。
    各机巢驻巢无人机数的逐周期演化已独立成图 ``allocation_status.gif``。
    """
    style_axes(ax, title="(b) 滚动进度")
    ax.set_title(ax.get_title(), fontsize=_F_TITLE)
    ax.set_xlim(-0.6, max(n_cyc - 1, 0) + 0.6)
    ax.set_ylim(0, 1.5)
    ax.set_yticks([])
    ax.set_xticks(range(n_cyc))
    ax.set_xticklabels([f"C{i + 1}" for i in range(n_cyc)])
    for lab in ax.get_xticklabels():
        lab.set_fontsize(9.0)
    for i in range(n_cyc):
        if c is None:
            state = "pending"
        elif i < c:
            state = "done"
        elif i == c:
            state = "current"
        else:
            state = "pending"
        y = 1.0
        if state == "done":
            ax.scatter([i], [y], s=140, color=PALETTE["executed"],
                       edgecolors="white", linewidths=1.2, zorder=3)
        elif state == "current":
            ax.scatter([i], [y], s=155, facecolors="white",
                       edgecolors=PALETTE["selected"], linewidths=2.2, zorder=3)
        else:
            ax.scatter([i], [y], s=140, color="#c3cbd4",
                       edgecolors="white", linewidths=1.2, zorder=3)
    rate = len(done) / n_tasks if n_tasks else 0.0
    ax.barh(0.45, rate, left=0.0, height=0.26,
            color=PALETTE["executed"], edgecolor="none")
    ax.barh(0.45, 1.0 - rate, left=rate, height=0.26,
            color="#e6e9ed", edgecolor="none")
    ax.text(0.5, 0.45, f"完成率 {rate*100:.0f}%  ({len(done)}/{n_tasks})",
            ha="center", va="center", fontsize=9.5, color="#132238", zorder=5)
    ax.text(0.0, 1.36, "绿●已完成   青环=当前   灰○未到", fontsize=_F_NOTE,
            color="#7b8794", ha="left", va="center")
    ax.grid(False)


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

    style_axes(ax, title="(c) 当前周期 DMDE 收敛（最优适应度）",
               xlabel="代数 g", ylabel="最优适应度")
    ax.set_title(ax.get_title(), fontsize=_F_TITLE)
    ax.tick_params(axis="both", labelsize=8.5)

    if not hists or all(len(h) < 2 for h in hists):
        ax.text(0.5, 0.5, "无 cost_history", transform=ax.transAxes,
                ha="center", va="center", fontsize=FS["base"], color="0.5")
        return

    ci = c if (c is not None and 0 <= c < len(hists)
               and len(hists[c]) >= 2) else 0
    hist = hists[ci]
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
    # 全程淡线作参考，揭示段实线
    ax.plot(g, hist, "-", drawstyle="steps-post", color=col, lw=1.0,
            alpha=0.25, zorder=2)
    gen_reveal = 0 if c is None else min(max(int(round(p * x_max)), 0), x_max)
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

