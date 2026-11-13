# -*- coding: utf-8 -*-
"""exp03_event_rolling 图清单：唯一事实来源。

新增一张图 = 加一行 FIGURES。命名规则：

    模块名 ≡ 图 id ≡ 绘出函数名去掉 plot_；输出 = 编号前缀 + 图 id
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .event_response_timeline import plot_event_response_timeline
from .event_overview import plot_event_overview
from .completion_comparison import plot_completion_comparison
from .replan_comparison import plot_replan_comparison
from .event_types_bar import plot_event_types_bar
from .metrics_radar import plot_metrics_radar
from .summary_table import plot_summary_table
from .event_timeline import plot_event_timeline
from .response_animation import plot_response_animation

__all__ = ["Figure", "FIGURES", "resolve"]


@dataclass(frozen=True)
class Figure:
    id: str
    fn: Callable
    group: str = "report"
    animated: bool = False


#: 图清单（顺序 = 叙事线）
FIGURES: list[Figure] = [
    Figure("event_response_timeline", plot_event_response_timeline),  # 核心图
    Figure("event_overview", plot_event_overview),
    Figure("completion_comparison", plot_completion_comparison),
    Figure("replan_comparison", plot_replan_comparison),
    Figure("event_types_bar", plot_event_types_bar),
    Figure("metrics_radar", plot_metrics_radar),
    Figure("summary_table", plot_summary_table),
    Figure("event_timeline", plot_event_timeline),
    Figure("response_animation", plot_response_animation, animated=True),
]


def resolve(
    names: list[str] | None = None,
    groups: list[str] | None = None,
) -> list[Figure]:
    out = FIGURES
    if names:
        want = set(names)
        out = [f for f in out if f.id in want]
    if groups:
        want_g = set(groups)
        out = [f for f in out if f.group in want_g]
    return out
