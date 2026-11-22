# -*- coding: utf-8 -*-
"""exp01_smoke 出图：两轮四机 tour 的全域三维图（PNG/PDF）写入 ``results/<seed>/figures/``。

口径：一次运行一张图，文件名固定 ``P01_multi_round_3d.*``，不按时间戳起名——
实验结果目录里"每个图一个名字"，重跑覆盖旧图。全域 = ``data_bounds_xy`` 整片研究区；
竖向放大 3× 只作阅读约定，写进图注。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from path_planning.common.scenario_loader import load_scenario
from .support.experiment_config import load_experiment_config
from .support.result_store import trajectory_from_dict
from .support.scene_3d import render_scene

_HERE = Path(__file__).resolve().parent
_NAME = "P01_multi_round_3d"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="exp01_smoke 三维图")
    parser.add_argument("--config", default=str(_HERE / "config.yaml"))
    parser.add_argument("--run", default=None, help="结果目录（缺省 = results/seed_<首个种子>）")
    parser.add_argument("--out", default=None, help="输出目录（缺省 = <run>/figures）")
    parser.add_argument("--z-scale", type=float, default=3.0)
    parser.add_argument("--min-building", type=float, default=0.0,
                        help="只画高于该值的建筑；0 = 全部建筑（默认，城市肌理完整）")
    parser.add_argument("--size", default="3600x2700", help="像素宽x高")
    parser.add_argument("--azimuth", type=float, default=-55.0)
    parser.add_argument("--elevation", type=float, default=33.0)
    args = parser.parse_args(argv)

    cfg = load_experiment_config(args.config)
    run_dir = Path(args.run) if args.run else cfg.results_dir / f"seed_{cfg.seeds[0]}"
    out_dir = Path(args.out) if args.out else run_dir / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = json.loads((run_dir / "round_trajectories.json").read_text(encoding="utf-8"))
    meta = json.loads((run_dir / "run_meta.json").read_text(encoding="utf-8"))
    trajs = {k: trajectory_from_dict(v) for k, v in raw.items()}
    tasks = {
        int(s["id"]): s["position"]
        for r in meta["rounds"] for u in r.get("stops", [])
        for s in u["stops"] if s["kind"] == "task"
    }

    scenario = load_scenario(
        cfg.scenario.data_dir, name=cfg.scenario.name,
        nest_count=cfg.scenario.nest_count,
        terrain_step_m=float(cfg.scenario.terrain_step_m),
        bbox_margin_m=float(cfg.scenario.bbox_margin_m),
    )
    width, height = (int(v) for v in args.size.lower().split("x"))
    render_scene(
        trajs, tasks, scenario,
        out_png=out_dir / f"{_NAME}.png",
        out_pdf=out_dir / f"{_NAME}.pdf",
        title="多机多轮 tour 航迹（exp01_smoke）",
        subtitle="轮1 UAV1/UAV2（新-新协调）｜轮2 UAV3/UAV4（既有航迹约束下规划）",
        stage_label="轮", z_scale=float(args.z_scale),
        min_building_m=float(args.min_building), size=(width, height),
        azimuth=float(args.azimuth), elevation=float(args.elevation),
    )
    print(f"[exp01] 图：{out_dir / _NAME}.png / .pdf")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
