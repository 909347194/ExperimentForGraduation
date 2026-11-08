# -*- coding: utf-8 -*-
"""exp10_capacity_pressure —— 容量压力场景下，终点机巢「层 1 投影 vs 层 3 MCF」对照。

定稿 §11.11「后续待办」第 1 项：exp09 的基准场景（3 机巢 × 泊位 2 = 6
泊位，机队 6 架）**缺少终点巢容量竞争**，冒烟 / mid 档层 3 完全退化、
正式档只在 seed 11 出现一次分叉。本实验在不改任何生产默认值的前提下，
用显式场景覆盖制造泊位压力，回答「有竞争时层 3 的 MCF 解决什么问题」。

**场景因子**（``--scenario``，只覆盖 ``problem.*``，初始不超容是前置条件）::

    loose     3 巢 × 泊位 3 = 9 泊位 / 6 架 ⇒ 3 个空位，竞争最弱（负对照）
    baseline  3 巢 × 泊位 2 = 6 泊位 / 6 架 ⇒ 0 个空位（= exp09 场景）
    tight     3 巢 × 泊位 1 = 3 泊位 / 3 架 ⇒ 每巢仅 1 位，跨巢降落必须等量让位
    burst     机队 / 泊位完全不动，后续到达 5+5 → 10+10 ⇒ 活跃周期与并发返航增多

前三个是「泊位余量 / 每巢容量」梯度，``burst`` 是定稿并列的另一条压力
路径「提高并发架次」。实测发现单纯把泊位压到 1 必须同时把机队减到 3，
反而**缩小**了可行分配空间（smoke 四档完全同解），所以正式档以 ``burst``
为主压力档，``tight`` 只作结构性对照。

**2×2 四档**（标签两位：第 1 位 = 解码期层 1，第 2 位 = 交付期层 3；
``n`` = 最近合法巢投影，``m`` = 给定路径下条件最优的 MCF）::

                      层 3 投影         层 3 MCF
    层 1 投影            nn                nm
    层 1 MCF             mn                mm（当前生产口径）

判据（定稿原文）：**终点巢分配签名是否分叉 + 返航段同 seed 配对差**，
铁律不变（完成率=1 / overflow=0 / n_hard=0 / eliminating=0）。
层 3 改善若只出现在返航段、去程不变，说明它是「给定路径下联合最优 vs
逐机最近合法巢贪心」的纯成本差异，**不是可行性修复**。

⚠️ **口径限制**：① 层 1 作用于每个候选个体的解码 ⇒ 改变整条 DE 搜索
轨迹，层 1 主效应是端到端效应；② 层 3 只在 ``post_cost_polish`` /
``post_polish`` 真正被调用的周期生效，零精修周期两档相同（算子可得性
差异），交互项**禁写协同效应 / 独立收益**；③ 不同场景的任务集与机队规模
不同 ⇒ **只能做描述性对照，禁止跨场景配对 t 检验**；④ 冒烟 / mid 结论
不可外推正式档；⑤ p 不显著如实声明，也不得写成「无差异」。

命令行::

    python run.py --scenario tight --preset smoke --seeds 7
    python run.py --scenario tight --preset mid --seeds 7,11,23
    python run.py --scenario tight --preset formal --seeds 7,11,23

产物 ``results/<scenario>/<preset>/``：逐档逐 seed 的 metrics/run_meta、
``summary.json``、``summary.csv``。跑批不渲图；图由 ``plot/compare.py``
事后单独生成。本实验自持 config/build/paths/metrics/plot，
不建 _shared、不抽公共包。
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

MODES = ("nn", "nm", "mn", "mm")

#: 四档唯一差异：解码期层 1 / 交付期层 3 的终点机巢策略。
#: 其余字段逐位不动（用 dataclasses.replace 锁定）。
#: ``n`` = 最近合法巢确定性投影；``m`` = 给定路径下条件最优的 MCF。
MODE_SPEC: dict[str, dict[str, Any]] = {
    "nn": dict(end_nest_l1_mode="nearest", end_nest_l3_mode="nearest"),
    "nm": dict(end_nest_l1_mode="nearest", end_nest_l3_mode="mcf"),
    "mn": dict(end_nest_l1_mode="mcf", end_nest_l3_mode="nearest"),
    "mm": dict(end_nest_l1_mode="mcf", end_nest_l3_mode="mcf"),
}

PRESETS: dict[str, dict[str, Any]] = {
    "smoke": {},
    "mid": {
        "tasks.n_tasks": 12,
        "solver.population": 25,
        "solver.max_iter": 60,
        "rolling.n_cycles": 20,
        "rolling.arrivals.counts": [3, 3],
    },
    "formal": {
        "tasks.n_tasks": 20,
        "solver.population": 50,
        "solver.max_iter": 200,
        "rolling.n_cycles": 60,
        "rolling.arrivals.counts": [5, 5],
    },
}

#: 场景因子：只覆盖 ``problem.*``，且**必须保持初始不超容**
#: （n_uavs <= n_nests × nest_capacity），否则 t=0 就 overflow、铁律失效。
#: 三个档位构成「泊位余量 / 每巢容量」梯度；跨场景只做描述性对照。
SCENARIOS: dict[str, dict[str, Any]] = {
    "loose": {"problem.nest_capacity": 3},        # 9 泊位 / 6 架 ⇒ 3 空位
    "baseline": {},                                # 6 泊位 / 6 架 ⇒ 0 空位
    "tight": {"problem.nest_capacity": 1, "problem.n_uavs": 3},  # 每巢 1 位
    # 并发压力：机队 / 泊位完全不动，只把后续两批到达从 5+5 提到 10+10。
    # 活跃周期变多 ⇒ 末态多机同时返航的次数变多，终点巢竞争机会上升。
    # 注意这会改变总任务数，与 exp09 正式档不再逐位可比（见 caveats）。
    "burst": {"rolling.arrivals.counts": [10, 10]},
}

SMOKE_SIGNATURE = {
    "tasks.n_tasks": 8,
    "solver.population": 12,
    "solver.max_iter": 20,
    "rolling.n_cycles": 8,
    "rolling.arrivals.counts": [2, 2],
}

#: 配对质量指标（越小/越好分开判读；这里只报差值，不做方向性断言）
QUALITY_KEYS = (
    "final_fitness_sum", "total_path_m", "total_return_leg_m",
    "nest_overflow_total", "on_time_rate_final", "total_lateness_s",
    "n_hard_violations_total", "completion_rate_final",
)
#: 过程量（不进配对 t 检验）：MCF 重解次数 + P3-b 三本账
LEDGER_KEYS = (
    "cost_polish_n_mcf_resolve",
    "l3a_geometric_actions", "l3b_relocate_actions",
    "n_task_to_uav_changes", "cost_polish_n_accepted",
    "l3b_ungated_monotone_relocate",
)

CAVEATS = [
    "2×2 两层设计的主效应与交互项并列报告；禁写「独立收益」与「协同效应」。",
    "终点机巢 MCF 是给定路径下的条件最优（非全局最优），不是「归属+顺序+巢」联合最优。",
    "层 1 策略作用于每个候选个体的解码，改变整条 DE 搜索轨迹；层 1 主效应是端到端效应，"
    "不是「同一解上换巢」的局部收益。",
    "层 3 策略只在 post_cost_polish / post_polish 真正被调用的周期生效；"
    "零精修周期两档相同，属算子可得性差异，交互项不能解释成纯二阶交互。",
    "本实验不用 projection_only 做 MCF 对照：它同时剥掉 _consolidate 成本合并与 "
    "_best_order 的 NN / 航程裁决，是复合档，归因不纯。",
    "nest_overflow 为 check_nest_capacity 逐巢 overflow 之和；若恒 0 说明两策略都满足"
    "容量约束，差异只体现在返航代价，不得写成「可行率提升」。",
    "mid / smoke 结论不可外推正式档；正式档须单独跑批。",
    "单一场景（广州 3 机巢）、固定到达流、3 seed ⇒ df=2 效能极低；"
    "p 不显著须如实声明，也不得写成「无差异」。",
    "完成率/恒等式/eliminating 为实验层硬断言；independent_audit 的"
    "几何重建重评仍属技术债（RollingHorizon 未暴露审计钩子）。",
    "end_nest_l1_mode / end_nest_l3_mode 默认 auto = 生产 MCF 口径，"
    "零行为变化（26 份 mid JSON 逐字段核验通过）。",
    "场景档只覆盖 problem.nest_capacity / problem.n_uavs；初始超容会在跑批前硬拒绝，"
    "因为 t=0 的 overflow 会让铁律失效、归因失真。",
    "不同场景的任务集、机队规模与可行域不同 ⇒ 跨场景只允许描述性对照，"
    "禁止把两个场景的指标做同 seed 配对 t 检验，也禁止写成「容量压力带来 X 收益」。",
    "层 3 改善若全部落在返航段且去程不变，只能写成给定路径下联合最优与逐机最近合法巢"
    "投影的成本差异；不得写成可行性修复、可行率提升或 MCF 无用 / 可删。",
    "burst 档把后续到达从 5+5 提到 10+10 ⇒ 总任务数与活跃周期都变了，"
    "与 exp08/exp09 正式档不再逐位可比，禁写「逐位复现」或跨实验配对。",
]


# ---------------------------------------------------------------------------
# CLI 与配置（自持，不依赖其它实验目录）
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


def _fingerprint(raw_cfg: Mapping[str, Any], mode: str, seed: int) -> str:
    blob = json.dumps({"config": raw_cfg, "mode": mode, "seed": int(seed)},
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="容量压力场景 × 终点机巢层 1 / 层 3 策略 2×2 对照")
    p.add_argument("--preset", choices=tuple(PRESETS), default="mid")
    p.add_argument("--scenario", choices=tuple(SCENARIOS), default="burst")
    p.add_argument("--seeds", nargs="+", metavar="SEED")
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


# ---------------------------------------------------------------------------
# 单次运行
# ---------------------------------------------------------------------------


def _run_once(
    raw_cfg: dict, seed: int, mode: str, *,
    scenario: Any, terrain: Any, buildings: Any, estimator: Any,
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    t0 = time.time()
    seed_cfg = copy.deepcopy(dict(raw_cfg))
    seed_cfg.setdefault("experiment", {})["seed"] = seed
    seed_cfg.setdefault("solver", {})["seed"] = seed
    bundle = build_config_bundle(seed_cfg)

    spec = MODE_SPEC[mode]
    stage_cfg = replace(
        bundle.stage_model,
        cost_polish_accept_mode="f",       # 统一判据（§11.9）
        end_nest_l1_mode=str(spec["end_nest_l1_mode"]),
        end_nest_l3_mode=str(spec["end_nest_l3_mode"]),
    )
    got = (stage_cfg.end_nest_l1_mode, stage_cfg.end_nest_l3_mode)
    want = (spec["end_nest_l1_mode"], spec["end_nest_l3_mode"])
    if got != want:
        raise RuntimeError(
            f"{mode}: 分层开关未按预期生效 got={got} want={want}"
        )
    if bool(getattr(stage_cfg, "projection_only", False)):
        raise RuntimeError(
            f"{mode}: projection_only 意外为开，会污染 MCF 归因"
        )

    uavs = build_uavs(scenario.nests, raw_cfg)
    task_cfg = build_task_config(raw_cfg, seed)
    batch = build_arrivals(scenario.nests, scenario, task_cfg, raw_cfg)
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
        records, pool=pool, batch=batch, seed=seed, mode=mode,
        wall_seconds=round(time.time() - t0, 3),
        expected_accept_mode="f",   # 四档判据统一，档名不是判据名
    )
    meta = {
        "experiment": "exp10_capacity_pressure",
        "mode": mode, "seed": seed,
        "scenario": {
            "n_nests": _get(raw_cfg, "problem.n_nests"),
            "nest_capacity": _get(raw_cfg, "problem.nest_capacity"),
            "n_uavs": _get(raw_cfg, "problem.n_uavs"),
        },
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config_fingerprint": _fingerprint(raw_cfg, mode, seed),
        "stage_effective": _stage_effective(stage_cfg),
        "arrivals_per_cycle": batch.per_cycle_counts(),
        "stop_reason": getattr(records[-1], "stop_reason", None) if records else None,
    }
    return metrics, meta, stage_cfg


# ---------------------------------------------------------------------------
# 2×2 报告（主效应 + 交互项，禁写独立收益）
# ---------------------------------------------------------------------------


def _config_purity(stages: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    allowed = {"end_nest_l1_mode", "end_nest_l3_mode",
               "cost_polish_accept_mode"}
    if set(stages) != set(MODES) or any(not stages[m] for m in MODES):
        return {"error": "样本不完整", "counts": {m: len(v) for m, v in stages.items()}}
    base = asdict(stages["nn"][0])
    differing: dict[str, Any] = {}
    for mode in MODES[1:]:
        cur = asdict(stages[mode][0])
        for k in sorted(set(base) | set(cur)):
            if base.get(k) != cur.get(k):
                differing.setdefault(k, {})[mode] = cur.get(k)
    within = {
        m: all(asdict(s) == asdict(stages[m][0]) for s in stages[m])
        for m in MODES
    }
    return {
        "differing_fields": differing,
        "n_differing_fields": len(differing),
        "only_expected_fields_differ": set(differing) <= allowed,
        "unexpected_fields": sorted(set(differing) - allowed),
        "within_mode_identical_across_seeds": within,
        "all_within_mode_identical": all(within.values()),
        "per_seed_samples": {m: len(stages[m]) for m in MODES},
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


def _build_2x2_report(
    per_mode: Mapping[str, Sequence[dict[str, Any]]], *,
    seeds: Sequence[int], profile: str, purity: Mapping[str, Any],
    scenario: str,
) -> dict[str, Any]:
    rows = {m: {int(r["seed"]): r for r in per_mode[m]} for m in MODES}
    paired = [s for s in seeds if all(s in rows[m] for m in MODES)]
    unpaired = [s for s in seeds if s not in paired]

    def vals(mode: str, key: str) -> list[float]:
        return [float(rows[mode][s].get(key, 0.0) or 0.0) for s in paired]

    report: dict[str, Any] = {
        "experiment": "exp10_capacity_pressure",
        "profile": profile, "seeds": list(seeds),
        "scenario": scenario, "scenario_overrides": dict(SCENARIOS[scenario]),
        "paired_seeds": paired, "unpaired_seeds": unpaired,
        "modes": list(MODES), "mode_spec": MODE_SPEC,
        "config_purity": dict(purity),
        "caveats": list(CAVEATS),
        "quality_by_mode": {},
        "ledger_by_mode": {},
        "effects": {},
        "invariant_failures": [],
        "all_invariants_passed": True,
    }
    for key in QUALITY_KEYS:
        report["quality_by_mode"][key] = {
            m: vals(m, key) for m in MODES
        }
    for key in LEDGER_KEYS:
        report["ledger_by_mode"][key] = {
            m: vals(m, key) for m in MODES
        }

    # 主效应 / 交互：interaction = (nn − nm) − (mn − mm)
    # nn=层1投影/层3投影, nm=层1投影/层3MCF,
    # mn=层1MCF/层3投影, mm=层1MCF/层3MCF（生产口径）
    for key in QUALITY_KEYS:
        nn, nm = vals("nn", key), vals("nm", key)
        mn, mm = vals("mn", key), vals("mm", key)
        report["effects"][key] = {
            "main_L1_at_L3n": _paired_stats([a - b for a, b in zip(mn, nn)]),
            "main_L1_at_L3m": _paired_stats([a - b for a, b in zip(mm, nm)]),
            "main_L3_at_L1n": _paired_stats([a - b for a, b in zip(nm, nn)]),
            "main_L3_at_L1m": _paired_stats([a - b for a, b in zip(mm, mn)]),
            "interaction": _paired_stats(
                [(a - b) - (c - d) for a, b, c, d in zip(nn, nm, mn, mm)]
            ),
        }

    for m in MODES:
        for r in per_mode[m]:
            if not r.get("invariants_passed", False):
                report["invariant_failures"].append(
                    {"mode": m, "seed": r.get("seed"),
                     "violations": r.get("invariant_violations")}
                )
    report["all_invariants_passed"] = not report["invariant_failures"]

    f = report["effects"]["final_fitness_sum"]
    report["verdict"] = (
        "2×2 两层策略的主效应与交互项见 effects；差值均为同 seed 配对，"
        "不得写成独立收益或协同效应。"
        f" 交互项均值={f['interaction']['mean']}，"
        f"p={f['interaction']['p_two_sided']}（{f['interaction']['p_reason']}）。"
        " 若 p 不显著须如实声明；层 1 作用于整条搜索轨迹、层 3 有算子"
        "可得性限制，见 caveats。"
        f" 场景={scenario}：不同场景只作描述性对照，禁止跨场景配对检验。"
    )
    return report


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    args = _args(argv)
    raw_cfg = load_config(EXP_DIR / "config.yaml")
    for dotted, value in PRESETS[args.preset].items():
        _dotted_set(raw_cfg, dotted, value)
    # 场景因子覆盖在 preset 之后、用户显式 --set 之前：
    # preset 定规模、场景定容量、--set 留作人工微调（最高优先）。
    for dotted, value in SCENARIOS[args.scenario].items():
        _dotted_set(raw_cfg, dotted, value)
    _apply_dotted_set(raw_cfg, args.set)
    n_nests = int(_get(raw_cfg, "problem.n_nests") or 0)
    cap = int(_get(raw_cfg, "problem.nest_capacity") or 0)
    n_uavs = int(_get(raw_cfg, "problem.n_uavs") or 0)
    if n_nests <= 0 or cap <= 0 or n_uavs <= 0 or n_uavs > n_nests * cap:
        raise SystemExit(
            f"场景 {args.scenario} 初始超容或不合法："
            f"n_uavs={n_uavs} > n_nests×capacity={n_nests * cap}；"
            "t=0 就会 overflow，铁律失效，拒绝跑批。"
        )
    if args.seeds:
        raw_cfg.setdefault("experiment", {})["seeds"] = _parse_seeds(args.seeds)
    if args.preset == "formal":
        _check_formal(raw_cfg)
    seeds = [int(s) for s in (raw_cfg["experiment"].get("seeds")
                              or [raw_cfg["experiment"].get("seed")])]
    print(
        f"[exp10] preset={args.preset} scenario={args.scenario} modes={MODES} "
        f"seeds={seeds} n_tasks={_get(raw_cfg, 'tasks.n_tasks')} "
        f"pop={_get(raw_cfg, 'solver.population')} "
        f"gen={_get(raw_cfg, 'solver.max_iter')} "
        f"n_cycles={_get(raw_cfg, 'rolling.n_cycles')} "
        f"泊位={n_nests}×{cap} 机队={n_uavs} "
        f"空位={n_nests * cap - n_uavs}",
        flush=True,
    )
    if args.dry_run:
        print(json.dumps(raw_cfg, ensure_ascii=False, indent=2, default=str))
        return 0

    out_root = EXP_DIR / "results" / args.scenario / args.preset
    scenario = build_scenario(raw_cfg)
    terrain = build_terrain(scenario, raw_cfg)
    buildings = build_building_layer(raw_cfg, scenario)
    estimator = build_cost_estimator(terrain, raw_cfg, buildings=buildings)
    print(
        f"[exp10] 场景：{scenario.count} 机巢，"
        f"建筑={buildings.count if buildings is not None else 0} 栋",
        flush=True,
    )

    per_mode: dict[str, list[dict[str, Any]]] = {m: [] for m in MODES}
    stage_of_mode: dict[str, list[Any]] = {m: [] for m in MODES}
    # 严格串行：同一 seed 跑完四档再进下一个 seed；按 seed 交替档序，
    # 抵消共用 estimator/场景的缓存预热偏差。
    for seed_idx, seed in enumerate(seeds):
        order = MODES if seed_idx % 2 == 0 else tuple(reversed(MODES))
        for mode in order:
            t0 = time.time()
            metrics, meta, stage = _run_once(
                raw_cfg, seed, mode,
                scenario=scenario, terrain=terrain,
                buildings=buildings, estimator=estimator,
            )
            per_mode[mode].append(metrics)
            stage_of_mode[mode].append(stage)
            run_dir = out_root / mode / f"seed_{seed}"
            write_json(run_dir / "metrics.json", metrics)
            write_json(run_dir / "run_meta.json", meta)
            print(
                f"[exp10] {mode} seed={seed}: "
                f"f={metrics['final_fitness_sum']:.3f} "
                f"航程={metrics['total_path_m'] / 1000:.2f}km "
                f"返航段={metrics['total_return_leg_m'] / 1000:.2f}km "
                f"overflow={metrics['nest_overflow_total']} "
                f"完成率={metrics['completion_rate_final']:.3f} "
                f"按时={metrics['on_time_rate_final']:.3f} "
                f"L3a={metrics['l3a_geometric_actions']} "
                f"L3b={metrics['l3b_relocate_actions']} "
                f"MCF重解={metrics.get('cost_polish_n_mcf_resolve', 0)} "
                f"归属变动={metrics['n_task_to_uav_changes']} "
                f"检查={'PASS' if metrics['invariants_passed'] else 'FAIL'} "
                f"墙钟={time.time() - t0:.1f}s",
                flush=True,
            )

    purity = _config_purity(stage_of_mode)
    report = _build_2x2_report(
        per_mode, seeds=seeds, profile=args.preset, purity=purity,
        scenario=args.scenario,
    )
    write_json(out_root / "summary.json", report)
    write_summary_csv(out_root / "summary.csv", per_mode)
    f = report["effects"]["final_fitness_sum"]
    p = report["effects"]["total_path_m"]
    r = report["effects"]["total_return_leg_m"]
    print(
        f"[exp10] 配置纯度={purity.get('only_expected_fields_differ')} "
        f"差异字段={purity.get('n_differing_fields')} "
        f"铁律全过={report['all_invariants_passed']} 配对={paired_or_none(report)}",
        flush=True,
    )
    print(
        f"[exp10] f 主效应 L1@L3n={f['main_L1_at_L3n']['mean']} "
        f"L1@L3m={f['main_L1_at_L3m']['mean']} "
        f"L3@L1n={f['main_L3_at_L1n']['mean']} "
        f"L3@L1m={f['main_L3_at_L1m']['mean']} "
        f"交互={f['interaction']['mean']} p={f['interaction']['p_two_sided']}",
        flush=True,
    )
    print(
        f"[exp10] 航程 交互={p['interaction']['mean']} "
        f"p={p['interaction']['p_two_sided']}",
        flush=True,
    )
    print(
        f"[exp10] 返航段 交互={r['interaction']['mean']} "
        f"p={r['interaction']['p_two_sided']}",
        flush=True,
    )
    print(f"[exp10] verdict: {report['verdict']}", flush=True)
    print(f"[exp10] 产物目录：{out_root}", flush=True)

    rc = 0
    if not purity.get("only_expected_fields_differ"):
        print(f"[exp10] ⚠️ 配置出现非预期差异：{purity.get('unexpected_fields')}", flush=True)
        rc = 1
    if purity.get("all_within_mode_identical") is False:
        print("[exp10] ⚠️ 同档跨 seed 配置漂移！", flush=True)
        rc = 1
    if not report["all_invariants_passed"]:
        print(f"[exp10] ⚠️ 铁律失败：{report['invariant_failures']}", flush=True)
        rc = 1
    if not report["paired_seeds"] or report["unpaired_seeds"]:
        print(f"[exp10] ⚠️ 配对不完整：{report['unpaired_seeds']}", flush=True)
        rc = 1
    return rc


def paired_or_none(report: Mapping[str, Any]) -> list[int]:
    return list(report.get("paired_seeds") or [])


if __name__ == "__main__":
    raise SystemExit(main())
