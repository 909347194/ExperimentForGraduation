# -*- coding: utf-8 -*-
"""exp08_gain_decomposition —— 交付后精修的「几何 vs 归属」收益拆分。

定稿 §11.4 P4 待办第 1 项：用 P3-b 的三本账
（``l3a_geometric_actions`` / ``l3b_relocate_actions`` /
``n_task_to_uav_changes``）配合同 seed 配对，把交付后精修的收益拆开。

**2×2 同 seed 配对**（四档标签直白命名，不用「臂 / 变体」黑话）::

                     归属关 B=0        归属开 B=1
    几何关 G=0        g0b0              g0b1
    几何开 G=1        g1b0              g1b1

* 因子 G（几何 L3a）：``cost_polish_enabled`` 控制交付后
  组内 2-opt/Or-opt + 终点巢 MCF；``polish_moves`` 的 position 也属 L3a。
* 因子 B（归属 L3b）：``l3b_enabled`` 同时约束 polish_assignment 的
  relocate/swap 与交付后成本 polish 的跨机 relocate。

⚠️ **口径限制（必须在论文里如实声明）**：``cost_polish_enabled=False``
会整段跳过 ``post_cost_polish``，因此 G=0 档的 B=1 **只覆盖
polish_assignment 的 relocate/swap**，不含通道 B 的交付后跨机改派。
交互项因此混有「算子可得性」差异，不能解释成纯二阶交互；本实验只报
主效应与交互项并列，不写「独立收益」（定稿 §11.4 / P4 纪律）。

命令行::

    python run.py --preset smoke --seeds 7            # 设计自检
    python run.py --preset mid --seeds 7,11,23        # 中间档（不可外推）
    python run.py --preset formal --seeds 7,11,23     # 正式档

产物 ``results/<preset>/``：逐档逐 seed 的 metrics/run_meta、
``summary.json``（2×2 主效应 + 交互项 + 三本账）、``summary.csv``。
跑批不渲图；图由 ``plot/compare.py`` 事后单独生成。
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

MODES = ("g0b0", "g0b1", "g1b0", "g1b1")

#: 四档唯一差异；其余字段逐位不动（用 dataclasses.replace 锁定）。
MODE_SPEC: dict[str, dict[str, Any]] = {
    "g0b0": dict(cost_polish_enabled=False, l3b_enabled=False,
                 polish_moves=()),
    "g0b1": dict(cost_polish_enabled=False, l3b_enabled=True,
                 polish_moves=("relocate", "swap")),
    "g1b0": dict(cost_polish_enabled=True, l3b_enabled=False,
                 polish_moves=None),
    "g1b1": dict(cost_polish_enabled=True, l3b_enabled=True,
                 polish_moves=None),
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

SMOKE_SIGNATURE = {
    "tasks.n_tasks": 8,
    "solver.population": 12,
    "solver.max_iter": 20,
    "rolling.n_cycles": 8,
    "rolling.arrivals.counts": [2, 2],
}

#: 配对质量指标（越小/越好分开判读；这里只报差值，不做方向性断言）
QUALITY_KEYS = (
    "final_fitness_sum", "total_path_m", "on_time_rate_final",
    "total_lateness_s", "n_hard_violations_total", "completion_rate_final",
)
#: P3-b 三本账（过程量，不进配对 t 检验）
LEDGER_KEYS = (
    "l3a_geometric_actions", "l3b_relocate_actions",
    "n_task_to_uav_changes", "cost_polish_n_accepted",
    "l3b_ungated_monotone_relocate",
)

CAVEATS = [
    "2×2 单因素设计的主效应与交互项并列报告；禁写「独立收益」。",
    "G=0 档（cost_polish_enabled=False）整段跳过 post_cost_polish，"
    "B=1 时只剩 polish_assignment 的 relocate/swap，不含交付后通道 B；"
    "交互项混有算子可得性差异，不能解释为纯二阶交互。",
    "若 g0b1 三本账显示零/极少归属动作，说明 B 因子在 G=0 档未真正"
    "激活，交互项主要反映「G=1 档才拿得到通道 B」，不得写作「协同效应」。",
    "终点机巢 MCF 是给定路径下的条件最优（非全局最优）。",
    "mid / smoke 结论不可外推正式档；正式档须单独跑批。",
    "单一场景（广州 3 机巢）、固定到达流、3 seed ⇒ df=2 效能极低；"
    "p 不显著须如实声明，也不得写成「无差异」。",
    "完成率/恒等式/eliminating 为实验层硬断言；independent_audit 的"
    "几何重建重评仍属技术债（RollingHorizon 未暴露审计钩子）。",
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
    p = argparse.ArgumentParser(description="交付后精修 2×2 收益拆分")
    p.add_argument("--preset", choices=tuple(PRESETS), default="mid")
    p.add_argument("--seeds", nargs="+", metavar="SEED")
    p.add_argument("--set", action="append", default=[], metavar="dotted.path=value")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def _stage_effective(stage: Any) -> dict[str, Any]:
    keys = ("cost_polish_enabled", "l3b_enabled", "effective_l3b_enabled",
            "l3b_source", "polish_moves", "effective_polish_moves",
            "effective_cost_polish_relocate", "cost_polish_accept_mode",
            "cost_polish_accept_mode_effective", "cost_polish_eps_f",
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
        cost_polish_enabled=bool(spec["cost_polish_enabled"]),
        l3b_enabled=bool(spec["l3b_enabled"]),
        polish_moves=(None if spec["polish_moves"] is None
                      else tuple(spec["polish_moves"])),
    )
    eff_l3b = bool(stage_cfg.effective_l3b_enabled)
    if eff_l3b != bool(spec["l3b_enabled"]):
        raise RuntimeError(
            f"{mode}: 总闸未按预期生效 effective={eff_l3b} "
            f"source={stage_cfg.l3b_source}"
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
        "experiment": "exp08_gain_decomposition",
        "mode": mode, "seed": seed,
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
    allowed = {"cost_polish_enabled", "l3b_enabled", "polish_moves",
               "cost_polish_accept_mode"}
    if set(stages) != set(MODES) or any(not stages[m] for m in MODES):
        return {"error": "样本不完整", "counts": {m: len(v) for m, v in stages.items()}}
    base = asdict(stages["g0b0"][0])
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
) -> dict[str, Any]:
    rows = {m: {int(r["seed"]): r for r in per_mode[m]} for m in MODES}
    paired = [s for s in seeds if all(s in rows[m] for m in MODES)]
    unpaired = [s for s in seeds if s not in paired]

    def vals(mode: str, key: str) -> list[float]:
        return [float(rows[mode][s].get(key, 0.0) or 0.0) for s in paired]

    report: dict[str, Any] = {
        "experiment": "exp08_gain_decomposition",
        "profile": profile, "seeds": list(seeds),
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

    # 主效应 / 交互（定稿口径 interaction = (f00-f01)-(f10-f11)）
    for key in QUALITY_KEYS:
        f00, f01 = vals("g0b0", key), vals("g0b1", key)
        f10, f11 = vals("g1b0", key), vals("g1b1", key)
        report["effects"][key] = {
            "main_G_at_B0": _paired_stats([a - b for a, b in zip(f10, f00)]),
            "main_G_at_B1": _paired_stats([a - b for a, b in zip(f11, f01)]),
            "main_B_at_G0": _paired_stats([a - b for a, b in zip(f01, f00)]),
            "main_B_at_G1": _paired_stats([a - b for a, b in zip(f11, f10)]),
            "interaction": _paired_stats(
                [(a - b) - (c - d) for a, b, c, d in zip(f00, f01, f10, f11)]
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
        "2×2 主效应与交互项见 effects；差值均为同 seed 配对，"
        "不得写成独立收益。"
        f" 交互项均值={f['interaction']['mean']}，"
        f"p={f['interaction']['p_two_sided']}（{f['interaction']['p_reason']}）。"
        " 若 p 不显著须如实声明；G=0 档 B=1 的算子可得性限制见 caveats。"
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
    _apply_dotted_set(raw_cfg, args.set)
    if args.seeds:
        raw_cfg.setdefault("experiment", {})["seeds"] = _parse_seeds(args.seeds)
    if args.preset == "formal":
        _check_formal(raw_cfg)
    seeds = [int(s) for s in (raw_cfg["experiment"].get("seeds")
                              or [raw_cfg["experiment"].get("seed")])]
    print(
        f"[exp08] preset={args.preset} modes={MODES} seeds={seeds} "
        f"n_tasks={_get(raw_cfg, 'tasks.n_tasks')} "
        f"pop={_get(raw_cfg, 'solver.population')} "
        f"gen={_get(raw_cfg, 'solver.max_iter')} "
        f"n_cycles={_get(raw_cfg, 'rolling.n_cycles')}",
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
    print(
        f"[exp08] 场景：{scenario.count} 机巢，"
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
                f"[exp08] {mode} seed={seed}: "
                f"f={metrics['final_fitness_sum']:.3f} "
                f"航程={metrics['total_path_m'] / 1000:.2f}km "
                f"完成率={metrics['completion_rate_final']:.3f} "
                f"按时={metrics['on_time_rate_final']:.3f} "
                f"L3a={metrics['l3a_geometric_actions']} "
                f"L3b={metrics['l3b_relocate_actions']} "
                f"归属变动={metrics['n_task_to_uav_changes']} "
                f"检查={'PASS' if metrics['invariants_passed'] else 'FAIL'} "
                f"墙钟={time.time() - t0:.1f}s",
                flush=True,
            )

    purity = _config_purity(stage_of_mode)
    report = _build_2x2_report(
        per_mode, seeds=seeds, profile=args.preset, purity=purity,
    )
    write_json(out_root / "summary.json", report)
    write_summary_csv(out_root / "summary.csv", per_mode)
    f = report["effects"]["final_fitness_sum"]
    p = report["effects"]["total_path_m"]
    print(
        f"[exp08] 配置纯度={purity.get('only_expected_fields_differ')} "
        f"差异字段={purity.get('n_differing_fields')} "
        f"铁律全过={report['all_invariants_passed']} 配对={paired_or_none(report)}",
        flush=True,
    )
    print(
        f"[exp08] f 主效应 G@B0={f['main_G_at_B0']['mean']} "
        f"G@B1={f['main_G_at_B1']['mean']} "
        f"B@G0={f['main_B_at_G0']['mean']} "
        f"B@G1={f['main_B_at_G1']['mean']} "
        f"交互={f['interaction']['mean']} p={f['interaction']['p_two_sided']}",
        flush=True,
    )
    print(
        f"[exp08] 航程 交互={p['interaction']['mean']} "
        f"p={p['interaction']['p_two_sided']}",
        flush=True,
    )
    print(f"[exp08] verdict: {report['verdict']}", flush=True)
    print(f"[exp08] 产物目录：{out_root}", flush=True)

    rc = 0
    if not purity.get("only_expected_fields_differ"):
        print(f"[exp08] ⚠️ 配置出现非预期差异：{purity.get('unexpected_fields')}", flush=True)
        rc = 1
    if purity.get("all_within_mode_identical") is False:
        print("[exp08] ⚠️ 同档跨 seed 配置漂移！", flush=True)
        rc = 1
    if not report["all_invariants_passed"]:
        print(f"[exp08] ⚠️ 铁律失败：{report['invariant_failures']}", flush=True)
        rc = 1
    if not report["paired_seeds"] or report["unpaired_seeds"]:
        print(f"[exp08] ⚠️ 配对不完整：{report['unpaired_seeds']}", flush=True)
        rc = 1
    return rc


def paired_or_none(report: Mapping[str, Any]) -> list[int]:
    return list(report.get("paired_seeds") or [])


if __name__ == "__main__":
    raise SystemExit(main())
