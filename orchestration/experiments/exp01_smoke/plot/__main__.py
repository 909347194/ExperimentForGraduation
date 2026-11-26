# -*- coding: utf-8 -*-
"""全量 / 分族出图：``python -m orchestration.experiments.exp01_smoke.plot``。

只读一次场景（建筑/地形）逐图渲染；``--group`` 按「账」选族、``--only`` 按 id 前缀选图。
``--csv`` 额外把四张台账落成 CSV（论文附录/答辩备查），出图本身不依赖 CSV。
"""

from __future__ import annotations

import argparse
import sys

from . import FIGURES, GROUPS, render
from .theme import add_common


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="exp01_smoke 出图（I/TA/PP/S 四层）")
    add_common(parser)
    parser.add_argument("--group", default=None,
                        help="只出某一层：initial / ta / pp / system（逗号分隔可多选）")
    parser.add_argument("--only", default=None,
                        help="只出 id 以该前缀开头的图：I / TA / PP / S，或 PP01")
    parser.add_argument("--dpi", type=int, default=None, help="位图 dpi（缺省 190）")
    parser.add_argument("--csv", action="store_true",
                        help="同时落四张台账 CSV（task/tour/task_cycle/cycle）")
    args = parser.parse_args(argv)

    groups = [g.strip() for g in args.group.split(",")] if args.group else None
    if groups:
        unknown = [g for g in groups if g not in GROUPS]
        if unknown:
            parser.error(f"未知分层 {unknown}；可用：{list(GROUPS)}")
    if args.only and not [f for f in FIGURES if f.id.lower().startswith(args.only.lower())]:
        parser.error(f"--only={args.only!r} 没有匹配的图；可用：{[f.id for f in FIGURES]}")

    render(args.run and args.run, None, groups=groups, only=args.only,
           pdf=args.pdf, dpi=args.dpi, with_csv=args.csv)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
