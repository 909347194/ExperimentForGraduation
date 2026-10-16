# -*- coding: utf-8 -*-
"""run_chunked.py — 长程滚动实验的「分块续跑」驱动。

动机：24 周期 × 500 代单次连续跑约需 70~90 分钟，而本环境的工具调用有
~10 分钟上限、后台进程跨回合会被回收。本驱动把同一场实验切成多个
前台块：每跑完一个周期就原子存档（pool/fleet/records/到达批次/初始
快照），到达时间预算即干净退出；下次调用（哪怕跨回合）自动从存档
续跑。求解器每周期以 ``solver.seed`` 独立播种，续跑结果与一口气跑
逐位一致。

用法：:

    python run_chunked.py                    # 默认预算 480s，跑/续跑到完
    python run_chunked.py --budget-seconds 420
    python run_chunked.py --fresh            # 丢弃存档重开
    python run_chunked.py --no-plots --no-tables   # 只攒 json 数值产物

存档：``results/.chunk_state.pkl``（指纹 = config 字节 + seed + n_cycles
+ max_iter；不匹配自动重开，防配置改了还拿旧状态续）。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import os
import pickle
import sys
import tempfile
import time
from pathlib import Path

from paths import DATA_DIR, EXP_DIR, ROOT

from build import (
    build_arrivals,
    build_cost_estimator,
    build_scenario,
    build_task_config,
    build_terrain,
    build_uavs,
)
from config import build_config_bundle, load_config
from metrics import (
    RunFacts,
    build_metrics,
    build_run_meta,
    build_solution,
    print_cycle_report,
    print_summary,
    write_json,
)

from task_allocation.methods.rolling.horizon import RollingHorizon
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet

STATE_PATH = EXP_DIR / "results" / ".chunk_state.pkl"


def _state_path(out_dir: str | None) -> Path:
    """存档路径跟随输出目录：变体实验（--out）各自独立存档不打架。"""
    if not out_dir:
        return STATE_PATH
    return EXP_DIR / out_dir / ".chunk_state.pkl"


def _fingerprint(raw_cfg: dict, seed: int, max_iter: int | None,
                 out_dir: str | None) -> str:
    """配置指纹：config 原文 + 关键旋钮，防止改配置后错配旧存档。"""
    cfg_bytes = (EXP_DIR / "config.yaml").read_bytes()
    rolling = raw_cfg.get("rolling", {}) or {}
    solver = raw_cfg.get("solver", {}) or {}
    key = (
        cfg_bytes,
        str(seed).encode(),
        str(rolling.get("n_cycles")).encode(),
        str(solver.get("max_iter")).encode(),
        str(max_iter if max_iter is not None else "-").encode(),
        str(solver.get("population")).encode(),
        str(out_dir or "-").encode(),
    )
    return hashlib.sha1(b"|".join(key)).hexdigest()


def _save_state(path: Path, state: dict) -> None:
    """原子写档：先写临时文件再 replace，避免半截存档被下次读取。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            pickle.dump(state, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _load_state(path: Path, fp: str) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            state = pickle.load(fh)
    except Exception as exc:
        print(f"[chunk] 存档损坏，重开（{exc}）")
        return None
    if state.get("fingerprint") != fp:
        print("[chunk] 配置指纹不匹配，丢弃旧存档重开")
        return None
    return state


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="exp01 长程滚动分块续跑驱动")
    ap.add_argument("--budget-seconds", type=float, default=480.0,
                    help="本块墙钟预算（含启动）；到点存档干净退出")
    ap.add_argument("--seed", type=int, default=None,
                    help="覆盖实验种子（默认按 yaml）")
    ap.add_argument("--fresh", action="store_true", help="丢弃存档重开")
    ap.add_argument("--max-iter", type=int, default=None,
                    help="覆盖 solver.max_iter（敏感性实验用；进入指纹）")
    ap.add_argument("--out", default=None,
                    help="覆盖 experiment.output_dir（变体实验目录，如 results_s43；"
                         "存档/图表/表格全部跟随；进入指纹）")
    ap.add_argument("--no-plots", action="store_true", help="全部跑完才出图")
    ap.add_argument("--no-tables", action="store_true")
    args = ap.parse_args(argv)

    t_start = time.monotonic()
    raw_cfg = load_config(EXP_DIR / "config.yaml")
    # 命令行覆盖：max_iter / output_dir 先进 raw_cfg，再构建 bundle
    if args.max_iter is not None:
        raw_cfg.setdefault("solver", {})["max_iter"] = int(args.max_iter)
    if args.out:
        raw_cfg.setdefault("experiment", {})["output_dir"] = args.out
        raw_cfg["experiment"]["figures_dir"] = f"{args.out}/figures"
    seed = args.seed if args.seed is not None else int(
        (raw_cfg.get("experiment", {}) or {}).get("seed", 42))
    fp = _fingerprint(raw_cfg, seed, args.max_iter, args.out)
    state_path = _state_path(args.out)

    # 两条路径（新建/续跑）都必须用注入 seed 的配置构建 bundle，
    # 否则续跑块的 solver.seed 会退回 yaml 默认值导致搜索轨迹漂移。
    seed_cfg = copy.deepcopy(dict(raw_cfg))
    seed_cfg.setdefault("experiment", {})["seed"] = seed
    seed_cfg.setdefault("solver", {})["seed"] = seed
    bundle = build_config_bundle(seed_cfg)
    n_cycles = int(bundle.n_cycles)

    # ── 场景（与种子无关，每块重建；DEM 采样 ~6s）──────────────────
    scenario = build_scenario(raw_cfg, DATA_DIR)
    nests = scenario.nests
    terrain = build_terrain(scenario, raw_cfg)
    estimator = build_cost_estimator(terrain, raw_cfg)

    # ── 恢复或新建 ─────────────────────────────────────────────────
    state = None if args.fresh else _load_state(state_path, fp)
    if state is not None:
        pool: TaskPool = state["pool"]
        fleet: UAVFleet = state["fleet"]
        batch = state["batch"]
        initial_uavs = state["initial_uavs"]
        records = state["records"]
        h_time = float(state["h_time"])
        known_unavailable = set(state["known_unavailable"])
        next_cycle = int(state["next_cycle"])
        wall_seconds = float(state["wall_seconds"])
        print(f"[chunk] 续跑：seed={seed} 从周期 {next_cycle}/{n_cycles} 开始"
              f"（已完成 {len(records)} 周期，累计墙钟 {wall_seconds:.0f}s）")
    else:
        uavs = build_uavs(nests, raw_cfg)
        task_cfg = build_task_config(raw_cfg, seed)
        batch = build_arrivals(nests, scenario, task_cfg, raw_cfg)
        per_cycle = ", ".join(f"周期{c}={n}" for c, n in batch.per_cycle_counts().items())
        print(f"[chunk] 新跑：seed={seed} 共 {len(batch.all_tasks())} 个任务（{per_cycle}）")
        pool = TaskPool(current_time=0.0)
        fleet = UAVFleet(uavs, current_time=0.0)
        initial_uavs = copy.deepcopy(uavs)
        records = []
        h_time = 0.0
        known_unavailable = {n.id for n in nests if not n.available}
        next_cycle = 1
        wall_seconds = 0.0

    last_arrival = max(batch.arrivals.keys()) if batch.arrivals else 1

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
    # 注入续跑状态（horizon.records 与本地 records 保持同一对象）
    horizon.records = records
    horizon._time = h_time
    horizon._known_unavailable = known_unavailable

    # ── 分块主循环 ─────────────────────────────────────────────────
    t_last = t_start
    while next_cycle <= n_cycles:
        rec = horizon.run_cycle(new_tasks=batch.arrivals.get(next_cycle))
        next_cycle += 1
        t_now = time.monotonic()
        wall_seconds += t_now - t_last
        t_last = t_now
        state = {
            "fingerprint": fp,
            "pool": pool,
            "fleet": fleet,
            "batch": batch,
            "initial_uavs": initial_uavs,
            "records": records,
            "h_time": horizon._time,
            "known_unavailable": horizon._known_unavailable,
            "next_cycle": next_cycle,
            "wall_seconds": wall_seconds,
        }
        _save_state(state_path, state)
        solve_s = float(rec.muas.get("solve_seconds", 0.0))
        print(f"[chunk] 周期 {rec.cycle}/{n_cycles} 完成："
              f"执行={len(rec.executed_ids)} 交付={len(rec.completed_ids)} "
              f"废飞={len(rec.infeasible_ids)} 淘汰={len(rec.expired_ids)} "
              f"solve={solve_s:.1f}s 累计墙钟={wall_seconds:.0f}s", flush=True)
        if horizon._converged(next_cycle - 1, last_arrival):
            print(f"[chunk] 收敛于周期 {next_cycle-1}：池内未完成任务数连续 "
                  f"{horizon._conv_streak} 周期不变（={len(horizon.pool)}），提前停止。",
                  flush=True)
            break
        if time.monotonic() - t_start >= args.budget_seconds and next_cycle <= n_cycles:
            print(f"[chunk] 到达时间预算，存档退出（下周期 {next_cycle}）", flush=True)
            return 0

    # ── 全部周期完成：汇总产物 ─────────────────────────────────────
    facts = RunFacts(
        bundle=bundle, scenario=scenario, nests=nests, terrain=terrain,
        uavs=fleet.all(), initial_uavs=initial_uavs, batch=batch,
        records=records, pool=pool, fleet=fleet, wall_seconds=wall_seconds,
    )
    final_metrics = build_metrics(facts)
    rep_solution = build_solution(facts)

    out_dir = EXP_DIR / str(bundle.experiment.get("output_dir", "results"))
    figures_dir = EXP_DIR / str(bundle.experiment.get("figures_dir", "results/figures"))
    out_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    if bundle.save_results:
        meta = build_run_meta(facts, cfg_path=EXP_DIR / "config.yaml", root=ROOT)
        write_json(out_dir / "metrics.json", final_metrics)
        write_json(out_dir / "run_meta.json", meta)
        write_json(out_dir / "solution.json", rep_solution)
        state_path.unlink(missing_ok=True)

    print_cycle_report(records)
    print_summary(final_metrics, len(batch.all_tasks()), out_dir)

    # ── 出图（与 run.py 同逻辑：动画图自动补 gif）──────────────────
    if bundle.make_plots and not args.no_plots:
        from plot import make_plots
        from plot.registry import resolve as _resolve_figures

        plot_cfg = bundle.plot
        formats = tuple(plot_cfg.get("formats") or ("png",))
        if any(f.animated for f in _resolve_figures(names=None, groups=None)):
            if not ({"gif", "mp4"} & {str(f).lower().lstrip(".") for f in formats}):
                formats = (*formats, "gif")
        make_plots(final_metrics, rep_solution, figures_dir,
                   formats=formats, dpi=int(plot_cfg.get("dpi", 200)))

    # ── 结果表（只读 json 制表 + latexmk 编译 PDF）─────────────────
    if bundle.save_results and bundle.make_tables and not args.no_tables:
        try:
            from run import _compile_tables
            from tables import render as render_tables

            tables_dir = out_dir / "tables"
            tex_path = render_tables(tables_dir / "exp01_tables.tex", results_dir=out_dir)
            print(f"[chunk] 结果表已生成：{tex_path}")
            _compile_tables(tables_dir, verbose=True)
        except Exception as exc:
            print(f"[chunk] 结果表生成失败（不影响主结果）：{exc}")

    print("[chunk] 全部完成", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
