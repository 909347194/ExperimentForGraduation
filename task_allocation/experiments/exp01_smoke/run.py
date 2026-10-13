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

import copy
import argparse
import sys
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
    build_seed_summary,
    build_solution,
    print_cycle_report,
    print_summary,
    write_json,
)

from task_allocation.methods.rolling.horizon import RollingHorizon
from task_allocation.methods.task_pool import TaskPool
from task_allocation.methods.uav_state import UAVFleet


def _compile_tables(tables_dir: Path, *, verbose: bool) -> None:
    """编译结果表 PDF（ctex 需 xelatex，经 latexmk 驱动）。

    无 LaTeX 引擎或编译失败只警告、不阻断主流程——.tex 已是完整产物，
    PDF 只是方便直览。
    """
    import shutil
    import subprocess

    if shutil.which("latexmk") is None:
        print("[exp01] 未找到 latexmk，跳过表格 PDF 编译（.tex 已生成）")
        return
    try:
        proc = subprocess.run(
            ["latexmk", "-xelatex", "-interaction=nonstopmode",
             "-halt-on-error", "exp01_tables.tex"],
            cwd=tables_dir, capture_output=True, text=True, timeout=300,
        )
    except Exception as exc:  # 超时/启动失败等
        print(f"[exp01] 表格 PDF 编译异常（不影响主结果）：{exc}")
        return
    if proc.returncode != 0:
        print(f"[exp01] 表格 PDF 编译失败（详见 {tables_dir / 'exp01_tables.log'}），"
              f".tex 已生成")
    elif verbose:
        print(f"[exp01] 表格 PDF 已编译：{tables_dir / 'exp01_tables.pdf'}")


