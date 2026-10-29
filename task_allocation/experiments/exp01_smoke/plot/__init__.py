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
    ├── registry.py     图清单：唯一事实来源（3 张图，新增图 = 加一行）
    ├── theme.py        学术绘图样式与保存约定（唯一 import pyplot 的地方）
    ├── base/           被图依赖、但自己不是图的共享绘制件
    │                     scene（场景数据定位）· dem / buildings（两套背景）
    │                     · backdrop（背景调度：有建筑优先建筑，只有地形画 DEM）
    │                     · geo（图标、图例、图层排版）
    ├── initial/        ① 初始状态：地形 / 机巢 / 初始机队 / 首批任务（与算法无关）
    ├── process/        ② 运行过程观测：滚动闭环怎么跑（逐周期演化）
    └── report/         ③ 结果分析：可以直接引用的成图

命名约定（**四者同名 + 分类编号前缀**，见 ``registry.py`` 顶部说明）::

    模块名 ≡ 图 id ≡ 绘制函数名去掉 ``plot_``；输出文件名 = 分类编号前缀 + 图 id
    （S/I/P/R + 两位序号，按叙事线排序，figures 目录天然有序不乱）

常用方式::

    from plot import render_from_results
    render_from_results("results")                      # 全部图（3 张）
    render_from_results("results", groups=["report"])   # 只出结论层（进论文）
    render_from_results("results", names=["geo_overview", "allocation_anim"])

    # 单图独立调试（不重跑实验，直接读 results/*.json）：
    python plot/process/allocation_anim.py              # 直接脚本方式（自举转 -m）
    python -m plot.process.allocation_anim              # 等价的模块方式
    python plot/process/allocation_anim.py --pdf        # 动画图默认带 gif；--pdf 加矢量
    python plot/process/allocation_anim.py --out /tmp/fig --dpi 80   # 自定义输出目录 / dpi
    # 以上等价于各模块末尾 `run_script(__file__.stem, sys.argv[1:])`
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

# 包内导入一律用单点相对（2026-10-23 修正）：此前被批量替换脚本误改成
# `from ...plot import ...`（三点），按本包实际层级恒解析不到目标模块，
# 且以顶层 `plot` 身份导入时直接抛 "attempted relative import beyond
# top-level package" —— 任何一次出图都会 ImportError（run.py 的出图环节）。
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
    """读取 ``results/metrics.json`` 与 ``results/solution.json``。

    ``solution.json`` 缺失时返回空 dict——只读 metrics 的图仍然可画
    （当前 3 张图均声明需要 solution）。
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
    两组配置的对照运行，跨配置对照图（已裁剪，历史见 git）靠这张对照表绘图。返回
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
    前置条件时（如单 seed 无跨 seed 统计、无 DEM 时跳过地图）由各图自行
    跳过，这里不报错。

    ``extras`` 是给**跨配置对照图**的附加数据（如 ``{"variants": {...}}``，
    见 :func:`discover_variants`）。只有声明了 ``extras`` 形参的绘制函数才会
    收到它，其余图签名不变——保证「统一签名」的约定不被单个图破坏。
    """
    figures = resolve(names=names, groups=groups)
    if not figures:
        # 空图集曾静默返回：分组名写错 / 图 id 拼错都表现为「什么都没出」而
        # 无任何提示（2026-10-24：--group scene 是无效分层名，resolve 抛
        # ValueError 才暴露；但拼错单个图 id 时仍会静默 0 张）。
        print(f"[plot] 警告：图集为空（names={list(names) if names else None} "
              f"groups={list(groups) if groups else None}）——请核对图 id / 分层名")
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
        # 动图格式（gif/mp4/poster）只发给动画图：run.py 为刷新 allocation_anim 会把
        # gif 补进 formats，若不过滤，每张静态图都会顺带落一个单帧 GIF。
        # poster = 动画图专用的「只出末帧海报」令牌，静态图无此概念，同样过滤。
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
        f"[exp01] 图：{len(written)} 个文件（{len(figures)} 张，层={groups_desc}）"
        f" → {out_dir}（字体 {font}）"
    )
    for p in written:
        print(f"[exp01]   wrote {p}")
    if skipped:
        print(f"[exp01]   跳过（数据不满足前置条件）：{', '.join(skipped)}")
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
    """单图脚本**独立绘制**入口：直接 ``python plot/process/allocation.py``。

    只从已落盘的 ``results/`` 读数据（**不重跑实验**），输出到
    ``results/figures/``，便于单独调试某张图的绘制逻辑。可选 ``argv``：

    * ``--pdf``          额外输出 PDF 矢量图
    * ``--dpi N``        指定 dpi（缺省读 ``config.yaml`` 的 ``plot.dpi``，再缺省 200）
    * ``--results DIR``  指定 results 目录（缺省 ``<实验根>/results``）
    * ``--out DIR``      指定输出目录（缺省 ``<results>/figures``）
    * ``--no-gif``       动画图也不出 GIF

    每个绘图模块只需在末尾加一段自举（本包已统一约定，直接复制即可）：:

        if __name__ == "__main__":
            import sys
            from pathlib import Path as _P
            _r = _P(__file__).resolve().parents[2]   # 实验根 = plot 的上两级
            if str(_r) not in sys.path:
                sys.path.insert(0, str(_r))
            from plot import run_script
            run_script(_P(__file__).stem, sys.argv[1:])

    注：``python -m plot.process.allocation`` 同样可用（``-m`` 下包已可导入，
    ``run_script`` 内部一律用绝对 ``from plot import ...``，两种启动方式通用）。
    """
    import argparse
    from pathlib import Path as _Path

    here = _Path(__file__).resolve().parent  # exp01_smoke/plot
    exp_root = here.parent                    # exp01_smoke
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
    p.add_argument("--no-gif", action="store_true", help="动画图也不出 GIF")
    ns = p.parse_args(list(argv) if argv is not None else [])

    dpi = ns.dpi if ns.dpi is not None else (_read_plot_dpi(exp_root) or 200)

    formats = ["png"]
    if fig.animated and not ns.no_gif:
        formats.append("gif")
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
