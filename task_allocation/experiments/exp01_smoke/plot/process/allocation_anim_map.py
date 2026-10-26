# -*- coding: utf-8 -*-
"""图 ``allocation_anim_map``——分配过程动画（**仅左栏地图**，高清大图）。

``allocation_anim`` 是「左栏地图（含底部滚动进度条）+ 右栏 DMDE 收敛」的左右
两栏组合动图；本图把**左栏地图单独抽出来**，去掉右栏，让 DEM + 航迹回放占满
整张画布，目标是「高清、大」——用于论文正文大图或投影展示时，地形纹理与航迹
生长细节不被压缩。叙事与 ``allocation_anim`` 左栏基本一致（不含进度条）：

    * 机巢与地形为静态底图（只画一次），UAV 航迹随周期逐段「生长」；
    * 任务一旦被航迹扫过即点亮（按其承运 UAV 着色，标号 T#·Ck，Ck=到达周期）；
    * 已到达但未执行者显红；地图上方的实时状态行给出「当前周期 / 已到达 / 已执行 /
      累计里程」（本图为纯地图版，不含进度条与收敛曲线）；
    * 每座机巢三角形**正下方**实时标注「n 架」= 当前驻巢无人机数（航次执行期间
      在空不计巢、归巢后计入返航巢，与无人机图标同步；奇偶巢 badge 左右错开，
      避免相邻巢 5/6 标号相压；零驻巢置灰）。

实现要点（见 2026-10-13 绘图审查）：

    * 竖向画布把地图压成竖条 + 全局 255 色版量化 = DEM 模糊；本图用**横向
      （实际是匹配 DEM 范围的竖版）满幅画布**，地图占 ~97% 宽、满高，DEM 近
      1:1 铺底、清晰锐利；
    * 渲染 dpi 上限 200，超采样到目标宽再 LANCZOS 缩放（画质优先，体积非瓶颈）；
    * 底图范围 = DEM 视图范围（满幅铺底，避免轴比 DEM 大时四周露底色）。

输出：``allocation_anim_map.gif``（Pillow 合成）+ 末帧海报 ``allocation_anim_map_poster.png``。
``formats`` 不含 ``gif`` / ``mp4`` 时本图直接跳过，避免拖慢默认 PNG 出图。

数据来源：``solution.json`` 的 ``cycles[].tours``（start / start_nest_id /
end_nest_id / task_ids / uav_id / est_cost）+ ``tasks`` / ``nests`` / ``dem``，
叠加 ``allocation`` 的到达周期映射。
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
from .allocation_anim import (
    _C_NEST_CNT,
    _C_NEST_CNT0,
    _build_tours,
    _cumulative_cost,
    _draw_nest_counts,
    _draw_one_tour,
    _frame_nest_counts,
    _nest_by_id,
    _owner_map,
    _task_by_id,
)
from .allocation_status import _stationed_by_cycle

__all__ = ["plot_allocation_anim_map"]

# GIF 友好参数（位图动图体极易膨胀，需压帧数；但本图为「高清大图」诉求，
# 渲染 dpi / 目标宽都给足，体积非瓶颈）
_GIF_DPI_CAP = 200      # 渲染 dpi 上限（超采样后再缩放到目标宽，保证文字/线条锐利）
_GROW_FRAMES = 12      # 单周期内航迹生长帧数（放慢，使红点→点亮过渡可见）
_HOLD_FRAMES = 5       # 单周期生长结束后的定格帧
_ARRIVE_FRAMES = 4     # 周期起始「到达揭示」：新到达任务先以红点停留若干帧，再开始生长
_INTRO_FRAMES = 2      # 开场（仅底图，等待任务到达）
_OUTRO_FRAMES = 4      # 收尾定格（全部完成 + 总里程）
_FPS = 6              # 播放稍慢，便于看清航迹生长与任务点亮过程
# 目标宽（px）：地图占满整幅，DEM 近 1:1 铺底、清晰锐利；体积非瓶颈，画质优先。
# 渲染原生宽 = fig_w × dpi = 14.0 × 200 = 2800 px，与 TARGET_W 一致 → LANCZOS
# 等同 1:1，无上采样模糊；地图框宽 ≈ 0.97×2800 ≈ 2716 px，高度 ≈ 0.825×3720 ≈ 3070 px。
_TARGET_W = 2800
_C_PENDING = "#ff2d2d"   # 待执行任务：亮红实心（白边），醒目区分于承运 UAV 着色

# 大画布专用字号（figure 14×18.6in，地图框宽 ≈ allocation_anim 整幅宽，按画布比例放大）
_F_TITLE = 14.0        # 面板标题
_F_LEGEND = 12.0       # 图例
_F_SUPTITLE = 16.0     # 总标题
_F_STATUS = 14.0       # 左上角实时状态文字
_F_TASK = 11.0         # 任务标签 T0..T19
_F_NEST_CNT = 11.0     # 机巢驻巢数 badge（与任务标签同档）
_F_NOTE = 10.0         # 脚注


def plot_allocation_anim_map(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("gif",),
    *,
    dpi: int = 200,
) -> list[Path]:
    fmt_set = {str(f).lower().lstrip(".") for f in formats}
    # "poster" = 只出末帧静态海报（不渲 GIF）；默认 PNG 出图仍整体跳过动画图
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
    arrival_cycle = _arrival_cycle_map(solution)
    # 逐周期起始驻巢快照（帧态「当前驻巢无人机数」标注，口径同 allocation_anim）
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
    frames: list[tuple[int | None, float]] = [(None, 0.0)] * _INTRO_FRAMES
    for c in range(n_cyc):
        frames += [(c, 0.0)] * _ARRIVE_FRAMES   # 到达揭示：红点先出现并停留
        for g in range(_GROW_FRAMES):
            frames.append((c, (g + 1) / _GROW_FRAMES))
        frames += [(c, 1.0)] * _HOLD_FRAMES
    frames += [(n_cyc - 1, 1.0)] * _OUTRO_FRAMES
    n_frames = len(frames)

    # ── 画布 ─────────────────────────────────────────────────────
    # 匹配 DEM 范围（约 101.6km × 115.0km，宽高比 ≈ 0.884）的竖版满幅画布：
    # 地图占 ~97% 宽、满高，DEM 近 1:1 铺底、清晰锐利（体积非瓶颈，画质优先）。
    # 旧横版组合图把地图压成左列竖条，DEM 被全局 255 色版量化压糊——本图根治之。
    fig = plt.figure(figsize=(14.0, 19.0))
    gs = fig.add_gridspec(
        1, 1, left=0.02, right=0.99, top=0.90, bottom=0.09,
    )
    ax_map = fig.add_subplot(gs[0])

    # 静态底图：DEM + 机巢（只画一次）
    _draw_dem(ax_map, solution.get("dem"), solution)
    _draw_nests(ax_map, nests)
    _strip_axes(ax_map)
    # 底图范围 = DEM 视图范围：地形满幅铺底，避免轴比 DEM 大时在四周露出
    # base/dem.py 的橄榄暗色（#77784b）边。DEM 视图已含场景余量，任务/机巢
    # 均在其内，无需对场景额外 pad。
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
    ax_map.set_title("分配过程动画：DEM + 航迹回放（滚动闭环）",
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

    # 图例（机巢 / 无人机 / 待执行），固定位置，大画布上放大字号与图标便于辨识
    handles = [
        _IconPatch(_G_DRONE, fc=uav_color(next(iter(owner.values()))), ec="white",
                   lw=1.0, label="UAV 航迹"),
        _IconPatch(_G_HOUSE, fc=PALETTE["nest"], ec="white", lw=1.0,
                   label="机巢（○=返航巢；下方「n 架」=当前驻巢数）"),
        _IconPatch(_G_DOT, fc=_C_PENDING, ec="white", lw=0.9, label="待执行任务（红）"),
    ]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.05),
               handler_map={_IconPatch: _IconHandler()}, ncol=3,
               fontsize=_F_LEGEND, columnspacing=1.6, handletextpad=0.6,
               borderpad=0.5, frameon=False)

    # 状态行**移出地图**（画布级）：放在顶部边距带（suptitle 之下、地图之上）。
    # 与 P06 同因：状态文字宽占地图轴 1/3 以上，图内任何角落都会压住贴顶的
    # 任务标号（逐帧自检 frame#12-68 实测 T10 互压 29%~42%）。
    status_txt = fig.text(
        0.5, 0.928, "", ha="center", va="center", fontsize=_F_STATUS,
        color="#1f2933", zorder=10,
        bbox=dict(boxstyle="round,pad=0.34", fc="white", ec="none", alpha=0.9),
    )

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
                                 xytext=(0, -16), ha="center", fontsize=_F_TASK,
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

        # 机巢驻巢数（当前帧）：巢形三角形下方「n 架」，逐帧随航迹更新
        counts = _frame_nest_counts(
            snapshots,
            tours_by_cycle[c] if c is not None else [],
            nest_ids, c, p,
        )
        _draw_nest_counts(ax_anim, nests, counts, fontsize=_F_NEST_CNT)

        # 左上角实时状态文字
        arrived = sum(1 for a in arrival_cycle.values() if a <= cur_cycle)
        if c is None:
            status_txt.set_text(f"周期 0/{n_cyc} · 等待任务到达 · 0 km")
        else:
            # 当前周期已揭示部分的里程（按比例累加本周期各航迹）
            cur_km = sum(t["est_cost"] for t in tours_by_cycle[c]) / 1000.0 * p
            total_km = (cum_km[c - 1] if c > 0 else 0.0) + cur_km
            status_txt.set_text(
                f"周期 {c + 1}/{n_cyc} · 已到达 {arrived}/{n_tasks} · "
                f"已执行 {len(done)}/{n_tasks} 任务 · {total_km:.0f} km"
            )

    # 渲染各帧并保存（手动逐帧抓取 → Pillow 合成，规避本机 matplotlib
    # PillowWriter 不接受 optimize/pil_kwargs 的 grab 限制，且对 GIF 体积更可控）
    written: list[Path] = []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gif_name = "P08_allocation_anim_map"
    gif_dpi = min(int(dpi), _GIF_DPI_CAP)

    # 总标题与口径脚注必须在抓帧/保存之前添加——否则 GIF 与 poster 都缺两者。
    # 脚注必须手动换行：单行超长会溢出画布右缘——GIF 被裁、poster 被
    # bbox_inches="tight" 撑出大片白边（2026-10-13 实测）。
    fig.suptitle("exp01_smoke：分配过程动画（地图 · DEM + 航迹回放）",
                 fontsize=_F_SUPTITLE)
    footnote(fig, "口径：航迹按周期逐段生长，任务被航迹扫过即点亮（按承运 UAV 着色，标号 T#·Ck，Ck=到达周期）；"
                  "任务点于其到达周期才出现，未到达前不绘制，已到达但未执行者显红。\n"
                  "地图上方的实时状态行给出当前周期 / 已到达 / 已执行 / 累计里程（置于图外，避免压住贴顶任务标号）；里程 = 各航迹 est_cost 之和（km）；"
                  "底图地形与机巢为静态层。本图为 allocation_anim 左栏地图的单独高清版，"
                  "DMDE 收敛见原图右侧栏、滚动进度见原图地图底部进度条。\n"
                  "各机巢三角形下方的「n 架」= 当前驻巢无人机数（逐帧随航迹更新）：航次执行期间该 UAV 在空、不计入任何巢，"
                  "完成航次回到返航巢后计入，与画面上的无人机图标、归巢空心环严格同步；零驻巢置灰。\n"
                  "Ck 标出到达周期，晚到任务（如 T19·C3）无法并入早周期航次。")

    try:
        from PIL import Image

        fig.set_dpi(gif_dpi)
        frames_rgb: list = []
        # poster-only 快路径：逐帧抓取只服务 GIF/MP4 编码；只要末帧海报时
        # 直接渲染末帧（docstring 承诺「不渲 GIF」即不逐帧渲染）。
        if fmt_set & {"gif", "mp4"}:
            for idx in range(n_frames):
                _update(idx)
                # 逐帧排版修正 + 自检（见 allocation_anim 同名注释）
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
            elif frames_rgb[0].width < W:
                # 超采样兜底：原生宽不足目标宽时上采样（本图原生宽恰等于目标宽，通常不触发）
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
                print(f"[exp01] allocation_anim_map：跳过 mp4（{exc}）")
    except Exception as exc:
        import traceback as _tb

        if exc.__context__ is not None:
            _tb.print_exception(type(exc.__context__), exc.__context__,
                                exc.__context__.__traceback__)
        _tb.print_exc()
        print(f"[exp01] allocation_anim_map：动画生成失败（{exc}），回退静态海报")
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

if __name__ == "__main__":
    import sys
    from pathlib import Path as _P
    _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
    if str(_r) not in sys.path:
        sys.path.insert(0, str(_r))
    from task_allocation.experiments.exp01_smoke.plot import run_script
    run_script(_P(__file__).stem, sys.argv[1:])
