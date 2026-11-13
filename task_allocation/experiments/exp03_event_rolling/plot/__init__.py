# -*- coding: utf-8 -*-
"""exp03_event_rolling 可视化包：一图一脚本，registry 统一调度。

用法::

    from plot import make_plots
    make_plots(all_summaries, figures_dir)

    # 单图调试
    python -m plot.event_overview
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .registry import FIGURES, Figure, resolve

__all__ = ["make_plots", "FIGURES", "Figure", "resolve"]


def make_plots(
    all_summaries: dict[str, Any],
    figures_dir: Path,
    *,
    names: list[str] | None = None,
    groups: list[str] | None = None,
) -> list[str]:
    """按 registry 逐图绘制，返回产物文件名列表。"""
    figures_dir = Path(figures_dir)
    figures_dir.mkdir(parents=True, exist_ok=True)

    produced: list[str] = []
    for fig in resolve(names=names, groups=groups):
        try:
            name = fig.fn(all_summaries, figures_dir)
            produced.append(name)
            print(f"[plot] {name}")
        except Exception as exc:
            print(f"[plot] {fig.id} 失败: {exc}")
    return produced
