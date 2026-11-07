# -*- coding: utf-8 -*-
"""exp09_mcf_layers 指标汇总与配对统计（本实验自持）。

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

#: 交付解**质量**指标：只有这些能进 all_quality_identical 与 verdict。
#: 过程计数不同 ≠ 解不同，禁止混入（审查 B1）。
QUALITY_METRIC_KEYS = (
    "completion_rate_final",
    "on_time_rate_final",
    "total_path_m",
    "total_lateness_s",
    "n_hard_violations_total",
    "final_fitness_sum",
)

#: 过程/诊断量：照常配对落盘，但不参与「解是否相同」的判定。
DIAGNOSTIC_METRIC_KEYS = (
    "cost_polish_n_accepted",
    "cost_polish_n_drift",
    "cost_polish_n_f_only_gain",
)

#: 纯观测量（墙钟含缓存预热，不作判据效应证据，见审查 B4）。
OBSERVATION_KEYS = ("wall_seconds",)

#: 参与配对差值 / t 检验的全部指标键（f 档 − path 档）
PAIR_METRIC_KEYS = QUALITY_METRIC_KEYS + DIAGNOSTIC_METRIC_KEYS + OBSERVATION_KEYS

#: 「逐 seed 相同」的容差：主指标落盘已 round 到 1e-3~1e-6，
#: 这里显式声明为绝对容差，不再对已 round 的列表用 1e-12 伪装精确零。
ZERO_DIFF_TOL = 1e-9


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
    expected_accept_mode: str | None = None,
) -> dict[str, Any]:
    """汇总一次（档 × seed）滚动运行的指标、漂移计数与铁律检查。

    ``mode`` 是本次运行的**对照档标签**（采纳判据对照里是 path/f，L3b
    对照里是 off/on）；``expected_accept_mode`` 才是 diagnostics 里
    ``cost_polish_accept_mode`` 的期望值。两者默认相同，L3b 对照须显式
    传 ``"path"``，否则「生效 mode 一致」检查会拿错对象误报（本轮修复）。
    """
    expect_accept = str(expected_accept_mode) if expected_accept_mode is not None else str(mode)
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
    # 恒等式残差：**不钳位**。旧版 max(0, …) 会把「分子大于分母」这类
    # 审计失配悄悄抹平；负值必须原样暴露并触发硬失败（审查 A3）。
    n_remain_raw = n_arrived - n_completed - n_unreachable - n_dormant_end

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
    # A1 修复证据：f 档局部搜索里「f 更优、但航程未下降」的移动数。
    # >0 说明候选域里确实存在只有 f 判据会采纳的解（旧实现恒 0）。
    n_f_only_gain = sum(
        int(m.get("cost_polish_n_f_only_gain", 0) or 0) for m in muas
    )

    # ── 铁律检查（硬断言口径，定稿 §4.3 第 1 条）────────────────
    # 完成率（分母排除 UNREACHABLE / 停机休眠）必须恒 1；恒等式残差必须
    # 非负；eliminating 必须 0。旧版只观察不判定，等于自己给自己打分。
    has_elim_probe = any("n_eliminating" in m for m in muas)
    n_eliminating = sum(int(m.get("n_eliminating", 0) or 0) for m in muas)
    # diagnostics 里没有 n_eliminating 字段（该字段只是 independent_audit 的
    # 入参）。此处 0 是「TaskPool 已无超期出池路径」的**结构性事实**，
    # 不是独立测量；如实标注，不把它包装成「铁律已独立验证」。
    eliminating_basis = "diagnostics_probe" if has_elim_probe else "structural_zero_unverified"
    violations: list[str] = []
    if n_eliminating != 0:
        violations.append(f"eliminating（非不可达）= {n_eliminating}，须为 0")
    if n_final_denom > 0 and abs(completion_rate_final - 1.0) > 1e-9:
        violations.append(
            f"完成率 {completion_rate_final:.6f} != 1（分母 {n_final_denom}"
            " 已排除不可达/休眠）——完成是约束，此为该次运行的硬失败"
        )
    if n_remain_raw < 0:
        violations.append(
            "arrived 恒等式破缺："
            f"{n_arrived} < {n_completed}+{n_unreachable}+{n_dormant_end}"
            "（旧版 max(0,…) 钳位会掩盖此失配）"
        )
    if mode_values and mode_values != {expect_accept}:
        violations.append(
            f"生效采纳判据不一致：{sorted(mode_values)} != [{expect_accept}]"
        )
    if drift_modes and drift_modes != {expect_accept}:
        violations.append(
            f"drift 记录的采纳判据不一致：{sorted(drift_modes)} != [{expect_accept}]"
        )
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
        "n_remain": n_remain_raw,
        "identity_residual": n_remain_raw,
        "n_final_denom": n_final_denom,
        "completion_rate_final": round(completion_rate_final, 6),
        "on_time_rate_final": round(on_time_rate_final, 6),
        "n_late": n_late,
        "total_lateness_s": round(total_lateness_s, 3),
        "total_path_m": round(total_path_m, 3),
        "final_fitness_sum": round(final_fitness_sum, 6),
        "n_hard_violations_total": n_hard_violations_total,
        # ── P4 exp09：终点机巢对照的两个直接指标（定稿 §4.1）──
        # return_leg_m = 每周期交付解 Σ(末任务→终点巢的有向代价)；
        # nest_overflow = check_nest_capacity 逐巢 overflow 之和（0 为可行）。
        # 两者都是 stage_runner 的纯观测诊断，不参与任何判定。
        "total_return_leg_m": round(
            sum(_num(m.get("return_leg_m")) for m in muas), 3
        ),
        "nest_overflow_total": int(sum(
            int(m.get("nest_overflow", 0) or 0) for m in muas
        )),
        # 逐周期终点巢分配签名（字符串，仅供人工核对四档是否真的分叉；
        # 不进配对 t 检验）。若 nn 与 nm 的签名逐位相同，说明本场景
        # 无容量竞争 ⇒ 层 3 投影与 MCF 退化为同一分配。
        "end_nests_signature": "|".join(
            ",".join(f"{u}>{n}" for u, n in sorted(
                (m.get("end_nests") or {}).items()
            ))
            for m in muas
        ),
        "n_infeasible_delivery": sum(
            len(getattr(r, "infeasible_ids", []) or []) for r in records
        ),
        "n_capacity_violations": sum(
            len(getattr(r, "capacity_violations", []) or []) for r in records
        ),
        "wall_seconds": round(float(wall_seconds), 3),
        # ── 采纳判据对账 ──
        "cost_polish_accept_mode": str(expect_accept),
        "cost_polish_eps_f": next(
            (m.get("cost_polish_eps_f") for m in muas
             if m.get("cost_polish_eps_f") is not None), None
        ),
        "cost_polish_n_candidates_observed": len(drift_rows),
        "cost_polish_n_accepted": n_accepted,
        "cost_polish_n_drift": n_drift,
        "cost_polish_n_f_only_gain": n_f_only_gain,
        "cost_polish_n_path_view_true": n_path_view_true,
        "cost_polish_n_f_view_true": n_f_view_true,
        "cost_polish_mode_values": sorted(mode_values),
        # ── L3a / L3b 分账（P3-b 字段，逐周期求和）──
        "cost_polish_n_mcf_resolve": sum(
            int(m.get("cost_polish_n_mcf_resolve", 0) or 0) for m in muas
        ),
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
            "硬断言：完成率=1（分母排除不可达/休眠）、arrived 恒等式残差>=0、"
            "n_eliminating=0、n_hard=0、生效 mode 一致。"
            "未接入：independent_audit 的几何重建重评（problem 实例属核心层"
            "局部对象，实验层无法取得；见 caveats 技术债）。"
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
    modes: tuple[str, str] = ("path", "f"),
    caveats: Sequence[str] | None = None,
    subject: str = "path 与 f",
) -> dict[str, Any]:
    """同 seed 配对差值 + 配对 t 检验（禁写「独立收益」）。

    ``modes`` / ``caveats`` / ``subject`` 供同一实验内的第二组对照
    （L3b 开/关）复用统计链路，默认仍为采纳判据对照、逐字不变。
    """
    a_label, b_label = modes
    path_rows = {int(r["seed"]): r for r in per_mode.get(a_label, [])}
    f_rows = {int(r["seed"]): r for r in per_mode.get(b_label, [])}
    paired_seeds = [s for s in seeds if s in path_rows and s in f_rows]
    missing = [s for s in seeds if s not in paired_seeds]

    metrics: dict[str, Any] = {}
    raw_diffs: dict[str, list[float]] = {}
    for key in PAIR_METRIC_KEYS:
        diffs = [
            float(f_rows[s].get(key, 0.0) or 0.0) - float(path_rows[s].get(key, 0.0) or 0.0)
            for s in paired_seeds
        ]
        raw_diffs[key] = diffs  # 未 round 的原始差值，供零差判定（审查 B2）
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

    # 只用质量指标、只用未 round 的原始差值、显式容差（审查 B1/B2）。
    quality_keys = list(QUALITY_METRIC_KEYS)
    all_quality_identical = all(
        all(abs(d) < ZERO_DIFF_TOL for d in raw_diffs[k])
        for k in quality_keys
    )
    verdict = (
        f"两档在所有质量指标上逐 seed 完全一致；本次跑批无法区分 {subject}，"
        "既不能据此翻转默认，也不能写成「两档等价」。"
        "须同时检查 cost_polish_n_f_only_gain：若恒 0，说明候选域里本就没有"
        "判据分歧解（不是样本量问题）。"
        if all_quality_identical else
        "两档存在逐 seed 质量差异；须结合 effect size 与配对 p 值判读，"
        "配对差值不得写成「独立收益」。本实验是两档单因素设计，没有 2×2，"
        "因此没有交互项可算。"
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
        "direction": (
            "diff = f 档 − path 档（f 档 = 按完整目标 f 裁决）"
            if modes == ("path", "f")
            else f"diff = {b_label} 档 − {a_label} 档"
        ),
        "modes": list(modes),
        "config_diff_between_modes": config_diff or {},
        "metrics": metrics,
        "verdict": verdict,
        "all_quality_metrics_identical": all_quality_identical,
        "quality_metric_keys": quality_keys,
        "zero_diff_tolerance": ZERO_DIFF_TOL,
        "invariant_failures": invariant_failures,
        "all_invariants_passed": not invariant_failures,
        "caveats": list(caveats) if caveats is not None else [
            "本环境 venv 未装 scipy；p 值由正则化不完全 beta 精确计算 Student t 双尾分布，"
            "没有用正态近似冒充精确检验，也没有把 p 不可用写成「不显著」。",
            "A1 修复（本轮）：f 档局部搜索改用 λ·航程 + w_late·迟到 作目标，"
            "不再被「航程必须严格下降」的成本门截断；path 档走原代码路径、"
            "与改动前逐位一致。cost_polish_n_f_only_gain 是该修复是否生效的直接证据。",
            "cost_polish_accept 有三重截断：① 只在 post_cost_polish 有采纳时产生；"
            "② 一周期内多次局部移动被聚合成**一个**最终候选，中间移动的两视图"
            "分歧不可见；③ 每周期只留最后一个候选快照。"
            "cost_polish_n_candidates_observed 实为「有候选的周期数」而非候选数，"
            "低频场景会低估判据分歧次数。",
            "两档执行顺序按 seed 交替，以抵消 estimator/场景缓存预热；但 wall_seconds "
            "仍非纯判据效应，只作观测、不参与质量判定（审查 B4）。",
            "n_eliminating 不是 diagnostics 字段：0 是「TaskPool 已无超期出池路径」的"
            "结构性事实，非独立测量；实验层已把完成率/恒等式/eliminating 改为硬断言，"
            "但 independent_audit 的几何重建重评仍未接入——problem 实例是核心层局部"
            "对象，须由 RollingHorizon 暴露审计钩子后才能调用（技术债，未擅自改核心）。",
            "冒烟档只验证实验设计与统计口径，结论不可外推正式档。",
        ],
        "notes": [
            "配对差值不得写成「独立收益」；两档单因素设计没有 2×2 交互项。",
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
        "total_return_leg_m", "nest_overflow_total",
        "cost_polish_n_mcf_resolve",
        "n_hard_violations_total", "cost_polish_n_accepted",
        "cost_polish_n_drift", "cost_polish_n_f_only_gain",
        "l3a_geometric_actions",
        "l3b_relocate_actions", "wall_seconds", "invariants_passed",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for mode, rows in per_mode.items():
            for r in rows:
                w.writerow({**r, "mode": mode})
    return path
