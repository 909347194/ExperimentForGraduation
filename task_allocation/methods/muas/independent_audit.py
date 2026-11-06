# -*- coding: utf-8 -*-
"""independent_audit.py — 独立事后评价器（定稿 §4.3 边界 C「独立交付验收」）。

**独立性口径**：本模块不经过搜索链、不读任何缓存。具体地——

- 不接触 ``MUASAssignmentEvaluator._cache``、``cost_history``、
  ``best_fitness``、``polish`` / ``memetic`` 档案等搜索期状态；
- 从交付解 ``S_final`` 的 **tours 几何**重建一个全新的
  :class:`AllocationSolution`，**丢弃全部派生 meta**（``feasibility`` /
  ``fitness_weights`` / ``gate_stats`` / 台账标记），只保留
  ``candidate_task_ids`` 与 tours 的几何字段；
- 用 :class:`SelectiveMUASProblem` 的公开评价入口重新
  ``evaluate`` / ``scalar_fitness`` / ``hard_stats`` / ``soft_violation`` /
  ``path_cost``，得到一套完全独立于搜索链的数字。

§4.3 五条门槛中，本模块实现前三条（可代码断言）：

1. 完成率 100% + ``arrived = done + unreachable + remain`` 恒等式成立 +
   ``eliminating``（非不可达）= 0；
2. ``gate_mode()`` 恒 True、``coverage_audit``（旧名
   ``lexicographic_coverage``）开；``coverage_gate`` 因与 §3.3「门禁只作
   终态校验与补漏」存在张力且生产默认 False，设为**可选严格档**
   （``require_coverage_gate=True`` 才强制），不静默改默认；
3. 重建重评数字与搜索链自报（``MUASStageResult.diagnostics``）**逐位一致**。

第 4 条（全量 pytest + exp01 审计 25/25）与第 5 条（与 method-phase1
同 seed 配对 t 检验）属测试/实验层，由调用方执行，不在本模块内。

⚠️ 跨周期口径：完成率的分母 ``arrived`` 与 ``unreachable`` 名单来自实验层
的任务生命周期记录（不是单周期解能推出的）。调用方应显式传入
``arrived_ids`` / ``unreachable_ids``；未传时退化为**单周期口径**
（``arrived = candidate_task_ids``、不可达为空），并在报告中标注。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from task_allocation.common.types import AllocationSolution, UAVTour

__all__ = [
    "IndependentAuditReport",
    "reconstruct_solution",
    "independent_audit",
    "assert_audit_passed",
]


@dataclass(frozen=True)
class IndependentAuditReport:
    """独立重评结果 + 门槛判定。``passed`` 等价于 ``failures`` 为空。"""

    # 完成口径（跨周期名单由调用方提供；单周期退化见模块 docstring）
    n_arrived: int
    n_done: int
    n_unreachable: int
    n_remain: int
    n_eliminating: int
    completion_rate: float
    on_time_rate: float
    scope: str                       # "cycle" | "provided"
    # 重建重评数字
    fitness: float
    n_hard: int
    hard_violation: float
    path_m: float
    late_s: float
    n_skip: int
    # 门禁口径
    gate_mode: bool
    coverage_audit: bool
    coverage_gate: bool | None
    # 与搜索链自报的逐位一致性
    consistency: Mapping[str, dict[str, Any]]
    failures: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.failures


def reconstruct_solution(solution: AllocationSolution) -> AllocationSolution:
    """从 ``S_final`` 的 tours 几何重建新解，**丢弃全部派生 meta**。

    ``executed_task_ids`` 由 tours 重新汇总（不信任原字段），
    ``f1_reward`` / ``f2_cost`` / ``feasible`` 回到默认值，等待
    ``problem.evaluate`` 重填。起点快照（``start_*``）属于解的几何输入，
    原样保留以保证航程口径一致；``est_cost`` / ``est_reward`` / tour 的
    ``meta`` 属评价派生量，一律不复制。
    """
    tours = [
        UAVTour(
            uav_id=int(t.uav_id),
            task_ids=list(t.task_ids),
            end_nest_id=t.end_nest_id,
            start_x=float(t.start_x),
            start_y=float(t.start_y),
            start_z=float(t.start_z),
            start_nest_id=t.start_nest_id,
        )
        for t in solution.tours
    ]
    executed = sorted({int(tid) for t in tours for tid in t.task_ids})
    return AllocationSolution(
        tours=tours,
        candidate_task_ids=list(solution.candidate_task_ids),
        executed_task_ids=executed,
        meta={},
    )


def independent_audit(
    solution: AllocationSolution,
    problem: Any,
    *,
    reported: Mapping[str, Any] | None = None,
    stage_config: Any | None = None,
    arrived_ids: Sequence[int] | None = None,
    unreachable_ids: Sequence[int] | None = None,
    n_eliminating: int = 0,
    require_coverage_gate: bool = False,
) -> IndependentAuditReport:
    """独立重评 + §4.3 前三条门槛。

    Args:
        solution: 交付解 ``S_final``。
        problem: :class:`SelectiveMUASProblem`（或其等价接口）。本函数只调用
            其公开评价入口，不接触任何求解器/评价器缓存。
        reported: ``MUASStageResult.diagnostics``（或其子集）。给定时做
            第 3 条「逐位一致」断言；不给定则跳过该项。
        stage_config: 给定时读取 ``coverage_gate``（仅记录，默认不强制）。
        arrived_ids: 跨周期 ``arrived`` 名单；None ⇒ 单周期口径。
        unreachable_ids: 不可达任务名单（从任务池出池，分母排除）。
        n_eliminating: 非不可达的「淘汰」计数；>0 即门槛失败（铁律）。
        require_coverage_gate: True 时强制 ``coverage_gate`` 为 True。
    """
    # ---- 1) 重建 + 重评（独立数字） ---------------------------------
    rebuilt = reconstruct_solution(solution)
    problem.evaluate(rebuilt)
    fitness = float(problem.scalar_fitness(rebuilt))
    n_hard, hard_violation = problem.hard_stats(rebuilt)
    path_m = float(problem.path_cost(rebuilt))
    late_s = float(problem.soft_violation(rebuilt))
    n_skip = int((rebuilt.meta.get("gate_stats") or {}).get("n_skip", 0) or 0)
    gate_mode = bool(problem.gate_mode(rebuilt))
    coverage_audit = bool(getattr(problem.config, "coverage_audit", False))
    coverage_gate = (
        None if stage_config is None
        else bool(getattr(stage_config, "coverage_gate", False))
    )

    # ---- 2) 完成口径 -------------------------------------------------
    if arrived_ids is None:
        arrived = [int(t) for t in solution.candidate_task_ids]
        scope = "cycle"
    else:
        arrived = [int(t) for t in arrived_ids]
        scope = "provided"
    unreachable = {int(t) for t in (unreachable_ids or ())} & set(arrived)
    # 不可达优先：UNREACHABLE 任务已从任务池出池，既不计 done 也不计分母。
    # 显式去重，防止「同一 id 同时出现在 executed 与 unreachable」时把
    # 分子算成 > 分母（完成率 > 1 的伪结果）。
    done_set = (
        {int(t) for t in rebuilt.executed_task_ids} & set(arrived)
    ) - unreachable
    n_done = len(done_set)
    n_remain = len(set(arrived) - done_set - unreachable)
    denom = len(arrived) - len(unreachable)
    completion_rate = (n_done / denom) if denom > 0 else 1.0

    # 按时率：从重算出的 lateness 表独立统计（不是搜索链自报）
    lateness = (rebuilt.meta.get("feasibility") or {}).get("lateness") or {}
    on_time = sum(
        1 for tid in done_set
        if float(lateness.get(int(tid), 0.0) or 0.0) <= 0.0
    )
    on_time_rate = (on_time / n_done) if n_done > 0 else 1.0

    # ---- 3) 与搜索链自报数字逐位一致 --------------------------------
    consistency: dict[str, dict[str, Any]] = {}
    if reported is not None:
        pairs = {
            "final_fitness": (reported.get("final_fitness"), fitness),
            "final_path_m": (reported.get("final_path_m"), path_m),
            "final_late_s": (reported.get("final_late_s"), late_s),
            "final_n_hard": (reported.get("final_n_hard"), int(n_hard)),
            "final_excess_m": (reported.get("final_excess_m"), float(hard_violation)),
            "n_skip_final": (reported.get("n_skip_final"), n_skip),
            "n_executed": (reported.get("n_executed"), len(rebuilt.executed_task_ids)),
        }
        for key, (old, new) in pairs.items():
            if old is None:
                consistency[key] = {"reported": None, "recomputed": new,
                                    "equal": None, "note": "reported 缺失"}
            else:
                consistency[key] = {"reported": old, "recomputed": new,
                                    "equal": bool(old == new)}

    # ---- 4) 三条门槛 -------------------------------------------------
    failures: list[str] = []
    if denom > 0 and completion_rate < 1.0 - 1e-12:
        failures.append(
            f"完成率 {completion_rate:.6f} < 1（分母 {denom} 已排除不可达）"
        )
    if len(arrived) != n_done + len(unreachable) + n_remain:
        failures.append(
            "arrived 恒等式不成立："
            f"{len(arrived)} != {n_done}+{len(unreachable)}+{n_remain}"
        )
    if int(n_eliminating) != 0:
        failures.append(
            f"eliminating（非不可达）= {int(n_eliminating)}，须为 0（铁律）"
        )
    if not gate_mode:
        failures.append("gate_mode() 非 True（覆盖门禁为唯一主口径）")
    if not coverage_audit:
        failures.append("coverage_audit 未开（旧名 lexicographic_coverage）")
    if require_coverage_gate and coverage_gate is not True:
        failures.append(
            f"严格档要求 coverage_gate=True，实际 {coverage_gate!r}"
        )
    for key, rec in consistency.items():
        if rec["equal"] is False:
            failures.append(
                f"口径不一致 {key}: 自报 {rec['reported']!r} vs "
                f"重建重评 {rec['recomputed']!r}"
            )

    return IndependentAuditReport(
        n_arrived=len(arrived),
        n_done=n_done,
        n_unreachable=len(unreachable),
        n_remain=n_remain,
        n_eliminating=int(n_eliminating),
        completion_rate=float(completion_rate),
        on_time_rate=float(on_time_rate),
        scope=scope,
        fitness=fitness,
        n_hard=int(n_hard),
        hard_violation=float(hard_violation),
        path_m=path_m,
        late_s=late_s,
        n_skip=n_skip,
        gate_mode=gate_mode,
        coverage_audit=coverage_audit,
        coverage_gate=coverage_gate,
        consistency=consistency,
        failures=tuple(failures),
    )


def assert_audit_passed(report: IndependentAuditReport) -> None:
    """把门槛判定转成断言，供 pytest / 实验脚本直接调用。"""
    if not report.passed:
        raise AssertionError(
            "独立交付验收未通过：\n  - " + "\n  - ".join(report.failures)
        )
