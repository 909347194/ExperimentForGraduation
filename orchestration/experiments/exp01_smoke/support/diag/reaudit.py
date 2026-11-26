# -*- coding: utf-8 -*-
"""exp01_smoke 审计重算：只读已落盘的 ``loop_trace.json``，不重跑求解。

用途：改审计文案/判据后，不必再花一轮求解就能刷新 ``audit.json``。
边界：只用结果目录里的记录还原对象，判据仍由 ``support/audit.py`` 决定，
      不改任何数值，也不改 metric/report 的其它文件。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from orchestration.protocol import CostUpdate

from ..audit import audit_trace
from ..config import default_run_dir, load_config
from ..fleet import load_uav_config
from ..tasks import load_tasks


def _attempt(a: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        attempt_id=int(a["attempt"]), snapshot_id=str(a["snapshot"]),
        cost_version=str(a["cost_version"]),
        est_cost={int(k): float(v) for k, v in a["est_cost"].items()},
        residuals={int(k): float(v) for k, v in a["residual_m"].items()},
        failures={int(k): str(v) for k, v in a["fails"].items()},
    )


def _cycle(c: dict[str, Any]) -> SimpleNamespace:
    report = SimpleNamespace(
        tickets={int(k): _ticket(v) for k, v in c["tickets"].items()},
        moved={int(k): str(v) for k, v in c["moved"].items()},
        conflicts_before=int(c["conflicts_before"]),
        conflicts_after=int(c["conflicts_after"]),
        unplanned=[int(v) for v in c["unplanned"]],
    )
    infeasible = {(int(e[0]), int(e[1])) for e in (c.get("infeasible_edges") or [])}
    update = None if not infeasible else CostUpdate(
        infeasible=frozenset(infeasible), source_attempt=1,
    )
    busy_until = {int(k): float(v) for k, v in c["busy_until"].items()}
    # 旧落盘值可能是在周期末 prune 之后读的（恒 0）。成功票的 t_end 就是该机
    # 的真实占用结束时刻，用它补齐——与 LoopDriver 修复后"prune 前采集"同口径。
    for uid, ticket in report.tickets.items():
        if ticket.ok:
            busy_until[uid] = max(busy_until.get(uid, 0.0), float(ticket.t_end))
    return SimpleNamespace(
        cycle=int(c["cycle"]), t_start=float(c["t_start"]), t_end=float(c["t_end"]),
        est_cost={int(k): float(v) for k, v in c["est_cost"].items()},
        report=report,
        busy_until=busy_until,
        overruns=[int(v) for v in c["overruns"]],
        rolled_back=[int(v) for v in c["rolled_back"]],
        reopened=[int(v) for v in c["reopened"]],
        assignments={int(k): [int(v) for v in vals] for k, vals in c["assignments"].items()},
        executed_ids=[int(v) for v in c["executed_ids"]],
        attempts=[_attempt(a) for a in (c.get("attempts") or [])],
        n_resolves=int(c.get("n_resolves") or 0),
        n_intermediate_failures=int(c.get("n_intermediate_failures") or 0),
        cost_version=str(c.get("cost_version") or "base"),
        cost_update=update,
        unresolved_infeasible_edges=tuple(
            (int(e[0]), int(e[1])) for e in (c.get("unresolved_infeasible_edges") or [])
        ),
        infeasible_but_survived=tuple(
            (int(e[0]), int(e[1])) for e in (c.get("infeasible_but_survived") or [])
        ),
        resolve_error=str(c.get("resolve_error") or ""),
    )


def _ticket(t: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        ok=bool(t["ok"]), t0=float(t["t0"]), t_end=float(t["t_end"]),
        duration_s=float(t["duration_s"]), path_len_m=float(t["path_len_m"]),
        time_source=str(t["time_source"]), failure=t["failure"],
        detail=str(t.get("detail") or ""), gate=dict(t.get("gate") or {}),
        task_ids=[int(v) for v in t["task_ids"]],
    )



def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reaudit")
    ap.add_argument("--out", type=Path, default=None,
                    help="结果目录（缺省 = 配置 seed 对应的 results/seed_N）")
    args = ap.parse_args(argv)
    cfg = load_config()
    out_dir = args.out or default_run_dir(cfg)

    trace_doc = json.loads((out_dir / "loop_trace.json").read_text(encoding="utf-8"))
    meta = json.loads((out_dir / "run_meta.json").read_text(encoding="utf-8"))
    trace = SimpleNamespace(
        cycles=[_cycle(c) for c in trace_doc["cycles"]],
        executed_ids=[int(v) for v in trace_doc["executed_ids"]],
        overrun_cycles=[int(v) for v in trace_doc["overrun_cycles"]],
        failures={int(k): str(v) for k, v in trace_doc["failures"].items()},
    )
    tasks = load_tasks(cfg)
    stagger = float(cfg["loop"]["depart_stagger_s"])
    fleet_ids = [int(r["id"]) for r in load_uav_config()]
    problems = audit_trace(
        trace, cfg, tasks=tasks, context=meta["context"],
        scene_meta={"building_count": int(meta["estimator"]["building_count"]),
                    "nests": int(meta["context"]["nests"])},
        allowed_offsets={float(i) * stagger for i in range(8)},
        fleet_ids=fleet_ids,
    )
    audit = {"passed": not problems, "problems": list(problems),
             "criteria": dict(cfg["audit"])}
    (out_dir / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[reaudit] {out_dir}")
    print(f"  pass={not problems}  problems={len(problems)}")
    for p in problems:
        print(f"  - {p}")
    return 0 if not problems else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
