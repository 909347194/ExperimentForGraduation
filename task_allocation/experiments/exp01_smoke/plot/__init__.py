# -*- coding: utf-8 -*-
"""exp01_smoke 可视化模块：读 ``results/`` 数值产物，画图。

职责边界（见 ``experiments/README.md``）：

    * 本包**只负责绘制**——不跑算法、不改结果、不决定图存哪；
    * 图的落盘目录由 ``run.py`` 传入（约定在 ``results/figures/``），
      配置项 ``experiment.figures_dir``；
    * 读的是已经落盘的 ``metrics.json`` / ``solution.json``，因此图与数值产物
      同源、可独立复现（改图不必重跑实验）；
    * 图文件名固定，便于论文引用与跨实验扫描。

目录按「**看图目的**」分四层（论文叙事线），而不是按实现技术::

    plot/
    ├── registry.py     图清单：唯一事实来源（11 张图，新增图 = 加一行）
    ├── theme.py        学术绘图样式与保存约定（唯一 import pyplot 的地方）
    ├── base/           被图依赖、但自己不是图的共享绘制件（地理要素）
    ├── setup/          ① 实验设置：问题规模 / 算法超参 / 约束开关（参数即图）
    ├── initial/        ② 初始状态：地形 / 机巢 / 初始机队 / 首批任务（与算法无关）
    ├── process/        ③ 运行过程观测：滚动闭环怎么跑（逐周期演化与诊断）
    └── report/         ④ 结果分析：可以直接引用的成图（稳健性 / 对照）

命名约定（**四者同名**，见 ``registry.py`` 顶部说明）::

    模块名 ≡ 图 id ≡ 输出文件名 ≡ 绘制函数名去掉 ``plot_``

常用方式::

    from plot import render_from_results
    render_from_results("results")                      # 全部图
    render_from_results("results", groups=["report"])   # 只出结论层（进论文）
    render_from_results("results", names=["geo_overview", "allocation"])
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import registry as _registry  # noqa: F401  （对外再导出）
from . import theme as _theme  # 必须最先导入：切到 Agg 后端再引 pyplot
from .registry import FIGURES, GROUPS, Figure, resolve

__all__ = [
    "FIGURES",
    "FIGURE_NAMES",
    "GROUPS",
    "Figure",
    "make_plots",
    "load_results",
    "render_from_results",
    "resolve",
    "setup_style",
]

#: 落盘图名（不含扩展名）；兼容旧接口，事实来源是 ``registry.FIGURES``
FIGURE_NAMES = tuple(f.id for f in FIGURES)

setup_style = _theme.setup_style


def load_results(results_dir: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    """读取 ``results/metrics.json`` 与 ``results/solution.json``。

    ``solution.json`` 缺失时返回空 dict——只读 metrics 的图（如
    ``task_selection`` / ``dmde_search``）仍然可画。
    """
    results_dir = Path(results_dir)
    metrics = json.loads((results_dir / "metrics.json").read_text(encoding="utf-8"))
    solution_path = results_dir / "solution.json"
    if solution_path.exists():
        solution = json.loads(solution_path.read_text(encoding="utf-8"))
    else:
        solution = {}
    return metrics, solution


def discover_variants(results_dir: Path | str) -> dict[str, dict[str, Any]]:
    """发现同目录下的**归档对照** ``metrics_<tag>.json``，用于跨配置对比图。

    例如 ``results/metrics_tw_off.json`` / ``metrics_tw_on.json`` 是同一实验
    两组配置的对照运行，``report/ab_compare`` 靠这张对照表绘图。返回
    ``{tag: metrics}``；没有任何对照时返回空 dict（对应图会自动跳过）。
    """
    results_dir = Path(results_dir)
    variants: dict[str, dict[str, Any]] = {}
    for path in sorted(results_dir.glob("metrics_*.json")):
        tag = re.sub(r"^metrics_", "", path.stem)
        if not tag:
            continue
        try:
            variants[tag] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return variants


def make_plots(
    metrics: dict[str, Any],
    solution: dict[str, Any],
    out_dir: Path | str,
    formats: Sequence[str] = ("png",),
    dpi: int = 200,
    *,
    names: Iterable[str] | None = None,
    groups: Iterable[str] | None = None,
    extras: dict[str, Any] | None = None,
) -> list[Path]:
    """按 ``FIGURES`` 顺序生成图到 ``out_dir``，返回落盘路径列表。

    ``names`` / ``groups`` 用于只出一部分图（例如只出 ``report`` 层进论文）；
    两者都省略则出全部。绘制函数返回值可能为空列表——某张图的数据不满足
    前置条件时（如单 seed 无 ``seed_summary``、无 DEM 时跳过地图）由各图自行
    跳过，这里不报错。

    ``extras`` 是给**跨配置对照图**的附加数据（如 ``{"variants": {...}}``，
    见 :func:`discover_variants`）。只有声明了 ``extras`` 形参的绘制函数才会
    收到它，其余图签名不变——保证「统一签名」的约定不被单个图破坏。
    """
    figures = resolve(names=names, groups=groups)
    if not figures:
        return []

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    font = setup_style()

    written: list[Path] = []
    skipped: list[str] = []
    for fig in figures:
        kwargs: dict[str, Any] = {}
        if extras is not None and "extras" in inspect.signature(fig.func).parameters:
            kwargs["extras"] = extras
        produced = fig.func(metrics, solution, out_dir, formats, dpi=dpi, **kwargs)
        if produced:
            written += produced
        else:
            skipped.append(fig.id)

    groups_desc = ", ".join(dict.fromkeys(f.group for f in figures))
    print(
        f"[exp01] 图：{len(written)} 个文件（{len(figures)} 张，层={groups_desc}）"
        f" → {out_dir}（字体 {font}）"
    )
    for p in written:
        print(f"[exp01]   wrote {p}")
    if skipped:
        print(f"[exp01]   跳过（数据不满足前置条件）：{', '.join(skipped)}")
    return written


def render_from_results(
    results_dir: Path | str,
    out_dir: Path | str | None = None,
    *,
    formats: Sequence[str] = ("png",),
    dpi: int = 200,
    names: Iterable[str] | None = None,
    groups: Iterable[str] | None = None,
    with_variants: bool = True,
) -> list[Path]:
    """从已落盘的 ``results/`` 直接重画图（不重跑实验）。

    ``out_dir`` 省略时写到 ``results_dir/figures``（与 ``run.py`` 的约定一致）。
    默认同时发现归档对照 ``metrics_<tag>.json`` 并传给对照图
    （``with_variants=False`` 可关掉，对照图会自行跳过）。
    """
    results_dir = Path(results_dir)
    metrics, solution = load_results(results_dir)
    variants = discover_variants(results_dir) if with_variants else {}
    target = Path(out_dir) if out_dir is not None else results_dir / "figures"
    return make_plots(
        metrics, solution, target, formats, dpi,
        names=names, groups=groups,
        extras={"variants": variants} if variants else None,
    )
