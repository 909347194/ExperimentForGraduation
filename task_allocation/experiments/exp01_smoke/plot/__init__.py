# -*- coding: utf-8 -*-
"""exp01_smoke 可视化模块：读 ``results/`` 数值产物，写图到 ``plot/figures/``。

职责边界（见 ``experiments/README.md``）：
    * 本包只做**画图**，不跑算法、不改结果；
    * 读的是已经落盘的 ``metrics.json`` / ``solution.json``，因此图与数值产物
      总是同源、可独立复现（改图不必重跑实验）；
    * 图文件名固定，便于论文引用与跨实验扫描。

四张图：
    ``cycle_metrics``     周期指标面板（水位 / 双目标 / 泊位占用 / 求解开销）
    ``selection_funnel``  动态任务选择漏斗
    ``assignment_map``    机巢—任务—航次分配地图
    ``convergence``       DMDE 收敛曲线
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from . import theme as _theme  # 必须最先导入：切到 Agg 后端再引 pyplot
from .assignment import plot_assignment_map
from .convergence import plot_convergence
from .cycles import plot_cycle_metrics
from .selection import plot_selection_funnel

__all__ = [
    "FIGURE_NAMES",
    "make_plots",
    "load_results",
    "render_from_results",
    "setup_style",
]

#: 落盘图名（不含扩展名），顺序即生成顺序
FIGURE_NAMES = (
    "cycle_metrics",
    "selection_funnel",
    "assignment_map",
    "convergence",
)

setup_style = _theme.setup_style


def load_results(results_dir: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    """读取 ``results/metrics.json`` 与 ``results/solution.json``。"""
    results_dir = Path(results_dir)
    metrics = json.loads((results_dir / "metrics.json").read_text(encoding="utf-8"))
    solution_path = results_dir / "solution.json"
    if solution_path.exists():
        solution = json.loads(solution_path.read_text(encoding="utf-8"))
    else:
        solution = {}
    return metrics, solution


def make_plots(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path | str,
    *,
    formats: Sequence[str] = ("png", "pdf"),
    dpi: int = 200,
) -> list[Path]:
    """按固定顺序生成全部图，返回落盘路径列表。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    font = setup_style()

    written: list[Path] = []
    written += plot_cycle_metrics(metrics, out_dir, formats, dpi=dpi)
    written += plot_selection_funnel(metrics, out_dir, formats, dpi=dpi)
    written += plot_assignment_map(metrics, solution, out_dir, formats, dpi=dpi)
    written += plot_convergence(solution, out_dir, formats, dpi=dpi)

    print(f"[exp01] 图：{len(written)} 个文件 → {out_dir}（字体 {font}）")
    for p in written:
        print(f"[exp01]   wrote {p}")
    return written


def render_from_results(
    results_dir: Path | str,
    out_dir: Path | str,
    *,
    formats: Sequence[str] = ("png", "pdf"),
    dpi: int = 200,
) -> list[Path]:
    """从已落盘的 ``results/`` 直接重画图（不重跑实验）。"""
    metrics, solution = load_results(results_dir)
    return make_plots(metrics, solution, out_dir, formats=formats, dpi=dpi)
