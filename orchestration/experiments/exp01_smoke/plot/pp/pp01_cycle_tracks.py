# -*- coding: utf-8 -*-
"""PP01 逐周期真实航迹（PP）：每周期飞成了几条、按什么次序、哪一段断了。

【PP 层 · 下层 path planning 的账 · 只有求解之后才知道】

一个周期一栏，画的是**下层真正交付的航迹**（``trajectories.json`` 的逐时刻
采样点），不是站点之间的直线：

* 任务号 T# 直接写在圆里（不另拉标号，杜绝压字）；访问次序看箭头与面板上的站序串；
* 方向记号 ``>`` 沿真实航迹摆在两站之间的中点，表示飞行方向；
* **失败航次**不整条丢弃：断点前已解好的段按真实节点折线画**实线**（前缀即
  已解几何），断掉的那一段按空间层初值（RRT 折线）画**虚线**、标红并写清失败
  原因（几次尝试、卡在净空还是迭代）。读者因此能区分"哪几段飞成、哪一段断"，
  而不是只看到一个红叉。

建筑只画轮廓、调淡到背景级：它们是尺度参照，不该和航迹抢墨。
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import matplotlib.patheffects as pe

from .. import theme as th
from ..base import scene as sc
from ..base import ledger as iv
from ..base.declutter import declutter

NAME = "PP01_cycle_tracks"


def _chevrons(ax, tr: dict, idx: list[int], color: str) -> None:
    """在相邻站点之间的真实航迹中点放一个 ``>``（沿航迹切线旋转）。"""
    import math

    import numpy as np

    xs, ys = np.asarray(tr["x"], float), np.asarray(tr["y"], float)
    for a, b in zip(idx, idx[1:]):
        if b - a < 4:
            continue
        seg = np.hypot(np.diff(xs[a:b + 1]), np.diff(ys[a:b + 1]))
        s = np.concatenate([[0.0], np.cumsum(seg)])
        if s[-1] <= 1e-6:
            continue
        i = a + int(np.searchsorted(s, 0.5 * s[-1]))
        i = min(max(i, a + 1), b - 1)
        dx, dy = xs[i + 1] - xs[i - 1], ys[i + 1] - ys[i - 1]
        if math.hypot(dx, dy) < 1e-9:
            continue
        ax.text(xs[i], ys[i], ">", rotation=math.degrees(math.atan2(dy, dx)),
                rotation_mode="anchor", ha="center", va="center", fontsize=11,
                color=color, fontweight="bold", zorder=7.6)


def _failing_leg(tour: iv.TourRow) -> tuple[int, dict] | None:
    """从 ``failure_diag`` 里定位断掉的那一段：(段号, 段诊断)。"""
    legs = [L for L in (tour.diag.get("legs") or []) if isinstance(L, dict)]
    if not legs:
        return None
    hit = [i for i, L in enumerate(legs) if L.get("status") == "all_candidates_failed"]
    if not hit:
        hit = [i for i, L in enumerate(legs) if L.get("rejected")]
    if not hit:
        return None
    i = hit[-1]
    return i, legs[i]


def _clearance_note(leg: dict) -> str:
    """一行说清"为什么这段没解出来"：优先取最后一次尝试的判决。"""
    tries = leg.get("tried")
    head = f"{int(tries)} 次尝试" if tries else ""
    rej = leg.get("rejected") or []
    last = rej[-1] if rej else None
    if isinstance(last, str):
        why = last
    elif isinstance(last, (list, tuple)) and len(last) >= 4:
        why = str(last[3] or last[1])
    else:
        why = str(leg.get("solver") or "求解失败")
    # 求解器原文是英文单位串，图上统一成中文口径（数值保留两位，尽量短）
    why = re.sub(r"净空 ([\d.]+)", lambda m: f"折线净空 {float(m.group(1)):.2f} m", why)
    why = (why.replace("< clearance 6.000", "< 6.00")
              .replace("折线净空 折线净空", "折线净空")
              .replace("（折线口径）", "")
              .replace("（SDF 口径）", "（端点 SDF）"))
    return f"{head}；{why}" if head else why


def build(ctx: sc.Ctx, *, dpi: int | None = None) -> list[Path]:
    import matplotlib.pyplot as plt
    import numpy as np

    inv = iv.build(ctx)
    scene = ctx.scenario
    cycles = inv.cycles

    xs = [t.x for t in inv.tasks]
    ys = [t.y for t in inv.tasks]
    for tour in inv.tours:
        if tour.track:
            xs += list(tour.track["x"])
            ys += list(tour.track["y"])
    for site in ctx.meta["context"]["all_nest_sites"]:
        xs.append(site["x"])
        ys.append(site["y"])
    x0, x1 = min(xs) - 320.0, max(xs) + 320.0
    y0, y1 = min(ys) - 320.0, max(ys) + 320.0

    # 单周期只有一栏：5.3 in 连副标题和图例都摆不下，加到 7.0 in（唯一一栏，宽一点不浪费）。
    panel_w = 5.3 if len(cycles) >= 2 else 7.0
    ratio = (y1 - y0) / (x1 - x0)
    map_h = (panel_w - 1.0) * ratio
    # 上下留白按"内容有多少英寸"算，不按画布比例：周期数变了以后，
    # 每航次一行明细与 9 项图例的行数都会变，写死的 0.775 / 0.248 会互相压字。
    n_lines_max = max((len(inv.tours_of(c)) for c in cycles), default=1)
    top_in = 0.80 + (0.17 * n_lines_max + 0.34)      # 指纹标题 + 周期标题 + 每航次明细
    n_leg_col = 4 if len(cycles) >= 2 else 2
    n_leg_items = 8 + len({t.uav for t in inv.tours if t.ok})   # 底图/方向/失败航次… + 每架机一条
    leg_rows = int(np.ceil(n_leg_items / n_leg_col))
    xl_in = 0.45                                      # 刻度文字 + x 轴标题
    leg_in = 0.26 * leg_rows + 0.10
    foot_in = 0.26                                    # 脚注 1 行（长解释在 CAPTIONS.md）
    bottom_in = xl_in + leg_in + foot_in + 0.10
    fig_h = top_in + map_h + bottom_in
    fig, axes = plt.subplots(1, len(cycles), figsize=(panel_w * len(cycles), fig_h),
                             sharex=True, sharey=True)
    axes = np.atleast_1d(axes)
    panel_head: list[tuple[str, list[str]]] = []

    for ax, cyc in zip(axes, cycles):
        plan = next(p for p in ctx.trace["cycles"] if int(p["cycle"]) == cyc)
        # 底图与 TA 侧 exp01 同款：建筑按屋顶高度分层设色（低→浅灰、高→深板岩），
        # 不再叠灰度地形——城区起伏不足 10 m，灰度只是噪声，会把航迹衬糊。
        sc.draw_city_backdrop(ax, scene, extent=(x0, x1, y0, y1))

        # ── 已交付航迹 ──────────────────────────────────────────
        for tour in inv.tours_of(cyc):
            color = th.UAV_COLOR.get(tour.uav, "#444444")
            if not tour.ok or not tour.track:
                continue
            tr = tour.track
            ax.plot(tr["x"], tr["y"], color=color, lw=1.75, zorder=7, alpha=0.95,
                    solid_capstyle="round")
            pts = [(inv.task(t).x, inv.task(t).y) for t in tour.stops]
            idx = iv.stop_indices(tr, pts)
            _chevrons(ax, tr, idx, color)
            # 任务站：小圆标位置、任务号挂在圈外；同址复访合成 T15/T25。
            # 标签分两泳道（上/下），横向太挤才换道——不在圆里塞三字符，避免溢出外圈
            clusters: list[dict] = []
            for tid, i in zip(tour.stops, idx):
                t = inv.task(tid)
                hit = next((c for c in clusters
                            if abs(t.x - c["x"]) < 90.0 and abs(t.y - c["y"]) < 90.0), None)
                if hit is None:
                    clusters.append({"x": t.x, "y": t.y, "tags": [inv.tag(tid)]})
                else:
                    hit["tags"].append(inv.tag(tid))
            nests = [(s["x"], s["y"]) for s in ctx.meta["context"]["all_nest_sites"]
                     if s["active"]]
            lanes: list[float | None] = [None, None, None]
            for c in clusters:
                ax.scatter([c["x"]], [c["y"]], s=80, c=color, edgecolors="white",
                           linewidths=0.9, zorder=8.5)
                lane = next((k for k in (0, 1, 2)
                             if lanes[k] is None or abs(c["x"] - lanes[k]) >= 150.0), 0)
                lanes[lane] = c["x"]
                # 统一挂在圆右侧（横排不挤），同列邻站上下错开；
                # 与机巢星号贴在一起的站点改挂圆下方，躲开星形与引线
                if any(abs(c["x"] - nx) < 90.0 and abs(c["y"] - ny) < 120.0 for nx, ny in nests):
                    xy, ha_, va_ = (0, -22), "center", "top"
                else:
                    xy, ha_, va_ = (9, (0, -10, 10)[lane]), "left", "center"
                ax.annotate("/".join(c["tags"]), (c["x"], c["y"]),
                            textcoords="offset points", xytext=xy, ha=ha_, va=va_,
                            fontsize=5.3, color=color, fontweight="bold", zorder=15,
                            path_effects=[pe.withStroke(linewidth=2.0, foreground="white")])
            ax.scatter([tr["x"][0]], [tr["y"][0]], marker="o", s=40, c=color,
                       edgecolors="white", linewidths=0.8, zorder=8)

        # ── 失败航次：已解段真实几何（实线）+ 断裂段（虚线） ──────
        # 失败航次不交付整条航迹，但**断点前已解好的段是真实几何**：prefix_nodes
        # 是逐段首末节点的折线，画实线即可看出"断点前确实飞了几段"；
        # 断掉的那一段只有空间层的初值（RRT 折线 failed_leg_path），画虚线，
        # 含义是"几何走得通、时间与净空没解出来"，不冒充已交付航迹。
        for tour in inv.tours_of(cyc):
            if tour.ok:
                continue
            color = th.UAV_COLOR.get(tour.uav, "#444444")
            prefix = tour.diag.get("prefix_nodes") or []
            failpath = tour.diag.get("failed_leg_path") or []
            n_pref = int(tour.diag.get("completed_prefix_len") or 0)

            if len(prefix) >= 2:
                P = np.asarray(prefix, dtype=float)
                ax.plot(P[:, 0], P[:, 1], color=color, lw=1.7, zorder=7,
                        alpha=0.95, solid_capstyle="round")
                for tid in tour.stops[:n_pref]:
                    t = inv.task(tid)
                    ax.scatter([t.x], [t.y], s=90, c=color, edgecolors="white",
                               linewidths=0.9, zorder=8.4)
                    ax.annotate(inv.tag(tid), (t.x, t.y), textcoords="offset points",
                                xytext=(9, -1), ha="left", va="center", fontsize=5.3,
                                color=color, fontweight="bold", zorder=15,
                                path_effects=[pe.withStroke(linewidth=2.0,
                                                            foreground="white")])

            broke = _failing_leg(tour)
            note_at: tuple[float, float] | None = None
            note = f"整航次失败：{tour.failure or '未知'}"
            if broke is not None:
                i, leg = broke
                tgt = tour.stops[i] if i < len(tour.stops) else None
                # 断裂段**不可行也给出轨迹**：取多次尝试里净空最大的那一手的折线，
                # 红虚线画出、最紧点打 X、注记写明"净空多少 < 6、不可行"。
                # 空间层初值（RRT 折线）另用细虚线对照，说明几何上本来打算怎么绕。
                best = leg.get("best_effort") or {}
                samples = best.get("samples") or []
                if samples:
                    S = np.asarray(samples, dtype=float)
                    ax.plot(S[:, 0], S[:, 1], color=th.FAIL, lw=2.0,
                            ls=(0, (4, 2)), zorder=7.6)
                a = (float(prefix[-1][0]), float(prefix[-1][1])) if prefix else None
                b = (inv.task(int(tgt)).x, inv.task(int(tgt)).y) if tgt is not None else None
                if not samples and a and b:
                    ax.plot([a[0], b[0]], [a[1], b[1]], color=th.FAIL, lw=2.0,
                            ls=(0, (4, 2)), zorder=7.6)
                if len(failpath) >= 2:
                    F = np.asarray(failpath, dtype=float)
                    ax.plot(F[:, 0], F[:, 1], color=th.FAIL, lw=0.9,
                            ls=(0, (2, 2)), alpha=0.6, zorder=7.0)
                if b is not None:
                    ax.scatter([b[0]], [b[1]], s=150, marker="o", facecolors="none",
                               edgecolors=th.FAIL, linewidths=1.6, zorder=8.6)
                wp = best.get("worst_point_m")
                if wp:
                    note_at = (float(wp[0]), float(wp[1]))
                elif a and b:
                    note_at = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                if note_at is not None:
                    ax.scatter([note_at[0]], [note_at[1]], marker="X", s=150,
                               c=th.FAIL, edgecolors="white", linewidths=0.8,
                               zorder=9)
                if tgt is not None:
                    w = best.get("worst_clearance_m")
                    if w is None:
                        tail = _clearance_note(leg)
                    else:
                        tail = f"最紧点折线净空 {float(w):.2f} m < 6.00（不可行）"
                    note = f"→ {inv.tag(tgt)} 断裂：{tail}"

            if note_at is None:
                continue
            lead = (f"机{tour.uav} · 已解 {n_pref}/{tour.n_stops} 段"
                    if n_pref else f"机{tour.uav} · 第 1 段即断")
            note = note.replace("这一段断裂：", "断裂：")
            # 注记钉在轴内左上角（几何最高站 y≈1100，轴顶 1800，留白够）：不压线、靠断点近
            ax.annotate(
                f"{lead}\n{note}",
                xy=(0.03, 0.975), xycoords="axes fraction",
                fontsize=7.6, color=th.FAIL, zorder=10, va="top", ha="left",
                bbox=dict(boxstyle="round,pad=0.24", fc="white", ec=th.FAIL,
                          lw=0.7, alpha=0.94),
            )

        # ── 本周期未派 / 未到达的任务点 ─────────────────────────
        touched = {tid for tour in inv.tours_of(cyc) for tid in tour.stops}
        for t in inv.tasks:
            if t.cycle != cyc or t.id in touched:
                continue
            ax.scatter([t.x], [t.y], s=62, marker="x", c="#9A9A9A", linewidths=1.2,
                       zorder=5.5)

        for site in ctx.meta["context"]["all_nest_sites"]:
            if site["active"]:
                ax.scatter(site["x"], site["y"], marker="*", s=330, c="#111111",
                           edgecolors="white", linewidths=0.8, zorder=9)

        # 面板副标题：每航次的站数/里程/时长 + 确定的站序串。
        # 放在坐标区之外（标题下方），避免压住航迹；跨周期同址任务在 C3 会让
        # 空间站序挤成一团，这行串是那处的确定答案。
        lines = []
        for tour in inv.tours_of(cyc):
            if tour.ok:
                head = (f"机{tour.uav} · {tour.n_stops} 站 · "
                        f"{tour.path_len_m / 1000:.2f} km · {tour.duration_s:.0f} s")
            else:
                head = (f"机{tour.uav} · {tour.n_stops} 站 · 未交付"
                        f"（已解 {int(tour.diag.get('completed_prefix_len') or 0)} 段）")
            lines.append(head + " ｜ " + "→".join(inv.tag(x) for x in tour.stops))
        panel_head.append((f"周期 {cyc}", lines))

        panel_head[-1] = (f"周期 {cyc}（{plan['t_start']:.0f}–{plan['t_end']:.0f} s）", lines)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_xlabel("东向 x（m）")
        ax.grid(alpha=0.15)
        for side in ("top", "right"):
            ax.spines[side].set_visible(True)
            ax.spines[side].set_color("#C4C4C4")
            ax.spines[side].set_linewidth(0.7)

    axes[0].set_ylabel("北向 y（m）")

    handles = [plt.Line2D([], [], color=th.UAV_COLOR.get(u, "#444444"), lw=2.2,
                          label=f"机 {u} 航迹")
               for u in sorted({t.uav for t in inv.tours if t.ok})]
    handles += [
        # 底图代理：一个中性灰块，说明"色深 ∝ 建筑高度"，不另占色条位
        plt.Rectangle((0, 0), 1, 1, fc="#959faa", ec="#6f7a86", lw=0.7,
                      label="建筑底图（色深 ∝ 高度）"),
        plt.Line2D([], [], marker="o", ls="none", markerfacecolor="#666666",
                   markeredgecolor="white", markersize=8, label="任务站（标号 = T#）"),
        plt.Line2D([], [], marker="$>$", ls="none", color="#555555", markersize=8,
                   label="飞行方向"),
        plt.Line2D([], [], color="#7A7A7A", lw=1.7,
                   label="已解段（实线，仅报告）"),
        plt.Line2D([], [], color=th.FAIL, lw=2.0, ls=(0, (4, 2)),
                   label="断裂段（虚线，不可行）"),
        plt.Line2D([], [], marker="X", ls="none", color=th.FAIL, markersize=8,
                   label="最紧点（净空不足）"),
        plt.Line2D([], [], marker="x", ls="none", color="#9A9A9A", markersize=7,
                   label="本周期未派任务"),
        plt.Line2D([], [], marker="*", ls="none", color="#111111", markersize=12,
                   label="启用机巢"),
    ]
    # 周期数不预设：单周期时画布只有 5.3 in 宽，9 项图例若排 4 列会一路伸到画布外，
    # 只能改成窄列、多行堆叠（底部留白由 bottom 参数一并让出）。
    n_panel = len(cycles)
    # 图例从"坐标区下沿之下"往下挂，顶部锚点 = 坐标区下沿 - 刻度留白，
    # 这样周期数变化时都不会压到刻度文字与脚注。
    fig.legend(handles=handles, loc="upper center", ncol=n_leg_col,
               fontsize=9.4,
               bbox_to_anchor=(0.5, (bottom_in - xl_in - 0.02) / fig_h), frameon=False,
               columnspacing=1.6, handletextpad=0.5)

    th.fingerprint("逐周期真实航迹（PP）：飞了哪几条、按什么次序、哪一段断了",
                  "下层交付的真实航迹；红色虚线 = 未交付的断裂段", y=0.985)
    # 图内只留一行口径：长解释（前缀段为何不交付、虚线为何不可行）在 CAPTIONS.md，
    # 占位从 3 行缩到 1 行，省下的高度全部还给坐标区。
    th.footnote(fig, "航迹 = 1 s 采样 ENU；圈外 = T#；＞ = 方向；实线段只报告不交付", y=0.012)
    fig.subplots_adjust(left=0.045, right=0.99, top=1.0 - top_in / fig_h,
                        bottom=bottom_in / fig_h, wspace=0.08)
    # 每航次明细画在**坐标区上沿之外**（标题之下）：位置按最终版面算，
    # 避免像先前那样按 axes 比例摆在 1.01 处、直接压在标题上。
    # 面板头自下而上堆叠：明细行 → 周期标题。全部由 fig.text 画，高度关系确定，
    # 不再依赖 set_title 的 pad（实测 pad 与 fig.text 混用会互相压字）。
    step = 0.17 / fig_h          # 每行明细 0.17 in，换成年内比例
    gap = 0.045 / fig_h
    for ax, (title, lines) in zip(axes, panel_head):
        pos = ax.get_position()
        cx = pos.x0 + pos.width / 2
        for k, line in enumerate(reversed(lines)):
            fig.text(cx, pos.y1 + gap + step * k, line, ha="center", va="bottom",
                     fontsize=7.6, color=th.FAIL if "未交付" in line else "#3c4148")
        fig.text(cx, pos.y1 + gap + step * len(lines) + 0.03 / fig_h, title, ha="center",
                 va="bottom", fontsize=12, fontweight="bold", color="#1f2933")
    declutter(fig)
    return th.save(fig, ctx.fig_dir / NAME, dpi=dpi)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(th.run_script(NAME, sys.argv[1:]))
