# -*- coding: utf-8 -*-
"""图 ``allocation_status``——机队再平衡：各机巢驻巢无人机数随周期演化（动图）。

从 ``allocation_anim`` 的右栏状态面板独立出来成一张图。本图从「机队—机巢资源」
视角回答一个具体问题：

    **无人机在各个机巢间怎么流动？每个周期结束时各巢停着几架？**

旧版（三面板：周期进度 / 净变化 / 驻巢柱）把「周期进度」与 ``allocation_anim``
重复、净变化只剩几根非零柱，且**完全没画出「谁、在哪个周期、从哪个巢搬到哪个巢」**
——那恰恰是「再平衡」的核心信息。故重画为**单主图 + 旁白条**：

* 实心柱 = 当前驻巢 UAV 数；虚轮廓柱 = 初态（k=0）分布；柱顶 ``+n/−n`` = 相对
  初态净变化（绿=增 / 红=减）；
* 弧线 = 返航巢迁移、标注 UAV 编号：深色=本周期迁移、浅色=往期迁移（末帧全深），
  按周期**累计**绘制——静态海报（末帧）即可看到全部换巢的来龙去脉；
* 旁白条逐周期叙述：「第 k 周期结束 · 已执行 x/N ｜ 本周期迁移：…」；
* 容量线仅在「逼近容量」时才画（exp01 全程 cap4、最高驻巢 2，不咬合→不画，
  脚注显式说明），避免 8 条悬空虚线成视觉噪音。

数据来源：``solution.json`` 的 ``cycles[].tours``（uav_id / start_nest_id /
end_nest_id）+ ``nests``（capacity）。驻巢数按连续巢归属链逐周期累计
（``end_nest(c)==start_nest(c+1)`` 实测恒成立）。

输出：``P07_allocation_status.gif``（Pillow 合成）+ 末帧海报
``P07_allocation_status_poster.png``；``formats`` 不含 ``gif``/``mp4`` 时跳过。
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
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from ..theme import (
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

__all__ = ["plot_allocation_status"]

# 动画参数（状态图节奏可比空间动画更快：无航迹生长过程，每周期几帧定格即可）
_HOLD_FRAMES = 8       # 每个周期快照定格帧数
_INTRO_FRAMES = 3      # 开场：初始驻巢态
_OUTRO_FRAMES = 4      # 收尾定格（末周期 + 全部任务完成）
_FPS = 4               # 播放稍慢，便于看清柱高随周期迁移
_TARGET_W = 2000       # GIF 目标宽度（px）：横向大画布，体积非瓶颈、画质优先
_GIF_DPI_CAP = 200

# 大画布专用字号（与 allocation_anim 一致的放大口径）
_F_TITLE = 13.0
_F_SUPTITLE = 14.0
_F_NOTE = 9.0
_F_LEGEND = 8.5

# 语义色
_C_SOLID = PALETTE["selected"]   # 当前驻巢柱（青）
_C_GHOST = "#b6c2cf"             # 初态虚轮廓
_C_ZERO = "#c3cbd4"              # 零驻巢柱（灰）
_C_UP = "#2f9e44"                # 净增（绿）
_C_DOWN = "#e03131"              # 净减（红）
_C_MOVE_NOW = "#2f3b4a"          # 本周期迁移弧线（深）
_C_MOVE_PAST = "#aab3bd"         # 往期迁移弧线（浅）


def _stationed_by_cycle(solution: dict[str, Any]) -> tuple[list[dict[int, int]], list[int], dict[int, int]]:
    """逐周期「各机巢驻留的无人机集合」。

    返回 ``(snapshots, nest_ids, capacities)``：

    * ``snapshots[k]`` 是 ``{uav_id: nest_id}``，k=0 为初始态（各 UAV 在其首飞
      起点巢），k=c+1 为第 c 个周期结束后的驻巢分布；
    * 未飞行的 UAV 保持上一周期位置（不更新）；
    * ``nest_ids`` / ``capacities`` 供绘图标刻度与容量判断。

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


def _counts(snapshot: dict[int, int], nest_ids: list[int]) -> dict[int, int]:
    """快照 → 各机巢驻巢数。"""
    return {n: sum(1 for _u, nn in snapshot.items() if nn == n) for n in nest_ids}


