# -*- coding: utf-8 -*-
"""exp03_event_rolling 入口：动态事件注入 × 多周期滚动闭环。

编排流程::

    config.yaml + --factor → 事件因子配置
    build.py               → 场景 + 任务批次（含 burst）+ 事件翻译
    RollingHorizon.run()   → 多周期滚动 + 事件注入
    metrics.py             → 事件指标 + 跨因子汇总
    audit_events.py        → 事件协议断言
    plot/                  → 图 / 表 / GIF

命令行::

    python run.py --factor E0                 # 无事件对照
    python run.py --factor E1 --seeds 42,43,44
    python run.py --all                        # 全因子 × 全 seed
    python run.py --factor E2 --dry-run       # 只验生效配置
"""

from __future__ import annotations

import copy
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import EXP_DIR, ROOT

from build import (
    TaskBatch,
    build_arrivals,
    build_building_layer,
    build_cost_estimator,
    build_scenario,
    build_task_config,
    build_terrain,
    build_uavs,
    inject_burst_tasks,
)
from config import FACTOR_DEFS, apply_factor, build_config_bundle, load_config
from metrics import (
    RunFacts,
    build_metrics,
    build_run_meta,
    build_solution,
    build_event_summary,
    write_json,
    print_summary,
)

from task_allocation.methods.rolling.horizon import RollingHorizon
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet


def _emit_cycle_progress(record, step: int, n_total: int) -> None:
    sel = record.selection or {}
    muas = record.muas or {}
    n_in = sel.get("n_tasks_in", "?")
    n_sel = muas.get("n_selected", "?")
    n_exec = len(getattr(record, "executed_ids", []) or [])
    n_defer = len(getattr(record, "deferred_ids", []) or [])
    n_events = len(getattr(record, "events", []) or [])
    solve = muas.get("solve_seconds", None)
    solve_s = f"{solve:.2f}s" if isinstance(solve, (int, float)) else "?"
    ev_str = f" 事件={n_events}" if n_events else ""
    replan = " [重规划]" if getattr(record, "replan", False) else ""
    print(
        f"[cycle {step}/{n_total}] 入池={n_in} 选中={n_sel} "
        f"执行={n_exec} 顺延={n_defer}{ev_str}{replan} | 求解={solve_s}",
        flush=True,
    )


def _run_single(
    raw_cfg: dict,
    seed: int,
    factor: str,
    *,
    scenario,
    nests,
    terrain,
    estimator,
    verbose: bool,
):
    t_seed = time.time()
    seed_cfg = copy.deepcopy(dict(raw_cfg))
    seed_cfg.setdefault("experiment", {})["seed"] = seed
    seed_cfg.setdefault("solver", {})["seed"] = seed

    bundle = build_config_bundle(seed_cfg, factor=factor)
    uavs = build_uavs(nests, raw_cfg)
    task_cfg = build_task_config(raw_cfg, seed)
    batch = build_arrivals(nests, scenario, task_cfg, raw_cfg)
    # 注入 burst 任务（E2）
    batch = inject_burst_tasks(batch, nests, scenario, raw_cfg)

    if verbose:
        per_cycle = ", ".join(f"周期{c}={n}" for c, n in sorted(batch.per_cycle_counts().items()))
        print(f"[exp03] seed={seed} factor={factor} 任务：共 {len(batch.all_tasks())} 个（{per_cycle}）")

    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)
    initial_uavs = copy.deepcopy(uavs)

    horizon = RollingHorizon(
        pool, fleet, nests,
        dem=terrain, estimator=estimator,
        horizon_config=bundle.horizon,
        selection_config=bundle.selection,
        problem_config=bundle.problem_model,
        stage_config=bundle.stage_model,
        solver_config=bundle.solver,
        event_config=bundle.event,
        nest_offset=bundle.nest_offset,
    )
    records = horizon.run(
        n_cycles=bundle.n_cycles,
        arrivals=batch.arrivals,
        progress_cb=_emit_cycle_progress if verbose else None,
    )

    facts = RunFacts(
        bundle=bundle,
        scenario=scenario,
        nests=nests,
        terrain=terrain,
        uavs=uavs,
        initial_uavs=initial_uavs,
        batch=batch,
        records=records,
        pool=pool,
        fleet=fleet,
        wall_seconds=round(time.time() - t_seed, 3),
    )
    return facts, build_metrics(facts), build_solution(facts)


def _parse_seed_list(tokens: list[str]) -> list[int]:
    seeds: list[int] = []
    for tok in tokens:
        for part in str(tok).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part[1:]:
                lo_s, hi_s = part.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
                if lo > hi:
                    lo, hi = hi, lo
                seeds.extend(range(lo, hi + 1))
            else:
                seeds.append(int(part))
    seen: set[int] = set()
    out: list[int] = []
    for s in seeds:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="exp03_event_rolling")
    parser.add_argument("--factor", choices=sorted(FACTOR_DEFS), help="事件因子 E0..E4")
    parser.add_argument("--all", action="store_true", help="跑全因子 × 全 seed")
    parser.add_argument("--seeds", nargs="+", metavar="SEED", help="覆盖 seeds")
    parser.add_argument("--dry-run", action="store_true", help="只打印生效配置")
    parser.add_argument("--no-plots", action="store_true")
    parser.add_argument("--no-audit", action="store_true")
    parser.add_argument("--set", action="append", default=[], metavar="k=v")
    return parser.parse_args(argv)


