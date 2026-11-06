# -*- coding: utf-8 -*-
"""exp07 的第二组对照：L3b（交付后跨机改派归属）到底需不需要。

用户裁定（2026-11-06）：「我们的方法需要什么就做什么，不需要什么去掉什么；
不确定的话就做实验对比」。本脚本就是那个对比，**不再讨论默认值开还是关**，
而是给出「开 vs 关」的同 seed 配对数字，供去留判断。

两档唯一差异：``l3b_enabled``（False / True），由 ``dataclasses.replace``
锁定；``cost_polish_accept_mode`` 两档都固定 ``path``，避免与采纳判据对照
混在一起。配置 diff 落盘并断言只有一个字段不同。

口径纪律：

* 串行、同 seed 配对、按 seed 交替两档先后（抵消缓存预热）；
* 复用 ``metrics.collect_run_metrics``（含完成率/恒等式硬断言）与
  ``metrics.build_pair_report``（配对差值 + 精确 t 检验），**不发明新口径**；
* ``preset=mid`` 只为在可控时间内拿到足够多的交付后 polish 候选，
  **不是正式档**，结论不可外推正式档；
* 完成率铁律与恒等式沿用本实验已修好的硬断言口径。

命令行::

    python compare_l3b.py                                  # mid 档 3 seed
    python compare_l3b.py --preset smoke --seeds 7        # 快速自检
    python compare_l3b.py --preset formal --seeds 7,11,23 # 正式档（长跑）
    python compare_l3b.py --preset mid --set problem.uav_speed=4.0  # 制造迟到
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

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
    build_pair_report,
    collect_run_metrics,
    write_json,
    write_summary_csv,
)
from run import (  # noqa: E402
    _apply_dotted_set,
    _parse_seed_list,
    _stage_effective,
)
from task_allocation.methods.rolling.horizon import RollingHorizon  # noqa: E402
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402

MODES = ("off", "on")

#: 可控规模：比冒烟大（让交付后 polish 有候选）、比正式档小（几分钟内跑完）。
#: ⚠️ 仍属「非冒烟的中间档」，结论不可外推正式档。
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

L3B_CAVEATS = [
    "本对照回答的是「阶段二方法是否需要交付后跨机改派归属」，不是「默认值该设成什么」。",
    "两档唯一差异是 l3b_enabled；cost_polish_accept_mode 固定 path，"
    "避免把采纳判据的效应混进 L3b 的配对差值。",
    "L3b 同时约束两个通道（polish_assignment 的 relocate/swap、交付后成本 polish "
    "的 _cost_polish_relocate）；L3a 纯几何（组内 2-opt/Or-opt、终点巢 MCF）不受影响。",
    "coverage_first 单调 polish 的跨机 relocate 是第三来源，不受本总闸；"
    "本实验 coverage_first=False，l3b_ungated_monotone_relocate 应恒 0。",
    "preset=mid 是可控中间档，不是正式档：只用于判断去留方向，结论不可外推正式档；"
    "若 mid 档零差异而论文需要正式结论，须单独跑 --preset formal。",
    "配对差值不得写成「独立收益」；两档单因素设计没有 2×2 交互项。",
    "完成率/恒等式/eliminating 为实验层硬断言；independent_audit 的几何重建重评"
    "仍属技术债（problem 实例未由 RollingHorizon 暴露）。",
]


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="L3b 去留的同 seed 配对对照")
    p.add_argument("--preset", choices=tuple(PRESETS), default="mid")
    p.add_argument("--seeds", nargs="+", metavar="SEED")
    p.add_argument("--set", action="append", default=[], metavar="dotted.path=value")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def _dotted_set(cfg: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    d = cfg
    for part in parts[:-1]:
        d = d.setdefault(part, {})
        if not isinstance(d, dict):
            raise ValueError(f"预设路径冲突：{dotted}")
    d[parts[-1]] = value


def _run_once(
    raw_cfg: dict,
    seed: int,
    mode: str,
    *,
    scenario: Any,
    nests: list,
    terrain: Any,
    buildings: Any,
    estimator: Any,
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    t0 = time.time()
    seed_cfg = copy.deepcopy(dict(raw_cfg))
    seed_cfg.setdefault("experiment", {})["seed"] = seed
    seed_cfg.setdefault("solver", {})["seed"] = seed
    bundle = build_config_bundle(seed_cfg)

    # 唯一对照变量：只翻 L3b 总闸；采纳判据两档都固定 path。
    stage_cfg = replace(
        bundle.stage_model,
        l3b_enabled=(mode == "on"),
        cost_polish_accept_mode="path",
    )
    eff = bool(stage_cfg.effective_l3b_enabled)
    if eff != (mode == "on"):
        raise RuntimeError(
            f"总闸没有按预期生效：mode={mode} effective={eff} "
            f"source={stage_cfg.l3b_source}"
        )

    uavs = build_uavs(nests, raw_cfg)
    task_cfg = build_task_config(raw_cfg, seed)
    batch = build_arrivals(nests, scenario, task_cfg, raw_cfg)
    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)
    horizon = RollingHorizon(
        pool, fleet, nests, dem=terrain, estimator=estimator,
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
        expected_accept_mode="path",  # L3b 对照里采纳判据两档固定 path
    )
    stage_eff = _stage_effective(stage_cfg)
    meta = {
        "experiment": "exp07_l3b_necessity",
        "mode": mode,
        "seed": seed,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "stage_effective": stage_eff,
        "arrivals_per_cycle": batch.per_cycle_counts(),
        "stop_reason": getattr(records[-1], "stop_reason", None) if records else None,
    }
    return metrics, meta, stage_cfg


def _config_diff(stages: Mapping[str, list[Any]]) -> dict[str, Any]:
    if set(stages) != set(MODES) or any(not stages[m] for m in MODES):
        counts = {m: len(v) for m, v in stages.items()}
        return {"error": f"样本不完整：{counts}"}
    a, b = asdict(stages["off"][0]), asdict(stages["on"][0])
    diff = {
        k: {"off": a.get(k), "on": b.get(k)}
        for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)
    }
    within = {
        m: all(asdict(s) == asdict(stages[m][0]) for s in stages[m]) for m in MODES
    }
    return {
        "differing_fields": diff,
        "only_l3b_enabled_differs": set(diff) == {"l3b_enabled"},
        "n_differing_fields": len(diff),
        "per_seed_samples": {m: len(stages[m]) for m in MODES},
        "within_mode_identical_across_seeds": within,
        "all_within_mode_identical": all(within.values()),
    }


def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    raw_cfg = load_config(EXP_DIR / "config.yaml")
    for dotted, value in PRESETS[args.preset].items():
        _dotted_set(raw_cfg, dotted, value)
    _apply_dotted_set(raw_cfg, args.set)
    if args.seeds:
        raw_cfg.setdefault("experiment", {})["seeds"] = _parse_seed_list(args.seeds)
    seeds = [int(s) for s in (raw_cfg["experiment"].get("seeds")
                              or [raw_cfg["experiment"].get("seed")])]
    print(
        f"[l3b] preset={args.preset} modes={MODES} seeds={seeds} "
        f"n_tasks={raw_cfg['tasks']['n_tasks']} "
        f"pop={raw_cfg['solver']['population']} "
        f"gen={raw_cfg['solver']['max_iter']} "
        f"n_cycles={raw_cfg['rolling']['n_cycles']}",
        flush=True,
    )
    if args.dry_run:
        print(json.dumps(raw_cfg, ensure_ascii=False, indent=2, default=str))
        return 0

    out_root = EXP_DIR / "results" / f"l3b_{args.preset}"
    scenario = build_scenario(raw_cfg)
    terrain = build_terrain(scenario, raw_cfg)
    buildings = build_building_layer(raw_cfg, scenario)
    estimator = build_cost_estimator(terrain, raw_cfg, buildings=buildings)
    print(
        f"[l3b] 场景：{scenario.count} 机巢，"
        f"建筑={buildings.count if buildings is not None else 0} 栋",
        flush=True,
    )

    per_mode: dict[str, list[dict[str, Any]]] = {m: [] for m in MODES}
    stage_of_mode: dict[str, list[Any]] = {m: [] for m in MODES}
    for seed_idx, seed in enumerate(seeds):
        modes = MODES if seed_idx % 2 == 0 else tuple(reversed(MODES))
        for mode in modes:
            metrics, meta, stage = _run_once(
                raw_cfg, seed, mode,
                scenario=scenario, nests=scenario.nests, terrain=terrain,
                buildings=buildings, estimator=estimator,
            )
            per_mode[mode].append(metrics)
            stage_of_mode[mode].append(stage)
            run_dir = out_root / mode / f"seed_{seed}"
            write_json(run_dir / "metrics.json", metrics)
            write_json(run_dir / "run_meta.json", meta)
            print(
                f"[l3b] {mode:<3} seed={seed}: "
                f"完成率={metrics['completion_rate_final']:.3f} "
                f"按时={metrics['on_time_rate_final']:.3f} "
                f"航程={metrics['total_path_m'] / 1000:.2f}km "
                f"f之和={metrics['final_fitness_sum']:.3f} "
                f"L3b动作={metrics['l3b_relocate_actions']} "
                f"归属变动={metrics['n_task_to_uav_changes']} "
                f"检查={'PASS' if metrics['invariants_passed'] else 'FAIL'} "
                f"墙钟={metrics['wall_seconds']:.1f}s",
                flush=True,
            )

    config_diff = _config_diff(stage_of_mode)
    report = build_pair_report(
        per_mode, seeds=seeds, profile=f"l3b_{args.preset}",
        config_diff=config_diff, modes=MODES,
        caveats=L3B_CAVEATS, subject="L3b 开与关",
    )
    report["preset"] = args.preset
    report["question"] = "阶段二方法是否需要交付后跨机改派归属（L3b）"
    write_json(out_root / "summary.json", report)
    write_summary_csv(out_root / "summary.csv", per_mode)

    print(
        f"[l3b] 配置 diff 字段数={config_diff.get('n_differing_fields')} "
        f"仅总闸不同={config_diff.get('only_l3b_enabled_differs')} "
        f"各项检查全过={report['all_invariants_passed']} "
        f"配对 seed={report['paired_seeds']}",
        flush=True,
    )
    print(f"[l3b] verdict: {report['verdict']}", flush=True)
    for key in ("total_path_m", "final_fitness_sum", "on_time_rate_final"):
        v = report["metrics"][key]
        print(
            f"[l3b]   {key}: off={v['per_seed_path']} on={v['per_seed_f']} "
            f"mean_diff={v['mean_diff']} p={v['p_two_sided']} ({v['p_reason']})",
            flush=True,
        )
    # 动作数属过程量、不在配对指标键里，逐 seed 单列展示
    for label in MODES:
        rows = {int(r["seed"]): r for r in per_mode[label]}
        print(
            f"[l3b]   {label} 档 L3b 归属动作 = "
            f"{[rows[s]['l3b_relocate_actions'] for s in report['paired_seeds']]}",
            flush=True,
        )
    print(f"[l3b] 产物目录：{out_root}", flush=True)

    rc = 0
    if not config_diff.get("only_l3b_enabled_differs"):
        print("[l3b] ⚠️ 两档配置存在额外差异，配对结论不可用！", flush=True)
        rc = 1
    if config_diff.get("all_within_mode_identical") is False:
        print("[l3b] ⚠️ 同档跨 seed 配置漂移！", flush=True)
        rc = 1
    if not report["all_invariants_passed"]:
        print(f"[l3b] ⚠️ 铁律检查失败：{report['invariant_failures']}", flush=True)
        rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
