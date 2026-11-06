# -*- coding: utf-8 -*-
"""criteria_drift.py — 四层判据漂移测量（P0 基线 → P1/P2 收敛）。

来源：定稿 §8 P0「另需报告一份『四层判据漂移率』基线供 P1/P2 使用」。

测量对象是 plan §4.5 的四处「另写判据」漂移点，均为 :mod:`spec` 中
具名视图对：

1. **时间窗**：``window_deadline_model``（task_window 元组口径）vs
   ``window_deadline_decode``（直读 task.latest）；
2. **时序**：``sequence_decode_violated``（单调判定）vs
   ``sequence_model_inversions``（逆序对计数）；
3. **机巢容量三份账**：门禁 ``nest_capacity_gate_loads`` vs 模型
   ``check_nest_capacity`` vs MCF（``solve_end_nest_assignment`` 的容量可行
   性，采样按「MCF 输出必可行」对照）；
4. **采纳判据**：``accept_cost_polish_view``（航程分量门）vs
   ``accept_f_view``（f + 约束向量不退化，P3 目标判据）。

所有函数只读采样数据、返回分账字典；不改任何判定、不进生产路径
（实验/测试脚本消费）。
"""

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
