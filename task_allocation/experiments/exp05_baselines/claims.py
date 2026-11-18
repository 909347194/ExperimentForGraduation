# -*- coding: utf-8 -*-
"""exp05 E1 主张定义、门槛断言与三态结论汇总（2026-11-13 收窄）。

对应 ``docs/exp05_e1_design_2026-11-13.md`` §1 / §3 / §5 / §6。
本模块只做「读落盘结果 → 出结论」，不跑算法、不写数值产物。

与 v3（C1–C5 五主张 / 两把尺子）的差异：
- **一条主张 E1**：「不是随便一个可行分配也能做到整体有效性」。
  唯一推断主判据 = ``M0 − B1`` 在**同一把** DEM 有向三维航程尺下的
  每完成航程（``path_m_audit / n_completed``）；删去 v3 的 C2
  ``ruler_effect`` 两把尺子主判据与 C5 组件去留索引。
- C1 / C3 降级为**门槛层断言**（完成率 / 恒等式 / eliminating / 硬违约 /
  独立重评一致性），不是成绩、不排序。
- B2 / D0 只作**描述性梯度**，只给估计量与区间，不产出 verdict。

硬约束（改这里等于改论文口径，须同步改定稿）：
- E1 只证「整体有效性不是平凡可达」，**禁写性能提升 / 优于 / SOTA**；
- 主判据唯一，B2/D0 与所有绝对量一律描述性、不出三态结论；
- 唯一比较尺 = DEM 有向三维航程；f_audit / 自报尺 / euclid3d 留档不进主表；
- 任何 ``inconclusive`` 都写「本实验未检出差异」；门槛反例逐格归因、不静默删除；
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

#: 写作红线（与定稿 §1「不主张清单」/ §6 逐条一致）
RED_LINES = (
    "E1 只证「整体有效性不是平凡可达」，禁写「性能提升」「全面优于现有方法」"
    "「SOTA」「显著优于基线」；主判据被检出 ≠ 排行榜结论",
    "禁写「M0 完成率更高」：完成率是正确性不变量，由共享滚动外壳承担，不排序",
    "禁把 M0−B1 差异归因到单组件：B1/B2 与 M0 是整个求解范式差异，D0 是复合档",
    "禁写「本实验证明求解端必须用 DEM」：D0 连 ETA/时间窗动力学一起换",
    "唯一比较尺 = path_m_audit（DEM 有向三维航程）；f_audit/自报尺/"
    "path_m_euclid3d 混尺子或不同量纲，留档不进主文比较",
    "「Δf=0.1·Δ航程」是线性缩放，不是两条独立证据",
    "完成约束有 seed 反例（滚动早停遗留可完成任务），禁写「完成率恒 100%」",
    "p 不显著须如实声明；零方差写 zero_variance_no_test，不写「不显著」；"
    "inconclusive 只能写「本实验未检出差异」，禁写「无差异 / 等价」",
    "冒烟 / mid 结论不可外推正式档；跨实验禁配对 t 检验",
    "旧 7 臂与 v1/v2 数字一律不进主文；作废数字禁引用（λ 的 −13% / "
    "−15.8pp、方案 B −2.4%、旧求解耗时 335 s/seed、v3 ruler_effect p=0.202）",
    "描述性档（B2/D0/绝对量/代价）只给估计量与区间，不作方法优劣结论",
)


@dataclass(frozen=True)
class ClaimSpec:
    """一条主张的检验规格（E1 收窄后只有一条）。

    Attributes:
        id:              E1。
        statement:       论文可写的原句。
        kind:            ``effectiveness_pair``（单一推断主判据 + 描述性梯度）。
        pair:            主判据配对 ``(A, B)``；差值恒为 A − B。
        primary_metric:  主判据指标键（派生列，逐 seed 归一后配对）。
        descriptive:     描述性梯度对照方法 ID。
        note:            必须随结论一起出现的限制说明。
    """

    id: str
    statement: str
    kind: str
    pair: tuple[str, str] = ("M0", "B1")
    primary_metric: str = "path_m_audit_per_completion"
    descriptive: tuple[str, ...] = ("B2", "D0")
    note: str = ""


CLAIMS: tuple[ClaimSpec, ...] = (
    ClaimSpec(
        id="E1",
        statement="整体有效性不是随便一个可行分配也能做到：在完成作为硬约束、"
                  "成本作为目标的前提下，把随机可行分配、收益/代价比贪心插入、"
                  "以及同架构但把规划距离换成平面近似，与本文方法放在同一把"
                  "垂直切面 DEM 有向三维航程尺下重评，前者达不到本文方法同时"
                  "守住完成约束与压低每完成航程的水平",
        kind="effectiveness_pair",
        pair=("M0", "B1"),
        primary_metric="path_m_audit_per_completion",
        descriptive=("B2", "D0"),
        note="唯一推断主判据 = M0−B1 的每完成 DEM 航程（path_m_audit / "
             "n_completed，逐 seed 归一后配对），expected=negative（M0 更省）；"
             "检验的是「平凡可达性是否被否定」，不是优越性宣称。B2（贪心）与 "
             "D0（同架构换平面近似，复合档）只作描述性梯度，不出三态结论；"
             "B1/B2 与 M0 是整个第二阶段求解范式差异，禁止单因素归因。"
             "完成率/恒等式/eliminating/独立重评一致性由门槛层硬断言承担，"
             "不作成绩排序；完成约束有 seed 反例须逐格归因",
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
    primary_pair: Sequence[str] = ("M0", "B1"),
    descriptive_pairs: Sequence[str] = ("B2", "D0"),
    primary_metric: str = "path_m_audit_per_completion",
    primary_ruler: str = "dem_vertical_section_3d",
    min_abs_dz: float = 0.5,
) -> dict[str, Any]:
    """逐主张（E1 一条）出配对统计与三态结论 + 门槛 + 一致性 + 参照。

    Args:
        rows: ``{method_id: {seed: summary_dict}}``，summary_dict 来自
            ``metrics.json``（须含 ``path_m_audit`` 等重评列，见 post_eval.py）。
        primary_methods: 进主表的方法 ID（门槛只对这些方法强制）。
        profile: smoke / mid / formal；冒烟与 mid 结论一律标不可外推。
        primary_pair: 唯一推断主判据的 ``(A, B)``。
        descriptive_pairs: 描述性梯度对照（不出 verdict）。
        primary_metric: 主判据派生列键。
        primary_ruler: 唯一比较尺（落盘声明）。
        min_abs_dz: ``supported`` 所需效应量下界。
    """
    seed_list = [int(s) for s in seeds]
    a, b = str(primary_pair[0]), str(primary_pair[1])
    report: dict[str, Any] = {
        "experiment": "exp05_baselines",
        "design": "docs/exp05_e1_design_2026-11-13.md",
        "design_version": "e1_single_ruler_4arm_2026-11-13",
        "profile": profile,
        "seeds": seed_list,
        "methods": [str(m) for m in primary_methods],
        "primary_methods": list(primary_methods),
        "primary_pair": [a, b],
        "primary_metric": primary_metric,
        "primary_ruler": primary_ruler,
        "caveats": list(CAVEATS),
        "red_lines": list(RED_LINES),
        "claims": {},
        "gate": {},
        "consistency": {},
        "unpaired_seeds": [],
        "all_pairs_complete": True,
        "primary_gates_passed": True,
    }

    # ---- 第 0 层门槛 ---------------------------------------------------
    gate: dict[str, Any] = {}
    for method, by_seed in rows.items():
        gate[method] = {
            int(s): check_invariants(v) for s, v in sorted(by_seed.items())
        }
        for res in gate[method].values():
            if not res["passed"] and method in primary_methods:
                report["primary_gates_passed"] = False
    report["gate"] = gate
    report["gate_failed_cells"] = [
        {"method": m, "seed": s, "checks": r["checks"], "terms": r["terms"]}
        for m, by_seed in gate.items()
        for s, r in by_seed.items()
        if not r["passed"]
    ]

    # ---- 独立重评一致性（门槛层断言，不是成绩）-------------------------
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
    m0_cells = consistency.get(a, [])
    # metrics.consistency_equal_all 落盘为 int(0|1)，不能用 `is True` 判。
    m0_all_equal = bool(m0_cells) and all(
        bool(c["consistency_equal_all"]) for c in m0_cells
    )
    report["consistency"] = {
        "m0_all_equal": m0_all_equal,
        "anchor_method": a,
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
            "single_cycle_completion_scope": "单周期口径退化（skip/shortfall），跨周期完成率由门槛层判定",
            "coverage_audit_off": "配置漂移：审计分账被关，须先查实现缺陷",
            "consistency_mismatch": "自报与重评不一致，须查实现，不得改数据抹平",
        },
        "wording": (
            "本文方法自报与几何重建 DEM 重评逐位一致"
            if m0_all_equal
            else "本文方法存在不一致或重评未接入，须先查实现缺陷"
        ),
    }

    # ---- 配对工具（缺配对一律显式登记为门槛失败）----------------------
    def _register_unpaired(unpaired: Sequence[int]) -> None:
        if unpaired:
            report["all_pairs_complete"] = False
            report["unpaired_seeds"] = sorted(
                set(report["unpaired_seeds"]) | {int(s) for s in unpaired}
            )

    def _pair(x: str, y: str, key: str):
        rx = {int(s): v for s, v in rows.get(x, {}).items()}
        ry = {int(s): v for s, v in rows.get(y, {}).items()}
        diffs, paired, unpaired = paired_difference(rx, ry, key, seed_list)
        _register_unpaired(unpaired)
        return paired_stats(diffs), paired, unpaired

    # ---- E1：唯一推断主判据 -------------------------------------------
    spec = CLAIMS[0]
    primary_norm = _normalized_pair(
        rows, a, b, seed_list, "path_m_audit", "n_completed"
    )
    _register_unpaired(primary_norm["unpaired_seeds"])
    primary_verdict = verdict(
        primary_norm["stats"], expected="negative", min_dz=float(min_abs_dz)
    )
    e1_primary = {
        "definition": f"{a}−{b} 的 {primary_metric}"
                      f"（path_m_audit / n_completed，逐 seed 归一后配对）",
        "ruler": "dem_vertical_section_3d",
        "metric": primary_metric,
        "pair": [a, b],
        "per_seed": primary_norm["per_seed"],
        "paired_seeds": primary_norm["paired_seeds"],
        "unpaired_seeds": primary_norm["unpaired_seeds"],
        "stats": primary_norm["stats"],
        "verdict": primary_verdict,
        "expected": "negative",
        "descriptive_only": False,
        "note": "唯一推断主判据；supported 表示「随便一个可行分配也能达到同样"
                "整体有效性」被本实验否定，不是优越性宣称",
    }

    # ---- E1：描述性梯度（B1/B2/D0，无 verdict）------------------------
    all_others = [b] + [str(x) for x in descriptive_pairs]
    pairs: dict[str, Any] = {}
    for other in all_others:
        entry: dict[str, Any] = {}
        # 主尺绝对量 + 时效/产出（全部描述性）
        for key in ("path_m_audit", "on_time_rate_final", "reward_yield"):
            st, paired, unpaired = _pair(a, other, key)
            entry[key] = {
                "stats": st, "paired_seeds": paired, "unpaired_seeds": unpaired,
                "descriptive_only": True,
                "ruler": "dem_directed_3d" if key.startswith("path_m") else "native",
            }
        # 逐 seed 归一的每完成航程（描述性）
        entry[primary_metric] = _normalized_pair(
            rows, a, other, seed_list, "path_m_audit", "n_completed"
        )
        _register_unpaired(entry[primary_metric]["unpaired_seeds"])
        # 代价并列（评估次数 / 墙钟），描述性
        for key in ("n_evals_total", "wall_seconds"):
            st, paired, unpaired = _pair(a, other, key)
            entry[f"cost:{key}"] = {
                "stats": st, "paired_seeds": paired, "unpaired_seeds": unpaired,
                "descriptive_only": True,
            }
        pairs[other] = entry

    # ---- E1：参照表结构（单源，供 build_reference_table.py）----------
    table_spec = {
        "role": "effectiveness_reference",
        "primary_ruler": "path_m_audit",
        "normalized_ruler": "path_m_audit_per_completion",
        "excluded_columns": (
            "f_audit", "total_path_m", "path_per_completion_m",
            "path_m_euclid3d", "ruler_gap_m",
        ),
        "exclusion_reason": "混尺子 / 自报尺 / 仅诊断留档；不进主文比较",
        "cost_columns": ("n_evals_total", "wall_seconds"),
        "inference": "single_primary_criterion",
        "wording": "只报估计量与区间，不作方法优劣结论；主判据唯一 = M0−B1",
    }

    report["claims"]["E1"] = {
        "statement": spec.statement,
        "kind": spec.kind,
        "note": spec.note,
        "caveats": list(CAVEATS),
        "primary_criterion": f"{a}_minus_{b}.{primary_metric}",
        "primary_pair": e1_primary,
        "descriptive_pairs": [str(x) for x in descriptive_pairs],
        "pairs": pairs,
        "reference": _build_reference(rows, seed_list, primary_methods),
        "table_spec": table_spec,
        "wording": _e1_wording(primary_verdict["state"]),
    }

    if profile != "formal":
        report["external_validity"] = (
            f"profile={profile}：冒烟/mid 档只验链路与配置纯度，结论不可外推正式档"
        )
    return report


def _e1_wording(state: str) -> str:
    return {
        "supported": "本实验否定了「随便一个可行分配也能达到同样整体有效性」"
                     "（配对差方向与主张一致且达到显著）；不写作优越性结论",
        "not_supported": "配对差方向与主张相反且达到显著，须修订主张措辞并排查实现",
        "inconclusive": "本实验未检出差异（含 p 不显著 / 零方差 / 配对不足 / 效应量过小）",
    }[state]


# ---------------------------------------------------------------------------
# 单尺参照：派生列与逐方法绝对量
# ---------------------------------------------------------------------------


def _ratio_or_none(
    row: Mapping[str, Any], num: str, den: str
) -> float | None:
    """逐 seed 归一；分母非正返回 None，绝不静默补 0。"""
    d = float(row.get(den, 0) or 0)
    if d <= 0:
        return None
    return float(row.get(num, 0.0) or 0.0) / d


def _abs_stats(values: Sequence[float]) -> dict[str, Any]:
    """绝对量只留估计量与区间；不做检验、不产出 p 或 verdict。"""
    st = paired_stats([float(v) for v in values])
    return {
        "n": st["n_pairs"],
        "mean": st["mean"],
        "std": st["std"],
        "ci95": st["ci95"],
    }


def _normalized_pair(
    rows: Mapping[str, Mapping[int, Mapping[str, Any]]],
    a: str,
    b: str,
    seeds: Sequence[int],
    num: str,
    den: str,
) -> dict[str, Any]:
    """``num / den`` 逐 seed 先归一再配对（差值恒 a − b）。

    归一发生在配对之前，分母缺失的 seed 显式计入 ``unpaired_seeds``，
    不补 0、不静默丢弃。
    """
    ra = {int(s): v for s, v in rows.get(a, {}).items()}
    rb = {int(s): v for s, v in rows.get(b, {}).items()}
    diffs: list[float] = []
    paired: list[int] = []
    unpaired: list[int] = []
    for s in seeds:
        if s not in ra or s not in rb:
            unpaired.append(int(s))
            continue
        va = _ratio_or_none(ra[s], num, den)
        vb = _ratio_or_none(rb[s], num, den)
        if va is None or vb is None:
            unpaired.append(int(s))
            continue
        diffs.append(va - vb)
        paired.append(int(s))
    return {
        "definition": f"{num} / {den}，逐 seed 先归一再配对（差值恒 {a} − {b}）",
        "stats": paired_stats(diffs),
        "per_seed": [round(x, 6) for x in diffs],
        "paired_seeds": paired,
        "unpaired_seeds": unpaired,
        "ruler": "dem_directed_3d",
        "table_role": "primary_ruler_per_completion",
        "expected": "negative",
        "descriptive_only": True,
        "note": "分母 n_completed ≤ 0 的 seed 显式列为未配对，不补 0",
    }


def _build_reference(
    rows: Mapping[str, Mapping[int, Mapping[str, Any]]],
    seeds: Sequence[int],
    primary_methods: Sequence[str],
) -> dict[str, Any]:
    """逐方法绝对量（单源，供参照主表直接取用）。

    只取 primary 方法；所有航程列一律来自 ``path_m_audit`` 这一把尺子，
    ``total_path_m`` / ``path_per_completion_m`` / ``f_audit`` /
    ``path_m_euclid3d`` 均不进入。
    """
    keys = (
        "path_m_audit", "n_completed", "on_time_rate_final",
        "reward_yield", "n_evals_total", "wall_seconds",
    )
    out: dict[str, Any] = {}
    for method in primary_methods:
        by_seed = {int(s): v for s, v in (rows.get(method) or {}).items()}
        cells = [by_seed[int(s)] for s in seeds if int(s) in by_seed]
        if not cells:
            continue
        metrics = {
            key: _abs_stats([float(c.get(key, 0.0) or 0.0) for c in cells])
            for key in keys
        }
        ratios: list[float] = []
        n_missing = 0
        for c in cells:
            r = _ratio_or_none(c, "path_m_audit", "n_completed")
            if r is None:
                n_missing += 1
            else:
                ratios.append(r)
        metrics["path_m_audit_per_completion"] = _abs_stats(ratios)
        out[str(method)] = {
            "n_seeds": len(cells),
            "n_missing_denominator": n_missing,
            "ruler": "dem_directed_3d",
            "metrics": metrics,
        }
    return out