def _moves_by_cycle(snapshots: list[dict[int, int]]) -> list[list[tuple[int, int, int]]]:
    """逐周期「返航巢迁移」：``moves[k-1]`` = 第 k 周期的 ``[(uav, from, to), ...]``。"""
    moves: list[list[tuple[int, int, int]]] = []
    for k in range(1, len(snapshots)):
        prev, cur = snapshots[k - 1], snapshots[k]
        mv = sorted(
            (u, prev[u], cur[u]) for u in cur if prev.get(u) != cur.get(u)
        )
        moves.append(mv)
    return moves


def _draw_main(
    ax,
    snap0: dict[int, int],
    snap: dict[int, int],
    nest_ids: list[int],
    capacities: dict[int, int],
    cum_moves: list[tuple[int, int, int, int]],   # [(cycle, uav, from, to), ...]
    k: int,
    emphasize_all: bool,
    total_uav: int,
) -> None:
    """主图：各机巢驻巢无人机数（实心=当前 / 虚轮廓=初态）+ 返航巢迁移弧线。

    * 实心柱=当前驻巢数，虚轮廓=初态分布，柱顶 ``+n/−n``=相对初态净变化；
    * 弧线=累计迁移（含第 k 周期及以前），深色=本周期 / 浅色=往期；
      ``emphasize_all``（末帧）时全部深色，保证静态海报读得出全部迁移；
    * 容量虚线仅在该巢驻巢数 ≥ 60% 容量时才画（不咬合不画，见脚注）。
    """
    style_axes(ax, title="(a) 各机巢驻巢无人机数：实心柱=当前，虚轮廓=初态，弧线=返航巢迁移（标 UAV 编号）")
    ax.set_title(ax.get_title(), fontsize=_F_TITLE)
    n_nest = len(nest_ids)
    xs = np.arange(n_nest)
    x_of = {n: i for i, n in enumerate(nest_ids)}
    cur = _counts(snap, nest_ids)
    ini = _counts(snap0, nest_ids)
    caps = [capacities.get(n, 0) for n in nest_ids]

    top_bar = max(list(cur.values()) + list(ini.values()) + [1])
    # 迁移弧线按「端点冲突」分配泳道：同起/同巢的弧线错开高度，避免相互叠压
    used: dict[int, set[int]] = {}
    lanes: list[int] = []
    for _cyc, _u, a, b in cum_moves:
        lane = 0
        taken = used.get(a, set()) | used.get(b, set())
        while lane in taken:
            lane += 1
        lanes.append(lane)
        used.setdefault(a, set()).add(lane)
        used.setdefault(b, set()).add(lane)
    arc_base = top_bar + 0.60                       # 弧线基准高度（避开柱顶标签）
    ymax = max(arc_base + 0.32 * (max(lanes, default=0) + 1) + 1.0,
               top_bar + 2.0, (max(caps) + 0.8) if caps else 0.0)

    ax.set_xlim(-0.7, n_nest - 0.3)
    ax.set_ylim(0, ymax)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"巢{n}" for n in nest_ids])
    for lab in ax.get_xticklabels():
        lab.set_fontsize(11.0)
    ax.set_yticks(range(0, int(ymax) + 1))
    ax.set_ylabel("驻巢无人机数（架）", fontsize=10.0)

    # 初态虚轮廓（ghost）：一眼看出「原来几架」
    for x, n in zip(xs, nest_ids):
        if ini[n] > 0:
            ax.bar(x, ini[n], width=0.62, facecolor="none", edgecolor=_C_GHOST,
                   linewidth=1.3, zorder=2)

    # 当前实心柱 + 计数 + 净变化徽标
    for x, n in zip(xs, nest_ids):
        c = cur[n]
        ax.bar(x, c, width=0.62,
               color=_C_ZERO if c == 0 else _C_SOLID,
               edgecolor="white", linewidth=1.1, zorder=3)
        ax.text(x, c + 0.10, f"{c}", ha="center", va="bottom", fontsize=11.0,
                color="#132238", zorder=5)
        d = c - ini[n]
        if d != 0:
            ax.text(x, c + 0.44, f"{d:+d}", ha="center", va="bottom", fontsize=9.0,
                    color=_C_UP if d > 0 else _C_DOWN, zorder=5, fontweight="bold")
        # 容量仅在逼近时才画（exp01 全程不咬合 → 不画，脚注说明）
        cap = capacities.get(n, 0)
        if cap > 0 and c >= 0.6 * cap:
            ax.plot([x - 0.31, x + 0.31], [cap, cap], "--", color="#b0b8c1",
                    linewidth=1.1, zorder=2)
            ax.text(x + 0.35, cap, f"cap{cap}", ha="left", va="center",
                    fontsize=7.5, color="#9aa5b1", zorder=5)

    # 迁移弧线（累计）：泳道高度错开；弧顶 = 基准 + rad*距离/2（arc3 二次贝塞尔峰值）
    for (cyc, u, a, b), lane in zip(cum_moves, lanes):
        if a not in x_of or b not in x_of:
            continue
        xa, xb = x_of[a], x_of[b]
        y = arc_base + 0.32 * lane
        dist = abs(xb - xa)
        rad = 0.16 if dist <= 1 else 0.30
        now = (cyc == k) or emphasize_all
        col = _C_MOVE_NOW if now else _C_MOVE_PAST
        ax.annotate(
            "", xy=(xb, y), xytext=(xa, y),
            arrowprops=dict(arrowstyle="-|>", color=col,
                            lw=1.8 if now else 1.1,
                            connectionstyle=f"arc3,rad={rad}",
                            shrinkA=3, shrinkB=3,
                            mutation_scale=13 if now else 9),
            zorder=4,
        )
        apex = y + 0.5 * rad * dist
        ax.text((xa + xb) / 2, apex + 0.10, f"U{u}", ha="center", va="bottom",
                fontsize=9.0 if now else 7.5, color=col, zorder=6,
                fontweight="bold" if now else "normal")

    # 右上角：驻巢总数（守恒 = 活跃机数）
    ax.text(0.985, 0.97, f"驻巢 {sum(cur.values())} / {total_uav}（守恒）",
            transform=ax.transAxes, ha="right", va="top", fontsize=10.5,
            color="#132238", zorder=6)
    ax.grid(axis="y", alpha=0.18)


