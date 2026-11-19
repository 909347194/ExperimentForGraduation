# -*- coding: utf-8 -*-
"""criteria_drift.py — 多套约束判据的一致性测量。

职责：比较同一对象在模型/解码/门禁等不同路径下的判定结果（时间窗、时序、机巢容量、
采纳判据），返回不一致条数。
边界：只读采样数据，返回分账字典；不改判定、不进生产路径，供实验/测试消费。
相关：muas/constraints/spec.py（各判据具名视图）、nest_capacity.py。"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from task_allocation.methods.muas.constraints import spec as constraint_spec
from task_allocation.methods.muas.constraints.nest_capacity import check_nest_capacity

__all__ = [
    "window_drift",
    "sequence_drift",
    "nest_capacity_drift",
    "acceptance_drift",
]


def window_drift(tasks: Iterable[Any]) -> dict[str, Any]:
    """时间窗两视图漂移：逐任务比较 deadline 字段口径。"""
    n = 0
    mismatch = 0
    examples: list[dict[str, Any]] = []
    for t in tasks:
        n += 1
        m = constraint_spec.window_deadline_model(t)
        d = constraint_spec.window_deadline_decode(t)
        if m != d:
            mismatch += 1
            if len(examples) < 5:
                examples.append({"task": getattr(t, "id", None), "model": m, "decode": d})
    return {
        "n_tasks": n,
        "n_mismatch": mismatch,
        "drift_rate": (mismatch / n) if n else 0.0,
        "examples": examples,
    }


def sequence_drift(samples: Iterable[Sequence[tuple[Any, float]]]) -> dict[str, Any]:
    """时序两视图漂移：逐采样比较「是否违约」判定（粒度差单列）。"""
    n = 0
    verdict_mismatch = 0
    n_violations = 0
    for pairs in samples:
        n += 1
        dv = constraint_spec.sequence_decode_violated(pairs)
        mv = constraint_spec.sequence_model_inversions(pairs)
        if dv != (mv > 0):
            verdict_mismatch += 1
        if mv > 0:
            n_violations += 1
    return {
        "n_samples": n,
        "n_violations": n_violations,
        "n_verdict_mismatch": verdict_mismatch,
        "drift_rate": (verdict_mismatch / n) if n else 0.0,
    }


def nest_capacity_drift(
    problem: Any,
    samples: Iterable[
        tuple[Mapping[int, Sequence[int]], Mapping[int, int | None]]
    ],
) -> dict[str, Any]:
    """机巢容量两账漂移：门禁 loads 账 vs 模型 check_nest_capacity 账。

    每个采样 = ``(routes, end_nests)``；比较两者判出的「超容巢集合」。
    另单列 ``explicit_none`` 采样数（end_nest 显式 None 的语义差：门禁账
    回落 home、模型账记 departure）。
    """
    n = 0
    mismatch = 0
    explicit_none = 0
    examples: list[dict[str, Any]] = []
    for routes, end_nests in samples:
        n += 1
        if any(v is None for v in end_nests.values()):
            explicit_none += 1
        # 门禁账：loads[巢] > capacity 判超容
        loads = constraint_spec.nest_capacity_gate_loads(problem, routes, end_nests)
        gate_overflow = set()
        for nid, load in loads.items():
            if nid is None:
                continue
            nest = problem.nest_by_id.get(nid)
            if nest is not None and load > int(nest.capacity):
                gate_overflow.add(nid)
        # 模型账：check_nest_capacity（assignments = z_ub）
        assignments = {
            u.id: end_nests.get(u.id, u.nest_id)
            for u in (problem.fleet_uavs or problem.uavs)
            if routes.get(u.id)
        }
        rep = check_nest_capacity(
            problem.fleet_uavs or problem.uavs, problem.nests, assignments
        )
        model_overflow = {
            nid for nid in rep.violations if rep.per_nest[nid]["overflow"] > 0
        }
        if gate_overflow != model_overflow:
            mismatch += 1
            if len(examples) < 5:
                examples.append({
                    "gate": sorted(gate_overflow),
                    "model": sorted(model_overflow),
                    "explicit_none": explicit_none,
                })
    return {
        "n_samples": n,
        "n_mismatch": mismatch,
        "drift_rate": (mismatch / n) if n else 0.0,
        "n_explicit_none": explicit_none,
        "examples": examples,
    }


def acceptance_drift(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """采纳判据漂移：cost_polish 视图（航程门）vs f 视图（目标口径）。

    每条记录需含：``old_exec`` / ``new_exec`` / ``cand_n_hard`` / ``cur_n_hard``
    / ``cand_excess`` / ``cur_excess`` / ``cand_path`` / ``cur_path`` / ``eps``
    / ``cand_f`` / ``cur_f`` / ``cand_late`` / ``cur_late``。
    """
    n = 0
    mismatch = 0
    accept_path_only = 0
    examples: list[dict[str, Any]] = []
    for r in records:
        n += 1
        v_path = constraint_spec.accept_cost_polish_view(
            r["old_exec"], r["new_exec"], r["cand_n_hard"], r["cur_n_hard"],
            r["cand_excess"], r["cur_excess"], r["cand_path"], r["cur_path"], r["eps"],
        )
        v_f = constraint_spec.accept_f_view(
            r["cand_f"], r["cur_f"], r.get("eps_f", r["eps"]),
            cand_n_hard=r["cand_n_hard"], cur_n_hard=r["cur_n_hard"],
            cand_exec=r["new_exec"], cur_exec=r["old_exec"],
            cand_late=r.get("cand_late", 0.0), cur_late=r.get("cur_late", 0.0),
        )
        if v_path != v_f:
            mismatch += 1
            if v_path and not v_f:
                accept_path_only += 1
            if len(examples) < 5:
                examples.append({k: (sorted(v) if isinstance(v, set) else v)
                                 for k, v in r.items()})
    return {
        "n_records": n,
        "n_mismatch": mismatch,
        "drift_rate": (mismatch / n) if n else 0.0,
        "n_accept_path_only": accept_path_only,
        "examples": examples,
    }
