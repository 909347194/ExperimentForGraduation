# -*- coding: utf-8 -*-
"""统一编排：一次跑齐三图两表。

用法（项目根下）：
  python -m path_planning.experiments.exp01_smoke.plot              # 全跑
  python -m path_planning.experiments.exp01_smoke.plot --only p02 t01
  python -m path_planning.experiments.exp01_smoke.plot --run <结果目录>

只读 ``results``，不重跑规划；改样式只重跑本脚本。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import (
    p01_multi_cycle_3d,
    p02_operation_zoom,
    p03_interactive_3d,
    t01_cycle_results,
    t02_cross_cycle,
    t03_seed_repeat,
)

from ._shared import add_common, prepare, short_path

#: 代号 → 模块（顺序即产出顺序：先图后表）。
ARTIFACTS = {
    "p01": p01_multi_cycle_3d,
    "p02": p02_operation_zoom,
    "p03": p03_interactive_3d,
    "t01": t01_cycle_results,
    "t02": t02_cross_cycle,
    "t03": t03_seed_repeat,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="exp01_smoke 出图出表（统一编排）")
    add_common(parser)
    parser.add_argument("--only", nargs="+", choices=sorted(ARTIFACTS),
                        help="只跑指定代号（缺省 = 全跑）")
    parser.add_argument("--z-scale", type=float, default=3.0)
    parser.add_argument("--min-building", type=float, default=0.0,
                        help="只画高于该值的建筑；0 = 全部建筑（城市肌理完整）")
    parser.add_argument("--size", default="3600x2700", help="像素 宽x高")
    parser.add_argument("--azimuth", type=float, default=-55.0)
    parser.add_argument("--elevation", type=float, default=33.0)
    args = parser.parse_args(argv)

    fig_style = {
        "z_scale": args.z_scale, "min_building_m": args.min_building,
        "size": tuple(int(v) for v in args.size.lower().split("x")),
        "azimuth": args.azimuth, "elevation": args.elevation,
    }
    keys = args.only or list(ARTIFACTS)
    need_fig = any(k.startswith("p") for k in keys)
    ctx = prepare(args.config, args.fleet, args.run,
                  need_trajs=need_fig or "t01" in keys or "t02" in keys,
                  need_metrics="t02" in keys)

    for key in keys:
        mod = ARTIFACTS[key]
        t0 = time.perf_counter()
        if key.startswith("p"):
            paths = mod.build(ctx, **fig_style)
        else:
            paths = mod.build(ctx)
        print(f"[exp01] {key.upper()}（{time.perf_counter() - t0:.1f} s）："
              + "；".join(short_path(p) for p in paths))
    return 0




if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

