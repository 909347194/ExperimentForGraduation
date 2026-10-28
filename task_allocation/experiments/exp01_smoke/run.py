# -*- coding: utf-8 -*-
"""exp01_smoke 入口：在真实机巢 / DEM 场景上端到端跑通两阶段 + 滚动闭环。

本文件只做**编排调度**，不堆细节：

    config.py   config.yaml → 配置对象
    build.py    场景 / DEM 地形 / 机队 / 任务批次
    metrics.py  指标汇总、运行元信息、分配结果与终端报表
    plot/       可视化（读 results/ 产物，写图到 results/figures/）
    results/    数值产物（metrics.json / run_meta.json / solution.json）

多 seed 时另落 ``results/seed_{id}/{metrics,solution}.json``；正确性审计需要
solution 的 tours / fitness_weights 才能复核 D/J/K/M/N 项，只靠
``metrics.json`` 的 ``per_seed[seed]["cycles"]`` 审不了其余 seed
（2026-10-24 修复，见 docs/exp01_code_audit_2026-10-24.md P0-1）。

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

命令行覆盖（不改 yaml 的调试入口，详见 ``_parse_args``）：

- ``--seeds``      覆盖 ``experiment.seeds``。支持 ``42``、``42,43,44``、
  ``42 43 44``、``42-46``（区间展开，去重保序）；缺省时按 yaml 走
  （``seeds`` 缺失或单元素 → 单 seed）。
- ``--window MIN MAX``  快捷覆盖任务时间窗（周期数），等价于两条
  ``--set tasks.deadline_cycles_min/max``。
- ``--range-km KM``  快捷覆盖满电航程（公里），等价于
  ``--set problem.uav_max_range=<KM*1000>``。
- ``--set dotted.path=value``  覆盖任意节点；值支持
  null/true/false/int/float/list[...]/dict{...}/str（dict/list 走 json 解析，
  不再被当字符串吞掉）。
- ``--dry-run``  只解析 + 合并配置并打印生效值（含所有覆盖），**不求解**——
  验证命令行覆盖是否写对时不必等 10 分钟。
- ``--no-plots``  跳过出图。多种子 × 多代数的长跑调试时只关心 json
  数值，不必每次等 16 张图。
- ``--no-audit``  跳过末尾的方法正确性审计（默认跑：20+ 项不变量/口径，
  实际条数随数据形态浮动——条件分支是否触发；FAIL 终端报警）。

运行进度：``horizon.run`` 每完成一个滚动周期都会打印一行
``[cycle k/N] 入池/选中/执行/顺延/不可达 + 单周期求解耗时``，长跑期间
能直接看出进度与成本去向（无需等到全部收敛）。"""

from __future__ import annotations

import copy
import argparse
import sys
import time
from pathlib import Path

# 任意调用方式可跑（python -m / 任意 cwd）：脚本目录先入 sys.path，
# 供 `from paths import` 在非脚本路径调用下也能解析（2026-10-22 修复）。
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


