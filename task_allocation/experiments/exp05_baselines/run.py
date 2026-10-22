# -*- coding: utf-8 -*-
"""exp05_baselines 入口：整体方法对照实验（方法 vs 基线 / 距离模型）。

对照臂（config.yaml 的 ``methods:`` 段）：

    M0  本文方法：选择 + DMDE-MUAS + 方案 A 终点 + DEM 三维代价
    D0  同架构欧氏对照：仅替换代价/距离函数（DEM → 三维直线）
    B1  random：随机可行分配（性能下界）
    B2  greedy：收益/代价比贪心插入
    B3  vrptw：Solomon 插入式 VRPTW 启发（硬时间窗）
    B2e greedy + 欧氏（朴素策略下距离模型是否仍有影响）

公平性协议（notes.md）：各臂共享同一滚动外壳、同一任务到达序列（同 seed
生成）、同一硬约束定义与评价指标；臂间唯一差异 = 第二阶段搜索引擎
（DMDE vs 构造式启发式）或代价模型（DEM vs 欧氏）。

产物::

    results/<ARM>/seed_<s>/metrics.json     单次运行汇总指标
    results/<ARM>/seed_<s>/run_meta.json    运行元信息（距离口径等）
    results/<ARM>/seed_<s>/solution.json    分配结构（逐周期航次）
    results/<ARM>/summary.json              该臂跨 seed mean±std
    results/summary.csv                     论文主表数据源

命令行覆盖（调试入口）：

    --arms M0,B2        只跑指定臂
    --seeds 42-46       覆盖 experiment.seeds（42 / 42,43 / 42-46 均可）
"""

from __future__ import annotations

import argparse
import copy
import csv
import itertools
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 任意调用方式可跑（python -m / 任意 cwd）：脚本目录先入 sys.path，
# 供 `from paths import` 在非脚本路径调用下也能解析（2026-10-22 修复）。
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

from paths import DATA_DIR, EXP_DIR, ROOT

from build import (
    build_arrivals,
    build_cost_estimator,
    build_scenario,
    build_task_config,
    build_terrain,
    build_uavs,
)
from config import ConfigBundle, MethodArm, build_config_bundle, load_config
from metrics import (
    CSV_COLUMNS,
    aggregate_summaries,
    build_summary,
    summary_csv_row,
    write_json,
)

from task_allocation.baselines import get_baseline
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
from task_allocation.methods.rolling.horizon import RollingHorizon
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet

__all__ = ["main"]


# ---------------------------------------------------------------------------
# 求解器工厂
# ---------------------------------------------------------------------------


def make_solver_factory(arm: MethodArm, seed: int):
    """对照臂 → 每周期求解器工厂。

    - ``dmde``：返回 None → ``run_selective_muas`` 走默认 DMDE 主算法；
    - 其它：从 ``baselines.REGISTRY`` 实例化（每周期一个新实例）。
      ``random`` 补确定性种子（实验 seed × 100000 + 周期序号），
      保证同 seed 可复现、不同周期不重复同一随机流。
    """
    if arm.solver == "dmde":
        return None
    cls = get_baseline(arm.solver)
    counter = itertools.count()

    def factory():
        params = dict(arm.params)
        if arm.solver == "random" and "seed" not in params:
            params["seed"] = int(seed) * 100_000 + next(counter)
        return cls(**params)

    return factory


# ---------------------------------------------------------------------------
# 单次运行（一个 arm × 一个 seed）
# ---------------------------------------------------------------------------


