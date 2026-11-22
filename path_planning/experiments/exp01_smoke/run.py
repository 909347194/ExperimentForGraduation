# -*- coding: utf-8 -*-
"""exp01_smoke：真实 Guangzhou 场景下单机单航次的端到端冒烟。

跑什么：一条 2.8 km 航段（机巢 0 → 机巢 1）+ 30 s 停靠，冻结 ``t0=0``。
看什么：航迹能交付、复核通过、真几何净空 ≥ 阈值、预算不超；单机无冲突。
记什么：``results/seed_0/{solution,metrics,run_meta,effective_config}.json``。
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from .support.experiment_run import (
    print_summary,
    run_fleet_experiment,
)
from .support.runtime import load_runtime

_HERE = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _HERE / "config.yaml"
_DEFAULT_FLEET = _HERE / "uav_config.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="exp01_smoke：单机单航次冒烟")
    parser.add_argument("--config", default=str(_DEFAULT_CONFIG))
    parser.add_argument("--fleet", default=str(_DEFAULT_FLEET))
    parser.add_argument("--seed", type=int, default=None, help="覆盖 RRT 基础种子")
    parser.add_argument("--out", default=None, help="结果目录（缺省 = results/seed_<种子>）")
    args = parser.parse_args(argv)

    runtime = load_runtime(args.config)
    if args.seed is not None:
        runtime.planner.context = dataclasses.replace(runtime.ctx, seed=int(args.seed))
    fleet = runtime.requests_from(args.fleet)
    out_dir = (
        Path(args.out)
        if args.out
        else runtime.config.results_dir / f"seed_{runtime.ctx.seed}"
    )

    plan, metrics, _ = run_fleet_experiment(
        runtime, fleet, out_dir,
        meta_extra={"fleet_file": str(Path(args.fleet).name)},
    )
    print_summary(runtime, plan, metrics, out_dir)
    return 0 if plan.ok else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
