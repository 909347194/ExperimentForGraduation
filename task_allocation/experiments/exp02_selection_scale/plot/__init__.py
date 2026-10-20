# -*- coding: utf-8 -*-
"""exp02_selection_scale 可视化模块：读 ``results/`` 数值产物，画图。

职责边界（与 exp01_smoke 同口径，见 ``experiments/README.md``）：

    * 本包**只负责绘制**——不跑算法、不改结果、不决定图存哪；
    * 图的落盘目录由 ``run.py`` 传入（约定在 ``results/figures/``）；
    * 读的是已经落盘的 ``metrics.json``，因此图与数值产物同源、可独立复现
      （改图不必重跑实验）；本实验无 ``solution.json``（单周期变体扫描），
      ``load_results`` 对其缺位返回空 dict，统一签名不变。

目录按「论文叙事线」分两层（本实验无过程层：没有逐周期数据）::

    plot/
    ├── registry.py     图清单：唯一事实来源（6 张图，新增图 = 加一行）
    ├── theme.py        学术绘图样式与保存约定（唯一 import pyplot 的地方）
    ├── base/           被图依赖、但自己不是图的共享绘制件（版面自检/去重叠）
    ├── setup/          ① 实验设置：场景 / 变体矩阵 / 求解预算（参数即图）
    └── report/         ④ 结果分析：规模压缩与标定结论（可直接进论文）

命名约定（四者同名 + 分类编号前缀，见 ``registry.py`` 顶部说明）::

    模块名 ≡ 图 id ≡ 绘制函数名去掉 ``plot_``；输出文件名 = 分类编号前缀 + 图 id
    （S/R + 两位序号，按叙事线排序，figures 目录天然有序不乱）

常用方式::

    from plot import render_from_results
    render_from_results("results")                      # 全部图
    render_from_results("results", groups=["report"])   # 只出结论层（进论文）
    render_from_results("results", names=["r01_tradeoff"])

    # 单图独立调试（不重跑实验，直接读 results/metrics.json）：
    python plot/report/r01_tradeoff.py                 # 直接脚本方式（自举转 -m）
    python -m plot.report.r01_tradeoff                 # 等价的模块方式
"""

from __future__ import annotations

import inspect
import json
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
    "run_script",
    "resolve",
    "setup_style",
]

#: 落盘图名（不含扩展名）；兼容旧接口，事实来源是 ``registry.FIGURES``
FIGURE_NAMES = tuple(f.id for f in FIGURES)

setup_style = _theme.setup_style


def load_results(results_dir: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    """读取 ``results/metrics.json``；``solution.json`` 缺位时返回空 dict。

    本实验是单周期变体扫描，不产出 ``solution.json``；统一签名保留该参数，
    使绘图函数与 exp01 同构（将来若补场景图件可直接接入）。
    """
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
    前置条件时由各图自行跳过，这里不报错。

    ``extras`` 预留（跨配置对照图的附加数据）；只有声明了 ``extras`` 形参的
    绘制函数才会收到它，保证「统一签名」的约定不被单个图破坏。
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
        # 动图格式只发给动画图（本实验暂无动画图；保留该过滤逻辑以备扩展）
        fig_formats = formats if fig.animated else tuple(
            f for f in formats if str(f).lower().lstrip(".") not in {"gif", "mp4", "poster"}
        )
        produced = fig.func(metrics, solution, out_dir, fig_formats, dpi=dpi, **kwargs)
        if produced:
            written += produced
        else:
            skipped.append(fig.id)

    groups_desc = ", ".join(dict.fromkeys(f.group for f in figures))
    print(
        f"[exp02] 图：{len(written)} 个文件（{len(figures)} 张，层={groups_desc}）"
        f" → {out_dir}（字体 {font}）"
    )
    for p in written:
        print(f"[exp02]   wrote {p}")
    if skipped:
        print(f"[exp02]   跳过（数据不满足前置条件）：{', '.join(skipped)}")
    return written


def _read_plot_dpi(exp_root: Path) -> int | None:
    """从实验根目录的 ``config.yaml`` 读 ``plot.dpi``（读不到退回 ``None``）。"""
    try:
        import yaml

        cfg_path = Path(exp_root) / "config.yaml"
        if cfg_path.exists():
            with cfg_path.open(encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            dpi = int((cfg.get("plot") or {}).get("dpi", 0) or 0)
            return dpi or None
    except Exception:
        return None
    return None


def run_script(figure_id: str, argv: Sequence[str] | None = None) -> list[Path]:
    """单图脚本**独立绘制**入口：直接 ``python plot/report/r01_tradeoff.py``。

    只从已落盘的 ``results/`` 读数据（**不重跑实验**），输出到
    ``results/figures/``，便于单独调试某张图的绘制逻辑。可选 ``argv``：

    * ``--pdf``          额外输出 PDF 矢量图
    * ``--dpi N``        指定 dpi（缺省读 ``config.yaml`` 的 ``plot.dpi``，再缺省 200）
    * ``--results DIR``  指定 results 目录（缺省 ``<实验根>/results``）
    * ``--out DIR``      指定输出目录（缺省 ``<results>/figures``）

    每个绘图模块只需在末尾加一段自举（本包已统一约定，直接复制即可）::

        if __name__ == "__main__":
            import sys
            from pathlib import Path as _P
            _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
            if str(_r) not in sys.path:
                sys.path.insert(0, str(_r))
            from plot import run_script
            run_script(_P(__file__).stem, sys.argv[1:])
    """
    import argparse
    from pathlib import Path as _Path

    here = _Path(__file__).resolve().parent  # exp02_selection_scale/plot
    exp_root = here.parent                    # exp02_selection_scale
    results_dir = exp_root / "results"
    figures_dir = results_dir / "figures"

    table = _registry._by_id()
    if figure_id not in table:
        raise SystemExit(
            f"[plot] 未知图 id={figure_id!r}；可用：{list(table)}"
        )
    fig = table[figure_id]

    p = argparse.ArgumentParser(prog=f"plot:{figure_id}")
    p.add_argument("--pdf", action="store_true", help="额外输出 PDF")
    p.add_argument("--dpi", type=int, default=None, help="指定 dpi（缺省读 config.yaml）")
    p.add_argument("--results", default=str(results_dir), help="results 目录")
    p.add_argument("--out", default=str(figures_dir), help="输出目录")
    ns = p.parse_args(list(argv) if argv is not None else [])

    dpi = ns.dpi if ns.dpi is not None else (_read_plot_dpi(exp_root) or 200)

    formats = ["png"]
    if ns.pdf:
        formats.append("pdf")

    return render_from_results(
        ns.results, ns.out,
        formats=formats, dpi=dpi, names=[figure_id],
    )


def render_from_results(
    results_dir: Path | str,
    out_dir: Path | str | None = None,
    *,
    formats: Sequence[str] = ("png",),
    dpi: int = 200,
    names: Iterable[str] | None = None,
    groups: Iterable[str] | None = None,
) -> list[Path]:
    """从已落盘的 ``results/`` 直接重画图（不重跑实验）。

    ``out_dir`` 省略时写到 ``results_dir/figures``（与 ``run.py`` 的约定一致）。
    """
    results_dir = Path(results_dir)
    metrics, solution = load_results(results_dir)
    target = Path(out_dir) if out_dir is not None else results_dir / "figures"
    return make_plots(
        metrics, solution, target, formats, dpi,
        names=names, groups=groups,
    )