def _run_once(
    raw_cfg: dict,
    seed: int,
    *,
    scenario,
    nests,
    terrain,
    estimator,
    verbose: bool,
) -> tuple[RunFacts, dict, dict]:
    """针对单个随机种子跑完整链路，返回 (facts, metrics_dict, solution_dict)。

    场景 / 地形 / 代价估算器在多次重复间复用（与种子无关）；
    任务批次、机队状态、滚动闭环每次重建以保证相互独立。
    求解器种子同步设为 ``seed``，使每次重复在任务生成与优化两端都不相同。
    """
    # 每个 seed 用独立配置副本：实验种子 + 求解器种子都设为该 seed
    seed_cfg = copy.deepcopy(dict(raw_cfg))
    seed_cfg.setdefault("experiment", {})["seed"] = seed
    seed_cfg.setdefault("solver", {})["seed"] = seed

    bundle = build_config_bundle(seed_cfg)
    uavs = build_uavs(nests, raw_cfg)
    task_cfg = build_task_config(raw_cfg, seed)
    batch = build_arrivals(nests, scenario, task_cfg, raw_cfg)
    if verbose:
        per_cycle = ", ".join(
            f"周期{c}={n}" for c, n in batch.per_cycle_counts().items()
        )
        print(f"[exp01] seed={seed} 任务：共 {len(batch.all_tasks())} 个（{per_cycle}）")

    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)

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
        wall_seconds=0.0,  # 由外层统一累计
    )
    return facts, build_metrics(facts), build_solution(facts)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析实验入口参数；PDF 只在本次运行显式开启。"""
    parser = argparse.ArgumentParser(description="运行 exp01_smoke 端到端实验")
    parser.add_argument(
        "--pdf", action="store_true", help="额外输出 PDF 矢量图（默认仅 PNG）"
    )
    parser.add_argument(
        "--group", action="append", dest="groups", metavar="LAYER",
        choices=("scene", "process", "report"),
        help="只出某一层的图：scene=环境初始配置 / process=运行过程 / "
             "report=结论插图；可重复指定，省略则出全部",
    )
    parser.add_argument(
        "--figure", action="append", dest="figures", metavar="ID",
        help="只出指定图（图 id，如 geo_overview）；可重复指定",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg_path = EXP_DIR / "config.yaml"
    raw_cfg = load_config(cfg_path)
    bundle0 = build_config_bundle(raw_cfg)

    out_dir = EXP_DIR / str(bundle0.experiment.get("output_dir", "results"))
    figures_dir = EXP_DIR / str(bundle0.experiment.get("figures_dir", "results/figures"))
    out_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    verbose = bundle0.verbose
    # 多 seed 复现：experiment.seeds 缺失时退回单 seed（= 原 experiment.seed）
    seeds = list(bundle0.experiment.get("seeds") or [bundle0.seed])
    multi = len(seeds) > 1
    if verbose:
        print(f"[exp01] 多 seed 复现：{seeds}" if multi else f"[exp01] 单 seed：{seeds[0]}")

    t_wall = time.time()

    # ── 场景（与种子无关，只构建一次）────────────────────────────
    scenario = build_scenario(raw_cfg, DATA_DIR)
    nests = scenario.nests
    terrain = build_terrain(scenario, raw_cfg)
    estimator = build_cost_estimator(terrain, raw_cfg)
    if verbose:
        print(
            f"[exp01] 场景：{scenario.count} 机巢，容量={nests[0].capacity}，"
            f"DEM={'已接入' if terrain is not None else '未接入'}，"
            f"frame=({scenario.frame.lat0:.6f}, {scenario.frame.lon0:.6f})"
        )

    # ── 逐 seed 运行 ─────────────────────────────────────────────
    results: list[tuple[int, RunFacts, dict, dict]] = []
    for seed in seeds:
        facts, metrics, solution = _run_once(
            raw_cfg, seed,
            scenario=scenario, nests=nests, terrain=terrain,
            estimator=estimator, verbose=verbose,
        )
        results.append((seed, facts, metrics, solution))

    # 代表性运行取首个 seed（供现有图与单值指标展示）
    rep_seed, rep_facts, rep_metrics, rep_solution = results[0]
    results[0][1].wall_seconds = time.time() - t_wall

    # ── 多 seed 聚合 ──────────────────────────────────────────────
    if multi:
        seed_summary = build_seed_summary([m["summary"] for _, _, m, _ in results])
    else:
        seed_summary = None

    # 最终 metrics：代表运行的 cycles/nests/problem 保持原结构（图向后兼容），
    # 另附 seeds / seed_summary / per_seed 供多 seed 分析与绘图。
    final_metrics = dict(rep_metrics)
    final_metrics["multi_seed"] = multi
    final_metrics["seeds"] = seeds
    if seed_summary is not None:
        final_metrics["seed_summary"] = seed_summary
    final_metrics["per_seed"] = {
        str(seed): {"summary": m["summary"], "cycles": m["cycles"]}
        for seed, _, m, _ in results
    }

    # ── 产物 ──────────────────────────────────────────────────────
    if verbose:
        print_cycle_report(rep_facts.records)

    if bundle0.save_results:
        meta = build_run_meta(rep_facts, cfg_path=cfg_path, root=ROOT)
        meta["seeds"] = seeds
        write_json(out_dir / "metrics.json", final_metrics)
        write_json(out_dir / "run_meta.json", meta)
        write_json(out_dir / "solution.json", rep_solution)

    n_arrived = len(rep_facts.batch.all_tasks())
    if verbose:
        print_summary(final_metrics, n_arrived, out_dir)

    # ── 出图（plot/ 只负责绘制，产物落 figures_dir）────────────
    if bundle0.make_plots:
        from plot import make_plots  # 延迟导入：不出图时不引 matplotlib

        plot_cfg = bundle0.plot
        formats = tuple(plot_cfg.get("formats") or ("png",))
        if args.pdf and "pdf" not in {str(f).lower().lstrip(".") for f in formats}:
            formats = (*formats, "pdf")
        # 动画图需 gif/mp4 才出。只要本次图集包含任意 animated 图（默认全部图
        # 即包含 allocation_anim / allocation_status），就自动补 gif——否则 GIF
        # 会停留在早年一次显式运行，与 json/PNG/表格全部脱节（2026-10-13 审查：
        # GIF 曾 stale 12 小时）。只想出静态图时用 --groups/--figures 排除动画图即可。
        from plot.registry import resolve as _resolve_figures
        if any(f.animated
               for f in _resolve_figures(names=args.figures, groups=args.groups)):
            if not ({"gif", "mp4"} & {str(f).lower().lstrip(".") for f in formats}):
                formats = (*formats, "gif")
        make_plots(
            final_metrics,
            rep_solution,
            figures_dir,
            formats=formats,
            dpi=int(plot_cfg.get("dpi", 200)),
            names=args.figures,
            groups=args.groups,
        )

    # ── 结果表（tables.py 只读 json、只制表；与 plot 同哲学）──────────
    # 必须放在 write_json 之后：tables.py 从盘上读刚写出的 metrics/solution，
    # 保证「数值产物 / 图 / 表」三者同源同步，不再需要手动跑 tables.py。
    if bundle0.save_results and bundle0.make_tables:
        try:
            from tables import render as render_tables  # 延迟导入

            tables_dir = out_dir / "tables"
            tex_path = render_tables(
                tables_dir / "exp01_tables.tex", results_dir=out_dir
            )
            if verbose:
                print(f"[exp01] 结果表已生成：{tex_path}")
            _compile_tables(tables_dir, verbose=verbose)
        except Exception as exc:  # 表格失败不阻断主结果
            print(f"[exp01] 结果表生成失败（不影响主结果）：{exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
