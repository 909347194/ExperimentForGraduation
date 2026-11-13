# -*- coding: utf-8 -*-
"""exp03_event_rolling 产物组装：指标 + 事件增量 + 跨因子汇总。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import paths  # noqa: F401

from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.rolling.horizon import cycle_record_has_activity

from build import TaskBatch
from config import ConfigBundle

__all__ = [
    "RunFacts",
    "build_metrics",
    "build_run_meta",
    "build_solution",
    "build_event_summary",
    "write_json",
    "print_summary",
]


@dataclass
class RunFacts:
    bundle: ConfigBundle
    scenario: Any
    nests: list[Nest]
    terrain: Any
    uavs: list[UAV]
    batch: TaskBatch
    records: list[Any]
    pool: Any
    fleet: Any
    wall_seconds: float
    initial_uavs: list[UAV] | None = None


def write_json(path: Path, payload: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------


def build_metrics(f: RunFacts) -> dict[str, Any]:
    records = f.records
    finished = f.pool.finished_tasks()
    completed = [t for t in finished if t.meta.get("completed_at") is not None]
    tasks = f.batch.all_tasks()
    n_arrived = len(tasks)

    total_path = sum(float(r.muas.get("final_path_m", 0.0) or 0.0) for r in records)
    total_reward = sum(float(r.muas.get("f1_reward", 0.0) or 0.0) for r in records)
    solve_s = sum(float(r.muas.get("solve_seconds", 0.0) or 0.0) for r in records)
    n_cycles = len(records) or int(f.bundle.horizon.n_cycles)
    n_oper_cycles = max(
        (i for i, r in enumerate(records, 1) if cycle_record_has_activity(r)),
        default=0,
    ) or n_cycles

    n_late = sum(len(r.late_ids) for r in records)
    n_unreachable = sum(len(r.unreachable_ids) for r in records)
    n_infeasible = sum(len(r.infeasible_ids) for r in records)
    n_dormant_events = sum(len(getattr(r, "dormant_ids", []) or []) for r in records)
    n_woken = sum(len(getattr(r, "woken_ids", []) or []) for r in records)
    _last = records[-1] if records else None
    n_dormant_end = (
        int((getattr(_last, "pool_counts", {}) or {}).get("n_dormant", 0))
        + len(getattr(_last, "dormant_ids", []) or [])
    ) if _last else 0

    n_final_denom = max(0, n_arrived - n_unreachable - n_dormant_end)
    completion_rate = (len(completed) / n_final_denom) if n_final_denom else 0.0
    on_time_rate = ((len(completed) - n_late) / len(completed)) if completed else 0.0

    # ── 事件指标（本实验新增）──────────────────────────
    all_events = [e for r in records for e in (r.events or [])]
    event_types = {}
    for e in all_events:
        t = e.get("type", "unknown")
        event_types[t] = event_types.get(t, 0) + 1

    n_replan = sum(1 for r in records if getattr(r, "replan", False))
    n_fault_injected = sum(
        len(v) for v in (f.bundle.horizon.faults or {}).values()
    )
    n_released_by_fault = sum(
        len(getattr(r, "released_by_fault", []) or []) for r in records
    )
    n_forced_ids = set()
    for r in records:
        sel = r.selection or {}
        for tid in sel.get("forced_task_ids", []) or []:
            n_forced_ids.add(int(tid))
    n_forced_in = len(n_forced_ids)

    n_nest_closures = sum(
        len(v) for v in (f.bundle.horizon.nest_closures or {}).values()
    )
    # 终点机巢变更次数（由 tours 相邻周期 end_nest 对比）
    n_end_nest_changed = 0
    prev_end_nests: dict[int, int] = {}
    for r in records:
        for tour in r.tours or []:
            uid = int(tour.get("uav_id", -1))
            en = tour.get("end_nest_id")
            if en is not None and uid in prev_end_nests and prev_end_nests[uid] != int(en):
                n_end_nest_changed += 1
            if en is not None:
                prev_end_nests[uid] = int(en)

    # response_latency: 注入周期 → 生效周期
    response_latency = []
    for r in records:
        if r.events:
            if r.replan:
                response_latency.append(1)

    # 每周期池计数
    pool_counts_by_cycle = {}
    for r in records:
        pool_counts_by_cycle[str(r.cycle)] = dict(r.pool_counts or {})

    summary = {
        "n_arrived": n_arrived,
        "n_completed": len(completed),
        "completion_rate": round(completion_rate, 4),
        "on_time_rate": round(on_time_rate, 4),
        "n_late": n_late,
        "n_unreachable": n_unreachable,
        "n_infeasible": n_infeasible,
        "n_dormant_events": n_dormant_events,
        "n_woken": n_woken,
        "n_dormant_end": n_dormant_end,
        "total_path_m": round(total_path, 1),
        "total_reward": round(total_reward, 4),
        "n_cycles": n_cycles,
        "n_oper_cycles": n_oper_cycles,
        "solve_seconds": round(solve_s, 3),
        # 事件增量
        "event_types": event_types,
        "n_event_occurrences": len(all_events),
        "n_replan_cycles": n_replan,
        "n_fault_injected": n_fault_injected,
        "n_released_by_fault": n_released_by_fault,
        "n_forced_in": n_forced_in,
        "n_nest_closures": n_nest_closures,
        "n_end_nest_changed": n_end_nest_changed,
        "response_latency_mean": round(
            sum(response_latency) / len(response_latency), 1
        ) if response_latency else None,
    }

    return {
        "summary": summary,
        "cycles": [
            {
                "cycle": r.cycle,
                "pool_counts": dict(r.pool_counts or {}),
                "n_executed": len(r.executed_ids),
                "n_completed": len(r.completed_ids),
                "n_deferred": len(r.deferred_ids),
                "n_events": len(r.events or []),
                "replan": bool(getattr(r, "replan", False)),
                "released_by_fault": list(getattr(r, "released_by_fault", []) or []),
                "events": list(r.events or []),
                "selected_ids": list((r.selection or {}).get("selected_ids", []) or []),
                "forced_task_ids": list((r.selection or {}).get("forced_task_ids", []) or []),
                "n_uav_avail": int((r.selection or {}).get("n_uav_avail", 0) or 0),
            }
            for r in records
        ],
        "pool_counts_by_cycle": pool_counts_by_cycle,
    }


def build_solution(f: RunFacts) -> dict[str, Any]:
    records = f.records
    return {
        "tasks": [
            {
                "id": int(t.id),
                "x": float(t.x), "y": float(t.y), "z": float(t.z),
                "reward": round(float(t.reward), 4),
                "arrival_time": round(float(t.arrival_time), 3),
                "latest": t.latest,
                "status": t.status.value,
                "meta": {k: v for k, v in (t.meta or {}).items() if k != "infeasible_tags"},
            }
            for t in f.batch.all_tasks()
        ],
        "nests": [
            {"id": n.id, "x": float(n.x), "y": float(n.y), "z": float(n.z), "capacity": n.capacity}
            for n in f.nests
        ],
        "uavs": [
            {
                "id": u.id,
                "x": float(u.x), "y": float(u.y), "z": float(u.z),
                "nest_id": u.nest_id,
                "max_range": float(u.max_range or 0),
                "speed": float(u.speed or 0),
            }
            for u in (f.initial_uavs or f.uavs)
        ],
        "cycles": [
            {
                "cycle": r.cycle,
                "tours": list(r.tours or []),
                "cost_history": list(r.cost_history or []),
                "events": list(r.events or []),
                "replan_context": dict(r.replan_context or {}),
            }
            for r in records
        ],
    }


def build_run_meta(f: RunFacts, *, cfg_path: Path, root: Path) -> dict[str, Any]:
    raw = f.bundle.raw
    cfg_bytes = json.dumps(raw, sort_keys=True, default=str).encode()
    return {
        "experiment": f.bundle.experiment.get("name", "exp03_event_rolling"),
        "factor": f.bundle.factor,
        "seed": f.bundle.seed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "wall_seconds": f.wall_seconds,
        "config_path": str(cfg_path),
        "config_hash": hashlib.sha256(cfg_bytes).hexdigest()[:16],
        "n_nests": len(f.nests),
        "n_uavs": len(f.uavs),
        "n_tasks": len(f.batch.all_tasks()),
    }


# ---------------------------------------------------------------------------
# 事件汇总（跨因子 / 跨 seed）
# ---------------------------------------------------------------------------


def build_event_summary(
    results: list[tuple[int, RunFacts, dict, dict]],
    factor: str,
) -> dict[str, Any]:
    """汇总一个因子下所有 seed 的事件指标。"""
    from statistics import mean, stdev

    summaries = [m["summary"] for _, _, m, _ in results]

    def _agg(key: str) -> dict[str, float]:
        vals = [s.get(key, 0) for s in summaries]
        if not vals:
            return {"mean": 0, "std": 0}
        m = mean(vals)
        s = stdev(vals) if len(vals) > 1 else 0.0
        return {"mean": round(m, 4), "std": round(s, 4)}

    # 合并 event_types
    merged_events: dict[str, int] = {}
    for s in summaries:
        for k, v in (s.get("event_types") or {}).items():
            merged_events[k] = merged_events.get(k, 0) + v

    return {
        "factor": factor,
        "n_seeds": len(results),
        "event_types": merged_events,
        "completion_rate": _agg("completion_rate"),
        "on_time_rate": _agg("on_time_rate"),
        "n_replan_cycles": _agg("n_replan_cycles"),
        "n_released_by_fault": _agg("n_released_by_fault"),
        "n_forced_in": _agg("n_forced_in"),
        "n_end_nest_changed": _agg("n_end_nest_changed"),
        "n_event_occurrences": _agg("n_event_occurrences"),
        "total_path_m": _agg("total_path_m"),
        "total_reward": _agg("total_reward"),
        "n_completed": _agg("n_completed"),
        "n_late": _agg("n_late"),
        "n_infeasible": _agg("n_infeasible"),
    }


# ---------------------------------------------------------------------------
# 终端报表
# ---------------------------------------------------------------------------


def print_summary(metrics: dict[str, Any], out_dir: Path) -> None:
    s = metrics.get("summary", {})
    factor = metrics.get("factor", "?")
    print(f"\n─── {factor} 结果摘要 ───")
    print(f"  到达={s.get('n_arrived')} 完成={s.get('n_completed')} "
          f"完成率={s.get('completion_rate'):.1%} 按时率={s.get('on_time_rate'):.1%}")
    print(f"  事件={s.get('n_event_occurrences')} 重规划={s.get('n_replan_cycles')} "
          f"故障释放={s.get('n_released_by_fault')} 强制纳入={s.get('n_forced_in')}")
    print(f"  总路径={s.get('total_path_m')}m 总奖励={s.get('total_reward')} "
          f"周期={s.get('n_cycles')}")
    print(f"  产物 → {out_dir}")
