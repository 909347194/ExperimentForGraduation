# -*- coding: utf-8 -*-
"""exp05_baselines 指标汇总：单次运行 summary + 跨 seed 聚合 + summary.csv 行。

主指标口径与全局协议对齐（experiments/README.md / exp05 notes.md「主指标」）：

    有效完成率 / 奖励产出率 / 按时率 / 总迟到 / 三维航程 / 硬违约与弹回 / 运行时间

口径与 exp01 逐字段一致（同一 metrics 语言，跨实验可直接对比）：
    - 迟到不是废飞：晚到计完成与吞吐，按 L/W 折减有效奖励；
    - 废飞 = 完成闸门弹回的物理不可行交付（infeasible_ids）；
    - 三维航程 = 各周期交付解 final_path_m 之和（欧氏臂注明距离定义）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "build_summary",
    "aggregate_summaries",
    "summary_csv_row",
    "write_json",
]

#: summary.csv 的列（论文主表数据源；均值行 = 同列 mean）
CSV_COLUMNS = (
    "arm",
    "solver",
    "cost_model",
    "seed",
    "n_arrived",
    "n_completed",
    "completion_rate",
    "effective_completion_rate",
    "reward_yield",
    "on_time_rate",
    "n_late",
    "total_lateness_s",
    "avg_lateness_s",
    "max_lateness_s",
    "total_path_m",
    "n_infeasible_delivery",
    "n_capacity_violations",
    "cycle_feasible_rate",
    "n_eliminated",
    "n_unreachable",
    "n_voluntary_skip",
    "n_capacity_shortfall",
    "n_gate_rescued",
    "throughput_per_cycle",
    "n_cycles",
    "n_active_cycles",
    "solve_time_ms",
    "wall_seconds",
)

#: 跨 seed 聚合的数值键（mean±std）
AGG_KEYS = tuple(k for k in CSV_COLUMNS if k not in ("arm", "solver", "cost_model", "seed"))


def write_json(path: Path, payload: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def build_summary(
    records: Sequence[Any],
    *,
    pool: Any,
    batch: Any,
    problem_model: Any,
    wall_seconds: float,
) -> dict[str, Any]:
    """单次滚动运行（一个 arm × 一个 seed）的汇总指标。

    Args:
        records: ``RollingHorizon.run`` 的 ``CycleRecord`` 列表。
        pool: 运行后的 ``TaskPool``（取 finished_tasks / 遗留数）。
        batch: 任务批次（到达口径）。
        problem_model: ``MUASProblemConfig``（迟到折减 W 等）。
        wall_seconds: 该次运行墙钟。
    """
    finished = pool.finished_tasks()
    completed = [t for t in finished if t.meta.get("completed_at") is not None]
    waits = [float(t.meta["completed_at"]) - float(t.arrival_time) for t in completed]

    tasks = batch.all_tasks()
    n_arrived = len(tasks)
    n_cycles = len(records) or 1

    # 三维航程：各周期交付解的 final_path_m 之和（米）
    total_path_m = sum(float(r.muas.get("final_path_m", 0.0) or 0.0) for r in records)
    solve_ms = sum(float(r.muas.get("solve_seconds", 0.0) or 0.0) for r in records) * 1000.0

    n_infeasible_delivery = sum(len(r.infeasible_ids) for r in records)
    n_late = sum(len(r.late_ids) for r in records)
    all_lateness = [float(v) for r in records for v in r.lateness_by_task.values()]
    n_eliminated = sum(len(r.expired_ids) for r in records)
    n_unreachable = sum(len(r.unreachable_ids) for r in records)
    n_capacity_violations = sum(len(r.capacity_violations) for r in records)
    # 覆盖门禁三分账（2026-10-22）：逐周期 gate_stats 累加；
    # 缺字段 = 未插桩/未开启（schema 容缺）按 0 计。
    def _gate_sum(key: str) -> int:
        return sum(
            int(((r.muas or {}).get("gate_stats") or {}).get(key, 0) or 0)
            for r in records
        )
    n_voluntary_skip = _gate_sum("n_skip")
    n_capacity_shortfall = _gate_sum("n_shortfall")
    n_gate_rescued = _gate_sum("n_rescued")

    # 硬违约归因（弹回标签聚合，供 notes 归因分析）
    infeasible_tag_counts: dict[str, int] = {}
    for r in records:
        for tags in (r.infeasible_tags or {}).values():
            for tag in tags:
                infeasible_tag_counts[str(tag)] = infeasible_tag_counts.get(str(tag), 0) + 1

    feas_flags = [
        bool(r.muas.get("feasible"))
        for r in records
        if r.muas.get("feasible") is not None
    ]
    cycle_feasible_rate = (sum(feas_flags) / len(feas_flags)) if feas_flags else 0.0
    n_active_cycles = sum(1 for r in records if len(r.executed_ids) > 0)
    n_executed_total = sum(len(r.executed_ids) for r in records)

    on_time_rate = (
        (n_executed_total - n_late - n_infeasible_delivery) / n_executed_total
        if n_executed_total
        else 0.0
    )
    avg_lateness = (sum(all_lateness) / len(all_lateness)) if all_lateness else 0.0
    max_lateness = max(all_lateness) if all_lateness else 0.0
    total_lateness = sum(all_lateness)

    # 有效奖励（迟到按 L/W 线性折减；与 exp01 I4 口径一致）
    decay_w = getattr(problem_model, "late_reward_decay_seconds", None)
    lateness_actual: dict[int, float] = {}
    for r in records:
        for k, v in (r.lateness_by_task or {}).items():
            lateness_actual[int(k)] = float(v)
    reward_possible = sum(max(float(t.reward), 0.0) for t in tasks)
    reward_effective = 0.0
    n_zero_reward_delivery = 0
    for t in completed:
        r_full = max(float(t.reward), 0.0)
        late = lateness_actual.get(int(t.id), 0.0)
        eff = r_full
        if decay_w is not None and float(decay_w) > 0 and late > 0:
            eff = r_full * max(0.0, 1.0 - late / float(decay_w))
        reward_effective += eff
        if r_full > 0 and eff <= 0.0:
            n_zero_reward_delivery += 1

    return {
        "n_arrived": n_arrived,
        "n_completed": len(completed),
        "completion_rate": round((len(completed) / n_arrived) if n_arrived else 0.0, 6),
        "effective_completion_rate": round(
            ((len(completed) - n_zero_reward_delivery) / n_arrived) if n_arrived else 0.0,
            6,
        ),
        "reward_yield": round(
            (reward_effective / reward_possible) if reward_possible else 0.0, 6
        ),
        "on_time_rate": round(on_time_rate, 6),
        "n_late": n_late,
        "total_lateness_s": round(total_lateness, 3),
        "avg_lateness_s": round(avg_lateness, 3),
        "max_lateness_s": round(max_lateness, 3),
        "total_path_m": round(total_path_m, 1),
        "n_infeasible_delivery": n_infeasible_delivery,
        "infeasible_tag_counts": dict(sorted(infeasible_tag_counts.items())),
        "n_capacity_violations": n_capacity_violations,
        "cycle_feasible_rate": round(cycle_feasible_rate, 6),
        "n_eliminated": n_eliminated,
        "n_unreachable": n_unreachable,
        "n_voluntary_skip": n_voluntary_skip,
        "n_capacity_shortfall": n_capacity_shortfall,
        "n_gate_rescued": n_gate_rescued,
        "throughput_per_cycle": round(len(completed) / n_cycles, 6),
        "n_cycles": n_cycles,
        "n_active_cycles": n_active_cycles,
        "n_zero_reward_delivery": n_zero_reward_delivery,
        "avg_wait_s": round((sum(waits) / len(waits)) if waits else 0.0, 3),
        "n_remaining_pool": len(pool),
        "solve_time_ms": round(solve_ms, 2),
        "wall_seconds": round(float(wall_seconds), 3),
    }


def aggregate_summaries(summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """跨 seed 聚合（mean ± std，ddof=0）。"""
    import numpy as np

    out: dict[str, Any] = {}
    for k in AGG_KEYS:
        vals = [float(s.get(k, 0.0)) for s in summaries]
        if not vals:
            out[k] = {"mean": 0.0, "std": 0.0}
            continue
        arr = np.asarray(vals, dtype=float)
        out[k] = {
            "mean": round(float(arr.mean()), 6),
            "std": round(float(arr.std(ddof=0)), 6),
        }
    return out


def summary_csv_row(
    summary: Mapping[str, Any],
    *,
    arm: str,
    solver: str,
    cost_model: str,
    seed: int | str,
    agg: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """summary（或聚合 mean/std）→ summary.csv 的一行。

    给 ``agg`` 时输出均值行（seed 列记 ``mean``），否则输出单 seed 行。
    """
    row: dict[str, Any] = {
        "arm": arm,
        "solver": solver,
        "cost_model": cost_model,
        "seed": seed,
    }
    for k in AGG_KEYS:
        if agg is not None:
            row[k] = (agg.get(k) or {}).get("mean", 0.0)
        else:
            row[k] = summary.get(k, 0.0)
    return row