def run_once(
    bundle: ConfigBundle,
    arm: MethodArm,
    seed: int,
    *,
    scenario: Any,
    nests: list,
    terrain: Any,
    estimator: Any,
    batch: Any,
    verbose: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """跑一个对照臂的一次滚动闭环，返回 (summary, run_meta, solution)。"""
    cfg = bundle.raw
    # 求解器种子 = 实验种子（DMDE 搜索随机性与任务生成同步可复现）
    solver_cfg = DMDEConfig(
        pop_size=bundle.solver.pop_size,
        max_generations=bundle.solver.max_generations,
        zeta=bundle.solver.zeta,
        delta=bundle.solver.delta,
        seed=seed,
        verbose=bundle.solver.verbose,
        log_interval=bundle.solver.log_interval,
        lamarckian=bundle.solver.lamarckian,
    )

    # 代价模型切换（D0 同架构对照）：DEM 三维代价 vs 三维直线。
    # 任务实例与场景逐位一致，只换求解端的距离/代价函数。
    use_dem = arm.cost_model == "dem"
    uavs = build_uavs(nests, cfg)
    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)

    horizon = RollingHorizon(
        pool,
        fleet,
        nests,
        dem=(terrain if use_dem else None),
        estimator=(estimator if use_dem else None),
        horizon_config=bundle.horizon,
        selection_config=bundle.selection,
        problem_config=bundle.problem_model,
        stage_config=bundle.stage_model,
        solver_config=solver_cfg,
        event_config=bundle.event,
        nest_offset=bundle.nest_offset,
        solver_factory=make_solver_factory(arm, seed),
    )
    t0 = time.time()
    # 每臂独立任务副本：TaskPool 会原位改写 status / meta，必须各臂各一份
    arrivals = copy.deepcopy(batch.arrivals)
    records = horizon.run(n_cycles=bundle.horizon.n_cycles, arrivals=arrivals)
    wall = round(time.time() - t0, 3)

    summary = build_summary(
        records,
        pool=pool,
        batch=batch,
        problem_model=bundle.problem_model,
        wall_seconds=wall,
    )
    summary.update({"arm": arm.id, "solver": arm.solver, "cost_model": arm.cost_model, "seed": seed})

    run_meta = {
        "experiment": bundle.experiment.get("name"),
        "arm": arm.id,
        "solver": arm.solver,
        "cost_model": arm.cost_model,
        # 距离定义必须随结果落盘（欧氏组单独注明，notes.md 主指标口径）
        "distance_definition": (
            "dem_vertical_section_3d" if use_dem else "euclidean_3d"
        ),
        "seed": seed,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str((EXP_DIR / "config.yaml").relative_to(ROOT)),
        "repo_root": str(ROOT),
    }

    solution = {
        "arm": arm.id,
        "seed": seed,
        "tasks": [
            {
                "id": int(t.id),
                "x": round(float(t.x), 3),
                "y": round(float(t.y), 3),
                "z": round(float(t.z), 3),
                "reward": round(float(t.reward), 4),
                "arrival_time": round(float(t.arrival_time), 3),
                "earliest": t.earliest,
                "latest": t.latest,
                "status": t.status.value,
            }
            for t in batch.all_tasks()
        ],
        "uavs_initial": [
            {"id": int(u.id), "x": float(u.x), "y": float(u.y), "z": float(u.z),
             "nest_id": u.nest_id, "remaining_range": float(u.remaining_range)}
            for u in build_uavs(nests, cfg)
        ],
        "cycles": [
            {
                "cycle": r.cycle,
                "executed_ids": list(r.executed_ids),
                "deferred_ids": list(r.deferred_ids),
                "completed_ids": list(r.completed_ids),
                "infeasible_ids": list(r.infeasible_ids),
                "late_ids": list(r.late_ids),
                "tours": list(r.tours),
                "cost_history": list(r.cost_history),
            }
            for r in records
        ],
    }
    return summary, run_meta, solution


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_seed_list(tokens: list[str]) -> list[int]:
    """解析 ``--seeds``：42 / 42,43 / 42-46 混用，去重保序（与 exp01 同构）。"""
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
    if not seeds:
        raise argparse.ArgumentTypeError("--seeds 解析结果为空")
    out: list[int] = []
    seen: set[int] = set()
    for s in seeds:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _coerce_set_value(v: str):
    s = v.strip().lower()
    if s in ("null", "none"):
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return v


def _apply_dotted_set(cfg: dict, items) -> None:
    """--set dotted.path=value 深合并进 raw_cfg（与 exp01/run_chunked 同语义）。"""
    if not items:
        return
    for it in items:
        if "=" not in it:
            raise argparse.ArgumentTypeError(
                f"--set 需要 dotted.path=value 形式：{it!r}")
        path, _, val = it.partition("=")
        parts = [p.strip() for p in path.strip().split(".")]
        d = cfg
        for p in parts[:-1]:
            d = d.setdefault(p, {})
            if not isinstance(d, dict):
                raise argparse.ArgumentTypeError(
                    f"--set 路径冲突（非 dict 节点）：{path}")
        d[parts[-1]] = _coerce_set_value(val)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 exp05_baselines 对照实验")
    parser.add_argument(
        "--arms", nargs="+", metavar="ARM",
        help="只跑指定臂（M0 D0 B1 B2 B3 B2e…）；支持逗号分隔；省略跑全部",
    )
    parser.add_argument(
        "--seeds", nargs="+", metavar="SEED",
        help="覆盖 experiment.seeds：42 / 42,43,44 / 42-46（可混用）",
    )
    parser.add_argument(
        "--set", action="append", default=[], metavar="dotted.path=value",
        help="覆盖 config.yaml 任意键，便于 500 代等敏感性实验，"
             "如 --set solver.max_iter=500；支持多次。",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="已存在 results/<ARM>/seed_<s>/metrics.json 的组合也重跑"
             "（默认断点续跑：直接读盘复用，只补缺失组合）",
    )
    return parser.parse_args(argv)


