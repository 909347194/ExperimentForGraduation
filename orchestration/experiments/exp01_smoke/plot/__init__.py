# -*- coding: utf-8 -*-
"""exp01_smoke 出图包：一图一模块，只读落盘结果，不重跑规划器。

按「**这张图给谁记账**」分四层（详见 ``registry.py`` 顶部说明）::

    plot/
    ├── registry.py   图清单：唯一事实来源（8 张，新增图 = 加一行）
    ├── theme.py      样式 / 保存 / 单图 CLI（唯一 import pyplot 的地方）
    ├── base/         共享件（不是图）
    │     scene.py      读结果、装底图、比例尺；Ctx
    │     ledger.py     四张台账（任务/航次/任务×周期/周期）——所有图的唯一口径
    │     declutter.py  标注去重叠（落盘前统一调用）
    ├── initial/      I   输入/设定（不带 TA/PP 标签）
    ├── ta/           TA  上层 task allocation 的账
    ├── pp/           PP  下层 path planning 的账
    ├── system/       S   整个系统（TA+PP 闭环，不带标签）
    ├── interactive_3d.py  可选：三维航迹 HTML（不进 8 张清单）
    └── captions.py   图题/图注/读法/待澄清 → figures/CAPTIONS.md

退役的旧图不在这里：`../archive/plot_retired/`（不出图、不被 import）。

常用方式::

    # 全量（8 张）→ results/<run>/figures
    python -m orchestration.experiments.exp01_smoke.plot

    # 只出某一族 / 某一相位 / 某一张
    python -m orchestration.experiments.exp01_smoke.plot --group pp
    python -m orchestration.experiments.exp01_smoke.plot --only TA
    python -m orchestration.experiments.exp01_smoke.plot --only PP01

    # 单图独立调试（不重跑实验，直接读 results/*.json）
    python -m orchestration.experiments.exp01_smoke.plot.pp.pp01_cycle_tracks
    python -m orchestration.experiments.exp01_smoke.plot.pp.pp01_cycle_tracks --pdf --dpi 300

    # 编程方式
    from orchestration.experiments.exp01_smoke.plot import render
    render("results/<run>", groups=["ta"])
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Sequence

from . import registry as _registry
from . import theme as _theme
from .registry import FIGURES, GROUPS, OWNERS, Figure, by_id, resolve
from .theme import DEFAULT_DPI, run_script, style

__all__ = [
    "FIGURES", "GROUPS", "OWNERS", "Figure", "by_id", "resolve",
    "render", "run_script", "style", "write_ledgers", "write_captions",
]


def _table_dir(fig_dir: Path) -> Path:
    """台账 CSV 不跟图混放：落到出图目录的兄弟目录 ``tables/``。"""
    d = Path(fig_dir).parent / "tables"
    d.mkdir(parents=True, exist_ok=True)
    return d


def render(
    run_dir: Path | str,
    out_dir: Path | str | None = None,
    *,
    groups: Iterable[str] | None = None,
    names: Iterable[str] | None = None,
    only: str | None = None,
    pdf: bool = False,
    dpi: int | None = None,
    with_csv: bool = False,
) -> list[Path]:
    """从已落盘 ``results/<run>`` 重画选定图（不重跑实验）。

    ``out_dir`` 省略时写 ``<run_dir>/figures``（与 ``run.py`` 的约定一致）。
    """
    import dataclasses

    from .base import ledger as _ledger
    from .base import scene as _scene

    figures = resolve(groups=groups, names=names, only=only)
    if not figures:
        print(f"[plot] 警告：图集为空（groups={list(groups) if groups else None}, "
              f"names={list(names) if names else None}, only={only!r}）——请核对分层名 / 图 id")
        return []

    _theme.EMIT_PDF = bool(pdf)
    font = style()
    # 结果目录由参数指定；场景与配置仍取实验侧（同一套 config/tasks）
    ctx = _scene.prepare(run=run_dir, need_scene=any(f.needs_scene for f in figures))
    if out_dir is not None:
        ctx = dataclasses.replace(ctx, fig_dir=Path(out_dir))
    if with_csv:
        tdir = _table_dir(ctx.fig_dir)
        for p in _ledger.write_ledgers(_ledger.build(ctx), tdir):
            print(f"[plot] 台账 → {tdir.name}/{p.name}")

    dpi = int(dpi or DEFAULT_DPI)
    written: list[Path] = []
    skipped: list[str] = []
    for fig in figures:
        t = time.perf_counter()
        produced = fig.build(ctx, dpi=dpi)
        if produced:
            written += produced
            print(f"[plot] {fig.id} → {', '.join(p.name for p in produced)}"
                  f"（{time.perf_counter() - t:.1f} s）")
        else:
            skipped.append(fig.id)
    groups_desc = ", ".join(dict.fromkeys(f"{f.owner}/{f.group}" for f in figures))
    # 图题/图注/读法/待澄清 与 PNG 同目录落盘：图内只留一行脚注，长解释都在这份 md 里。
    # 始终写**整份清单**（不是本次渲染的子集）：单图重绘不会把文档缩成一张。
    cap = write_captions(ctx.fig_dir)
    print(f"[plot] 图注 → {cap}")
    print(f"[plot] 共 {len(written)} 个文件 / {len(figures)} 张图"
          f"（{groups_desc}）→ {ctx.fig_dir}（字体 {font}）")
    if skipped:
        print(f"[plot] 跳过（数据不满足前置条件）：{', '.join(skipped)}")
    return written


def write_captions(fig_dir: Path | str, figures=None) -> Path:
    """把图题/图注/读法/待澄清写成 ``<fig_dir>/CAPTIONS.md``（与图同放）。"""
    from . import captions as _cap

    return _cap.write(Path(fig_dir) / "CAPTIONS.md", figures)


def write_ledgers(run_dir: Path | str, out_dir: Path | str | None = None) -> list[Path]:
    """单独落四张台账 CSV（论文附录/答辩备查；出图不依赖它）。"""
    from .base import ledger as _ledger
    from .base import scene as _scene

    ctx = _scene.prepare(run=run_dir, need_scene=False)
    if out_dir is not None:
        import dataclasses

        ctx = dataclasses.replace(ctx, fig_dir=Path(out_dir))
    return _ledger.write_ledgers(_ledger.build(ctx), _table_dir(ctx.fig_dir))
