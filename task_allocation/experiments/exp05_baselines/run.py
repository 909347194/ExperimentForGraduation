# -*- coding: utf-8 -*-
"""exp05_baselines v3 入口：方法主张检验台（2026-11-09 重新设计）。

设计定稿：``task_allocation/docs/exp05_redesign_2026-11-09.md``。
v3 不再问「我们是否比启发式更好」，而检验五条可证伪主张（C1–C5）。

与 v2 的三处关键差异：

1. **评价端统一**：所有方法的交付解一律按 DEM 有向三维航程重评；
   同一批交付解再用两把尺子各算一次，检验尺子本身是否改变结论；
2. **独立重评**：逐周期几何重建重评（``post_eval.IndependentEvalHook``），
   自报 vs 重评逐位一致性留档；
3. **配对统计与单源主表**：同 seed 配对差 + 精确 p + 三态结论，
   主表/附表全部由落盘结果生成，``notes.md`` 只引用不抄数。

产物（``results_v3/``，旧 ``results/`` 冻结为 v2 存档）::

    <ID>/seed_<s>/{metrics,run_meta,solution,audit}.json
    <ID>/summary.json                 跨 seed mean±std
    summary_primary.csv               论文主表（primary=true）
    summary_appendix.csv              异口径/双因素附表（primary=false）
    claims_report.json                逐主张证据 + 三态结论 + 红线
    paired_report.json                配对明细（claims_report 的配对子集）
    summary.csv                       全量逐 seed 明细（兼容旧脚本）

命令行覆盖（调试入口）：

    --arms M0,B2        只跑指定方法
    --seeds 42-46       覆盖 experiment.seeds（42 / 42,43 / 42-46 均可）
    --set evaluation.profile=smoke   跑冒烟档（结论不可外推正式档）
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import itertools
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# 任意调用方式可跑（python -m / 任意 cwd）：脚本目录先入 sys.path，
# 供 `from paths import` 在非脚本路径调用下也能解析（修复）。
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

from paths import EXP_DIR, ROOT

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
# v3：实验自持模块（不跨实验 import，见 experiments/README.md §1）
from claims import build_claims_report
from post_eval import IndependentEvalHook, recompute_two_rulers, summarize_audits

from task_allocation.baselines import get_baseline
from task_allocation.methods.muas.cost.vertical_section import make_distance_fn
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig
from task_allocation.methods.rolling.horizon import RollingHorizon
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet

__all__ = ["main"]


# ---------------------------------------------------------------------------
# 求解器工厂
# ---------------------------------------------------------------------------


def make_solver_factory(arm: MethodArm, seed: int):
    """对照方法 → 每周期求解器工厂。

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
    dem_distance_fn: Any = None,
    euclid_distance_fn: Any = None,
    nest_xyz: Mapping[int, tuple[float, float, float]] | None = None,
    audit_enabled: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """跑一个方法的一次滚动闭环，返回 (summary, run_meta, solution, audit)。"""
    cfg = bundle.raw
    budget = dict(arm.solver_budget or {})
    # 求解器种子 = 实验种子（DMDE 搜索随机性与任务生成同步可复现）
    solver_cfg = DMDEConfig(
        pop_size=int(budget.get("population", bundle.solver.pop_size)),
        max_generations=int(budget.get("max_iter", bundle.solver.max_generations)),
        zeta=bundle.solver.zeta,
        delta=bundle.solver.delta,
        seed=seed,
        verbose=bundle.solver.verbose,
        log_interval=bundle.solver.log_interval,
        lamarckian=bundle.solver.lamarckian,
    )

    # 代价模型切换只作用于**求解端**；评价端一律 DEM 有向三维航程（v3）。
    # D0 是复合档：estimator=None 会连带换掉 ETA / 时间窗动力学，
    # 故结论里禁止写成单因素归因（见定稿 §3）。
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
    # 每个方法独立任务副本：TaskPool 会原位改写 status / meta
    arrivals = copy.deepcopy(batch.arrivals)
    if audit_enabled:
        with IndependentEvalHook() as hook:
            records = horizon.run(
                n_cycles=bundle.horizon.n_cycles, arrivals=arrivals
            )
        audit_reports = list(hook.reports)
    else:
        records = horizon.run(n_cycles=bundle.horizon.n_cycles, arrivals=arrivals)
        audit_reports = []
    wall = round(time.time() - t0, 3)

    summary = build_summary(
        records,
        pool=pool,
        batch=batch,
        problem_model=bundle.problem_model,
        wall_seconds=wall,
    )
    summary.update({
        "arm": arm.id, "solver": arm.solver,
        "cost_model": arm.cost_model, "seed": seed,
    })

    run_meta = {
        "experiment": bundle.experiment.get("name"),
        "arm": arm.id,
        "solver": arm.solver,
        "cost_model": arm.cost_model,
        # 求解端口径（欧氏组单独注明）；评价端口径见 distance_definition_review
        "distance_definition": (
            "dem_vertical_section_3d" if use_dem else "euclidean_3d"
        ),
        "distance_definition_review": "dem_vertical_section_3d",
        "comparator_ruler": "euclidean_3d",
        "coordinate_precision": "full",
        "primary": bool(arm.primary),
        "solver_budget": budget,
        "design": "task_allocation/docs/exp05_redesign_2026-11-09.md",
        "evaluation_profile": str(bundle.evaluation.get("profile", "formal")),
        "seed": seed,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str((EXP_DIR / "config.yaml").relative_to(ROOT)),
        "repo_root": str(ROOT),
        # 生效配置指纹（含 --set、evaluation/claims 段与 arm 新字段）
        "config_fingerprint": _config_fingerprint(bundle.raw, arm),
    }

    # 坐标一律全精度落盘（铁律：离散包络查询，round 会让重评跨边界失真）
    solution = {
        "arm": arm.id,
        "seed": seed,
        "tasks": [
            {
                "id": int(t.id),
                "xyz": [float(t.x), float(t.y), float(t.z)],
                "x": float(t.x), "y": float(t.y), "z": float(t.z),
                "reward": round(float(t.reward), 4),
                "arrival_time": round(float(t.arrival_time), 3),
                "earliest": t.earliest,
                "latest": t.latest,
                "status": t.status.value,
            }
            for t in batch.all_tasks()
        ],
        "nests": [
            {"id": int(n.id), "xyz": [float(n.x), float(n.y), float(n.z)],
             "capacity": int(n.capacity)}
            for n in nests
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

    # ---- 两把尺子：同一批交付解，按实际飞行方向逐腿重算 -----------------
    rulers = recompute_two_rulers(
        solution,
        dem_distance_fn=dem_distance_fn,
        euclid_distance_fn=euclid_distance_fn,
        nest_xyz=nest_xyz or {},
    )
    summary.update({
        "path_m_audit": rulers.path_m_audit,
        "path_m_euclid3d": rulers.path_m_euclid3d,
        "ruler_gap_m": round(rulers.ruler_gap_m, 3),
        "n_legs_audit": rulers.n_legs,
        "n_missing_end_nest": rulers.n_missing_end_nest,
    })

    # ---- 独立几何重建重评：自报 vs 重评逐位一致性 -----------------------
    audit_cols = summarize_audits(audit_reports)
    summary.update({
        "f_audit": audit_cols["f_audit"],
        "path_m_audit_from_problem": audit_cols["path_m_audit_from_problem"],
        "late_s_audit": audit_cols["late_s_audit"],
        "n_hard_audit": audit_cols["n_hard_audit"],
        "n_skip_audit": audit_cols["n_skip_audit"],
        "n_audit_cycles": audit_cols["n_audit_cycles"],
        "n_audit_ok": audit_cols["n_audit_ok"],
        "n_audit_unavailable": audit_cols["n_audit_unavailable"],
        "consistency_equal_all": int(bool(audit_cols["consistency_equal_all"])),
        "n_consistency_checked": audit_cols["n_consistency_checked"],
        "audit_errors": audit_cols["audit_errors"],
        # 门槛 failures 只记账、不升级为实验失败；分类见 post_eval.summarize_audits
        "audit_failure_counts": audit_cols["audit_failure_counts"],
        "audit_failure_examples": audit_cols["audit_failure_examples"],
    })
    audit = {
        "arm": arm.id,
        "seed": seed,
        "reports": audit_reports,
        "columns": audit_cols,
        "two_rulers": {
            "path_m_audit": rulers.path_m_audit,
            "path_m_euclid3d": rulers.path_m_euclid3d,
            "ruler_gap_m": rulers.ruler_gap_m,
            "n_legs": rulers.n_legs,
            "n_tours": rulers.n_tours,
            "n_missing_end_nest": rulers.n_missing_end_nest,
        },
    }
    return summary, run_meta, solution, audit


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


def _config_fingerprint(raw_cfg: Mapping[str, Any], arm: Any) -> str:
    """当前**生效**配置（含 --set 覆盖）的稳定指纹。

    断点续跑的复用判据不能只看 metrics.json 是否存在：换 λ / 代数 / 门禁
    开关后跑进同一 results 目录，会静默混入旧口径结果并直接进论文主表
    （修复）。故把生效配置连同对照方法关键参数一起哈希，落盘
    run_meta 并在复用时比对；不匹配即不复用。
    """
    payload = {
        "config": raw_cfg,
        "arm": {
            "id": getattr(arm, "id", None),
            "solver": getattr(arm, "solver", None),
            "cost_model": getattr(arm, "cost_model", None),
            "overrides": getattr(arm, "overrides", None),
            # v3：主表归属、配对对象、搜索预算覆盖都必须参与指纹，
            # 否则换口径后旧结果会被静默复用（定稿 §4.3 批次防混）。
            "primary": getattr(arm, "primary", None),
            "pair_with": getattr(arm, "pair_with", None),
            "solver_budget": getattr(arm, "solver_budget", None),
        },
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 exp05_baselines 对照实验")
    parser.add_argument(
        "--arms", nargs="+", metavar="ARM",
        help="只跑指定方法（M0 D0 B1 B2 Q0 B3 B2e…）；支持逗号分隔；省略跑全部",
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
        raise SystemExit(f"没有匹配的对照方法（--arms {arm_ids}）；可用：{known}")

    verbose = bundle.verbose
    out_root = EXP_DIR / str(bundle.experiment.get("output_dir", "results_v3"))
    out_root.mkdir(parents=True, exist_ok=True)

    eval_cfg = dict(bundle.evaluation)
    claims_cfg = dict(bundle.claims)
    profile = str(eval_cfg.get("profile", "formal"))
    audit_enabled = bool(eval_cfg.get("independent_audit", True))
    two_rulers = bool(eval_cfg.get("two_rulers", True))

    # ── 场景（与 seed / 方法无关，只构建一次）────────────────────────
    scenario = build_scenario(raw_cfg)
    nests = scenario.nests
    terrain = build_terrain(scenario, raw_cfg)
    estimator = build_cost_estimator(terrain, raw_cfg)
    # 两把尺子：d_DEM（有向，生产口径）与 d_euclid3d（历史朴素口径）。
    # 缓存跨方法/seed 复用（精确坐标键，不同任务不会误命中）。
    dem_distance_fn = (
        make_distance_fn(estimator, cache={}) if two_rulers
        else (lambda a, b: 0.0)
    )
    euclid_distance_fn = (
        make_distance_fn(None, cache={}) if two_rulers
        else (lambda a, b: 0.0)
    )
    nest_xyz = {
        int(n.id): (float(n.x), float(n.y), float(n.z)) for n in nests
    }
    if verbose:
        print(
            f"[exp05] 场景：{scenario.count} 机巢 × 容量 {nests[0].capacity}，"
            f"DEM={'已接入' if terrain is not None else '未接入'}；"
            f"方法：{', '.join(a.id for a in arms)}；seeds={seeds}；"
            f"profile={profile}；两把尺子={two_rulers}；独立重评={audit_enabled}",
            flush=True,
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
            # 复用前必须比对配置指纹：只判"文件存在"会让换
            # λ / 代数 / 门禁开关后的旧口径结果被静默沿用，污染论文主表。
            metrics_path = run_dir / "metrics.json"
            meta_path = run_dir / "run_meta.json"
            if metrics_path.is_file() and not args.force:
                fp = _config_fingerprint(raw_cfg, arm)
                stored_fp = None
                if meta_path.is_file():
                    try:
                        stored_fp = json.loads(
                            meta_path.read_text(encoding="utf-8")
                        ).get("config_fingerprint")
                    except (OSError, ValueError):
                        stored_fp = None
                if stored_fp is not None and stored_fp == fp:
                    summary = json.loads(metrics_path.read_text(encoding="utf-8"))
                    per_arm[arm.id].append(summary)
                    if verbose:
                        print(f"[exp05] {arm.id:<4} seed={seed}：已存在且配置指纹一致，复用结果（--force 重跑）")
                    continue
                print(
                    f"[exp05] {arm.id:<4} seed={seed}：配置指纹不一致"
                    f"（存量={stored_fp or '缺失'} / 当前={fp}）⇒ 不复用，重跑"
                    "（旧结果口径与当前不符，沿用会污染主表）"
                )
            t0 = time.time()
            summary, run_meta, solution, audit = run_once(
                bundle, arm, seed,
                scenario=scenario, nests=nests, terrain=terrain,
                estimator=estimator, batch=batch, verbose=verbose,
                dem_distance_fn=dem_distance_fn,
                euclid_distance_fn=euclid_distance_fn,
                nest_xyz=nest_xyz,
                audit_enabled=audit_enabled,
            )
            per_arm[arm.id].append(summary)
            write_json(run_dir / "metrics.json", summary)
            write_json(run_dir / "run_meta.json", run_meta)
            write_json(run_dir / "solution.json", solution)
            write_json(run_dir / "audit.json", audit)
            if verbose:
                print(
                    f"[exp05] {arm.id:<4} seed={seed}：完成率(final) "
                    f"{summary['completion_rate_final']:.3f} / 奖励产出 "
                    f"{summary['reward_yield']:.3f} / 按时(交付集) "
                    f"{summary['on_time_rate_final']:.3f} / 自报航程 "
                    f"{summary['total_path_m'] / 1000:.1f} km / "
                    f"重评DEM {summary.get('path_m_audit', 0.0) / 1000:.1f} km / "
                    f"尺子差 {summary.get('ruler_gap_m', 0.0):.1f} m / "
                    f"独立重评 {summary.get('n_audit_ok', 0)} 周期 / "
                    f"{time.time() - t0:.1f}s",
                    flush=True,
                )

    # ── 聚合：主表 / 附表 / 全量明细，全部由落盘结果单源生成 ────────
    rows_all: list[dict[str, Any]] = []
    rows_primary: list[dict[str, Any]] = []
    rows_appendix: list[dict[str, Any]] = []
    for arm in arms:
        summaries = per_arm[arm.id]
        if not summaries:
            continue
        agg = aggregate_summaries(summaries)
        write_json(
            out_root / arm.id / "summary.json",
            {
                "arm": arm.id,
                "solver": arm.solver,
                "cost_model": arm.cost_model,
                "label": arm.label,
                "primary": bool(arm.primary),
                "seeds": seeds,
                "agg": agg,
                "per_seed": summaries,
            },
        )
        target = rows_primary if arm.primary else rows_appendix
        for s in summaries:
            row = summary_csv_row(
                s, arm=arm.id, solver=arm.solver,
                cost_model=arm.cost_model, seed=int(s["seed"]),
            )
            rows_all.append(row)
            target.append(row)
        mean_row = summary_csv_row(
            summaries[0], arm=arm.id, solver=arm.solver,
            cost_model=arm.cost_model, seed="mean", agg=agg,
        )
        rows_all.append(mean_row)
        target.append(mean_row)

    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in CSV_COLUMNS})

    _write_csv(out_root / "summary.csv", rows_all)
    _write_csv(out_root / "summary_primary.csv", rows_primary)
    _write_csv(out_root / "summary_appendix.csv", rows_appendix)

    # ── 主张检验：逐主张配对统计 + 三态结论 + 红线 ───────────────────
    rows_by_method: dict[str, dict[int, dict[str, Any]]] = {
        a.id: {int(s["seed"]): s for s in per_arm[a.id]} for a in arms
    }
    primary_methods = [
        str(x) for x in claims_cfg.get(
            "primary_methods", [a.id for a in arms if a.primary]
        )
    ]
    report = build_claims_report(
        rows_by_method,
        seeds=seeds,
        primary_methods=primary_methods,
        profile=profile,
    )
    write_json(out_root / "claims_report.json", report)
    c2 = report["claims"]["C2"]
    c4 = report["claims"]["C4"]
    write_json(
        out_root / "paired_report.json",
        {
            "experiment": "exp05_baselines",
            "design": "task_allocation/docs/exp05_redesign_2026-11-09.md",
            "profile": profile,
            "seeds": seeds,
            "paired_seeds": [
                int(s) for s in seeds if int(s) not in report["unpaired_seeds"]
            ],
            "unpaired_seeds": report["unpaired_seeds"],
            "all_pairs_complete": report["all_pairs_complete"],
            "primary_gates_passed": report["primary_gates_passed"],
            "primary_criterion": c2["primary_criterion"],
            "ruler_effect": c2["ruler_effect"],
            "ruler_gap": c2["ruler_gap"],
            "ruler_changes_direction": c2["ruler_changes_direction"],
            "ruler_bias_by_method": c2["ruler_bias_by_method"],
            "M0_vs_D0_descriptive": c2["method_pair_descriptive"],
            "M0_vs_B2_descriptive": c4["pairs"].get("B2", {}),
            "M0_vs_B1_descriptive": c4["pairs"].get("B1", {}),
            "caveats": report["caveats"],
            "red_lines": report["red_lines"],
        },
    )

    if verbose:
        print(
            f"[exp05] 主表 {out_root / 'summary_primary.csv'} / "
            f"附表 {out_root / 'summary_appendix.csv'} / "
            f"主张报告 {out_root / 'claims_report.json'}",
            flush=True,
        )
        print(
            f"[exp05] 全部 {len(arms)} 方法 × {len(seeds)} seed 完成；"
            f"门槛={report['primary_gates_passed']} "
            f"配对完整={report['all_pairs_complete']} "
            f"M0一致性={report['claims']['C3']['m0_all_equal']} "
            f"尺子改变方向={c2['ruler_changes_direction']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