def _flatten_arm_ids(tokens: list[str] | None) -> list[str] | None:
    if not tokens:
        return None
    out: list[str] = []
    for tok in tokens:
        out.extend(p.strip() for p in str(tok).split(",") if p.strip())
    return out or None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg_path = EXP_DIR / "config.yaml"
    raw_cfg = load_config(cfg_path)
    _apply_dotted_set(raw_cfg, args.set)
    bundle = build_config_bundle(raw_cfg)

    seeds = list(bundle.experiment.get("seeds") or [bundle.seed])
    if args.seeds:
        seeds = _parse_seed_list(args.seeds)
    arm_ids = _flatten_arm_ids(args.arms)
    arms = [a for a in bundle.arms if arm_ids is None or a.id in arm_ids]
    if not arms:
        known = ", ".join(a.id for a in bundle.arms)
        raise SystemExit(f"没有匹配的对照臂（--arms {arm_ids}）；可用：{known}")

    verbose = bundle.verbose
    out_root = EXP_DIR / str(bundle.experiment.get("output_dir", "results"))
    out_root.mkdir(parents=True, exist_ok=True)

    # ── 场景（与 seed / arm 无关，只构建一次）────────────────────────
    scenario = build_scenario(raw_cfg, DATA_DIR)
    nests = scenario.nests
    terrain = build_terrain(scenario, raw_cfg)
    estimator = build_cost_estimator(terrain, raw_cfg)
    if verbose:
        print(
            f"[exp05] 场景：{scenario.count} 机巢 × 容量 {nests[0].capacity}，"
            f"DEM={'已接入' if terrain is not None else '未接入'}；"
            f"对照臂：{', '.join(a.id for a in arms)}；seeds={seeds}"
        )

    # ── arm × seed 网格 ─────────────────────────────────────────────
    per_arm: dict[str, list[dict[str, Any]]] = {a.id: [] for a in arms}
    for seed in seeds:
        task_cfg = build_task_config(raw_cfg, seed)
        batch = build_arrivals(nests, scenario, task_cfg, raw_cfg)
        if verbose:
            counts = ", ".join(f"周期{c}={n}" for c, n in batch.per_cycle_counts().items())
            print(f"[exp05] seed={seed} 任务：共 {len(batch.all_tasks())} 个（{counts}）")
        for arm in arms:
            run_dir = out_root / arm.id / f"seed_{seed}"
            # 断点续跑：已有 metrics.json 的组合直接读盘复用（网格中断后
            # 只补缺失组合；--force 强制全量重跑）。
            metrics_path = run_dir / "metrics.json"
            if metrics_path.is_file() and not args.force:
                import json as _json

                summary = _json.loads(metrics_path.read_text(encoding="utf-8"))
                per_arm[arm.id].append(summary)
                if verbose:
                    print(f"[exp05] {arm.id:<4} seed={seed}：已存在，复用结果（--force 重跑）")
                continue
            t0 = time.time()
            summary, run_meta, solution = run_once(
                bundle, arm, seed,
                scenario=scenario, nests=nests, terrain=terrain,
                estimator=estimator, batch=batch, verbose=verbose,
            )
            per_arm[arm.id].append(summary)
            write_json(run_dir / "metrics.json", summary)
            write_json(run_dir / "run_meta.json", run_meta)
            write_json(run_dir / "solution.json", solution)
            if verbose:
                print(
                    f"[exp05] {arm.id:<4} seed={seed}：完成率 "
                    f"{summary['completion_rate']:.3f} / 有效完成 "
                    f"{summary['effective_completion_rate']:.3f} / 奖励产出 "
                    f"{summary['reward_yield']:.3f} / 按时 "
                    f"{summary['on_time_rate']:.3f} / 航程 "
                    f"{summary['total_path_m'] / 1000:.1f} km / "
                    f"{time.time() - t0:.1f}s"
                )

    # ── 聚合与 summary.csv ─────────────────────────────────────────
    rows: list[dict[str, Any]] = []
    for arm in arms:
        summaries = per_arm[arm.id]
        agg = aggregate_summaries(summaries)
        write_json(
            out_root / arm.id / "summary.json",
            {
                "arm": arm.id,
                "solver": arm.solver,
                "cost_model": arm.cost_model,
                "label": arm.label,
                "seeds": seeds,
                "agg": agg,
                "per_seed": summaries,
            },
        )
        for s in summaries:
            rows.append(
                summary_csv_row(
                    s, arm=arm.id, solver=arm.solver,
                    cost_model=arm.cost_model, seed=int(s["seed"]),
                )
            )
        rows.append(
            summary_csv_row(
                summaries[0], arm=arm.id, solver=arm.solver,
                cost_model=arm.cost_model, seed="mean", agg=agg,
            )
        )

    csv_path = out_root / "summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in CSV_COLUMNS})

    if verbose:
        print(f"[exp05] 汇总表：{csv_path.relative_to(ROOT)}")
        print(f"[exp05] 全部 {len(arms)} 臂 × {len(seeds)} seed 运行完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
