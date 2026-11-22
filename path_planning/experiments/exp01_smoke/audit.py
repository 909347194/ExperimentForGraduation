# -*- coding: utf-8 -*-
"""exp01_smoke 审计：单机单航次。

通用五项在 ``support/audit_checks.py``（覆盖 / 复核 / 真几何 / 预算 /
冲突）；单机专属三项在 ``support/quality_report.py``（时间来源 /
运动学裕度 / 稠密净空），并附**逐 leg** 明细与整条时间账。

本实验只有一条航段（巢0 → 巢1，终点真高 18 m、栖停 30 s），所以逐 leg 表只有
一行——多段航迹（巢→任务A→任务B→巢）的逐 leg 账由 ``exp05_multileg`` 覆盖。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .support.audit_checks import Check, audit_run, report
from .support.quality_report import trajectory_quality
from .support.result_store import trajectory_from_dict
from .support.runtime import load_runtime

_HERE = Path(__file__).resolve().parent


def quality_checks(runtime, fleet, run_dir: Path) -> list[Check]:
    payload = json.loads((run_dir / "solution.json").read_text(encoding="utf-8"))
    checks: list[Check] = []
    for uid_key, res in sorted(payload["results"].items()):
        if res.get("trajectory") is None:
            continue
        uid = int(uid_key)
        req = next(r for r in fleet.requests if r.uav_id == uid)
        qr = trajectory_quality(
            uid,
            trajectory_from_dict(res["trajectory"]),
            vehicle=fleet.vehicles[uid],
            field=runtime.field,
            clearance_m=float(runtime.config.params.clearance_m),
            cfg_step_m=float(runtime.config.params.clearance_sample_step_m),
            time_source=res["trajectory"].get("time_source"),
            stops=[(tuple(s.position), float(s.service_time_s)) for s in req.stops],
            endpoint_exempt_radius_m=float(runtime.config.endpoint_exempt_radius_m),
            endpoint_clearance_m=runtime.config.endpoint_clearance_m,
        )
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
    checks = audit_run(runtime, fleet, run_dir, extra=quality_checks(runtime, fleet, run_dir))
    return report(checks)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
