# -*- coding: utf-8 -*-
"""exp01_smoke 绘图样式与保存约定。

本模块是**唯一** import ``pyplot`` 的地方：先切到无后端 ``Agg``，
再导入 pyplot，保证不会在有显示环境时误开窗口、也不会因后端切换告警。

其余绘图模块统一 ``from .theme import plt, ...``，不直接 import pyplot。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")

import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

__all__ = [
    "plt",
    "PALETTE",
    "setup_style",
    "save_figure",
    "cycle_colors",
]

#: 优先使用的中文字体（按序回退；论文机器常见字体都列上）
_CJK_CANDIDATES = (
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "Microsoft YaHei",
    "PingFang SC",
    "SimHei",
)

#: 统一配色：数据系列与语义色
PALETTE = {
    "remain": "#9aa5b1",
    "new": "#4c78a8",
    "release": "#e45756",
    "selected": "#72b7b2",
    "executed": "#54a24b",
    "deferred": "#f58518",
    "reward": "#4c78a8",
    "cost": "#e45756",
    "solve": "#72b7b2",
    "evals": "#b279a2",
    "nest": "#4c78a8",
    "grid": "#d0d5da",
}

#: 多周期序列配色（tab10 前几色，足够 8 个周期）
_CYCLE_COLORS = (
    "#4c78a8",
    "#f58518",
    "#54a24b",
    "#e45756",
    "#72b7b2",
    "#b279a2",
    "#ff9da6",
    "#9d755d",
)


def setup_style() -> str:
    """应用全局样式，返回实际选中的字体名（便于日志确认中文可用）。"""
    available = {f.name for f in fm.fontManager.ttflist}
    chain = [n for n in _CJK_CANDIDATES if n in available]
    resolved = chain[0] if chain else "DejaVu Sans"

    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [*chain, "DejaVu Sans"],
            "axes.unicode_minus": False,
            "figure.dpi": 110,
            "savefig.dpi": 200,
            "savefig.bbox": "tight",
            "savefig.facecolor": "white",
            "axes.grid": True,
            "grid.alpha": 0.25,
            "grid.linestyle": ":",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "legend.frameon": False,
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.fontsize": 8.5,
        }
    )
    return resolved


def cycle_colors(n: int) -> list[str]:
    """按周期数给出稳定配色（超过预置色数则循环）。"""
    return [_CYCLE_COLORS[i % len(_CYCLE_COLORS)] for i in range(max(int(n), 1))]


def save_figure(
    fig,
    stem: Path | str,
    formats: Iterable[str] = ("png", "pdf"),
    *,
    dpi: int = 200,
) -> list[Path]:
    """按 ``stem``（不含扩展名）写出所有格式，返回落盘路径并关闭画布。

    同时给 png（论文插图预览 / 网页）与 pdf（矢量，直接进 LaTeX），
    两者共享同一份绘图代码，避免图像与版式各画一遍。
    """
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fmt in formats:
        ext = str(fmt).lstrip(".").lower()
        if not ext:
            continue
        out = stem.with_suffix(f".{ext}")
        fig.savefig(out, dpi=dpi, bbox_inches="tight", facecolor="white")
        written.append(out)
    plt.close(fig)
    return written


def style_axes(ax, *, title: str | None = None, xlabel: str | None = None,
               ylabel: str | None = None) -> None:
    """统一的坐标轴标注小工具（标题 / 轴标签一次设齐）。"""
    if title:
        ax.set_title(title)
    if xlabel:
        ax.set_xlabel(xlabel)
    if ylabel:
        ax.set_ylabel(ylabel)


__all__.append("style_axes")
