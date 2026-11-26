# -*- coding: utf-8 -*-
"""图题 / 图注 / 读法 / 待澄清 —— 从 ``registry.FIGURES`` 生成一份 Markdown。

为什么要有这份文件：图里的字是给"看图的人"看的，能省则省（省下的版面留给
几何与时序本身）；写不下的口径、数据来源、前提与坑，全部搬到这里。于是

* **图**：图题 + 图例 + 一行脚注，其余留白还给坐标区；
* **文档**：每张图的图注（论文里可直接用的那一句）、怎么读、需要澄清什么。

``figures/CAPTIONS.md`` 由 ``render()`` 自动生成（与 PNG 同目录）；也可单独跑::

    python -m orchestration.experiments.exp01_smoke.plot.captions --out CAPTIONS.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

from . import registry as reg

NAME = "CAPTIONS"

_LAYER = {"IN": "输入（无层标）", "TA": "上层 TA", "PP": "下层 PP", "SYS": "系统（TA+PP 闭环）"}


def build_markdown(figures: Iterable[reg.Figure] | None = None) -> str:
    """生成整份图注文档（默认覆盖 ``FIGURES`` 全部）。"""
    figs = list(figures) if figures is not None else list(reg.FIGURES)
    out: list[str] = [
        "# exp01_smoke 图件清单 · 图题与图注",
        "",
        "本文件由 `plot/captions.py` 从 `plot/registry.py` 生成，**不手改**；"
        "改图题、图注、读法请改 registry 里那张图的登记信息。",
        "",
        "约定：图内只放图题、图例与**一行**脚注；图注、读法、口径与坑一并放在这里，"
        "版面因此留给几何和时序本身。图 id ≡ 模块名 ≡ 文件名前缀。",
        "",
        "## 一、一览表",
        "",
        "| 图 id | 文件 | 层 | 图题 | 图注 |",
        "|---|---|---|---|---|",
    ]
    for f in figs:
        out.append(f"| `{f.id}` | `{f.id}.png` | {_LAYER.get(f.owner, f.owner)} "
                   f"| {f.title} | {f.caption} |")
    out.append("")
    out.append("## 二、逐图说明")
    for f in figs:
        out += [
            "",
            f"### {f.id}",
            "",
            f"**图题**：{f.title}",
            "",
            f"**图注**：{f.caption}",
            "",
            f"**这一张回答**：{f.question}",
            "",
            "**怎么读**：",
        ]
        for k, item in enumerate(f.reading, 1):
            out.append(f"{k}. {item}")
        if not f.reading:
            out.append("1. （未登记）")
        out += [
            "",
            f"**需要澄清 / 前提**：{f.clarify or '（未登记）'}",
            "",
            f"**数据来源**：{f.source}",
        ]
    out.append("")
    return "\n".join(out)


def write(path: Path | str, figures: Iterable[reg.Figure] | None = None) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_markdown(figures), encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plot.captions", add_help=True,
                                 description="生成图题/图注/读法/待澄清 的 Markdown")
    ap.add_argument("--out", default="CAPTIONS.md",
                    help="输出路径（默认 ./CAPTIONS.md）")
    ap.add_argument("--only", default=None, help="只写匹配前缀的图（如 pp、PP01）")
    ns = ap.parse_args(argv)
    figs = reg.resolve(only=ns.only) if ns.only else list(reg.FIGURES)
    p = write(ns.out, figs)
    print(f"[captions] {len(figs)} 张图 → {p}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
