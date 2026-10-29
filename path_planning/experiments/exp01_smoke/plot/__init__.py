# -*- coding: utf-8 -*-
"""exp01_smoke 绘图包：registry 图清单 + theme 样式 + 分图模块。

约定（沿用 task_allocation/experiments/exp01_smoke/plot/）：

    模块名 ≡ 图 id ≡ 绘制函数名去掉 ``plot_``；输出文件名 = ``fig_<id>.png``
    例：plot/route_vs_path.py → plot_route_vs_path → fig_route_vs_path.png

新增一张图 = 新建模块 + 在 ``registry.FIGURES`` 加一行，别处不动。
"""

from __future__ import annotations

from .registry import FIGURES, Figure, PlotContext, figure_ids, resolve
from .theme import setup_style

__all__ = ["FIGURES", "Figure", "PlotContext", "figure_ids", "resolve",
           "setup_style", "make_plots"]


def make_plots(ctx: PlotContext, out_dir: str, only: tuple[str, ...] = ()) -> list[str]:
    """按 registry 逐图绘制并落盘，返回产物路径列表。"""
    setup_style()
    saved: list[str] = []
    for fig in FIGURES:
        if only and fig.id not in only:
            continue
        saved.extend(fig.func(ctx, out_dir))
    return saved
