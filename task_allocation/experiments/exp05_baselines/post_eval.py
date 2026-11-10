# -*- coding: utf-8 -*-
"""exp05 v3 统一事后评价器：两把尺子 + 独立几何重建重评。

对应 ``docs/exp05_redesign_2026-11-09.md`` §4。两条能力都**不改生产代码**：

1. :func:`recompute_two_rulers` —— 从落盘 ``solution.json`` 的逐周期 tours
   几何，按**实际飞行方向**逐腿重算两把尺子的总航程：
   ``d_DEM``（唯一生产口径，垂直切面三维有向）与 ``d_euclid3d``（历史朴素口径）。
   同一批交付解、两把尺子 ⇒ 直接检验「换尺子会不会改变方法间结论」（C2 主证据）。
   坐标必须用全精度（铁律：落盘不得 round）。

2. :class:`IndependentEvalHook` —— 运行期包装，逐周期捕获真正使用的
   ``SelectiveMUASProblem`` 实例并对 ``S_final`` 做几何重建重评
   （``methods/muas/independent_audit.py``，已实现但 exp05 从未接入）。
   手法与 ``tests/test_p3_independent_audit.py`` 一致：包装
   ``SelectiveMUASProblem.from_entities`` 捕获实例，包装
   ``rolling.horizon.run_selective_muas`` 拿 ``stage.solution``。
   不接触搜索链缓存、不改任何判定。异常**只记账不中断**，如实落盘。

门槛归属：``independent_audit`` 的完成率门槛是单周期口径，在有
``n_skip`` / ``n_shortfall`` 的正常周期会假失败，故 v3 只取其**独立数字**
与 ``consistency``；完成率 / 恒等式 / eliminating 由实验层硬断言承担
（见 ``claims.check_invariants``）。
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

__all__ = [
    "RulerPath",
    "recompute_two_rulers",
    "IndependentEvalHook",
    "summarize_audits",
]


@dataclass(frozen=True)
class RulerPath:
    """同一批交付解在两把尺子下的总航程（米）。"""

    path_m_audit: float
    path_m_euclid3d: float
    n_legs: int
    n_missing_end_nest: int
    n_tours: int

    @property
    def ruler_gap_m(self) -> float:
        return self.path_m_audit - self.path_m_euclid3d


def _xyz(obj: Any) -> tuple[float, float, float]:
    """兼容 ``[x,y,z]`` / ``{"x":..}`` / 三元组三种落盘形态。"""
    if isinstance(obj, Mapping):
        return (float(obj["x"]), float(obj["y"]), float(obj.get("z", 0.0)))
    seq = list(obj)
    return (float(seq[0]), float(seq[1]), float(seq[2]) if len(seq) > 2 else 0.0)


def recompute_two_rulers(
    solution: Mapping[str, Any],
    *,
    dem_distance_fn,
    euclid_distance_fn,
    nest_xyz: Mapping[int, tuple[float, float, float]],
) -> RulerPath:
    """逐周期、逐航次、逐腿按**有向**方向重算两把尺子的总航程。

    航次腿序：``UAV 起点 → task[0] → … → task[-1] → 终点机巢``。
    终点巢缺失时退回起飞机巢；两者都缺则跳过返航腿并计数（不静默补 0）。

    Args:
        solution: ``results_v3/<ID>/seed_<s>/solution.json`` 的解析结果。
        dem_distance_fn: ``make_distance_fn(estimator)``（有向，不可对称化）。
        euclid_distance_fn: ``make_distance_fn(None)``（三维直线）。
        nest_xyz: ``{nest_id: (x, y, z)}``，全精度。
    """
    task_xyz = {
        int(t["id"]): _xyz(t.get("xyz", t))
        for t in solution.get("tasks", [])
    }
    dem_total = 0.0
    euc_total = 0.0
    n_legs = 0
    n_missing_end = 0
    n_tours = 0

    def _leg(a: tuple[float, float, float], b: tuple[float, float, float]) -> None:
        nonlocal dem_total, euc_total, n_legs
        # 方向固定为实际飞行方向：绝不交换 a/b、绝不做对称近似。
        dem_total += float(dem_distance_fn(a, b))
        euc_total += float(euclid_distance_fn(a, b))
        n_legs += 1

    for cycle in solution.get("cycles", []):
        for tour in cycle.get("tours", []):
            n_tours += 1
            start = _xyz(tour["start"])
            end_nest = tour.get("end_nest_id")
            start_nest = tour.get("start_nest_id")
            target_nest = end_nest if end_nest is not None else start_nest
            dest = nest_xyz.get(int(target_nest)) if target_nest is not None else None
            if dest is None:
                n_missing_end += 1

            points: list[tuple[float, float, float]] = [start]
            for tid in tour.get("task_ids", []):
                p = task_xyz.get(int(tid))
                if p is None:
                    # 任务坐标缺失 ⇒ 该腿无法重算，显式记账而不是猜一个点
                    continue
                points.append(p)
            if dest is not None:
                points.append(dest)

            for a, b in zip(points, points[1:]):
                _leg(a, b)

    return RulerPath(
        path_m_audit=round(dem_total, 3),
        path_m_euclid3d=round(euc_total, 3),
        n_legs=n_legs,
        n_missing_end_nest=n_missing_end,
        n_tours=n_tours,
    )


class IndependentEvalHook:
    """逐周期捕获 problem 实例并对交付解做独立几何重建重评。

    用法::

        with IndependentEvalHook() as hook:
            records = horizon.run(...)
        summary_cols = summarize_audits(hook.reports)

    包装只在本进程、本 ``with`` 块内生效；退出时无条件恢复原符号。
    """

    def __init__(self) -> None:
        self.reports: list[dict[str, Any]] = []
        self._orig_fe: Any = None
        self._orig_run: Any = None
        self._problem_cls: Any = None
        self._horizon_mod: Any = None
        self._captured: list[Any] = []

    def __enter__(self) -> "IndependentEvalHook":
        from task_allocation.methods.muas.independent_audit import (
            independent_audit,
            reconstruct_solution,
        )
        from task_allocation.methods.muas.problem import SelectiveMUASProblem
        import task_allocation.methods.rolling.horizon as horizon_mod

        self._problem_cls = SelectiveMUASProblem
        self._horizon_mod = horizon_mod
        self._orig_fe = SelectiveMUASProblem.from_entities
        self._orig_run = horizon_mod.run_selective_muas
        orig_fe = self._orig_fe
        orig_run = self._orig_run
        captured = self._captured
        reports = self.reports

        # classmethod 包装：保存已绑定类的引用，调用时不再传 cls（与测试同一手法）。
        def _from_entities(*args, **kwargs):
            p = orig_fe(*args, **kwargs)
            captured.append(p)
            return p

        def _wrapped_run(*args, **kwargs):
            captured.clear()
            stage = orig_run(*args, **kwargs)
            problem = captured[-1] if captured else None
            if problem is None:
                reports.append({
                    "status": "no_problem_captured",
                    "note": "该周期未捕获 problem 实例（预构建解通道），"
                            "几何重建重评不可用；两把尺子重算仍独立有效",
                })
                return stage
            try:
                rebuilt = reconstruct_solution(stage.solution)
                rep = independent_audit(
                    rebuilt,
                    problem,
                    reported=dict(stage.diagnostics or {}),
                )
                reports.append({"status": "ok", **asdict(rep)})
            except Exception as exc:  # 技术债未清时只记账，不中断实验
                reports.append({
                    "status": "audit_error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "note": "审计 FAIL 先怀疑实现缺陷，不得改数据掩盖",
                })
            return stage

        SelectiveMUASProblem.from_entities = staticmethod(_from_entities)
        horizon_mod.run_selective_muas = _wrapped_run
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._problem_cls.from_entities = self._orig_fe
            self._horizon_mod.run_selective_muas = self._orig_run
        finally:
            self._captured.clear()
        return None


def summarize_audits(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """逐周期独立重评报告 → 汇总列（尾插进 metrics.json / summary.csv）。"""
    ok = [r for r in reports if r.get("status") == "ok"]
    errors = [
        r.get("error") for r in reports if r.get("status") == "audit_error"
    ]
    unavailable = sum(1 for r in reports if r.get("status") == "no_problem_captured")
    keys = ("fitness", "path_m", "late_s", "n_hard", "n_skip")
    totals = {
        k: round(sum(float(r.get(k, 0.0) or 0.0) for r in ok), 6) for k in keys
    }
    consistency_pairs = [
        c for r in ok for c in (r.get("consistency") or {}).values()
    ]
    equal_flags = [
        c.get("equal") for c in consistency_pairs if c.get("equal") is not None
    ]
    # 门槛 failures 分类记账（不升级为实验失败，但必须如实落盘）。
    # 「单周期完成率 < 1」是口径退化：candidate_task_ids 里有 skip/shortfall，
    # 跨周期完成率由 claims.check_invariants 判定；
    # 「coverage_audit 未开」若出现，说明配置漂移，须先查实现。
    def _classify(msg: str) -> str:
        if "完成率" in msg:
            return "single_cycle_completion_scope"
        if "coverage_audit" in msg:
            return "coverage_audit_off"
        if "gate_mode" in msg:
            return "gate_mode_off"
        if "口径不一致" in msg:
            return "consistency_mismatch"
        if "恒等式" in msg or "eliminating" in msg:
            return "invariant"
        return "other"

    failure_counts: dict[str, int] = {}
    failure_examples: list[str] = []
    for r in ok:
        for msg in (r.get("failures") or []):
            text = str(msg)
            key = _classify(text)
            failure_counts[key] = failure_counts.get(key, 0) + 1
            if len(failure_examples) < 5 and text not in failure_examples:
                failure_examples.append(text)
    return {
        "audit_failure_counts": failure_counts,
        "audit_failure_examples": failure_examples,
        "f_audit": totals["fitness"],
        "path_m_audit_from_problem": totals["path_m"],
        "late_s_audit": totals["late_s"],
        "n_hard_audit": int(totals["n_hard"]),
        "n_skip_audit": int(totals["n_skip"]),
        "n_audit_cycles": len(reports),
        "n_audit_ok": len(ok),
        "n_audit_unavailable": unavailable,
        "consistency_equal_all": bool(equal_flags) and all(equal_flags),
        "n_consistency_checked": len(equal_flags),
        "audit_errors": errors,
        # 供交叉校验：几何重算（post_eval）与 problem 重评应同量级；
        # 差异不为 0 时先查坐标/终点巢口径，不得改数字抹平。
        "audit_vs_geometry_note": (
            "path_m_audit 来自两把尺子几何重算；path_m_audit_from_problem "
            "来自 problem.path_cost 重评，二者口径不同须并列留档"
        ),
        "_math_sanity": math.isfinite(totals["fitness"]),
    }
