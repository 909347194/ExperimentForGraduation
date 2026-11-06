# -*- coding: utf-8 -*-
"""exp07_polish_accept 指标汇总与配对统计（本实验自持）。

口径说明（重要）：

* 主指标沿用 exp01 / exp05 的既有定义（完成率分母排除 UNREACHABLE 与
  停机时仍休眠的任务；按时率 = 最终交付集中按时比例；三维航程 = 各周期
  交付解 ``final_path_m`` 之和）。**本模块不发明新口径**，只做子集抽取。
* ``n_remain`` 是由恒等式 ``arrived = done + unreachable + dormant + remain``
  导出的诊断量，不是独立测量；真正独立观测的是 arrived / done /
  unreachable / dormant，以及 ``n_eliminating``（必须为 0 的铁律）。
* 采纳判据漂移从 ``CycleRecord.muas["cost_polish_accept"]`` 逐周期读取，
  该字段是 stage_runner 在**两视图都算**后落盘的，mode 只决定采纳哪个，
  因此 path 档也能观测到 f 视图的结果，两档可直接对账。

统计纪律：冒烟档只验证设计与配对口径，**结论不可外推正式档**；
配对 t 检验 n=3 时检验效能极低，不显著必须如实写「不显著」。
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from task_allocation.methods.rolling.horizon import cycle_record_has_activity

__all__ = [
    "PAIR_METRIC_KEYS",
    "write_json",
    "collect_run_metrics",
    "build_pair_report",
    "write_summary_csv",
]

#: 参与配对差值 / t 检验的指标键（f 档 − path 档）
PAIR_METRIC_KEYS = (
    "completion_rate_final",
    "on_time_rate_final",
    "total_path_m",
    "total_lateness_s",
    "n_hard_violations_total",
    "final_fitness_sum",
    "wall_seconds",
    "cost_polish_n_accepted",
    "cost_polish_n_drift",
)


def write_json(path: Path, payload: Any) -> Path:
    """写 UTF-8 JSON（缩进 2），自动建父目录。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _gate_sum(records: Sequence[Any], key: str) -> int:
    return sum(
        int(((r.muas or {}).get("gate_stats") or {}).get(key, 0) or 0)
        for r in records
    )


