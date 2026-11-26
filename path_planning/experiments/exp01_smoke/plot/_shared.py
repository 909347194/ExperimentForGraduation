# -*- coding: utf-8 -*-
"""plot 目录共用的取数：读配置 / 机队 / 结果 JSON，算出各脚本都要的那几样东西。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..support.result_store import trajectory_from_dict
from ..support.runtime import load_runtime

_HERE = Path(__file__).resolve().parents[1]   # exp01_smoke/
CONFIG = _HERE / "config.yaml"
FLEET = _HERE / "uav_config.json"


@dataclass(frozen=True)
class Ctx:
    """一次出图/出表要用的全部输入（只读结果，不碰规划器）。"""

    cfg: Any
    runtime: Any
    fleet: Any
    run_dir: Path
    fig_dir: Path
    tab_dir: Path
    trajs: dict[str, Any]
    trace: dict[str, Any]
    metrics: dict[str, Any]
    tasks: dict[int, Any] = field(default_factory=dict)
    requests: dict[int, Any] = field(default_factory=dict)


def prepare(
    config: str | Path | None = None,
    fleet: str | Path | None = None,
    run: str | Path | None = None,
    *,
    need_trajs: bool = True,
    need_trace: bool = True,
    need_metrics: bool = True,
) -> Ctx:
    """按需加载结果文件：出图不需要 metrics，出表不需要 scenario 之外的场。"""
    runtime = load_runtime(str(config or CONFIG))
    fleet_in = runtime.requests_from(str(fleet or FLEET))
    run_dir = Path(run) if run else (
        runtime.config.results_dir / f"seed_{runtime.config.seeds[0]}"
    )

    trajs = {}
    if need_trajs:
        raw = json.loads((run_dir / "cycle_trajectories.json").read_text(encoding="utf-8"))
        trajs = {k: trajectory_from_dict(v) for k, v in raw.items()}
    trace = {}
    if need_trace:
        trace = json.loads((run_dir / "cycle_trace.json").read_text(encoding="utf-8"))
    metrics = {}
    if need_metrics:
        metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))

    return Ctx(
        cfg=runtime.config, runtime=runtime, fleet=fleet_in, run_dir=run_dir,
        fig_dir=run_dir / "figures", tab_dir=run_dir / "tables",
        trajs=trajs, trace=trace, metrics=metrics, tasks=tasks_of(run_dir),
        requests={int(r.uav_id): r for r in fleet_in.requests},
    )


def tasks_of(run_dir: Path) -> dict[int, Any]:
    """落盘 meta 里的任务停留点（编号 → ENU 坐标），给三维图打标用。"""
    meta = json.loads((Path(run_dir) / "run_meta.json").read_text(encoding="utf-8"))
    return {
        int(s["id"]): s["position"]
        for r in meta["cycles"] for u in r.get("stops", [])
        for s in u["stops"] if s["kind"] == "task"
    }


def add_common(parser: argparse.ArgumentParser) -> None:
    """单脚本直跑时共用的三个参数（统一编排由 ``run.py`` 负责传）。"""
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--fleet", default=str(FLEET))
    parser.add_argument("--run", default=None, help="结果目录（缺省 = results/seed_<首个种子>）")


def run_single(title: str, build, argv: list[str] | None) -> int:
    """单脚本入口：``python -m ...plot.p01_multi_cycle_3d`` 用。"""
    parser = argparse.ArgumentParser(description=title)
    add_common(parser)
    args = parser.parse_args(argv)
    ctx = prepare(args.config, args.fleet, args.run)
    paths = build(ctx)
    print("[exp01] " + "；".join(short_path(p) for p in paths))
    return 0


def short_path(p) -> str:
    """只显示相对结果目录的尾部，控制台不刷长路径。"""
    return "/".join(Path(p).parts[-2:])
