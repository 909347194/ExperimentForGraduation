# -*- coding: utf-8 -*-
"""exp02_selection_scale 入口：量化第一阶段「任务选择」的规模压缩收益与标定。

问题背景（Chapter3_README §1(4)）：
    当 |T_t| >> K(t) 时，把全部任务直接交给精细组合优化会显著扩大
    搜索空间。第一阶段用候选预筛把 T_t 压到 T_t^sel，据称能提高实时性。

exp01_smoke 跑在「机多任务少」一侧（10 任务 / 24 机，αK=60），n_cand 预筛从不
触发，无法验证该机制。本实验固定 K=8（可用无人机数），把任务规模拉到 50/100/200，
分别测量：

    T_hint    cost_hint（最近可用机，惰性表 O(n·K) 条边）
    T_sel     第一阶段耗时 + 压缩率 |T_t| → |T_t^sel|
    T_build   候选子集三维矩阵构建（惰性表共享缓存后的新增边）
    T_muas    第二阶段对候选集求解的耗时
    T_full    【对照】不经过第一阶段直接全量求解（建矩阵 + 求解分开计时）

由此回答三个问题：
    1. 压缩是否真的发生了？压到多少？（候选规模跟 N 还是跟 K/能力走）
    2. 分层省下的搜索时间，是否被 O(n²) 代价构建吃掉？
       （2026-10-20 延迟构建后：全量建矩阵不再发生在一阶段之前）
    3. 候选规模按「机数名额」还是「机队能力」标定，吞吐-耗时-质量
       平衡点各在哪里？（variants 扫描：quota_* vs cap_x*）

2026-10-20 起接入延迟构建（horizon 同款）：一阶段前只建惰性代价表
（按需算 + 缓存，值与全量 eager 建表逐位一致），一阶段后只对候选子集
建矩阵——N=200 时端到端不再被 O(n²) 全量建矩阵 dominate（旧版占 85.6%）。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

EXP_DIR = Path(__file__).resolve().parent
ROOT = EXP_DIR.parents[2]
DATA_DIR = ROOT / "task_allocation" / "data"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_allocation.common.scenario import build_nest_scenario  # noqa: E402
from task_allocation.common.task_scenario import (  # noqa: E402
    SyntheticTaskConfig,
    build_synthetic_tasks,
)
from task_allocation.common.types import UAV  # noqa: E402
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.cost.lazy_pairwise import make_lazy_pairwise  # noqa: E402
from task_allocation.methods.muas.cost.vertical_section import (  # noqa: E402
    VerticalSectionCostEstimator,
    make_distance_fn,
)
from task_allocation.methods.muas.problem import MUASProblemConfig  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)
from task_allocation.methods.selection import run as run_selection  # noqa: E402
from task_allocation.methods.selection import SelectionConfig  # noqa: E402
from task_allocation.methods.selection.capability import CapabilityScaleConfig  # noqa: E402
from task_allocation.methods.selection.feasibility import FeasibilityConfig  # noqa: E402
from task_allocation.methods.selection.marginal_gain import MarginalConfig  # noqa: E402
from task_allocation.methods.selection.priority import (  # noqa: E402
    PriorityConfig,
    PriorityWeights,
)


def load_config(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("需要 PyYAML")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def selection_config_from(
    cfg: dict,
    variant: dict[str, Any] | None = None,
    current_time: float = 0.0,
) -> SelectionConfig:
    """按 yaml 的 selection 段 + variant 覆盖构造 SelectionConfig。

    variant 可覆盖 ``alpha`` 与 ``capability``（规模标定扫描用）；
    缺省走 yaml 的 selection 段（生产口径）。
    """
    d = cfg.get("selection", {}) or {}
    v = variant or {}
    weights = d.get("weights") or {}
    pc = PriorityConfig(
        alpha=float(v.get("alpha", d.get("alpha", 2.5))),
        synergy_radius=float(d.get("synergy_radius", 5000.0)),
        wait_scale=float(d.get("wait_scale", 3600.0)),
        current_time=current_time,
    )
    if weights:
        pc.weights = PriorityWeights(**{k: float(v) for k, v in weights.items()}).normalized()
    cap_d = v.get("capability") or {}
    capability = CapabilityScaleConfig(
        enabled=bool(cap_d.get("enabled", True)),
        budget_scale=float(cap_d.get("budget_scale", 1.0)),
        quantile=float(cap_d.get("quantile", 0.5)),
        min_cap=int(cap_d.get("min_cap", 1)),
        mode=str(cap_d.get("mode", "intersect")),
        max_expand_factor=(
            None if cap_d.get("max_expand_factor") is None
            else float(cap_d["max_expand_factor"])
        ),
    )
    return SelectionConfig(
        priority=pc,
        feasibility=FeasibilityConfig(current_time=current_time),
        marginal=MarginalConfig(current_time=current_time),
        capability=capability,
        current_time=current_time,
    )


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
            raise ValueError(f"--set 需要 dotted.path=value 形式：{it!r}")
        path, _, val = it.partition("=")
        parts = [p.strip() for p in path.strip().split(".")]
        d = cfg
        for p in parts[:-1]:
            d = d.setdefault(p, {})
            if not isinstance(d, dict):
                raise ValueError(f"--set 路径冲突（非 dict 节点）：{path}")
        d[parts[-1]] = _coerce_set_value(val)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="exp02_selection_scale：规模压缩与标定扫描")
    ap.add_argument("--no-plots", action="store_true",
                    help="跳过出图（只落 metrics/run_meta json）")
    ap.add_argument("--formats", default=None,
                    help="逗号分隔出图格式（缺省读 config.yaml 的 plot.formats）")
    ap.add_argument("--dpi", type=int, default=None,
                    help="出图 dpi（缺省读 config.yaml 的 plot.dpi）")
    ap.add_argument(
        "--set", action="append", default=[], metavar="dotted.path=value",
        help="覆盖 config.yaml 任意键，便于 500 代等敏感性实验，"
             "如 --set solver.max_generations=500；支持多次。")
    args = ap.parse_args(argv)

    cfg_path = EXP_DIR / "config.yaml"
    cfg = load_config(cfg_path)
    _apply_dotted_set(cfg, args.set)
    out_dir = EXP_DIR / cfg["experiment"]["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 场景：机巢 + DEM + 局部平面 + 估算器 + 统一距离尺子 ─────────
    data_cfg = cfg["data"]
    problem_cfg = cfg["problem"]
    scenario = build_nest_scenario(
        DATA_DIR / data_cfg["path"],
        count=problem_cfg["n_nests"],
        capacity=problem_cfg["nest_capacity"],
        column=data_cfg.get("coordinate_column", "Coordinate"),
        dem_path=DATA_DIR / data_cfg["dem"],
    )
    terrain = scenario.dem.to_metric_terrain(scenario.frame, step_m=200.0)
    estimator = VerticalSectionCostEstimator(dem_terrain=terrain)
    # 统一距离口径（与 horizon 同款）：带记忆化，跨变体/跨规模共享缓存
    distance_fn = make_distance_fn(estimator)

    n_uavs = int(problem_cfg["n_uavs"])
    max_range = float(problem_cfg.get("uav_max_range", 80000.0))
    # 2026-10-20 修复：UAV 必须**驻留其机巢**（exp01 build_uavs 同口径）。
    # 旧版把全部 UAV 放在局部平面原点 (0,0)，而任务锚定在散布的机巢周围
    # （nest span 约 70km×87km）——最近可用机距离中位数被推到 39km、
    # cap(t)=B/c̃ 缩到 16、半数任务被往返预筛误杀（24/50），整个规模实验
    # 测的是「原点邻域」这个退化构型。驻巢后中位 15.2km、cap(t)≈42、
    # 与 full baseline 的 34/50 执行一致，能力驱动标定才真正可测。
    nest_list = list(scenario.nests)
    uavs = [
        UAV(
            id=i,
            x=nest_list[i % len(nest_list)].x,
            y=nest_list[i % len(nest_list)].y,
            z=nest_list[i % len(nest_list)].z,
            nest_id=i % len(nest_list),
            remaining_range=max_range,
            speed=15.0,
        )
        for i in range(n_uavs)
    ]

    # ── 算法配置 ─────────────────────────────────────────────────
    solver_cfg = DMDEConfig(
        pop_size=int(cfg["solver"].get("pop_size", 20)),
        max_generations=int(cfg["solver"].get("max_generations", 60)),
        seed=int(cfg["experiment"].get("seed", 42)),
    )
    m = cfg.get("problem", {})
    problem_model = MUASProblemConfig(
        allow_unassigned=bool(m.get("allow_unassigned", True)),
        allow_idle_uav=bool(m.get("allow_idle_uav", True)),
        lambda_cost=float(m.get("lambda_cost", 1.0e-4)),
        w_penalty=float(m.get("w_penalty", 1.0)),
        w_nest_capacity=float(m.get("w_nest_capacity", 1.0)),
        w_time=float(m.get("w_time", 0.0)),
    )
    s = cfg.get("stage", {}) or {}
    stage_cfg = MUASStageConfig(
        prune_unprofitable=bool(s.get("prune_unprofitable", True)),
        prune_max_rounds=int(s.get("prune_max_rounds", 8)),
        prune_margin=float(s.get("prune_margin", 0.0)),
        prune_wait_bonus=float(s.get("prune_wait_bonus", 1.0)),
        wait_scale=float(s.get("wait_scale", 3600.0)),
        participating_policy=str(s.get("participating_policy", "cheapest")),
        participation_cost=float(s.get("participation_cost", 0.0)),
        marginal_tour_cap=(
            None if s.get("marginal_tour_cap") is None
            else int(s["marginal_tour_cap"])
        ),
        polish_enabled=bool(s.get("polish_enabled", True)),
        polish_max_rounds=int(s.get("polish_max_rounds", 30)),
        polish_max_evals=int(s.get("polish_max_evals", 2000)),
        polish_eps=float(s.get("polish_eps", 1.0e-6)),
    )
    variants = list(cfg.get("variants") or [{"name": "default", "alpha": None}])
    baseline_scales = set(cfg["run"].get("full_muas_baseline_scales") or [])

    print(f"[exp02] K={n_uavs}, n_nests={problem_cfg['n_nests']}, "
          f"max_range={max_range/1000:.0f}km, variants={[v['name'] for v in variants]}")

    results: list[dict[str, Any]] = []
    for scale in cfg["scales"]:
        n_tasks = int(scale)
        tcfg = SyntheticTaskConfig(
            n_tasks=n_tasks,
            seed=int(cfg["experiment"].get("seed", 42)) + n_tasks,
            # 撒点域显式接线（默认 nest_anchored；dem_extent 为 exp01 设定）
            domain=str(cfg["tasks"].get("domain", "nest_anchored")).strip().lower(),
            spread_m=float(cfg["tasks"].get("spread_m", 25000.0)),
            reward_min=float(cfg["tasks"].get("reward_min", 1.0)),
            reward_max=float(cfg["tasks"].get("reward_max", 10.0)),
            deadline_min=float(cfg["tasks"].get("deadline_min", 3600.0)),
            deadline_max=float(cfg["tasks"].get("deadline_max", 28800.0)),
            anchor_to_nests=bool(cfg["tasks"].get("anchor_to_nests", True)),
        )
        tasks = build_synthetic_tasks(scenario.nests, scenario.frame, scenario.dem, tcfg)

        for variant in variants:
            vname = str(variant.get("name", "default"))
            row: dict[str, Any] = {
                "n_tasks": n_tasks,
                "n_uav_avail": n_uavs,
                "variant": vname,
            }

            # 1) 惰性代价表（一阶段前不建全量矩阵；值 = 全量 eager 逐位一致）
            lazy = make_lazy_pairwise(
                tasks, uavs, scenario.nests, estimator=estimator,
            )
            # 1a) cost_hint：最近可用机（n·K 条边，命中即缓存）
            t0 = time.perf_counter()
            cost_hint: dict[int, float] = {}
            for t in tasks:
                cost_hint[t.id] = min(
                    lazy[(-(1 + u.id), t.id)] for u in uavs if u.is_available
                )
            row["t_hint_ms"] = (time.perf_counter() - t0) * 1000.0

            # 2) 第一阶段：任务选择（pairwise 走惰性活视图）
            sel_cfg = selection_config_from(cfg, variant, current_time=0.0)
            t0 = time.perf_counter()
            sel = run_selection(
                tasks, uavs, sel_cfg,
                cost_hint=cost_hint,
                pairwise_cost=lazy,
                distance_fn=distance_fn,
            )
            row["t_selection_ms"] = (time.perf_counter() - t0) * 1000.0
            row["selection"] = dict(sel.diagnostics)
            row["n_selected"] = len(sel.selected)
            row["compression_ratio"] = round(n_tasks / max(len(sel.selected), 1), 3)

            # 3) 候选子集矩阵（与一阶段共享惰性表：已算边直接命中）
            t0 = time.perf_counter()
            cb_sel = build_pairwise_costs(
                sel.selected, uavs, scenario.nests,
                estimator=estimator, pairwise_source=lazy,
                distance_fn=distance_fn,
            )
            row["t_build_ms"] = (time.perf_counter() - t0) * 1000.0
            row["lazy_edges"] = lazy.n_computed

            # 4) 第二阶段：对候选集求解
            t0 = time.perf_counter()
            res = run_selective_muas(
                sel.selected, uavs, scenario.nests, cb_sel,
                problem_config=problem_model, stage_config=stage_cfg,
                solver_config=solver_cfg,
            )
            row["t_muas_ms"] = (time.perf_counter() - t0) * 1000.0
            diag = res.diagnostics or {}
            row["muas"] = {
                "model_type": res.meta.get("model_type") if getattr(res, "meta", None) else None,
                "n_executed": len(res.solution.executed_task_ids),
                "n_deferred": len(sel.selected) - len(res.solution.executed_task_ids),
                "f1_reward": round(res.solution.f1_reward, 3),
                "f2_cost": round(res.solution.f2_cost, 1),
                # n_evals 在 MUASStageResult 属性上（diagnostics 字典无此键，
                # 误取会恒为 0——2026-10-20 首版即此错，已修）
                "n_evals": int(getattr(res, "n_evals", 0) or 0),
                "polish_evals": int(diag.get("polish_evals", 0)),
                "polish_accepted": int(sum((diag.get("polish_accepted") or {}).values())),
                "final_fitness": (
                    None if diag.get("final_fitness") is None
                    else round(float(diag["final_fitness"]), 1)
                ),
            }
            row["t_total_ms"] = (
                row["t_hint_ms"] + row["t_selection_ms"]
                + row["t_build_ms"] + row["t_muas_ms"]
            )
            results.append(row)
            print(
                f"[exp02] N={n_tasks:>4} {vname:<12} | "
                f"hint={row['t_hint_ms']:>7.0f} sel={row['t_selection_ms']:>7.1f} "
                f"build={row['t_build_ms']:>7.0f} muas={row['t_muas_ms']:>8.0f}ms | "
                f"sel {n_tasks}->{row['n_selected']} ({row['compression_ratio']}x) | "
                f"exec={row['muas']['n_executed']} F1={row['muas']['f1_reward']}"
            )

        # 5) 对照：不经过第一阶段，直接对全量任务求解
        if n_tasks in baseline_scales:
            t0 = time.perf_counter()
            cb_full = build_pairwise_costs(
                tasks, uavs, scenario.nests, estimator=estimator,
                distance_fn=distance_fn,
            )
            t_build = (time.perf_counter() - t0) * 1000.0
            t0 = time.perf_counter()
            res_full = run_selective_muas(
                tasks, uavs, scenario.nests, cb_full,
                problem_config=problem_model, stage_config=stage_cfg,
                solver_config=solver_cfg,
            )
            t_muas = (time.perf_counter() - t0) * 1000.0
            results.append({
                "n_tasks": n_tasks,
                "n_uav_avail": n_uavs,
                "variant": "full_baseline",
                "t_hint_ms": 0.0,
                "t_selection_ms": 0.0,
                "t_build_ms": t_build,
                "t_muas_ms": t_muas,
                "t_total_ms": t_build + t_muas,
                "n_selected": n_tasks,
                "compression_ratio": 1.0,
                "lazy_edges": len(cb_full.provider.pairwise),
                "muas": {
                    "n_executed": len(res_full.solution.executed_task_ids),
                    "n_deferred": 0,
                    "f1_reward": round(res_full.solution.f1_reward, 3),
                    "f2_cost": round(res_full.solution.f2_cost, 1),
                    "n_evals": int(getattr(res_full, "n_evals", 0) or 0),
                    "polish_evals": int((res_full.diagnostics or {}).get("polish_evals", 0)),
                    "polish_accepted": int(sum(
                        ((res_full.diagnostics or {}).get("polish_accepted") or {}).values()
                    )),
                    "final_fitness": (
                        None if (res_full.diagnostics or {}).get("final_fitness") is None
                        else round(float(res_full.diagnostics["final_fitness"]), 1)
                    ),
                },
            })
            print(
                f"[exp02] N={n_tasks:>4} {'full_baseline':<12} | "
                f"build={t_build:>7.0f} muas={t_muas:>8.0f}ms | "
                f"exec={len(res_full.solution.executed_task_ids)} "
                f"F1={round(res_full.solution.f1_reward, 1)}"
            )

    # ── 汇总表（终端速览：规模 × 变体的压缩-耗时-质量）──────────────
    print("\n[exp02] === 汇总：N × variant（sel=候选数, exec=执行数, "
          "F1=收益, T=端到端秒） ===")
    header = f"{'N':>5} {'variant':<14} {'sel':>4} {'exec':>5} {'F1':>8} {'T/s':>7}"
    print(header)
    for row in results:
        if row["variant"] == "full_baseline":
            continue
        print(f"{row['n_tasks']:>5} {row['variant']:<14} {row['n_selected']:>4} "
              f"{row['muas']['n_executed']:>5} {row['muas']['f1_reward']:>8.1f} "
              f"{row['t_total_ms'] / 1000.0:>7.1f}")
    for row in results:
        if row["variant"] != "full_baseline":
            continue
        print(f"{row['n_tasks']:>5} {'full_baseline':<14} {row['n_selected']:>4} "
              f"{row['muas']['n_executed']:>5} {row['muas']['f1_reward']:>8.1f} "
              f"{row['t_total_ms'] / 1000.0:>7.1f}")

    # ── 产物 ─────────────────────────────────────────────────────
    metrics = {
        "status": "ok",
        "experiment": cfg["experiment"]["name"],
        "K": n_uavs,
        "uav_max_range_m": max_range,
        "scales": results,
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "experiment": cfg["experiment"]["name"],
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "config_path": str(cfg_path.relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[exp02] wrote {out_dir / 'metrics.json'}")

    # ── 出图（plot/ 只负责绘制，产物落 figures_dir；--no-plots 只跳绘制）──
    if not args.no_plots:
        figures_dir = EXP_DIR / str(
            cfg["experiment"].get("figures_dir", "results/figures")
        )
        plot_cfg = cfg.get("plot") or {}
        formats = tuple(
            f.strip().lower().lstrip(".")
            for f in (args.formats or ",".join(plot_cfg.get("formats") or ["png"])).split(",")
            if f.strip()
        )
        dpi = int(args.dpi or plot_cfg.get("dpi", 200) or 200)
        try:
            if str(EXP_DIR) not in sys.path:
                sys.path.insert(0, str(EXP_DIR))
            from plot import make_plots  # 延迟导入：不出图时不引 matplotlib

            make_plots(metrics, {}, figures_dir, formats=formats, dpi=dpi)
        except Exception as exc:  # 出图失败不阻断数值产物
            print(f"[exp02][warn] 出图失败（json 已落盘）：{exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