def collect_run_metrics(
    records: Sequence[Any],
    *,
    pool: Any,
    batch: Any,
    seed: int,
    mode: str,
    wall_seconds: float,
) -> dict[str, Any]:
    """汇总一次（档 × seed）滚动运行的指标、漂移计数与铁律检查。"""
    muas = [(r.muas or {}) for r in records]
    finished = pool.finished_tasks()
    completed = [t for t in finished if t.meta.get("completed_at") is not None]
    tasks = batch.all_tasks()

    n_arrived = len(tasks)
    n_completed = len(completed)
    n_unreachable = sum(len(getattr(r, "unreachable_ids", []) or []) for r in records)
    _last = records[-1] if records else None
    n_dormant_end = (
        int((getattr(_last, "pool_counts", {}) or {}).get("n_dormant", 0))
        + len(getattr(_last, "dormant_ids", []) or [])
    ) if _last else 0
    n_remain = max(0, n_arrived - n_completed - n_unreachable - n_dormant_end)

    # 主指标口径（与 exp01/exp05 同源）
    n_final_denom = max(0, n_arrived - n_unreachable - n_dormant_end)
    completion_rate_final = n_completed / n_final_denom if n_final_denom else 0.0
    n_late = sum(len(getattr(r, "late_ids", []) or []) for r in records)
    on_time_rate_final = (
        (n_completed - n_late) / n_completed if n_completed else 0.0
    )
    all_lateness = [
        float(v) for r in records for v in (r.lateness_by_task or {}).values()
    ]
    total_lateness_s = sum(all_lateness)
    total_path_m = sum(_num(m.get("final_path_m")) for m in muas)
    final_fitness_sum = sum(_num(m.get("final_fitness")) for m in muas)
    n_hard_violations_total = sum(int(m.get("final_n_hard", 0) or 0) for m in muas)

    # ── 采纳判据双视图对账 ────────────────────────────────────────
    # stage_runner 对「交付后成本 polish 的每个候选」都算 path/f 两视图；
    # muas["cost_polish_accept"] 保存**最后一次**候选的结果。
    drift_rows = [m.get("cost_polish_accept") or {} for m in muas]
    drift_rows = [d for d in drift_rows if d]
    n_drift = sum(1 for d in drift_rows if bool(d.get("drift")))
    n_accepted = sum(1 for d in drift_rows if bool(d.get("accepted")))
    n_path_view_true = sum(1 for d in drift_rows if bool(d.get("path_view")))
    n_f_view_true = sum(1 for d in drift_rows if bool(d.get("f_view")))
    mode_values = {
        str(m.get("cost_polish_accept_mode")) for m in muas
        if m.get("cost_polish_accept_mode") is not None
    }
    drift_modes = {str(d.get("mode")) for d in drift_rows if d.get("mode") is not None}

    # ── 铁律检查（区分「硬失败」与「如实记录」）───────────────────
    has_elim_probe = any("n_eliminating" in m for m in muas)
    n_eliminating = sum(int(m.get("n_eliminating", 0) or 0) for m in muas)
    # diagnostics 里没有 n_eliminating 字段（该字段只是 independent_audit 的
    # 入参）。此处 0 是「TaskPool 已无超期出池路径」的**结构性事实**，
    # 不是独立测量；如实标注，不把它包装成「铁律已独立验证」。
    eliminating_basis = "diagnostics_probe" if has_elim_probe else "structural_zero_unverified"
    violations: list[str] = []
    if n_eliminating != 0:
        violations.append(f"eliminating（非不可达）= {n_eliminating}，须为 0")
    if mode_values and mode_values != {mode}:
        violations.append(f"生效 mode 不一致：{sorted(mode_values)} != [{mode}]")
    if drift_modes and drift_modes != {mode}:
        violations.append(f"drift 记录的 mode 不一致：{sorted(drift_modes)} != [{mode}]")
    if n_hard_violations_total != 0:
        violations.append(f"硬违约累计 {n_hard_violations_total} != 0")

    return {
        "seed": int(seed),
        "mode": str(mode),
        "n_cycles": len(records),
        "n_active_cycles": sum(1 for r in records if cycle_record_has_activity(r)),
        "n_arrived": n_arrived,
        "n_completed": n_completed,
        "n_unreachable": n_unreachable,
        "n_dormant_end": n_dormant_end,
        "n_remain": n_remain,
        "n_final_denom": n_final_denom,
        "completion_rate_final": round(completion_rate_final, 6),
        "on_time_rate_final": round(on_time_rate_final, 6),
        "n_late": n_late,
        "total_lateness_s": round(total_lateness_s, 3),
        "total_path_m": round(total_path_m, 3),
        "final_fitness_sum": round(final_fitness_sum, 6),
        "n_hard_violations_total": n_hard_violations_total,
        "n_infeasible_delivery": sum(
            len(getattr(r, "infeasible_ids", []) or []) for r in records
        ),
        "n_capacity_violations": sum(
            len(getattr(r, "capacity_violations", []) or []) for r in records
        ),
        "wall_seconds": round(float(wall_seconds), 3),
        # ── 采纳判据对账 ──
        "cost_polish_accept_mode": str(mode),
        "cost_polish_eps_f": next(
            (m.get("cost_polish_eps_f") for m in muas
             if m.get("cost_polish_eps_f") is not None), None
        ),
        "cost_polish_n_candidates_observed": len(drift_rows),
        "cost_polish_n_accepted": n_accepted,
        "cost_polish_n_drift": n_drift,
        "cost_polish_n_path_view_true": n_path_view_true,
        "cost_polish_n_f_view_true": n_f_view_true,
        "cost_polish_mode_values": sorted(mode_values),
        # ── L3a / L3b 分账（P3-b 字段，逐周期求和）──
        "l3a_geometric_actions": sum(int(m.get("l3a_geometric_actions", 0) or 0) for m in muas),
        "l3b_relocate_actions": sum(int(m.get("l3b_relocate_actions", 0) or 0) for m in muas),
        "n_task_to_uav_changes": sum(int(m.get("n_task_to_uav_changes", 0) or 0) for m in muas),
        "l3b_enabled": [m.get("l3b_enabled") for m in muas],
        "l3b_ungated_monotone_relocate": sum(
            int(m.get("l3b_ungated_monotone_relocate", 0) or 0) for m in muas
        ),
        "n_skip_eval_final": sum(int(m.get("n_skip_eval", 0) or 0) for m in muas),
        # ── 铁律 ──
        "n_eliminating": n_eliminating,
        "n_eliminating_probe_present": has_elim_probe,
        "n_eliminating_basis": eliminating_basis,
        "eliminating_independently_verified": False,
        "invariant_violations": violations,
        "invariants_passed": not violations,
        "invariants_scope": (
            "已查：n_hard=0、生效 mode 一致、eliminating 结构性为 0；"
            "未查：independent_audit 的独立重建重评（本实验未接入，见 caveats）"
        ),
    }


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs: Sequence[float]) -> float:
    """样本标准差（ddof=1）。"""
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _betacf(a: float, b: float, x: float) -> float:
    """不完全 beta 的连分式（Numerical Recipes 标准实现）。"""
    maxit, eps, fpmin = 300, 3.0e-14, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """正则化不完全 beta 函数 I_x(a, b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _two_sided_p(t: float, df: int) -> float | None:
    """Student t 双尾 p 的**精确**计算（不依赖 scipy、不用正态近似）。

    ``p = I_{df/(df+t^2)}(df/2, 1/2)``，用正则化不完全 beta 的连分式求值。
    本环境 venv 未装 scipy，故自带数值实现；避免把「p 不可用」误写成
    「不显著」，也避免用正态近似冒充精确检验。
    """
    if df <= 0 or not math.isfinite(t):
        return None
    return float(_betai(df / 2.0, 0.5, df / (df + t * t)))


def build_pair_report(
    per_mode: Mapping[str, Sequence[dict[str, Any]]],
    *,
    seeds: Sequence[int],
    profile: str,
    config_diff: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """两档同 seed 配对差值 + 配对 t 检验（禁写「独立收益」）。"""
    path_rows = {int(r["seed"]): r for r in per_mode.get("path", [])}
    f_rows = {int(r["seed"]): r for r in per_mode.get("f", [])}
    paired_seeds = [s for s in seeds if s in path_rows and s in f_rows]
    missing = [s for s in seeds if s not in paired_seeds]

    metrics: dict[str, Any] = {}
    for key in PAIR_METRIC_KEYS:
        diffs = [
            float(f_rows[s].get(key, 0.0) or 0.0) - float(path_rows[s].get(key, 0.0) or 0.0)
            for s in paired_seeds
        ]
        n = len(diffs)
        mean_d = _mean(diffs)
        sd_d = _std(diffs)
        t_stat = mean_d / (sd_d / math.sqrt(n)) if n >= 2 and sd_d > 0 else None
        p_val = _two_sided_p(t_stat, n - 1) if t_stat is not None and n >= 2 else None
        if n < 2:
            p_reason = "n_pairs_lt_2"
        elif sd_d == 0.0:
            p_reason = "zero_variance_no_test"
        elif t_stat is None:
            p_reason = "t_undefined"
        elif p_val is None:
            p_reason = "p_undefined"
        else:
            p_reason = "computed"
        metrics[key] = {
            "per_seed_path": [float(path_rows[s].get(key, 0.0) or 0.0) for s in paired_seeds],
            "per_seed_f": [float(f_rows[s].get(key, 0.0) or 0.0) for s in paired_seeds],
            "per_seed_diff_f_minus_path": [round(d, 6) for d in diffs],
            "n_pairs": n,
            "mean_diff": round(mean_d, 6),
            "std_diff": round(sd_d, 6),
            "t_stat": round(t_stat, 6) if t_stat is not None else None,
            "p_two_sided": round(p_val, 6) if p_val is not None else None,
            "p_available": p_val is not None,
            "p_method": "student_t_incomplete_beta_exact",
            "p_reason": p_reason,
            "significant_at_0_05": (bool(p_val < 0.05) if p_val is not None else None),
        }

    quality_keys = [k for k in PAIR_METRIC_KEYS if k != "wall_seconds"]
    all_quality_identical = all(
        all(abs(d) < 1e-12 for d in metrics[k]["per_seed_diff_f_minus_path"])
        for k in quality_keys
    )
    verdict = (
        "两档在所有质量指标上逐 seed 完全一致；本冒烟档无法区分 path 与 f，"
        "既不能据此翻转默认，也不能写成「两档等价」。"
        if all_quality_identical else
        "两档存在逐 seed 差异；须结合 effect size 与配对 p 值判读，"
        "主效应与交互项并列，禁写「独立收益」。"
    )

    invariant_failures = [
        {"mode": mode, "seed": int(r["seed"]), "violations": r["invariant_violations"]}
        for mode, rows in per_mode.items()
        for r in rows
        if not r.get("invariants_passed", True)
    ]

    return {
        "profile": profile,
        "seeds": list(seeds),
        "paired_seeds": paired_seeds,
        "unpaired_seeds": missing,
        "direction": "diff = f 档 − path 档（f 档 = 按完整目标 f 裁决）",
        "config_diff_between_modes": config_diff or {},
        "metrics": metrics,
        "verdict": verdict,
        "all_quality_metrics_identical": all_quality_identical,
        "invariant_failures": invariant_failures,
        "all_invariants_passed": not invariant_failures,
        "caveats": [
            "本环境 venv 未装 scipy；p 值由正则化不完全 beta 精确计算 Student t 双尾分布，"
            "没有用正态近似冒充精确检验，也没有把 p 不可用写成「不显著」。",
            "cost_polish_accept 是每周期**最后一次**候选的快照，不是全候选累计；"
            "低频候选场景会低估判据分歧次数。要精确统计须扩展 stage_runner 落盘累计计数"
            "（属代码改动，本轮未做）。",
            "n_eliminating 不是 diagnostics 字段：0 是「TaskPool 已无超期出池路径」的"
            "结构性事实，非独立测量；独立验收须走 methods/muas/independent_audit.py。",
            "冒烟档只验证实验设计与统计口径，结论不可外推正式档。",
        ],
        "notes": [
            "配对差值与交互项并列报告，禁写「独立收益」。",
            "n=3 时配对 t 检验 df=2，检验效能极低；不显著必须如实写不显著，不得表述为「无差异」。",
            "零方差时 t 检验不适用（p_reason=zero_variance_no_test），不是 p 值缺失。",
        ],
    }


def write_summary_csv(
    path: Path,
    per_mode: Mapping[str, Sequence[dict[str, Any]]],
) -> Path:
    """逐（档 × seed）主指标表，便于人工核对。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = (
        "mode", "seed", "n_arrived", "n_completed", "n_unreachable",
        "n_remain", "completion_rate_final", "on_time_rate_final",
        "total_path_m", "total_lateness_s", "final_fitness_sum",
        "n_hard_violations_total", "cost_polish_n_accepted",
        "cost_polish_n_drift", "l3a_geometric_actions",
        "l3b_relocate_actions", "wall_seconds", "invariants_passed",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for mode, rows in per_mode.items():
            for r in rows:
                w.writerow({**r, "mode": mode})
    return path
