# -*- coding: utf-8 -*-
"""exp05 v3 主张定义、门槛断言与三态结论汇总。

对应 ``docs/exp05_redesign_2026-11-09.md`` §1 / §2 / §6。
本模块只做「读落盘结果 → 出结论」，不跑算法、不写数值产物。

硬约束（改这里等于改论文口径，须同步改定稿）：
- C1 的完成率是**门槛**不是成绩；跨方法不排序；
- C2 的主判据是「两把尺子下 M0−D0 差值之差」，M0 vs D0 只作描述性；
  C2 **不检验求解端距离模型**，不得据此声称「求解端必须用 DEM」；
- 描述性档（C4、尺子绝对差）只给估计量与区间，**不产出三态结论**；
- 任何 ``inconclusive`` 都写「本实验未检出差异」；
- 禁写清单随报告一起落盘，防止下游只摘有利数字。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from paired_stats import CAVEATS, paired_difference, paired_stats, verdict

__all__ = [
    "ClaimSpec",
    "CLAIMS",
    "RED_LINES",
    "check_invariants",
    "build_claims_report",
]

#: 写作红线（与定稿 §1「不主张清单」逐条一致）
RED_LINES = (
    "禁写「性能提升」「全面优于现有方法」「SOTA」「显著优于基线」",
    "禁写「MCF 无用 / 可删」「全局最优」「独立收益」「协同效应」「双双验证」",
    "完成率恒 1 是正确性不变量，禁止当成绩排序",
    "p 不显著须如实声明；零方差写 zero_variance_no_test，不写「不显著」",
    "冒烟 / mid 结论不可外推正式档；跨实验禁配对 t 检验",
    "作废数字禁引用：λ 的 −13% / −15.8pp、方案 B −2.4%、旧求解耗时 335 s/seed",
    "「Δf=0.1·Δ航程」是线性缩放，不是两条独立证据",
    "MCF 只能写「给定路径下的条件最优」，不能写全局最优",
    "禁写「本实验证明求解端必须用 DEM」：C2 只检验评价端口径",
)


@dataclass(frozen=True)
class ClaimSpec:
    """一条主张的检验规格。

    Attributes:
        id:              C1..C5。
        statement:       论文可写的原句。
        kind:            gate | ruler_pair | method_pair | consistency | index。
        pair:            method_pair 时的 (A, B)；差值恒为 A − B。
        metrics:         (指标键, 预期方向) 列表；方向 negative = 越小越好。
        descriptive_only: True ⇒ 只报差值与 p，不出三态结论（复合档/异口径）。
        note:            必须随结论一起出现的限制说明。
    """

    id: str
    statement: str
    kind: str
    pair: tuple[str, str] | None = None
    metrics: tuple[tuple[str, str], ...] = ()
    descriptive_only: bool = False
    note: str = ""


CLAIMS: tuple[ClaimSpec, ...] = (
    ClaimSpec(
        id="C1",
        statement="完成是硬约束、成本才是目标；未完成必须显式归因，不静默丢弃",
        kind="gate",
        note="该不变量主要由共享滚动外壳与任务池保证，不是 DMDE 独有贡献；"
             "基线未通过须单列归因，不得从表中静默删除",
    ),
    ClaimSpec(
        id="C2",
        statement="唯一评价尺子是垂直切面 DEM 三维有向航程；同一批交付解"
                  "换用三维直线重算会改变方法间差值，因此跨方法必须统一评价口径",
        kind="ruler_pair",
        metrics=(("ruler_effect_m", "any"),),
        note="主判据 = 两把尺子下 M0−D0 差值之差（difference-in-differences），"
             "只检验是否非零、不预设方向；尺子绝对差是定义性事实，只报量级、"
             "不作显著性主张。本实验**不检验求解端距离模型**：M0 vs D0 是"
             "复合档，只作描述性对照、不作单因素归因",
    ),
    ClaimSpec(
        id="C3",
        statement="交付解可独立重评，评价与交付一致（自报与几何重建重评逐位一致）",
        kind="consistency",
        note="independent_audit 的完成率门槛是单周期口径，v3 只取其独立数字与"
             "consistency；完成率/恒等式/eliminating 由实验层硬断言承担",
    ),
    ClaimSpec(
        id="C4",
        statement="在完成前提下压低成本与迟到，且代价必须显式报告",
        kind="method_pair",
        pair=("M0", "B2"),
        metrics=(
            ("f_audit", "negative"),
            ("path_m_audit", "negative"),
            ("on_time_rate_final", "positive"),
            ("reward_yield", "positive"),
        ),
        descriptive_only=True,
        note="M0 与构造式基线的差异是整个第二阶段的求解范式（含解码修复链、"
             "交付后精修、终点机巢分层），不是单因素；且 M0 求解代价高约"
             "3 个数量级，必须与评估次数/墙钟并列报告",
    ),
    ClaimSpec(
        id="C5",
        statement="分层解码与组件去留由证据裁决，而非先验宣称",
        kind="index",
        note="证据索引：docs/stage2_final_scheme_2026-11-06.md §11.8–§11.12；"
             "exp07–exp10 正式档所有 p>0.19，禁写显著收益",
    ),
)


def check_invariants(summary: Mapping[str, Any]) -> dict[str, Any]:
    """第 0 层门槛：完成率 / 恒等式 / eliminating / 硬违约。

    恒等式 ``arrived = done + unreachable + remain``；``n_remaining_pool``
    = 停机时池中全部剩余任务（**已包含**末态休眠 dormant_end，休眠任务
    不出池），故 dormant_end 只是 remain 的归因子集，不能再加一次。
    """
    n_arrived = int(summary.get("n_arrived", 0) or 0)
    n_done = int(summary.get("n_completed", 0) or 0)
    n_unreachable = int(summary.get("n_unreachable", 0) or 0)
    n_dormant = int(summary.get("n_dormant_end", 0) or 0)
    n_remain = int(summary.get("n_remaining_pool", 0) or 0)
    checks = {
        "completion_rate_is_one": abs(
            float(summary.get("completion_rate_final", 0.0) or 0.0) - 1.0
        ) <= 1e-9,
        "identity_closed": n_arrived == n_done + n_unreachable + n_remain,
        "eliminating_zero": int(summary.get("n_eliminated", 0) or 0) == 0,
        "hard_violations_zero": int(
            summary.get("n_hard_violations_total", 0) or 0
        ) == 0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "terms": {
            "n_arrived": n_arrived,
            "n_done": n_done,
            "n_unreachable": n_unreachable,
            "n_dormant_end": n_dormant,
            "n_remaining_pool": n_remain,
        },
        "unreachable_split": {
            "geo": int(summary.get("n_unreachable_geo", 0) or 0),
            "service": int(summary.get("n_unreachable_service", 0) or 0),
        },
    }


def build_claims_report(
    rows: Mapping[str, Mapping[int, Mapping[str, Any]]],
    *,
    seeds: Sequence[int],
    primary_methods: Sequence[str],
    profile: str,
) -> dict[str, Any]:
    """逐主张出配对统计与三态结论。

    Args:
        rows: ``{method_id: {seed: summary_dict}}``，summary_dict 来自
            ``metrics.json``（须含重评列，见 post_eval.py）。
        primary_methods: 进主表的方法 ID（C1 门槛只对这些方法强制）。
        profile: smoke / mid / formal；冒烟与 mid 结论一律标不可外推。
    """
    seed_list = [int(s) for s in seeds]
    report: dict[str, Any] = {
        "experiment": "exp05_baselines",
        "design": "docs/exp05_redesign_2026-11-09.md",
        "profile": profile,
        "seeds": seed_list,
        "primary_methods": list(primary_methods),
        "caveats": list(CAVEATS),
        "red_lines": list(RED_LINES),
        "claims": {},
        "gate": {},
        "pairs": {},
        "unpaired_seeds": [],
        "all_pairs_complete": True,
        "primary_gates_passed": True,
    }

    # ---- C1：门槛层 ----------------------------------------------------
    gate: dict[str, Any] = {}
    for method, by_seed in rows.items():
        gate[method] = {
            int(s): check_invariants(v) for s, v in sorted(by_seed.items())
        }
        for seed, res in gate[method].items():
            if not res["passed"] and method in primary_methods:
                report["primary_gates_passed"] = False
    report["gate"] = gate
    failed = [
        {"method": m, "seed": s, "checks": r["checks"], "terms": r["terms"]}
        for m, by_seed in gate.items()
        for s, r in by_seed.items()
        if not r["passed"]
    ]
    report["claims"]["C1"] = {
        "statement": CLAIMS[0].statement,
        "kind": "gate",
        "note": CLAIMS[0].note,
        "primary_gates_passed": report["primary_gates_passed"],
        "failed_cells": failed,
        "wording": (
            "所有可完成任务均被完成，未完成有显式归因，恒等式闭合；"
            "该不变量由共享滚动外壳承担"
            if report["primary_gates_passed"]
            else "主表方法存在门槛未通过项，须逐格归因，不得静默删除"
        ),
        "caveats": list(CAVEATS),
    }

    # ---- 配对工具 ------------------------------------------------------
    def _pair(a: str, b: str, key: str):
        ra = {int(s): v for s, v in rows.get(a, {}).items()}
        rb = {int(s): v for s, v in rows.get(b, {}).items()}
        diffs, paired, unpaired = paired_difference(ra, rb, key, seed_list)
        if unpaired:
            report["all_pairs_complete"] = False
            report["unpaired_seeds"] = sorted(
                set(report["unpaired_seeds"]) | set(unpaired)
            )
        return paired_stats(diffs), paired, unpaired

    # ---- C2：两把尺子（主判据 = 差值之差）+ M0 vs D0（描述性） ----------
    c2: dict[str, Any] = {
        "statement": CLAIMS[1].statement,
        "kind": "ruler_pair",
        "note": CLAIMS[1].note,
        "caveats": list(CAVEATS),
        "primary_criterion": "ruler_effect",
        "ruler_gap": {},
        "method_pair_descriptive": {},
        "ruler_bias_by_method": {},
    }
    # ① 尺子绝对差：DEM 有向航程 ≥ 三维直线是定义性事实，只报量级，
    #    对其做显著性检验没有信息量 ⇒ 不产出三态结论。
    for method in ("M0", "D0", "B2", "B1"):
        if method not in rows:
            continue
        gaps = []
        for s in seed_list:
            row = rows[method].get(int(s))
            if row is not None:
                gaps.append(
                    float(row.get("path_m_audit", 0.0) or 0.0)
                    - float(row.get("path_m_euclid3d", 0.0) or 0.0)
                )
        st = paired_stats(gaps)
        c2["ruler_gap"][method] = {
            "per_seed": st["per_seed"],
            "stats": st,
            "descriptive_only": True,
            "note": "同一解换尺子的绝对差（口径量级），定义性事实，不作显著性主张",
        }
    # ② 两把尺子下 M0 vs D0 的差值方向是否一致（描述性，不宣称单因素）
    for key in ("path_m_audit", "path_m_euclid3d", "f_audit"):
        st, paired, unpaired = _pair("M0", "D0", key)
        c2["method_pair_descriptive"][key] = {
            "stats": st, "paired_seeds": paired, "unpaired_seeds": unpaired,
            "descriptive_only": True,
        }
    c2["ruler_changes_direction"] = _direction_change(
        c2["method_pair_descriptive"].get("path_m_audit", {}),
        c2["method_pair_descriptive"].get("path_m_euclid3d", {}),
    )
    # ③ 主判据：同一 seed 下，两把尺子各自算 M0−D0，再取两者之差。
    #    检验「换尺子是否改变方法间差值」（非零即可，不预设方向）。
    dem_deltas: list[float] = []
    euc_deltas: list[float] = []
    paired_ruler: list[int] = []
    unpaired_ruler: list[int] = []
    for s in seed_list:
        r0 = rows.get("M0", {}).get(s)
        rd = rows.get("D0", {}).get(s)
        if r0 is None or rd is None:
            unpaired_ruler.append(s)
            continue
        dem_deltas.append(
            float(r0.get("path_m_audit", 0.0) or 0.0)
            - float(rd.get("path_m_audit", 0.0) or 0.0)
        )
        euc_deltas.append(
            float(r0.get("path_m_euclid3d", 0.0) or 0.0)
            - float(rd.get("path_m_euclid3d", 0.0) or 0.0)
        )
        paired_ruler.append(s)
    if unpaired_ruler:
        report["all_pairs_complete"] = False
        report["unpaired_seeds"] = sorted(
            set(report["unpaired_seeds"]) | set(unpaired_ruler)
        )
    effect = [a - b for a, b in zip(dem_deltas, euc_deltas)]
    effect_stats = paired_stats(effect)
    c2["ruler_effect"] = {
        "definition": "(M0−D0)@d_DEM − (M0−D0)@d_euclid3d，逐 seed 配对",
        "per_seed": [round(v, 6) for v in effect],
        "delta_dem_m_per_seed": [round(v, 6) for v in dem_deltas],
        "delta_euclid3d_m_per_seed": [round(v, 6) for v in euc_deltas],
        "paired_seeds": paired_ruler,
        "unpaired_seeds": unpaired_ruler,
        "stats": effect_stats,
        "verdict": verdict(effect_stats, expected="any"),
        "note": "只检验换尺子是否改变方法间差值（差值非零），不预设方向；"
                "M0/D0 是复合档，本量不得解读为求解端距离模型的单因素效应",
    }
    # ④ 尺子偏差是否因方法而异：d_DEM/d_euclid3d 比值，M0 与 D0 逐 seed 比较。
    for method in ("M0", "D0"):
        ratios = []
        for s in seed_list:
            row = rows.get(method, {}).get(s)
            if row is None:
                continue
            e = float(row.get("path_m_euclid3d", 0.0) or 0.0)
            if e > 0.0:
                ratios.append(float(row.get("path_m_audit", 0.0) or 0.0) / e)
        c2["ruler_bias_by_method"][method] = {
            "ratio_per_seed": [round(v, 9) for v in ratios],
            "mean_ratio": round(sum(ratios) / len(ratios), 9) if ratios else None,
            "descriptive_only": True,
        }
    bias_diff: list[float] = []
    for s in seed_list:
        r0 = rows.get("M0", {}).get(s)
        rd = rows.get("D0", {}).get(s)
        if r0 is None or rd is None:
            continue
        e0 = float(r0.get("path_m_euclid3d", 0.0) or 0.0)
        ed = float(rd.get("path_m_euclid3d", 0.0) or 0.0)
        if e0 > 0.0 and ed > 0.0:
            bias_diff.append(
                float(r0.get("path_m_audit", 0.0) or 0.0) / e0
                - float(rd.get("path_m_audit", 0.0) or 0.0) / ed
            )
    c2["ruler_bias_by_method"]["M0_minus_D0_ratio"] = {
        "per_seed": [round(v, 9) for v in bias_diff],
        "stats": paired_stats(bias_diff),
        "descriptive_only": True,
    }
    report["claims"]["C2"] = c2

    # ---- C3：一致性 ----------------------------------------------------
    consistency: dict[str, Any] = {}
    for method, by_seed in rows.items():
        cells = []
        for s, row in sorted(by_seed.items()):
            cells.append({
                "seed": int(s),
                "consistency_equal_all": row.get("consistency_equal_all"),
                "n_hard_audit": row.get("n_hard_audit"),
                "path_gap_m": round(
                    float(row.get("total_path_m", 0.0) or 0.0)
                    - float(row.get("path_m_audit", 0.0) or 0.0), 3
                ),
                "audit_errors": row.get("audit_errors", []),
                "audit_failure_counts": row.get("audit_failure_counts", {}),
            })
        consistency[method] = cells
    m0_cells = consistency.get("M0", [])
    # metrics.consistency_equal_all 落盘为 int(0|1)，不能用 `is True` 判；
    # 否则所有通过的一致性都会被误报为失败（审计 FAIL 先怀疑实现缺陷）。
    m0_all_equal = bool(m0_cells) and all(
        bool(c["consistency_equal_all"]) for c in m0_cells
    )
    report["claims"]["C3"] = {
        "statement": CLAIMS[2].statement,
        "kind": "consistency",
        "note": CLAIMS[2].note,
        "m0_all_equal": m0_all_equal,
        "per_method": consistency,
        "failure_counts_by_method": {
            m: {
                k: sum(int(c["audit_failure_counts"].get(k, 0)) for c in cells)
                for k in {
                    key
                    for c in cells
                    for key in (c.get("audit_failure_counts") or {})
                }
            }
            for m, cells in consistency.items()
        },
        "failure_classification": {
            "single_cycle_completion_scope": "单周期口径退化（skip/shortfall），跨周期完成率由 C1 判定",
            "coverage_audit_off": "配置漂移：审计分账被关，须先查实现缺陷",
            "consistency_mismatch": "自报与重评不一致，须查实现，不得改数据抹平",
        },
        "wording": (
            "本文方法自报与几何重建重评逐位一致"
            if m0_all_equal
            else "本文方法存在不一致或重评未接入，须先查实现缺陷"
        ),
        "caveats": list(CAVEATS),
    }

    # ---- C4：质量—代价（描述性） --------------------------------------
    c4: dict[str, Any] = {
        "statement": CLAIMS[3].statement,
        "kind": "method_pair",
        "note": CLAIMS[3].note,
        "descriptive_only": True,
        "caveats": list(CAVEATS),
        "pairs": {},
    }
    for other in ("B2", "B1"):
        entry: dict[str, Any] = {}
        for key, expected in CLAIMS[3].metrics:
            st, paired, unpaired = _pair("M0", other, key)
            entry[key] = {
                "stats": st,
                "paired_seeds": paired,
                "unpaired_seeds": unpaired,
                "expected": expected,
                # 描述性档只给估计量与区间：产出 verdict 会被下游误读成
                # 「方法优劣的结论」，与 C4 的 descriptive_only 口径冲突。
                "descriptive_only": True,
            }
        for key in ("n_evals_total", "wall_seconds"):
            st, paired, unpaired = _pair("M0", other, key)
            entry[f"cost:{key}"] = {
                "stats": st, "paired_seeds": paired, "unpaired_seeds": unpaired,
                "descriptive_only": True,
            }
        c4["pairs"][other] = entry
    report["claims"]["C4"] = c4

    # ---- C5：证据索引 --------------------------------------------------
    report["claims"]["C5"] = {
        "statement": CLAIMS[4].statement,
        "kind": "index",
        "note": CLAIMS[4].note,
        "evidence": [
            "docs/stage2_final_scheme_2026-11-06.md §11.8（采纳判据统一 f / L3b 默认开）",
            "§11.10（2×2 收益拆分，所有 p>0.19）",
            "§11.11（终点机巢分层，MCF 是给定路径下条件最优）",
            "§11.12（容量压力，同一现象在更大任务规模下复现，非第二条独立证据）",
        ],
        "caveats": list(CAVEATS),
    }

    if profile != "formal":
        report["external_validity"] = (
            f"profile={profile}：冒烟/mid 档只验链路与配置纯度，结论不可外推正式档"
        )
    return report


def _direction_change(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool | None:
    """两把尺子下 M0−D0 的配对差均值符号是否相反；无数据返回 None。"""
    ma = (a.get("stats") or {}).get("mean")
    mb = (b.get("stats") or {}).get("mean")
    if ma is None or mb is None:
        return None
    return bool(float(ma) * float(mb) < 0.0)