def _coerce(v: str):
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


def _apply_set(cfg: dict, items):
    for it in items:
        if "=" not in it:
            continue
        path, _, val = it.partition("=")
        parts = [p.strip() for p in path.strip().split(".")]
        d = cfg
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = _coerce(val)


def _run_audit(out_dir: Path, factor: str, verbose: bool) -> bool:
    import subprocess
    try:
        proc = subprocess.run(
            [sys.executable, str(EXP_DIR / "audit_events.py"), "--results", out_dir.name, "--factor", factor],
            capture_output=True, text=True, timeout=300,
        )
    except Exception as exc:
        print(f"[exp03] 审计异常：{exc}")
        return True
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.returncode == 1:
        print("\n[exp03] \033[1;31m事件协议审计 FAIL！\033[0m")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg_path = EXP_DIR / "config.yaml"
    base_cfg = load_config(cfg_path)
    _apply_set(base_cfg, args.set)

    factors = sorted(FACTOR_DEFS) if args.all else [args.factor or "E0"]

    # 构建共享场景
    scenario = build_scenario(base_cfg)
    nests = scenario.nests
    terrain = build_terrain(scenario, base_cfg)
    buildings = build_building_layer(base_cfg, scenario)
    estimator = build_cost_estimator(terrain, base_cfg, buildings=buildings)

    all_summaries = {}

    for factor in factors:
        print(f"\n{'='*60}")
        print(f"  因子 {factor}: {FACTOR_DEFS[factor]['description']}")
        print(f"{'='*60}")

        cfg = apply_factor(base_cfg, factor)
        bundle0 = build_config_bundle(cfg, factor=factor)

        if args.dry_run:
            print(json.dumps(cfg.get("events", {}), ensure_ascii=False, indent=2))
            continue

        seeds = list(bundle0.experiment.get("seeds") or [bundle0.seed])
        if args.seeds:
            seeds = _parse_seed_list(args.seeds)

        out_dir = EXP_DIR / "results" / factor
        out_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for seed in seeds:
            facts, metrics, solution = _run_single(
                cfg, seed, factor,
                scenario=scenario, nests=nests, terrain=terrain,
                estimator=estimator, verbose=bundle0.verbose,
            )
            results.append((seed, facts, metrics, solution))
            print(f"[exp03] {factor} seed={seed} 完成，耗时 {facts.wall_seconds:.1f}s")

        rep_seed, rep_facts, rep_metrics, rep_solution = results[0]

        # 多 seed 聚合
        final_metrics = dict(rep_metrics)
        final_metrics["factor"] = factor
        final_metrics["seeds"] = seeds
        final_metrics["per_seed"] = {
            str(seed): {"summary": m["summary"], "cycles": m["cycles"]}
            for seed, _, m, _ in results
        }

        # 事件汇总
        event_summary = build_event_summary(results, factor)
        final_metrics["event_summary"] = event_summary

        if bundle0.save_results:
            meta = build_run_meta(rep_facts, cfg_path=cfg_path, root=ROOT)
            meta["factor"] = factor
            meta["seeds"] = seeds
            write_json(out_dir / "metrics.json", final_metrics)
            write_json(out_dir / "run_meta.json", meta)
            write_json(out_dir / "solution.json", rep_solution)
            write_json(out_dir / "effective_config.json", cfg)
            # 多 seed 逐 seed 落盘
            for seed, _, m, sol in results:
                sd = out_dir / f"seed_{seed}"
                write_json(sd / "metrics.json", {**m, "seeds": [seed]})
                write_json(sd / "solution.json", sol)

        if bundle0.verbose:
            print_summary(final_metrics, out_dir)

        all_summaries[factor] = event_summary

        # 审计
        if bundle0.save_results and not args.no_audit:
            _run_audit(out_dir, factor, verbose=bundle0.verbose)

    # 跨因子 summary.json
    if not args.dry_run:
        summary_path = EXP_DIR / "results" / "summary.json"
        write_json(summary_path, all_summaries)
        print(f"\n[exp03] 跨因子汇总 → {summary_path}")

        # 出图
        if not args.no_plots:
            try:
                from plot import make_plots as _make_plots
                figures_dir = EXP_DIR / "results" / "figures"
                figures_dir.mkdir(parents=True, exist_ok=True)
                _make_plots(all_summaries, figures_dir)
                print(f"[exp03] 图件 → {figures_dir}")
            except Exception as exc:
                print(f"[exp03] 出图失败（不影响数据）：{exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
