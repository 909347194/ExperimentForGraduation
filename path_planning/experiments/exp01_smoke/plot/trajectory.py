# -*- coding: utf-8 -*-
"""fig_trajectory —— trajectory = path + 时间参数化。

按到达时刻给**整条连续轨迹**着色（LineCollection 分段着色 + colorbar），
直观表达 trajectory = path + 时间维，而不是把 path 画成一堆散点。

数据用 ``ctx.planned`` 里的**全分辨率** ``res.path.pts`` 与 ``res.trajectory.times``：
metrics 里的 ``path_xy`` 为控制文件体积做过降采样（每条只剩 9–18 点），
拿它画图会退化成"一团噪点"，看不出轨迹形状——故本图不走 metrics。

注意：当前时间是匀速占位（uniform_placeholder），非 Lin2021 §2.4 时间最优轨迹。
仅画可行且平滑成功（有 path/trajectory）的 OD。
"""

from __future__ import annotations

import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize

from .registry import PlotContext
from .scene import draw_buildings, draw_dynamic_nofly
from .theme import PALETTE, plt
from . import theme

#: 时间着色色标：plasma 首尾都不会落到接近白/灰的区间，
#: 压在浅灰建筑背景上仍有足够对比（viridis 末端偏黄、首端偏深紫，浅底上都不清晰）。
_CMAP = "plasma"


def _collect(ctx: PlotContext):
    """(k, res, pts(N,3), times(N,)) 列表，只取可行且有完整 path/trajectory 的。

    k 是 ``ctx.planned`` 里的下标，即 metrics 的 ``od_id``（同源同序）。
    """
    items = []
    for k, (_o, _d, res) in enumerate(ctx.planned):
        if not (res.feasible and res.path is not None and len(res.path.pts) >= 2):
            continue
        pts = np.asarray(res.path.pts, dtype=float)
        t = (np.asarray(res.trajectory.times, dtype=float)
             if res.trajectory is not None and len(res.trajectory.times) > 0
             else np.arange(len(pts), dtype=float))
        if len(t) != len(pts):  # 兜底：长度不齐时用等间隔占位
            t = np.linspace(0.0, float(len(pts) - 1), len(pts))
        # 着色的时间轴用「相对起飞时刻」：本组 OD 里有 time_control 这类故意晚起飞
        # （t_dep≈930 s）的用例，若用绝对时刻，色标会被拉到 0–950 s，
        # 其余 t_dep=0 的 OD 全部挤在最深色一端，颜色失去区分力。
        t_dep = float(t[0])
        items.append((k, res, pts, t - t_dep, t_dep))
    return items


def plot_trajectory(ctx: PlotContext, out_dir: str) -> list[str]:
    env = ctx.env
    items = _collect(ctx)

    fig, ax = plt.subplots(figsize=theme.FIG_SQUARE)
    draw_buildings(ax, env)
    draw_dynamic_nofly(ax, env, first_only=False)

    if items:
        tmin = min(float(t.min()) for _, _, _, t, _ in items)
        tmax = max(float(t.max()) for _, _, _, t, _ in items)
        if tmax - tmin < 1e-9:
            tmax = tmin + 1.0
        norm = Normalize(vmin=tmin, vmax=tmax)

        for k, res, pts, t, t_dep in items:
            xy = pts[:, :2]
            segs = np.stack([xy[:-1], xy[1:]], axis=1)
            # 段颜色取两端时刻均值，得到沿轨迹连续的到达时刻渐变
            seg_t = 0.5 * (t[:-1] + t[1:])
            lc = LineCollection(segs, cmap=_CMAP, norm=norm,
                                linewidths=2.4, zorder=3, capstyle="round")
            lc.set_array(seg_t)
            ax.add_collection(lc)
            # 起终点 + OD 编号（多 OD 同图时靠编号区分，颜色已让给时间维）
            ax.plot([xy[0, 0]], [xy[0, 1]], "o", color=PALETTE["origin"],
                    ms=7, zorder=5)
            ax.plot([xy[-1, 0]], [xy[-1, 1]], "s", color=PALETTE["dest"],
                    ms=7, zorder=5)
            mid = len(xy) // 2
            tag = f"OD{k}" if t_dep < 1.0 else f"OD{k}(t_dep={t_dep:.0f}s)"
            ax.annotate(tag, (xy[mid, 0], xy[mid, 1]),
                        fontsize=theme.FS["footnote"], color="#333333",
                        zorder=6, ha="center", va="center")

        cbar = fig.colorbar(ScalarMappable(norm=norm, cmap=_CMAP), ax=ax)
        cbar.set_label("相对起飞时刻 t − t_dep (s)  [匀速占位，非时间最优]")

    ax.set_aspect("equal")
    ax.set_title("trajectory = path + 时间参数化（颜色=相对起飞时刻，当前为匀速占位）")
    path = theme.save_figure(fig, out_dir, "trajectory")
    return [path]
