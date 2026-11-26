# -*- coding: utf-8 -*-
"""exp01_smoke 审计：多机多周期（两周期五机、无巢口径）。

核心项在 ``support/audit_checks.py``（覆盖 / 航次自洽 / 复核 / 真几何 / 预算 /
端点净空 / 必撞触发 / 旧侧冻结 / 在飞中规划 / 冲突归零 / 状态账）；
逐机质量项在 ``support/quality_report.py``（时间来源 / 运动学裕度 / 稠密净空 /
分段边界 / 单机质量账）。

本实验端点**无豁免**（``endpoint_exempt_radius_m=0``）：起始点由实验选址、
实测 SDF ≥ 8 m，端点与全域同判 6 m——比单机冒烟的"端点豁免球"更严。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .support.audit_checks import Check, audit_cycles, report
from .support.quality_report import trajectory_quality
from .support.result_store import trajectory_from_dict
from .support.runtime import load_runtime

_HERE = Path(__file__).resolve().parent


def quality_checks(runtime, fleet, run_dir: Path) -> list[Check]:
    """逐机质量项：从落盘航迹复算（键 "周期:机号"）。"""
    raw = json.loads((run_dir / "cycle_trajectories.json").read_text(encoding="utf-8"))
    trace = json.loads((run_dir / "cycle_trace.json").read_text(encoding="utf-8"))
    failures = {
        (int(c["cycle"]), int(t["uav_id"])): t.get("failure")
        for c in trace["cycles"] for t in c["tickets"]
    }
    requests = {int(r.uav_id): r for r in fleet.requests}
    checks: list[Check] = []
    for key in sorted(raw, key=lambda k: (int(k.split(":")[0]), int(k.split(":")[1]))):
        r_idx, uid = (int(v) for v in key.split(":"))
        traj = trajectory_from_dict(raw[key])
        req = requests[uid]
        qr = trajectory_quality(
            uid,
            traj,
            vehicle=fleet.vehicles[uid],
            field=runtime.field,
            clearance_m=float(runtime.config.params.clearance_m),
            cfg_step_m=float(runtime.config.params.clearance_sample_step_m),
            time_source=raw[key].get("time_source"),
            failure_code=failures.get((r_idx, uid)),
            stops=[(tuple(s.position), float(s.service_time_s)) for s in req.stops],
            endpoint_exempt_radius_m=float(runtime.config.endpoint_exempt_radius_m),
            endpoint_clearance_m=runtime.config.endpoint_clearance_m,
        )
        print(f"--- 质量账 周期{r_idx} 机{uid} ---")
        for line in qr.lines:
            print(line)
        checks.extend(qr.checks)
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="exp01_smoke 审计")
    parser.add_argument("--config", default=str(_HERE / "config.yaml"))
    parser.add_argument("--fleet", default=str(_HERE / "uav_config.json"))
    parser.add_argument("--run", default=None, help="结果目录（缺省 = results/seed_<首个种子>）")
    args = parser.parse_args(argv)

    runtime = load_runtime(args.config)
    fleet = runtime.requests_from(args.fleet)
    run_dir = (
        Path(args.run)
        if args.run
        else runtime.config.results_dir / f"seed_{runtime.config.seeds[0]}"
    )
    checks = audit_cycles(runtime, fleet, run_dir) + quality_checks(runtime, fleet, run_dir)
    return report(checks)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