def _narration(k: int, n_tasks: int, done: set[int], moves_k: list[tuple[int, int, int]],
               snap0: dict[int, int], nest_ids: list[int], total_uav: int) -> str:
    """底部旁白条：逐周期一句话讲清「现在到哪一步、这周期谁换了巢」。"""
    if k == 0:
        c0 = _counts(snap0, nest_ids)
        occ = sum(1 for n in nest_ids if c0[n] > 0)
        empty = sum(1 for n in nest_ids if c0[n] == 0)
        mx = max(c0.values()) if c0 else 0
        return (f"初始驻巢态：{total_uav} 架活跃无人机驻于 {occ} 个巢"
                f"（单巢最多 {mx} 架、{empty} 个空巢）· 已执行 0/{n_tasks} 任务")
    mv = "、".join(f"UAV{u} 巢{a}→巢{b}" for u, a, b in moves_k) or "无巢间迁移"
    return f"第 {k} 周期结束 · 已执行 {len(done)}/{n_tasks} ｜ 本周期迁移：{mv}"


def plot_allocation_status(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path,
    formats: Sequence[str] = ("gif",),
    *,
    dpi: int = 200,
) -> list[Path]:
    """绘制「机队再平衡」动图：各机巢驻巢无人机数随周期演化 + 返航巢迁移。"""
    fmt_set = {str(f).lower().lstrip(".") for f in formats}
    # "poster" = 只出末帧静态海报（不渲 GIF）；默认 PNG 出图仍整体跳过动画图
    if not (fmt_set & {"gif", "mp4", "poster"}):
        return []

    nests = list(solution.get("nests") or [])
    cycles = list(solution.get("cycles") or [])
    tasks = list(solution.get("tasks") or [])
    if not nests or not cycles:
        return []

    snapshots, nest_ids, capacities = _stationed_by_cycle(solution)
    completed_after = _completed_after(solution)
    moves_by_cycle = _moves_by_cycle(snapshots)
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

    # ── 画布：横向单主图（8 巢横向铺开，弧线有足够横向空间）+ 旁白条 ──
    fig = plt.figure(figsize=(11.8, 7.4))
    gs = fig.add_gridspec(1, 1, left=0.06, right=0.985, top=0.88, bottom=0.20)
    ax = fig.add_subplot(gs[0])

    # 图例（放主图下方，避开弧线区）
    handles = [
        Patch(facecolor=_C_SOLID, edgecolor="white", label="当前驻巢 UAV 数"),
        Patch(facecolor="none", edgecolor=_C_GHOST, label="初态（k=0）分布"),
        Line2D([0], [0], color=_C_MOVE_NOW, lw=1.8, label="本周期迁移"),
        Line2D([0], [0], color=_C_MOVE_PAST, lw=1.1, label="往期迁移"),
    ]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.135),
               ncol=4, frameon=False, fontsize=_F_LEGEND,
               handlelength=1.6, columnspacing=1.6)

    narr = fig.text(0.06, 0.082, "", fontsize=9.5, color="#3b4653",
                    ha="left", va="bottom")

    def _update(idx: int) -> None:
        k = frames[idx]                       # 快照序号（0..n_cyc）
        snap = snapshots[k]
        done = completed_after[k - 1] if k >= 1 else set()
        moves_k = moves_by_cycle[k - 1] if k >= 1 else []
        cum = [(cyc, u, a, b)
               for cyc in range(1, k + 1)
               for (u, a, b) in moves_by_cycle[cyc - 1]]

        ax.clear()
        _draw_main(ax, snapshots[0], snap, nest_ids, capacities, cum, k,
                   emphasize_all=(k >= n_cyc), total_uav=total_uav)
        narr.set_text(_narration(k, n_tasks, done, moves_k,
                                 snapshots[0], nest_ids, total_uav))

    # 容量是否咬合（决定脚注怎么说）
    peak = max((_counts(s, nest_ids) for s in snapshots), key=lambda c: max(c.values()))
    peak_max = max(peak.values()) if peak else 0
    cap_any = max(capacities.values()) if capacities else 0

    # 总标题与口径脚注必须在抓帧/保存之前添加
    fig.suptitle("exp01_smoke：机队再平衡——各机巢驻巢无人机数随周期演化（资源视角）",
                 fontsize=_F_SUPTITLE)
    cap_note = (f"容量 cap{cap_any} 全程未咬合（最高驻巢 {peak_max} 架），故不画容量线。"
                if cap_any and peak_max < 0.6 * cap_any else "")
    footnote(fig, "读法：实心柱=当前驻巢 UAV 数，虚轮廓=初态（k=0）分布，柱顶 +n/−n=相对初态净变化"
                  "（绿=增 / 红=减）；弧线=返航巢迁移（标 UAV 编号），深色=本周期、浅色=往期，末帧全部深色。\n"
                  "口径：UAV 完成航次后驻留返航巢 end_nest，未飞行者保持原位；巢归属链连续"
                  "（end_nest(c)=start_nest(c+1)）故可逐周期精确累计；仅统计 tours 中活跃机队，驻巢总数守恒。\n"
                  + cap_note)

    written: list[Path] = []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    gif_name = "P07_allocation_status"
    gif_dpi = min(int(dpi), _GIF_DPI_CAP)

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
            # 统一缩放到目标宽度，避免高分屏 GIF 膨胀；语义色显式入调色板
            # （同 allocation_anim：防小面积色块被 MEDIANCUT 吞色）。
            W = _TARGET_W
            if frames_rgb[0].width > W:
                h = int(frames_rgb[0].height * W / frames_rgb[0].width)
                frames_rgb = [im.resize((W, h), Image.LANCZOS) for im in frames_rgb]
            pal_img = build_gif_palette(
                frames_rgb,
                extra_colors=[*PALETTE.values(), _C_SOLID, _C_GHOST, _C_ZERO,
                              _C_UP, _C_DOWN, _C_MOVE_NOW, _C_MOVE_PAST,
                              "#e6e9ed"],
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
                print(f"[exp01] allocation_status：跳过 mp4（{exc}）")
    except Exception as exc:
        import traceback as _tb

        _tb.print_exc()
        print(f"[exp01] allocation_status：动画生成失败（{exc}），回退静态海报")
        _update(n_frames - 1)
        if "gif" in fmt_set:
            poster = out_dir / f"{gif_name}.gif"
            save_poster(fig, poster, dpi=gif_dpi)
            written.append(poster)

    # 静态海报 PNG（末帧，论文直接引用，高 dpi）
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
