# -*- coding: utf-8 -*-
"""exp03_event_rolling 入口：动态事件注入 × 多周期滚动闭环。

编排结构::

    ┌─ 1. 配置装载 ─── config.yaml + --factor → 事件因子配置
    │
    ├─ 2. 场景构建 ─── build.py → 机巢 / 地形 / 代价估算器（共享，与 seed 无关）
    │
    ├─ 3. 因子循环 ─── for factor in E0..E4
    │   ├─ 3a. 因子配置   apply_factor() 覆盖 events.schedule
    │   ├─ 3b. seed 循环  _run_single() → facts / metrics / solution
    │   ├─ 3c. 聚合汇总   build_event_summary() → 跨 seed 统计
    │   ├─ 3d. 产物落盘   metrics.json / run_meta.json / solution.json / effective_config.json
    │   └─ 3e. 审计校验   audit_events.py → 事件协议断言
    │
    ├─ 4. 跨因子汇总 ── summary.json
    │
    └─ 5. 可视化 ────── plot/ → 逐图出片（registry 调度）

命令行::

    python run.py --factor E0                 # 无事件对照
    python run.py --factor E1 --seeds 42,43,44
    python run.py --all                        # 全因子 × 全 seed
    python run.py --factor E2 --dry-run       # 只验生效配置
    python run.py --all --no-plots --no-audit  # 纯数据跑
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
    build_event_summary,
    build_metrics,
    build_run_meta,
    build_solution,
    print_summary,
    write_json,
)
from task_allocation.methods.rolling.horizon import RollingHorizon
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet


# ════════════════════════════════════════════════════════════════
# 1. CLI
# ════════════════════════════════════════════════════════════════

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="exp03_event_rolling")
    p.add_argument("--factor", choices=sorted(FACTOR_DEFS), help="事件因子 E0..E4")
    p.add_argument("--all", action="store_true", help="跑全因子 × 全 seed")
    p.add_argument("--seeds", nargs="+", metavar="SEED", help="覆盖 seeds（支持 42 42,43 42-46）")
    p.add_argument("--dry-run", action="store_true", help="只打印生效配置，不求解")
    p.add_argument("--no-plots", action="store_true", help="跳过出图")
    p.add_argument("--no-audit", action="store_true", help="跳过事件审计")
    p.add_argument("--set", action="append", default=[], metavar="k=v",
                   help="覆盖任意配置节点，如 --set solver.population=30")
    return p.parse_args(argv)


def _coerce(v: str):
    s = v.strip()
    low = s.lower()
    if low in ("null", "none"): return None
    if low == "true": return True
    if low == "false": return False
    if s[:1] in ("[", "{") and s[-1:] in ("]", "}"):
        try: return json.loads(s)
        except json.JSONDecodeError: pass
    try: return int(s)
    except ValueError: pass
    try: return float(s)
    except ValueError: pass
    return s


def _apply_set(cfg: dict, items: list[str]) -> None:
    for it in items:
        if "=" not in it: continue
        path, _, val = it.partition("=")
        parts = [p.strip() for p in path.strip().split(".")]
        d = cfg
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = _coerce(val)


def _parse_seeds(tokens: list[str]) -> list[int]:
    seeds: list[int] = []
    for tok in tokens:
        for part in str(tok).split(","):
            part = part.strip()
            if not part: continue
            if "-" in part[1:]:
                lo, hi = part.split("-", 1)
                lo, hi = int(lo), int(hi)
                if lo > hi: lo, hi = hi, lo
                seeds.extend(range(lo, hi + 1))
            else:
                seeds.append(int(part))
    seen, out = set(), []
    for s in seeds:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ════════════════════════════════════════════════════════════════
# 2. 进度回调
# ════════════════════════════════════════════════════════════════

def _emit_cycle_progress(record, step: int, n_total: int) -> None:
    sel = record.selection or {}
    muas = record.muas or {}
    n_exec = len(getattr(record, "executed_ids", []) or [])
    n_defer = len(getattr(record, "deferred_ids", []) or [])
    n_events = len(getattr(record, "events", []) or [])
    solve = muas.get("solve_seconds", None)
    solve_s = f"{solve:.2f}s" if isinstance(solve, (int, float)) else "?"
    ev_str = f" 事件={n_events}" if n_events else ""
    replan = " [重规划]" if getattr(record, "replan", False) else ""
    print(
        f"[cycle {step}/{n_total}] "
        f"入池={sel.get('n_tasks_in', '?')} 选中={muas.get('n_selected', '?')} "
        f"执行={n_exec} 顺延={n_defer}{ev_str}{replan} | 求解={solve_s}",
        flush=True,
    )


# ════════════════════════════════════════════════════════════════
# 3. 单次运行（seed 级）
# ════════════════════════════════════════════════════════════════

def _run_single(
    raw_cfg: dict, seed: int, factor: str,
    *, scenario, nests, terrain, estimator, verbose: bool,
):
    """单 seed 完整链路：配置 → 场景 → 滚动闭环 → 指标 + 解。"""
    t0 = time.time()

    # 3a. seed 级配置（实验种子 + 求解器种子同步）
    seed_cfg = copy.deepcopy(dict(raw_cfg))
    seed_cfg.setdefault("experiment", {})["seed"] = seed
    seed_cfg.setdefault("solver", {})["seed"] = seed
    bundle = build_config_bundle(seed_cfg, factor=factor)

    # 3b. 实体装配
    uavs = build_uavs(nests, raw_cfg)
    task_cfg = build_task_config(raw_cfg, seed)
    batch = inject_burst_tasks(build_arrivals(nests, scenario, task_cfg, raw_cfg),
                               nests, scenario, raw_cfg)
    if verbose:
        counts = ", ".join(f"周期{c}={n}" for c, n in sorted(batch.per_cycle_counts().items()))
        print(f"[exp03] seed={seed} factor={factor} 任务：共 {len(batch.all_tasks())} 个（{counts}）")

    # 3c. 滚动闭环
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

    # 3d. 产物组装
    facts = RunFacts(
        bundle=bundle, scenario=scenario, nests=nests, terrain=terrain,
        uavs=uavs, initial_uavs=initial_uavs, batch=batch,
        records=records, pool=pool, fleet=fleet,
        wall_seconds=round(time.time() - t0, 3),
    )
    return facts, build_metrics(facts), build_solution(facts)


# ════════════════════════════════════════════════════════════════
# 4. 因子级编排
# ════════════════════════════════════════════════════════════════

def _run_factor(
    factor: str, base_cfg: dict, *,
    scenario, nests, terrain, estimator,
    seeds: list[int], args: argparse.Namespace,
) -> dict:
    """跑一个因子的全 seed，落盘产物，返回 event_summary。"""
    print(f"\n{'=' * 60}")
    print(f"  因子 {factor}: {FACTOR_DEFS[factor]['description']}")
    print(f"{'=' * 60}")

    cfg = apply_factor(base_cfg, factor)
    bundle0 = build_config_bundle(cfg, factor=factor)

    # dry-run 只打印事件配置
    if args.dry_run:
        print(json.dumps(cfg.get("events", {}), ensure_ascii=False, indent=2))
        return {}

    out_dir = EXP_DIR / "results" / factor
    out_dir.mkdir(parents=True, exist_ok=True)

    # 4a. seed 循环
    results = []
    for seed in seeds:
        facts, metrics, solution = _run_single(
            cfg, seed, factor,
            scenario=scenario, nests=nests, terrain=terrain,
            estimator=estimator, verbose=bundle0.verbose,
        )
        results.append((seed, facts, metrics, solution))
        print(f"[exp03] {factor} seed={seed} 完成，耗时 {facts.wall_seconds:.1f}s")

    # 4b. 跨 seed 聚合
    rep_seed, rep_facts, rep_metrics, rep_solution = results[0]
    final_metrics = dict(rep_metrics)
    final_metrics["factor"] = factor
    final_metrics["seeds"] = seeds
    final_metrics["per_seed"] = {
        str(s): {"summary": m["summary"], "cycles": m["cycles"]}
        for s, _, m, _ in results
    }
    event_summary = build_event_summary(results, factor)
    final_metrics["event_summary"] = event_summary

    # 4c. 产物落盘
    if bundle0.save_results:
        meta = build_run_meta(rep_facts, cfg_path=EXP_DIR / "config.yaml", root=ROOT)
        meta["factor"] = factor
        meta["seeds"] = seeds
        write_json(out_dir / "metrics.json", final_metrics)
        write_json(out_dir / "run_meta.json", meta)
        write_json(out_dir / "solution.json", rep_solution)
        write_json(out_dir / "effective_config.json", cfg)
        for s, _, m, sol in results:
            sd = out_dir / f"seed_{s}"
            write_json(sd / "metrics.json", {**m, "seeds": [s]})
            write_json(sd / "solution.json", sol)

    if bundle0.verbose:
        print_summary(final_metrics, out_dir)

    return event_summary


def _run_audit(out_dir: Path, factor: str) -> bool:
    """事件协议断言（成功标准 1/2 自动化）。"""
    import subprocess
    try:
        proc = subprocess.run(
            [sys.executable, str(EXP_DIR / "audit_events.py"),
             "--results", "results", "--factor", factor],
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


# ════════════════════════════════════════════════════════════════
# 5. 主入口
# ════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # ── 1. 配置 ──────────────────────────────────────────────
    base_cfg = load_config(EXP_DIR / "config.yaml")
    _apply_set(base_cfg, args.set)
    factors = sorted(FACTOR_DEFS) if args.all else [args.factor or "E0"]

    # ── 2. 共享场景（与 seed / factor 无关，只建一次）────────
    scenario = build_scenario(base_cfg)
    nests = scenario.nests
    terrain = build_terrain(scenario, base_cfg)
    buildings = build_building_layer(base_cfg, scenario)
    estimator = build_cost_estimator(terrain, base_cfg, buildings=buildings)

    # ── 3. 因子循环 ─────────────────────────────────────────
    all_summaries: dict[str, dict] = {}
    for factor in factors:
        cfg = apply_factor(base_cfg, factor)
        bundle0 = build_config_bundle(cfg, factor=factor)
        seeds = list(bundle0.experiment.get("seeds") or [bundle0.seed])
        if args.seeds:
            seeds = _parse_seeds(args.seeds)

        summary = _run_factor(
            factor, base_cfg,
            scenario=scenario, nests=nests, terrain=terrain, estimator=estimator,
            seeds=seeds, args=args,
        )
        if summary:
            all_summaries[factor] = summary

        # 审计
        if not args.dry_run and bundle0.save_results and not args.no_audit:
            _run_audit(EXP_DIR / "results" / factor, factor)

    if args.dry_run:
        return 0

    # ── 4. 跨因子汇总 ───────────────────────────────────────
    summary_path = EXP_DIR / "results" / "summary.json"
    write_json(summary_path, all_summaries)
    print(f"\n[exp03] 跨因子汇总 → {summary_path}")

    # ── 5. 可视化（registry 逐图调度）────────────────────────
    if not args.no_plots:
        try:
            from plot import make_plots
            figures_dir = EXP_DIR / "results" / "figures"
            make_plots(all_summaries, figures_dir)
            print(f"[exp03] 图件 → {figures_dir}")
        except Exception as exc:
            print(f"[exp03] 出图失败（不影响数据）：{exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
