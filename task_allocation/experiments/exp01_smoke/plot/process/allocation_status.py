# -*- coding: utf-8 -*-
"""图 ``allocation_status``——各机巢「当前驻巢无人机数」随周期的演化（动图）。

从 ``allocation_anim`` 的右栏状态面板独立出来成一张图。机巢状态（每个机巢
当前驻留的无人机数）是论文关心的「机队—机巢资源」维度；塞进动画单图会被压小、
且会拖垮地图 DEM 的清晰度，故独立成图、给足空间。本图专注回答：

    **每个机巢当前停着几架无人机？它们随周期怎么流动？**

做法：

* UAV 巢归属链连续——实测 ``end_nest(c) == start_nest(c+1)`` 恒成立（见下方
  ``_stationed_by_cycle``），所以「驻巢数」可逐周期精确累计：每架 UAV 完成
  航次后驻留在其 ``end_nest``，未飞行的 UAV 保持上一周期的位置不变；
* 快照 ``k``（k=0..n_cyc）表示「第 k 个周期结束后」各机巢的驻巢分布；k=0 为
  初始态（各 UAV 在其首飞起点巢）；动画逐周期推进，柱高即体现无人机在机巢间
  的迁移（如异巢终止使某巢无人机数增减）；
* 中排附「各巢驻巢数净变化（当前 − 初态）」，绿=增多 / 红=减少，直观显示机队
  再平衡方向；上排为周期进度刻度。

输出：``P07_allocation_status.gif``（Pillow 合成）；``formats`` 不含 ``gif``/``mp4``
时本图跳过。另落一张末帧海报 ``P07_allocation_status_poster.png`` 供论文直接引用。

数据来源：``solution.json`` 的 ``cycles[].tours``（uav_id / start_nest_id /
end_nest_id）+ ``nests``（capacity）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np

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

__all__ = ["plot_allocation_status"]

# 动画参数（状态图节奏可比空间动画更快：无航迹生长过程，每周期几帧定格即可）
_HOLD_FRAMES = 8       # 每个周期快照定格帧数
_INTRO_FRAMES = 3      # 开场：初始驻巢态
_OUTRO_FRAMES = 4      # 收尾定格（末周期 + 100% 完成）
_FPS = 4               # 播放稍慢，便于看清柱高随周期迁移
_TARGET_W = 1400       # GIF 目标宽度（px）：体积非瓶颈，画质优先
_GIF_DPI_CAP = 200

# 大画布专用字号（与 allocation_anim 一致的放大口径）
_F_TITLE = 13.0
_F_SUPTITLE = 14.0
_F_NOTE = 9.0
_F_LEGEND = 11.0


def _stationed_by_cycle(solution: dict[str, Any]) -> tuple[list[dict[int, int]], list[int], dict[int, int]]:
    """逐周期「各机巢驻留的无人机集合」。

    返回 ``(snapshots, nest_ids, capacities)``：

    * ``snapshots[k]`` 是 ``{uav_id: nest_id}``，k=0 为初始态（各 UAV 在其首飞
      起点巢），k=c+1 为第 c 个周期结束后的驻巢分布；
    * 未飞行的 UAV 保持上一周期位置（不更新）；
    * ``nest_ids`` / ``capacities`` 供绘图标刻度与容量虚线。

    正确性依赖巢归属链连续（end_nest(c)==start_nest(c+1)）；若某 UAV 缺失
    start_nest/end_nest 则该架不参与计数（只统计出现在 tours 里的活跃机队）。
    """
    nests = sorted(
        (int(n["id"]) for n in (solution.get("nests") or [])),
        key=lambda x: x,
    )
    capacities = {int(n["id"]): int(n.get("capacity", 0) or 0)
                  for n in (solution.get("nests") or [])}

    # 初始位置 = 每架 UAV 的「首飞起点巢」
    location: dict[int, int] = {}
    for cyc in solution.get("cycles") or []:
        for t in cyc.get("tours") or []:
            if not t.get("task_ids"):
                continue
            u = int(t["uav_id"])
            sid = t.get("start_nest_id")
            if sid is not None and u not in location:
                location[u] = int(sid)

    snapshots: list[dict[int, int]] = [dict(location)]  # k=0 初始态
    for cyc in solution.get("cycles") or []:
        for t in cyc.get("tours") or []:
            if not t.get("task_ids"):
                continue
            eid = t.get("end_nest_id")
            if eid is not None:
                location[int(t["uav_id"])] = int(eid)
        snapshots.append(dict(location))
    return snapshots, nests, capacities


def _completed_after(solution: dict[str, Any]) -> list[set[int]]:
    """逐周期累计「已执行的任务集合」（按 tours 的 task_ids 累加）。"""
    out: list[set[int]] = []
    acc: set[int] = set()
    for cyc in solution.get("cycles") or []:
        for t in cyc.get("tours") or []:
            if not t.get("task_ids"):
                continue
            acc |= set(int(x) for x in t["task_ids"])
        out.append(set(acc))
    return out


def _draw_cycles(ax, c: int | None, n_cyc: int) -> None:
    """上排：周期进度刻度（C1..Cn，done/current/pending）。"""
    style_axes(ax, title="(a) 周期进度")
    ax.set_title(ax.get_title(), fontsize=_F_TITLE)
    ax.set_xlim(-0.6, max(n_cyc - 1, 0) + 0.6)
    ax.set_ylim(0, 1.4)
    ax.set_yticks([])
    ax.set_xticks(range(n_cyc))
    ax.set_xticklabels([f"C{i + 1}" for i in range(n_cyc)])
    for lab in ax.get_xticklabels():
        lab.set_fontsize(10.0)
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
            ax.scatter([i], [y], s=150, color=PALETTE["executed"],
                       edgecolors="white", linewidths=1.2, zorder=3)
        elif state == "current":
            ax.scatter([i], [y], s=165, facecolors="white",
                       edgecolors=PALETTE["selected"], linewidths=2.4, zorder=3)
        else:
            ax.scatter([i], [y], s=150, color="#c3cbd4",
                       edgecolors="white", linewidths=1.2, zorder=3)
    ax.text(0.0, 1.30, "绿●已完成   青环=当前   灰○未到", fontsize=_F_NOTE,
            color="#7b8794", ha="left", va="center")
    ax.grid(False)


def _draw_netflow(ax, snap0: dict[int, int], snap: dict[int, int],
                  nest_ids: list[int], k: int, n_cyc: int) -> None:
    """中排：机队再平衡——各机巢驻巢数净变化（当前快照 − 初态），随周期动画。

    绿=增多 / 红=减少 / 灰=不变；直观显示「机队在机巢间再平衡」的方向与幅度，
    是本图相对 ``allocation_anim`` 右栏进度条独有的信息（资源视角）。
    """
    style_axes(ax, title="(b) 机队再平衡：各巢驻巢数净变化（当前 − 初态）")
    ax.set_title(ax.get_title(), fontsize=_F_TITLE)
    n_nest = len(nest_ids)
    xs = np.arange(n_nest)
    base = {n: sum(1 for u, nn in snap0.items() if nn == n) for n in nest_ids}
    cur = {n: sum(1 for u, nn in snap.items() if nn == n) for n in nest_ids}
    delta = [cur[n] - base[n] for n in nest_ids]
    ymax = max(max(abs(d) for d in delta), 1) + 0.8
    ax.set_xlim(-0.6, (n_nest - 0.4) if n_nest else 0.5)
    ax.set_ylim(-ymax, ymax)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"巢{n}" for n in nest_ids])
    for lab in ax.get_xticklabels():
        lab.set_fontsize(10.0)
    ax.axhline(0, color="#9aa5b1", lw=0.9, zorder=2)
    for x, nest, d in zip(xs, nest_ids, delta):
        if d == 0:
            ax.bar(x, 0.0, width=0.6, color="#c3cbd4", edgecolor="white",
                   linewidth=1.0, zorder=3)
            continue
        col = "#2f9e44" if d > 0 else "#e03131"
        ax.bar(x, d, width=0.6, color=col, edgecolor="white",
               linewidth=1.1, zorder=3)
        ax.text(x, d + (0.12 if d > 0 else -0.12), f"{d:+d}", ha="center",
                va="bottom" if d > 0 else "top", fontsize=10.0,
                color="#132238", zorder=5)
    ax.text(0.02, 0.94, f"第 {k} 周期结束（相对初态）", fontsize=_F_NOTE,
            color="#5b6472", ha="left", va="center", transform=ax.transAxes)
    ax.grid(axis="y", alpha=0.18)


def _draw_nest(ax, snapshot: dict[int, int], nest_ids: list[int],
               capacities: dict[int, int], total_uav: int) -> None:
    """下排（主图）：各机巢当前驻巢无人机数（柱），随周期柱高变化即体现迁移。"""
    style_axes(ax, title="(c) 主图：各机巢当前驻巢无人机数（机队再平衡）")
    ax.set_title(ax.get_title(), fontsize=_F_TITLE)
    n_nest = len(nest_ids)
    counts = [sum(1 for u, n in snapshot.items() if n == nest) for nest in nest_ids]
    caps = [capacities.get(nest, 0) for nest in nest_ids]
    ymax = max(max(counts + [1]), max(caps + [1])) + 0.8
    xs = np.arange(n_nest)
    ax.set_xlim(-0.6, (n_nest - 0.4) if n_nest else 0.5)
    ax.set_ylim(0, ymax)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"巢{n}" for n in nest_ids])
    for lab in ax.get_xticklabels():
        lab.set_fontsize(10.0)
    for x, nest, cnt, cap in zip(xs, nest_ids, counts, caps):
        col = "#c3cbd4" if cnt == 0 else PALETTE["selected"]
        ax.bar(x, cnt, width=0.6, color=col, edgecolor="white",
               linewidth=1.1, zorder=3)
        ax.text(x, cnt + 0.10, f"{cnt}", ha="center", va="bottom",
                fontsize=11.0, color="#132238", zorder=5)
        if cap > 0:
            ax.plot([x - 0.3, x + 0.3], [cap, cap], "--", color="#b0b8c1",
                    linewidth=1.1, zorder=2)
            ax.text(x + 0.34, cap, f"cap{cap}", ha="left", va="center",
                    fontsize=7.5, color="#9aa5b1", zorder=5)
    ax.text(0.98, 0.96, f"驻巢总数 {sum(counts)} / {total_uav}",
            transform=ax.transAxes, ha="right", va="top", fontsize=10.0,
            color="#132238", zorder=5)
    ax.grid(axis="y", alpha=0.18)


def plot_allocation_status(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("gif",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """绘制各机巢驻巢无人机数随周期演化的动图。"""
    fmt_set = {str(f).lower().lstrip(".") for f in formats}
    if not (fmt_set & {"gif", "mp4"}):
        return []

    nests = list(solution.get("nests") or [])
    cycles = list(solution.get("cycles") or [])
    tasks = list(solution.get("tasks") or [])
    if not nests or not cycles:
        return []

    snapshots, nest_ids, capacities = _stationed_by_cycle(solution)
    completed_after = _completed_after(solution)
    n_cyc = len(cycles)
    n_tasks = len(tasks)
    total_uav = len({int(t["uav_id"])
                     for c in cycles for t in c.get("tours") or []
                     if t.get("task_ids")})

    # ── 帧调度：intro(初始态) + 每周期快照定格 + outro(末态) ──
    frames: list[int] = [0] * _INTRO_FRAMES          # k=0 初始驻巢态
    for k in range(1, n_cyc + 1):
        frames += [k] * _HOLD_FRAMES                  # 第 k 周期结束后的驻巢分布
    frames += [n_cyc] * _OUTRO_FRAMES
    n_frames = len(frames)

    # ── 画布：竖向 3 面板（周期刻度 / 完成率 / 机巢驻巢），给足主图空间 ──
    fig = plt.figure(figsize=(9.0, 10.5))
    gs = fig.add_gridspec(3, 1, height_ratios=[0.5, 0.5, 1.6],
                          hspace=0.34, left=0.10, right=0.96,
                          top=0.90, bottom=0.13)
    ax_cyc = fig.add_subplot(gs[0])
    ax_rate = fig.add_subplot(gs[1])
    ax_nest = fig.add_subplot(gs[2])

    def _update(idx: int) -> None:
        k = frames[idx]                       # 快照序号（0..n_cyc）
        snap = snapshots[k]
        done = completed_after[k - 1] if k >= 1 else set()
        cur_cycle = k                          # 当前展示到第 k 周期结束
        status_c = n_cyc if k >= n_cyc else (k - 1 if k >= 1 else None)

        ax_cyc.clear()
        ax_rate.clear()
        ax_nest.clear()
        _draw_cycles(ax_cyc, status_c, n_cyc)
        _draw_netflow(ax_rate, snapshots[0], snap, nest_ids, k, n_cyc)
        _draw_nest(ax_nest, snap, nest_ids, capacities, total_uav)

        # 顶部状态文字
        if k == 0:
            ax_nest.set_xlabel(
                f"初始驻巢态（执行 0/{n_tasks} 任务）", fontsize=10.0,
                color="#5b6472", labelpad=8)
        else:
            ax_nest.set_xlabel(
                f"第 {k} 周期结束 · 已执行 {len(done)}/{n_tasks} 任务",
                fontsize=10.0, color="#5b6472", labelpad=8)

    # 总标题与口径脚注必须在抓帧/保存之前添加
    fig.suptitle("exp01_smoke：机队再平衡——各机巢驻巢无人机数随周期演化（资源视角）",
                 fontsize=_F_SUPTITLE)
    footnote(fig, "本图从「机队—机巢资源」视角回答：每周期结束时各机巢停着几架无人机、"
                  "机队如何在机巢间再平衡？(c) 主图柱高=当前驻巢 UAV 数，虚线=容量，"
                  "右上角=全队驻巢总数（守恒=活跃机数）；(b) 各巢净变化（当前−初态），"
                  "绿=增多/红=减少，直观显示再平衡方向；(a) 周期进度。口径：UAV 完成航次后"
                  "驻留返航巢 end_nest，未飞行者保持原位；巢归属链连续"
                  "（end_nest(c)=start_nest(c+1)）故可逐周期精确累计。仅统计 tours 中活跃机队。")

    written: list[Path] = []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gif_name = "P07_allocation_status"
    gif_dpi = min(int(dpi), _GIF_DPI_CAP)

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
            W = _TARGET_W
            if frames_rgb[0].width > W:
                h = int(frames_rgb[0].height * W / frames_rgb[0].width)
                frames_rgb = [im.resize((W, h), Image.LANCZOS) for im in frames_rgb]
            # 语义色显式入调色板（同 allocation_anim：防 DEM 暖色吞掉小面积色块）
            pal_img = build_gif_palette(
                frames_rgb,
                extra_colors=[*PALETTE.values(), "#c3cbd4", "#e6e9ed"],
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
            except Exception as exc:
                print(f"[exp01] allocation_status：跳过 mp4（{exc}）")
    except Exception as exc:
        import traceback as _tb

        _tb.print_exc()
        print(f"[exp01] allocation_status：动画生成失败（{exc}），回退静态海报")
        _update(n_frames - 1)
        if "gif" in fmt_set:
            poster = out_dir / f"{gif_name}.gif"
            robust_savefig(fig, poster, dpi=gif_dpi, bbox_inches="tight", facecolor="white")
            written.append(poster)

    # 静态海报 PNG（末帧，论文直接引用，高 dpi）
    if "png" in fmt_set:
        _update(n_frames - 1)
        poster_png = out_dir / f"{gif_name}_poster.png"
        robust_savefig(fig, poster_png, dpi=300, bbox_inches="tight", facecolor="white")
        written.append(poster_png)

    if not written:
        plt.close(fig)
    return written