def _run_audit(out_dir: Path, *, verbose: bool) -> bool:
    """跑方法正确性审计（audit_correctness.py，持续门禁）。

    从落盘 json 独立复核结构不变量与口径（A~N1/N2, J/K/L/M）。
    多 seed 时审计脚本会遍历 ``results/seed_{id}/`` 逐 seed 复核——早期版本
    只读顶层产物，3 seed 跑完只有 1/3 被验证（2026-10-24 修复 P0-1）。
    有 FAIL → 返回 False 并在终端醒目报警（主流程不阻断——产物已落盘，
    报警促人核查）；仅 WARN → 返回 True。审计本身异常（缺 json 等）
    不阻断主流程，只警告。
    """
    import subprocess

    res_name = out_dir.name
    try:
        proc = subprocess.run(
            [sys.executable, str(EXP_DIR / "audit_correctness.py"),
             "--results", res_name],
            capture_output=True, text=True, timeout=600,
        )
    except Exception as exc:
        print(f"[exp01] 审计调用异常（不影响主结果）：{exc}")
        return True
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.returncode == 1:
        print("\n[exp01] \033[1;31m正确性审计发现 FAIL 项！"
              "落盘结果与理论口径不一致，请核查后再用于论文。\033[0m")
        return False
    return True


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
    # 墙钟在**本函数内**计时并在 build_metrics 之前写入 RunFacts（2026-10-24
    # 修复 P2-1）：旧口径由调用方在 build_metrics 之后回填 summary 字段，
    # build_metrics 读到的恒为 0.0，调用顺序一改就退回历史 bug（I7）。
    t_seed = time.time()
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

    # t=0 初始驻留快照：UAVFleet 在滚动求解中原位修改 UAV 对象的
    # x/y/z/nest_id/status，不先快照的话 solution.json 的 uavs 表落盘的
    # 就是终态位置（周期 1 的起点巢被周期 1 的落点覆盖），而该表按契约
    # 是「初始分布（t=0）」，geo_overview 的「初始机队」层也据此绘制。
    initial_uavs = copy.deepcopy(uavs)

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
    records = horizon.run(
        n_cycles=bundle.n_cycles,
        arrivals=batch.arrivals,
        progress_cb=_emit_cycle_progress,
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
    """解析 ``--seeds`` 的 token 列表为种子列表（去重保序）。

    支持三种写法（可混用）：

    - 单个：``--seeds 42``
    - 逗号：``--seeds 42,43,44``
    - 区间：``--seeds 42-46`` → ``[42, 43, 44, 45, 46]``

    区间专为「多种子稳健性统计」设计：``--seeds 42-51`` 即跑 10 个种子，
    不必在 yaml 里手写一列。空 token 跳过；非法输入抛 ``argparse`` 错误。
    """
    seeds: list[int] = []
    for tok in tokens:
        for part in str(tok).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part[1:]:  # 从第 2 字符起找 '-'，容忍前导符号
                lo_s, hi_s = part.split("-", 1)
                try:
                    lo, hi = int(lo_s), int(hi_s)
                except ValueError:
                    # 形如 1-3-5 会被切成 ("1", "3-5")，int 抛裸 ValueError
                    # 用户只看到 traceback（2026-10-24 修复 P2-3）
                    raise argparse.ArgumentTypeError(
                        f"--seeds 区间格式非法：{part!r}（应为 lo-hi，如 42-46）"
                    ) from None
                if lo > hi:
                    lo, hi = hi, lo
                seeds.extend(range(lo, hi + 1))
            else:
                try:
                    seeds.append(int(part))
                except ValueError:
                    raise argparse.ArgumentTypeError(
                        f"--seeds 无法解析为整数：{part!r}"
                    ) from None
    if not seeds:
        raise argparse.ArgumentTypeError("--seeds 解析结果为空")
    seen: set[int] = set()
    out: list[int] = []
    for s in seeds:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _coerce_set_value(v: str):
    """把 ``--set k=v`` 的 ``v`` 解析成合适类型。

    优先级：null/none → None；true/false → bool；``[...]``/``{...}`` 字面量
    走 json（支持 ``[3,4,5]``、``{"a":1}``）；再试 int → float；都不行则原样 str。
    旧实现把 ``[3,4,5]`` 当字符串留着，list/dict 覆盖会被静默吞掉，是 CLI 的
    主要坑（2026-10-26 修）。
    """
    import json

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
            pass  # 解析失败则回退到下面的标量/字符串逻辑
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _emit_cycle_progress(record, step: int, n_total: int) -> None:
    """逐周期进度行（挂到 ``horizon.run`` 的 ``progress_cb``）。

    每行一个周期，给出「入池/选中/执行/顺延/不可达」规模与单周期求解耗时，
    让长跑期间能看出进度与成本去向，而不是一片空白。字段均取自
    ``CycleRecord``（选择层与二阶段诊断已落盘其中）。
    """
    sel = record.selection or {}
    muas = record.muas or {}
    n_in = sel.get("n_tasks_in", "?")
    n_sel = muas.get("n_selected", "?")
    n_exec = len(getattr(record, "executed_ids", []) or [])
    n_defer = len(getattr(record, "deferred_ids", []) or [])
    n_unreach = len(getattr(record, "unreachable_ids", []) or [])
    solve = muas.get("solve_seconds", None)
    evals = muas.get("n_evals", None)
    solve_s = f"{solve:.2f}s" if isinstance(solve, (int, float)) else "?"
    evals_s = f"{evals}" if evals is not None else "?"
    print(
        f"[cycle {step}/{n_total}] 入池={n_in} 选中={n_sel} "
        f"执行={n_exec} 顺延={n_defer} 不可达={n_unreach} | "
        f"求解={solve_s} 评估={evals_s}",
        flush=True,
    )


def _apply_dotted_set(cfg: dict, items) -> None:
    """--set dotted.path=value 深合并进 raw_cfg（与 exp02/exp05/run_chunked 同语义）。"""
    if not items:
        return
    for it in items:
        if "=" not in it:
            raise ValueError(f"--set 需要 dotted.path=value 形式：{it!r}")
        path, _, val = it.partition("=")
        parts = [p.strip() for p in path.strip().split(".")]
        d = cfg
        for p in parts[:-1]:
            d = d.setdefault(p, {})
            if not isinstance(d, dict):
                raise ValueError(f"--set 路径冲突（非 dict 节点）：{path}")
        d[parts[-1]] = _coerce_set_value(val)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析实验入口参数；PDF 只在本次运行显式开启。"""
    parser = argparse.ArgumentParser(description="运行 exp01_smoke 端到端实验")
    parser.add_argument(
        "--seeds", nargs="+", metavar="SEED",
        help="覆盖 config.yaml 的 experiment.seeds：多种子稳健性运行的调试入口。"
             "支持 42 / 42,43,44 / 42-46（区间展开）等写法，可混用；"
             "去重保序。省略则按 yaml（单元素退化为单 seed）",
    )
    parser.add_argument(
        "--no-plots", action="store_true",
        help="跳过出图（多种子长跑调试用；json 数值产物仍落盘）",
    )
    parser.add_argument(
        "--pdf", action="store_true", help="额外输出 PDF 矢量图（默认仅 PNG）"
    )
    parser.add_argument(
        "--no-audit", action="store_true",
        help="跳过末尾的方法正确性审计（audit_correctness.py）",
    )
    parser.add_argument(
        "--group", action="append", dest="groups", metavar="LAYER",
        # 取值必须与 plot/registry.py 里 Figure.group 完全一致
        # （initial / process / report；2026-10-26 图件裁剪后 setup 层已删，
        # 旧值 "scene" 不存在于图集，而 resolve 对未知分层是抛 ValueError——
        # 照旧文档执行会在求解完成后崩溃并跳过末尾审计（2026-10-24 修复 P0-3）。
        choices=("initial", "process", "report"),
        help="只出某一层的图：initial=环境初始配置 / "
             "process=运行过程 / report=结论插图；可重复指定，省略则出全部",
    )
    parser.add_argument(
        "--figure", action="append", dest="figures", metavar="ID",
        help="只出指定图（图 id，如 geo_overview）；可重复指定",
    )
    parser.add_argument(
        "--set", action="append", default=[], metavar="dotted.path=value",
        help="深合并覆盖 config.yaml 任意节点（与 exp02/exp05 同语义），"
             "如 --set muas.lambda_cost=1e-1 --set experiment.output_dir=results_x；"
             "支持多次；值支持 null/true/false/int/float/list[...]/dict{...}/str。"
             "想先确认覆盖是否生效而不跑求解，加 --dry-run。",
    )
    parser.add_argument(
        "--window", nargs=2, metavar=("MIN", "MAX"), type=float,
        help="快捷覆盖任务时间窗（周期数）：--window 2.0 4.0 等价于 "
             "--set tasks.deadline_cycles_min=2.0 --set tasks.deadline_cycles_max=4.0",
    )
    parser.add_argument(
        "--range-km", dest="range_km", type=float, metavar="KM",
        help="快捷覆盖满电航程（公里）：--range-km 20 等价于 "
             "--set problem.uav_max_range=20000",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只解析+合并配置并打印生效值（含 --set/--window/--range-km 覆盖结果），"
             "不构建场景、不求解、不出图、不审计。验证命令行覆盖是否写对时很有用。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg_path = EXP_DIR / "config.yaml"
    raw_cfg = load_config(cfg_path)
    # --set 深合并（在 --seeds 覆盖与 bundle 构建之前）
    _apply_dotted_set(raw_cfg, args.set)
    # 快捷覆盖（等价于对应的 --set，写起来更干净）
    if args.window:
        raw_cfg.setdefault("tasks", {})["deadline_cycles_min"] = args.window[0]
        raw_cfg.setdefault("tasks", {})["deadline_cycles_max"] = args.window[1]
    if args.range_km is not None:
        raw_cfg.setdefault("problem", {})["uav_max_range"] = args.range_km * 1000.0
    # 命令行覆盖：--seeds 优先于 yaml 的 experiment.seeds（调试/长跑入口）
    if args.seeds:
        raw_cfg.setdefault("experiment", {})["seeds"] = _parse_seed_list(args.seeds)
    bundle0 = build_config_bundle(raw_cfg)

    # ── 仅校验覆盖（不求解）：打印生效配置后退出 ────────────────
    if args.dry_run:
        exp = raw_cfg.get("experiment", {}) or {}
        prob = raw_cfg.get("problem", {}) or {}
        tasks = raw_cfg.get("tasks", {}) or {}
        seeds = exp.get("seeds") or [exp.get("seed")]
        print("===== --dry-run：生效配置（已合并 --set/--window/--range-km） =====")
        print(f"  seeds              = {seeds}")
        print(f"  n_nests            = {prob.get('n_nests')}")
        print(f"  n_uavs             = {prob.get('n_uavs')}")
        print(f"  uav_max_range (km) = {prob.get('uav_max_range')/1000.0}")
        print(f"  deadline_cycles    = {tasks.get('deadline_cycles_min')} ~ "
              f"{tasks.get('deadline_cycles_max')}")
        print(f"  n_tasks (周期1)     = {tasks.get('n_tasks')}")
        print(f"  domain             = {tasks.get('domain')}")
        print(f"  output_dir         = {exp.get('output_dir', 'results')}")
        print("  ── 完整合并后的 raw_cfg（json） ──")
        import json

        print(json.dumps(raw_cfg, ensure_ascii=False, indent=2, default=str))
        print("===== --dry-run 结束（未执行求解） =====")
        return 0

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

    # ── 场景（与种子无关，只构建一次）────────────────────────────
    scenario = build_scenario(raw_cfg)
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
        print(f"[exp01] seed={seed} 完成，耗时 {facts.wall_seconds:.1f}s")

    # 代表性运行取首个 seed（供现有图与单值指标展示）
    rep_seed, rep_facts, rep_metrics, rep_solution = results[0]

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
        # 生效配置（含 --set 覆盖）：审计判据的唯一可信来源。审计若读
        # config.yaml 原文就看不到 --set，判据常数与 run_meta 指纹会分裂
        # （2026-10-24 修复 P0-2）。
        write_json(out_dir / "effective_config.json", raw_cfg)
        if multi:
            # 逐 seed 全量产物：D/J/K/M/N 五项依赖 solution 的 tours 与
            # fitness_weights，metrics.json 的 per_seed[seed]["cycles"]
            # 不足以审计其余 seed（2026-10-24 修复 P0-1）。目录名约定
            # seed_{id}，audit_correctness.py 据此遍历。
            for seed, _, m, sol in results:
                sd = out_dir / f"seed_{seed}"
                write_json(
                    sd / "metrics.json",
                    {**m, "seeds": [seed], "multi_seed": False},
                )
                write_json(sd / "solution.json", sol)

    # 多 seed 时各 seed 到达数可能不同（poisson/uniform 到达流）⇒ 报表分母
    # 取三 seed 均值，避免「分子是均值、分母是代表 seed 单值」不同源
    # （2026-10-24 修复 P1-2）。fixed 到达下均值即该值，逐位不变。
    if multi and final_metrics.get("seed_summary"):
        n_arrived = round(final_metrics["seed_summary"]["n_arrived"]["mean"], 1)
    else:
        n_arrived = len(rep_facts.batch.all_tasks())
    if verbose:
        print_summary(final_metrics, n_arrived, out_dir)

    # ── 出图（plot/ 只负责绘制，产物落 figures_dir）────────────
    # --no-plots 只跳绘制，不影响已落盘的 json / solution。
    make_plots = bundle0.make_plots and not args.no_plots
    if make_plots:
        from task_allocation.experiments.exp01_smoke.plot import make_plots  # 延迟导入：不出图时不引 matplotlib

        plot_cfg = bundle0.plot
        formats = tuple(plot_cfg.get("formats") or ("png",))
        if args.pdf and "pdf" not in {str(f).lower().lstrip(".") for f in formats}:
            formats = (*formats, "pdf")
        # 动画图需 gif/mp4 才出。只要本次图集包含任意 animated 图（默认全部图
        # 即包含 allocation_anim / allocation_status），就自动补 gif——否则 GIF
        # 会停留在早年一次显式运行，与 json/PNG/表格全部脱节（2026-10-13 审查：
        # GIF 曾 stale 12 小时）。只想出静态图时用 --groups/--figures 排除动画图即可。
        from task_allocation.experiments.exp01_smoke.plot.registry import resolve as _resolve_figures
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

    # ── 方法正确性审计（持续门禁，默认开）────────────────────────
    # 从落盘 json 独立复核 23 项不变量/口径；FAIL 只报警不阻断
    # （产物已落盘，报警促人核查）。--no-audit 可关。
    if bundle0.save_results and not args.no_audit:
        _run_audit(out_dir, verbose=verbose)

    return 0


if __name__ == "__main__":
    sys.exit(main())
