# -*- coding: utf-8 -*-
"""exp11_multi_depot —— 多机巢「异巢起降自由度」三臂 × 四梯度对照。

**论文要证的那句话**：允许异巢起降（终点机巢可自由改派）比「必须返原巢」
更省，且省得更多是**跨周期累积**出来的，不是单周期巧合。

三臂（唯一差异 = 终点机巢决策，其余字段逐位不动）::

    home     强制返原巢      终点 = 本航次起飞巢（P5 新增，取消改派自由度）
    nearest  就近贪心        每机独立选最近合法巢，无全局容量协调（先到先得）
    mcf      本文口径        给定路径下终点巢的条件最优分配（含容量耦合）

四梯度（跨周期累积）：周期数 T ∈ {2,4,6,10}，一律每周期 10 个任务
⇒ 任务总额 20 / 40 / 60 / 100。同 seed 下四个梯度是**同一到达流的前缀**，
故「省了多少」可按 T 拆开看，验证收益随累积周期增长。

⚠️ 口径纪律（写进 notes/summary，禁违反）
  1. 三臂同预算（population / max_iter 逐位相同）；本实验预算为标定档
     24×40（exp01 正式档 50×200 在本规模 × 4 梯度 × 3 臂 × 3 seed 下
     运行时长不可接受），故**绝对值不可与 exp01 正式档横比**。
  2. n=3 seed ⇒ df=2，检验效能极低；p 不显著只写「不显著」，禁写「无差异」。
  3. 三臂只翻终点巢策略，解码其余部分不变；层 1 策略作用于每个候选个体
     的解码 ⇒ 是**端到端效应**，不是「同一解上换巢」的局部收益。
  4. 终点 MCF 是给定路径下的条件最优，不是「归属+顺序+巢」联合最优。

命令行::

    python run.py --preset smoke  --seeds 7 --tiers 2      # 设计自检
    python run.py --preset mid    --seeds 7 --tiers 2,4    # 中间档（不可外推）
    python run.py --preset formal --seeds 7,11,23 --tiers 2,4,6,10

产物 ``results/<preset>/``：``T<T>/<arm>/seed_<s>/{metrics,run_meta}.json``、
``summary.json``（逐梯度主表 + 三组配对差 + 累积曲线）、``summary.csv``。
跑批不渲图；图与论文表格由 ``plot/compare.py`` / ``build_tables.py`` 事后生成。
本实验自持 config/build/paths/metrics/plot，不建 _shared、不抽公共包。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import EXP_DIR, ROOT  # noqa: E402
from build import (  # noqa: E402
    build_arrivals,
    build_building_layer,
    build_cost_estimator,
    build_scenario,
    build_task_config,
    build_terrain,
    build_uavs,
)
from config import build_config_bundle, load_config  # noqa: E402
from metrics import (  # noqa: E402
    _mean,
    _std,
    _two_sided_p,
    collect_run_metrics,
    write_json,
    write_summary_csv,
)

from task_allocation.methods.rolling.horizon import RollingHorizon  # noqa: E402
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402

__all__ = ["main"]

#: 三臂顺序即论文表格顺序（home / nearest / mcf）。
ARMS = ("home", "nearest", "mcf")

ARM_LABEL = {
    "home": "强制返原巢",
    "nearest": "就近贪心终点",
    "mcf": "本文 MCF",
}

ARM_SPEC: dict[str, dict[str, Any]] = {
    "home": dict(end_nest_l1_mode="home", end_nest_l3_mode="home"),
    "nearest": dict(end_nest_l1_mode="nearest", end_nest_l3_mode="nearest"),
    "mcf": dict(end_nest_l1_mode="mcf", end_nest_l3_mode="mcf"),
}

#: 梯度定义：T → 周期数。到达流恒为每周期 10 个（T=2 ⇒ 10 + 10 = 20）。
TIERS: dict[int, int] = {2: 2, 4: 4, 6: 6, 10: 10}
PER_CYCLE = 10

PRESETS: dict[str, dict[str, Any]] = {
    "smoke": {"solver.population": 8, "solver.max_iter": 8},
    "mid": {"solver.population": 16, "solver.max_iter": 20},
    "formal": {},
    # 容量压力档：预算同 formal，但每周期任务数由 --set 抬高（默认见 notes §5.8）。
    # 独立目录，避免覆盖正式档产物；结论只在本档内比较，不与 formal 横比。
    "pressure": {},
    # 完工时间档（notes §5.9）：全部任务 t=0 到达（--set tasks.n_tasks=100
    # + rolling.arrivals.initial_only=true），比较三臂交付完全部任务的时刻。
    "makespan": {},
}

#: 冒烟签名：formal 拒绝在这些参数下运行
SMOKE_SIGNATURE = {
    "tasks.n_tasks": 6,
    "solver.population": 8,
    "solver.max_iter": 8,
}

QUALITY_KEYS = (
    "final_fitness_sum", "total_path_m", "total_return_leg_m",
    "return_leg_per_sortie_m", "path_per_task_m",
    "n_sorties", "n_cross_nest_landings", "cross_nest_share",
    "completion_rate_final", "on_time_rate_final", "total_lateness_s",
    "nest_overflow_total", "n_hard_violations_total", "n_infeasible_delivery",
    "wall_seconds",
    # exp11 同解换终点反事实（P5）：同一组交付路径、终点改回起飞巢的费用差。
    "same_routes_gap_m", "same_routes_gap_per_sortie_m",
    "total_return_leg_if_home_m", "n_cycles_home_overflow",
)

CAVEATS = [
    "三臂只翻终点机巢策略（home / nearest / mcf），其余配置逐位相同；"
    "层 1 策略作用于每个候选个体的解码 ⇒ 差异是端到端效应，"
    "不是「同一个解上换巢」的局部收益。",
    "终点 MCF 是给定路径下的条件最优，不是「归属 + 顺序 + 终点巢」联合最优；"
    "home 臂的代价差是「取消改派自由度」的代价，不是「任何终点策略都能补回」的量。",
    "本实验预算为标定档（population 24 / max_iter 60），三臂同预算对照成立，"
    "但绝对值不可与 exp01 正式档（50×200）横比，也不可外推到满预算档。",
    "n=7 seed ⇒ 配对 t 检验 df=6，仍属小样本；p 不显著只能写「不显著」，"
    "禁写「无差异」「等价」。",
    "梯度 T=2/4/6/10 共用同一到达流，但**独立跑批**（每次 n_cycles=T）；"
    "审计显示多数格子短梯度解并不等于长梯度解的前缀，故边际差读作独立跑批之差。",
    "单一场景（广州 3 机巢）、固定到达流 10/周期 ⇒ 结论只在该场景族内成立。",
    "home 臂不制造容量溢出（每机回原巢，占用不变）；三臂 overflow 均为 0 时，"
    "差异只能记在返航段 / 总航程 / 目标值上，不得写成「可行性提升」。",
    "墙钟时间不是质量指标：三臂串行且共用场景/代价缓存，只作观测。",
    "完成率/恒等式/eliminating/hard=0 为实验层硬断言；independent_audit 的几何"
    "重建重评仍是技术债。",
]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _coerce(v: str) -> Any:
    s = v.strip()
    low = s.lower()
    if low in ("null", "none"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if s[:1] in ("[", "{") and s[-1:] in ("]", "}"):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _apply_dotted_set(cfg: dict, items: Sequence[str]) -> None:
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"--set 需要 dotted.path=value：{it!r}")
        path, _, val = it.partition("=")
        parts = [p.strip() for p in path.strip().split(".")]
        d = cfg
        for p in parts[:-1]:
            d = d.setdefault(p, {})
            if not isinstance(d, dict):
                raise ValueError(f"--set 路径冲突：{path}")
        d[parts[-1]] = _coerce(val)


def _dotted_set(cfg: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    d = cfg
    for p in parts[:-1]:
        d = d.setdefault(p, {})
        if not isinstance(d, dict):
            raise ValueError(f"预设路径冲突：{dotted}")
    d[parts[-1]] = value


def _parse_seeds(tokens: Sequence[str]) -> list[int]:
    out: list[int] = []
    for tok in tokens:
        for part in str(tok).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part[1:]:
                lo_s, hi_s = part.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
                out.extend(range(min(lo, hi), max(lo, hi) + 1))
            else:
                out.append(int(part))
    uniq: list[int] = []
    for s in out:
        if s not in uniq:
            uniq.append(s)
    if not uniq:
        raise ValueError("--seeds 为空")
    return uniq


def _parse_tiers(tokens: Sequence[str]) -> list[int]:
    got: list[int] = []
    for tok in tokens or []:
        for part in str(tok).split(","):
            part = part.strip()
            if not part:
                continue
            t = int(part)
            if t not in TIERS:
                raise ValueError(f"--tiers 只接受 {sorted(TIERS)}：{t}")
            if t not in got:
                got.append(t)
    return sorted(got) or sorted(TIERS)


def _get(cfg: Mapping[str, Any], dotted: str) -> Any:
    cur: Any = cfg
    for p in dotted.split("."):
        if not isinstance(cur, Mapping) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _sig_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (int, float)):
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return False
    if isinstance(expected, list):
        return (isinstance(actual, (list, tuple))
                and len(actual) == len(expected)
                and all(_sig_match(a, e) for a, e in zip(actual, expected)))
    return actual == expected


def _check_formal(raw_cfg: Mapping[str, Any]) -> None:
    stale = [f"{k}={v}" for k, v in SMOKE_SIGNATURE.items()
             if _sig_match(_get(raw_cfg, k), v)]
    if stale:
        raise SystemExit(
            "--preset formal 拒绝运行：仍是冒烟参数 " + ", ".join(stale)
            + "（冒烟/mid 结论不可外推正式档）。"
        )


def _fingerprint(raw_cfg: Mapping[str, Any], arm: str, seed: int, tier: int) -> str:
    blob = json.dumps({"config": raw_cfg, "arm": arm, "seed": int(seed),
                       "tier": int(tier)},
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="异巢起降三臂 × 梯度对照")
    p.add_argument("--preset", choices=tuple(PRESETS), default="mid")
    p.add_argument("--seeds", nargs="+", metavar="SEED")
    p.add_argument("--tiers", nargs="+", metavar="T",
                   help="周期梯度，如 2,4,6,10（默认全跑）")
    p.add_argument("--set", action="append", default=[], metavar="dotted.path=value")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def _stage_effective(stage: Any) -> dict[str, Any]:
    keys = ("cost_polish_enabled", "l3b_enabled", "effective_l3b_enabled",
            "l3b_source", "polish_moves", "effective_polish_moves",
            "effective_cost_polish_relocate", "cost_polish_accept_mode",
            "cost_polish_accept_mode_effective", "cost_polish_eps_f",
            "end_nest_l1_mode", "end_nest_l3_mode", "projection_only",
            "coverage_first", "coverage_gate", "decode_mode")
    out: dict[str, Any] = {}
    for k in keys:
        if hasattr(stage, k):
            v = getattr(stage, k)
            out[k] = list(v) if isinstance(v, tuple) else v
    return out


def _per_cycle(cfg: Mapping[str, Any]) -> int:
    """每周期到达任务数（= ``tasks.n_tasks``，初始批与后续批同量）。

    正式档为 10；容量压力档由 ``--set tasks.n_tasks=…`` 抬高，其余口径不变。
    """
    tasks = cfg.get("tasks") or {}
    return int(tasks.get("n_tasks", PER_CYCLE) or PER_CYCLE)


def _tier_tasks(per_cycle: int, tier: int, initial_only: bool) -> int:
    """该档任务总数：initial_only 档只有初始批，其余为 每周期 × T。"""
    return int(per_cycle) if initial_only else int(per_cycle) * int(TIERS[tier])


def _tier_config(raw_cfg: Mapping[str, Any], tier: int) -> dict[str, Any]:
    """把一个梯度装配成配置：周期数 T + 每周期固定到达（前缀到达流）。

    ``rolling.arrivals.initial_only: true`` 时把后续到达全置 0：全部任务
    在 t=0 一次性到达（完工时间/makespan 对照档用）。
    """
    cfg = copy.deepcopy(dict(raw_cfg))
    per = _per_cycle(cfg)
    initial_only = bool(((cfg.get("rolling") or {}).get("arrivals") or {})
                        .get("initial_only", False))
    counts = [0] * (int(TIERS[tier]) - 1) if initial_only else [per] * (int(TIERS[tier]) - 1)
    _dotted_set(cfg, "rolling.n_cycles", int(TIERS[tier]))
    _dotted_set(cfg, "tasks.n_tasks", per)  # 初始批 = 第 1 周期到达
    _dotted_set(cfg, "rolling.arrivals.counts", counts)
    return cfg


# ---------------------------------------------------------------------------
# 单次运行
# ---------------------------------------------------------------------------


def _run_once(
    raw_cfg: dict, seed: int, arm: str, tier: int, *,
    scenario: Any, terrain: Any, buildings: Any, estimator: Any,
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    t0 = time.time()
    tier_cfg = _tier_config(raw_cfg, tier)
    tier_cfg.setdefault("experiment", {})["seed"] = seed
    tier_cfg.setdefault("solver", {})["seed"] = seed
    bundle = build_config_bundle(tier_cfg)

    spec = ARM_SPEC[arm]
    stage_cfg = replace(
        bundle.stage_model,
        cost_polish_accept_mode="f",       # 三臂统一判据（其余字段逐位不动）
        end_nest_l1_mode=str(spec["end_nest_l1_mode"]),
        end_nest_l3_mode=str(spec["end_nest_l3_mode"]),
    )
    got = (stage_cfg.end_nest_l1_mode, stage_cfg.end_nest_l3_mode)
    want = (spec["end_nest_l1_mode"], spec["end_nest_l3_mode"])
    if got != want:
        raise RuntimeError(f"{arm}: 终点臂未按预期生效 got={got} want={want}")
    if bool(getattr(stage_cfg, "projection_only", False)):
        raise RuntimeError(f"{arm}: projection_only 意外为开，会污染终点臂归因")
    if stage_cfg.end_nest_l1_mode == "auto" or stage_cfg.end_nest_l3_mode == "auto":
        raise RuntimeError(f"{arm}: 终点臂落回 auto，对照失效")

    uavs = build_uavs(scenario.nests, tier_cfg)
    # ⚠️ 必须在 horizon.run 之前快照初始驻巢：RollingHorizon 会就地改写
    # UAV.nest_id（飞行/换巢后位置变化），跑完再读会把终态当成初始基线，
    # 异巢占比随即失真（本实验踩过）。
    initial_home = {u.id: u.nest_id for u in uavs}
    task_cfg = build_task_config(tier_cfg, seed)
    batch = build_arrivals(scenario.nests, scenario, task_cfg, tier_cfg)
    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)
    horizon = RollingHorizon(
        pool, fleet, scenario.nests, dem=terrain, estimator=estimator,
        horizon_config=bundle.horizon,
        selection_config=bundle.selection,
        problem_config=bundle.problem_model,
        stage_config=stage_cfg,
        solver_config=bundle.solver,
        event_config=bundle.event,
        nest_offset=bundle.nest_offset,
    )
    records = horizon.run(
        n_cycles=bundle.n_cycles,
        arrivals=copy.deepcopy(batch.arrivals),
        progress_cb=None,
    )
    metrics = collect_run_metrics(
        records, pool=pool, batch=batch, seed=seed, mode=arm,
        wall_seconds=round(time.time() - t0, 3),
        expected_accept_mode="f",
        initial_home=initial_home,
    )
    metrics["tier"] = int(tier)
    metrics["n_cycles_planned"] = int(TIERS[tier])
    meta = {
        "experiment": "exp11_multi_depot",
        "arm": arm, "arm_label": ARM_LABEL[arm], "seed": seed, "tier": int(tier),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config_fingerprint": _fingerprint(raw_cfg, arm, seed, tier),
        "stage_effective": _stage_effective(stage_cfg),
        "arrivals_per_cycle": batch.per_cycle_counts(),
        "initial_home": initial_home,
        "n_cycles_run": len(records),
        "stop_reason": getattr(records[-1], "stop_reason", None) if records else None,
        "population": _get(tier_cfg, "solver.population"),
        "max_iter": _get(tier_cfg, "solver.max_iter"),
    }
    return metrics, meta, stage_cfg


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def _purity(stages: Mapping[tuple[int, int], Mapping[str, Any]]) -> dict[str, Any]:
    """同 (tier, seed) 下三臂只应在终点模式字段上不同。"""
    allowed = {"end_nest_l1_mode", "end_nest_l3_mode", "cost_polish_accept_mode"}
    unexpected: dict[str, Any] = {}
    within: list[str] = []
    for (tier, seed), per_arm in stages.items():
        if set(per_arm) != set(ARMS):
            within.append(f"tier{tier}/seed{seed}: 臂不齐 {sorted(per_arm)}")
            continue
        base = asdict(per_arm["home"])
        for arm in ARMS[1:]:
            cur = asdict(per_arm[arm])
            for k in sorted(set(base) | set(cur)):
                if base.get(k) != cur.get(k):
                    unexpected.setdefault(k, {})[f"tier{tier}/seed{seed}/{arm}"] = cur.get(k)
    return {
        "differing_fields": sorted(unexpected),
        "n_differing_fields": len(unexpected),
        "only_expected_fields_differ": set(unexpected) <= allowed,
        "unexpected_fields": sorted(set(unexpected) - allowed),
        "shape_issues": within,
    }


def _paired_stats(diffs: Sequence[float]) -> dict[str, Any]:
    n = len(diffs)
    mu = _mean(diffs)
    sd = _std(diffs)
    t = mu / (sd / math.sqrt(n)) if n >= 2 and sd > 0 else None
    p = _two_sided_p(t, n - 1) if t is not None and n >= 2 else None
    if n < 2:
        reason = "n_pairs_lt_2"
    elif sd == 0.0:
        reason = "zero_variance_no_test"
    elif p is None:
        reason = "p_undefined"
    else:
        reason = "computed"
    return {
        "per_seed": [round(float(d), 6) for d in diffs],
        "n_pairs": n, "mean": round(mu, 6), "std": round(sd, 6),
        "t_stat": round(t, 6) if t is not None else None,
        "p_two_sided": round(p, 6) if p is not None else None,
        "p_reason": reason, "p_method": "student_t_incomplete_beta_exact",
        "significant_at_0_05": (bool(p < 0.05) if p is not None else None),
    }


def _build_report(
    per_cell: Mapping[tuple[int, str], Sequence[dict[str, Any]]], *,
    seeds: Sequence[int], tiers: Sequence[int], profile: str,
    purity: Mapping[str, Any], per_cycle: int = PER_CYCLE,
    initial_only: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "experiment": "exp11_multi_depot",
        "profile": profile, "seeds": list(seeds), "tiers": list(tiers),
        "arms": list(ARMS), "arm_labels": ARM_LABEL, "arm_spec": ARM_SPEC,
        "per_cycle_tasks": per_cycle,
        "arrivals_initial_only": bool(initial_only),
        "task_totals": {t: _tier_tasks(per_cycle, t, initial_only) for t in tiers},
        "config_purity": dict(purity),
        "caveats": list(CAVEATS),
        "tiers_report": {},
        "paired_seeds": list(seeds),
        "unpaired_seeds": [],
        "invariant_failures": [],
    }

    for tier in tiers:
        block: dict[str, Any] = {
            "tier": tier, "n_cycles": TIERS[tier],
            "n_tasks_total": _tier_tasks(per_cycle, tier, initial_only),
            "arms": {}, "effects": {},
        }
        rows: dict[str, dict[int, dict[str, Any]]] = {}
        for arm in ARMS:
            rr = list(per_cell.get((tier, arm), []))
            rows[arm] = {int(r["seed"]): r for r in rr}
            if not rr:
                continue
            block["arms"][arm] = {
                "label": ARM_LABEL[arm],
                "n_seeds": len(rr),
                **{
                    k: {
                        "mean": round(_mean([float(r.get(k, 0.0) or 0.0) for r in rr]), 6),
                        "std": round(_std([float(r.get(k, 0.0) or 0.0) for r in rr]), 6),
                    }
                    for k in QUALITY_KEYS
                },
            }
        paired = [s for s in seeds if all(s in rows.get(a, {}) for a in ARMS)]

        def diffs(a: str, b: str, key: str) -> list[float]:
            return [float(rows[a][s].get(key, 0.0) or 0.0)
                    - float(rows[b][s].get(key, 0.0) or 0.0) for s in paired]

        for key in QUALITY_KEYS:
            block["effects"][key] = {
                "home_minus_mcf": _paired_stats(diffs("home", "mcf", key)),
                "nearest_minus_mcf": _paired_stats(diffs("nearest", "mcf", key)),
                "home_minus_nearest": _paired_stats(diffs("home", "nearest", key)),
            }
        report["tiers_report"][str(tier)] = block

    accum: dict[str, Any] = {}
    for tier in tiers:
        blk = report["tiers_report"][str(tier)]
        eff = blk["effects"]
        arms = blk["arms"]
        accum[str(tier)] = {
            "n_cycles": TIERS[tier],
            "n_tasks_total": _tier_tasks(per_cycle, tier, initial_only),
            "extra_return_leg_m_home_vs_mcf": eff["total_return_leg_m"]["home_minus_mcf"]["mean"],
            "extra_path_m_home_vs_mcf": eff["total_path_m"]["home_minus_mcf"]["mean"],
            "extra_fitness_home_vs_mcf": eff["final_fitness_sum"]["home_minus_mcf"]["mean"],
            "extra_return_leg_m_nearest_vs_mcf": eff["total_return_leg_m"]["nearest_minus_mcf"]["mean"],
            "extra_path_m_nearest_vs_mcf": eff["total_path_m"]["nearest_minus_mcf"]["mean"],
            "cross_nest_share_mcf": (arms.get("mcf", {}).get("cross_nest_share", {}) or {}).get("mean"),
            "cross_nest_share_home": (arms.get("home", {}).get("cross_nest_share", {}) or {}).get("mean"),
            "cross_nest_share_nearest": (arms.get("nearest", {}).get("cross_nest_share", {}) or {}).get("mean"),
            # 同解换终点反事实（该解上终点自由度的直接价值，单位 m/航次）
            "same_routes_gap_per_sortie_mcf": (arms.get("mcf", {}).get("same_routes_gap_per_sortie_m", {}) or {}).get("mean"),
            "same_routes_gap_per_sortie_nearest": (arms.get("nearest", {}).get("same_routes_gap_per_sortie_m", {}) or {}).get("mean"),
            "same_routes_gap_per_sortie_home": (arms.get("home", {}).get("same_routes_gap_per_sortie_m", {}) or {}).get("mean"),
            # 「臂间实际差 / 同解直接价值」：<1 说明 home 臂通过改路径把一部分
            # 损失吸收掉了，端到端只保留了直接价值的一部分。
            "retention_home_vs_mcf": (
                (eff["return_leg_per_sortie_m"]["home_minus_mcf"]["mean"]
                 / (arms.get("mcf", {}).get("same_routes_gap_per_sortie_m", {}) or {}).get("mean", 0.0))
                if (arms.get("mcf", {}).get("same_routes_gap_per_sortie_m", {}) or {}).get("mean") else None
            ),
        }
    report["accumulation"] = accum

    for tier in tiers:
        for arm in ARMS:
            for r in per_cell.get((tier, arm), []):
                if not r.get("invariants_passed", False):
                    report["invariant_failures"].append(
                        {"tier": tier, "arm": arm, "seed": r.get("seed"),
                         "violations": r.get("invariant_violations")}
                    )
    report["all_invariants_passed"] = not report["invariant_failures"]

    t10 = report["tiers_report"].get("10")
    if t10:
        e = t10["effects"]["total_return_leg_m"]["home_minus_mcf"]
        report["verdict"] = (
            f"T10（100 任务）：强制返原巢相对本文 MCF 的返航段差均值 = {e['mean']} m"
            f"（p={e['p_two_sided']}, {e['p_reason']}）；"
            "三臂同预算、同到达流前缀；差值须与 accumulation 曲线一并读，"
            "单周期增量不等于跨周期累积收益。"
        )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = _args(argv)
    raw_cfg = load_config(EXP_DIR / "config.yaml")
    for dotted, value in PRESETS[args.preset].items():
        _dotted_set(raw_cfg, dotted, value)
    _apply_dotted_set(raw_cfg, args.set)
    if args.seeds:
        raw_cfg.setdefault("experiment", {})["seeds"] = _parse_seeds(args.seeds)
    if args.preset == "formal":
        _check_formal(raw_cfg)
    seeds = [int(s) for s in (raw_cfg["experiment"].get("seeds")
                              or [raw_cfg["experiment"].get("seed")])]
    tiers = _parse_tiers(args.tiers)
    per_cycle = _per_cycle(raw_cfg)
    initial_only = bool(((raw_cfg.get("rolling") or {}).get("arrivals") or {})
                        .get("initial_only", False))
    print(
        f"[exp11] preset={args.preset} arms={ARMS} seeds={seeds} tiers={tiers} "
        f"每周期={per_cycle} initial_only={initial_only} "
        f"任务总额={[_tier_tasks(per_cycle, t, initial_only) for t in tiers]} "
        f"pop={_get(raw_cfg, 'solver.population')} "
        f"gen={_get(raw_cfg, 'solver.max_iter')}",
        flush=True,
    )
    if args.dry_run:
        print(json.dumps(raw_cfg, ensure_ascii=False, indent=2, default=str))
        return 0

    out_root = EXP_DIR / "results" / args.preset
    scenario = build_scenario(raw_cfg)
    terrain = build_terrain(scenario, raw_cfg)
    buildings = build_building_layer(raw_cfg, scenario)
    estimator = build_cost_estimator(terrain, raw_cfg, buildings=buildings)
    print(f"[exp11] 场景：{scenario.count} 机巢，"
          f"建筑={buildings.count if buildings is not None else 0} 栋", flush=True)

    per_cell: dict[tuple[int, str], list[dict[str, Any]]] = {
        (t, a): [] for t in tiers for a in ARMS
    }
    stages: dict[tuple[int, int], dict[str, Any]] = {}
    for tier in tiers:
        for seed_idx, seed in enumerate(seeds):
            order = ARMS if (seed_idx + tier) % 2 == 0 else tuple(reversed(ARMS))
            cell_stage: dict[str, Any] = {}
            for arm in order:
                t0 = time.time()
                metrics, meta, stage = _run_once(
                    raw_cfg, seed, arm, tier,
                    scenario=scenario, terrain=terrain,
                    buildings=buildings, estimator=estimator,
                )
                per_cell[(tier, arm)].append(metrics)
                cell_stage[arm] = stage
                run_dir = out_root / f"T{tier}" / arm / f"seed_{seed}"
                write_json(run_dir / "metrics.json", metrics)
                write_json(run_dir / "run_meta.json", meta)
                print(
                    f"[exp11] T{tier} {arm} seed={seed}: "
                    f"f={metrics['final_fitness_sum']:.2f} "
                    f"航程={metrics['total_path_m'] / 1000:.2f}km "
                    f"返航段={metrics['total_return_leg_m'] / 1000:.3f}km "
                    f"异巢={metrics['cross_nest_share']:.3f} "
                    f"完成率={metrics['completion_rate_final']:.3f} "
                    f"按时={metrics['on_time_rate_final']:.3f} "
                    f"cycles={metrics['n_cycles']} "
                    f"检查={'PASS' if metrics['invariants_passed'] else 'FAIL'} "
                    f"墙钟={time.time() - t0:.1f}s",
                    flush=True,
                )
            stages[(tier, seed)] = cell_stage

    purity = _purity(stages)
    report = _build_report(per_cell, seeds=seeds, tiers=tiers,
                           profile=args.preset, purity=purity,
                           per_cycle=per_cycle, initial_only=initial_only)
    write_json(out_root / "summary.json", report)
    write_summary_csv(out_root / "summary.csv", per_cell)
    print(f"[exp11] 配置纯度={purity.get('only_expected_fields_differ')} "
          f"非预期差异={purity.get('unexpected_fields')} "
          f"铁律全过={report['all_invariants_passed']}", flush=True)
    for tier in tiers:
        b = report["tiers_report"][str(tier)]
        e = b["effects"]["total_return_leg_m"]
        print(f"[exp11] T{tier}: 返航段 home−mcf={e['home_minus_mcf']['mean']} "
              f"p={e['home_minus_mcf']['p_two_sided']} "
              f"nearest−mcf={e['nearest_minus_mcf']['mean']} "
              f"p={e['nearest_minus_mcf']['p_two_sided']} "
              f"异巢mcf={b['arms'].get('mcf', {}).get('cross_nest_share', {}).get('mean')}",
              flush=True)
    print(f"[exp11] 产物目录：{out_root}", flush=True)

    rc = 0
    if not purity.get("only_expected_fields_differ"):
        print(f"[exp11] ⚠️ 配置非预期差异：{purity.get('unexpected_fields')}", flush=True)
        rc = 1
    if not report["all_invariants_passed"]:
        print(f"[exp11] ⚠️ 铁律失败：{report['invariant_failures']}", flush=True)
        rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
