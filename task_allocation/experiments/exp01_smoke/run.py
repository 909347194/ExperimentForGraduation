# -*- coding: utf-8 -*-
"""exp01_smoke 入口：在真实机巢 / DEM 场景上端到端跑通两阶段 + 滚动闭环。

本文件只做**编排调度**，不堆细节：

    config.py   config.yaml → 配置对象
    build.py    场景 / DEM 地形 / 机队 / 任务批次
    metrics.py  指标汇总、运行元信息、分配结果与终端报表
    plot/       可视化（读 results/ 产物，写图到 plot/figures/）
    results/    数值产物（metrics.json / run_meta.json / solution.json）

链路（对齐 Chapter3_README §8 技术路线）::

    data/(机巢CSV + DEM) ──► build.build_scenario ──► Nest / LocalFrame
    config.yaml(tasks:)   ──► build.build_arrivals ──► Task
                                    ↓
    TaskPool.begin_cycle  ──► T_t = remain ∪ new ∪ release
                                    ↓
    selection.run         ──► T_t^sel   （优先级 → 可行性 → 边际收益）
                                    ↓
    muas.run_selective_muas ─► T_t^exec （分配 + 顺序 + 终点机巢，DMDE）
                                    ↓
    pool.apply_solution / fleet.apply_solution ──► 状态回写（含 z_ub）
                                    ↓
    完成反馈 ──► t+1（未执行的任务保留在池中，下周期以 REMAIN 重新参与）

所有可调参数均在 ``config.yaml``；本文件不写死任何数值。
"""

from __future__ import annotations

import sys
import time

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


def main() -> int:
    cfg_path = EXP_DIR / "config.yaml"
    cfg = load_config(cfg_path)
    bundle = build_config_bundle(cfg)

    out_dir = EXP_DIR / str(bundle.experiment.get("output_dir", "results"))
    figures_dir = EXP_DIR / str(bundle.experiment.get("figures_dir", "results/figures"))
    out_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    verbose = bundle.verbose
    t_wall = time.time()

    # ── 场景 ──────────────────────────────────────────────────────
    scenario = build_scenario(cfg, DATA_DIR)
    nests = scenario.nests
    terrain = build_terrain(scenario, cfg)
    estimator = build_cost_estimator(terrain, cfg)
    if verbose:
        print(
            f"[exp01] 场景：{scenario.count} 机巢，容量={nests[0].capacity}，"
            f"DEM={'已接入' if terrain is not None else '未接入'}，"
            f"frame=({scenario.frame.lat0:.6f}, {scenario.frame.lon0:.6f})"
        )

    # ── 实体 ──────────────────────────────────────────────────────
    uavs = build_uavs(nests, cfg)
    task_cfg = build_task_config(cfg, bundle.seed)
    batch = build_arrivals(nests, scenario, task_cfg, cfg)
    if verbose:
        per_cycle = ", ".join(
            f"周期{c}={n}" for c, n in batch.per_cycle_counts().items()
        )
        print(f"[exp01] 任务：共 {len(batch.all_tasks())} 个（{per_cycle}）")

    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)

    # ── 滚动求解（算法配置全部来自 config.py 装配）────────────────
    horizon = RollingHorizon(
        pool,
        fleet,
        nests,
        dem=terrain,
        estimator=estimator,
        horizon_config=bundle.horizon,
        selection_config=bundle.selection,
        problem_config=bundle.problem_model,
        stage_config=bundle.stage_model,
        solver_config=bundle.solver,
        event_config=bundle.event,
        nest_offset=bundle.nest_offset,
    )
    records = horizon.run(n_cycles=bundle.n_cycles, arrivals=batch.arrivals)

    facts = RunFacts(
        bundle=bundle,
        scenario=scenario,
        nests=nests,
        terrain=terrain,
        uavs=uavs,
        batch=batch,
        records=records,
        pool=pool,
        fleet=fleet,
        wall_seconds=time.time() - t_wall,
    )

    # ── 产物 ──────────────────────────────────────────────────────
    if verbose:
        print_cycle_report(records)

    metrics = build_metrics(facts)
    solution = build_solution(facts)

    if bundle.save_results:
        write_json(out_dir / "metrics.json", metrics)
        write_json(out_dir / "run_meta.json", build_run_meta(facts, cfg_path=cfg_path, root=ROOT))
        write_json(out_dir / "solution.json", solution)

    if verbose:
        print_summary(metrics, len(batch.all_tasks()), out_dir)

    # ── 出图（plot/ 只负责绘制，产物落 figures_dir）────────────
    if bundle.make_plots:
        from plot import make_plots  # 延迟导入：不出图时不引 matplotlib

        plot_cfg = bundle.plot
        formats = tuple(plot_cfg.get("formats") or ("png", "pdf"))
        make_plots(
            metrics,
            solution,
            figures_dir,
            formats=formats,
            dpi=int(plot_cfg.get("dpi", 200)),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
