# -*- coding: utf-8 -*-
"""exp02_selection_scale 入口：**单周期任务规模可扩展性台**。

职责（唯一）：在固定机队 K=6、固定求解预算、固定场景 guangzhou 下，把单周期
任务规模 N 扫到 10/50/100，测量第一阶段「任务选择」把进入二阶段的任务数
压到多少、分段墙钟与现算边数随 N 怎么变，并与「不做选择、直接对全量任务
求解」的同口径参照对比。产物是规模—耗时曲线与任务去向分账，**描述性**。

不负责（边界，见 notes.md）：
    * 不评价方法好坏、不给收益比值 / 耗时倍数 / 取值建议类结论；
    * 不评价「完成是约束、成本是目标」——本实验是**单周期静态快照**，
      被选择层刷掉的任务没有后续周期，一律记 remain（未完成）；
      完成约束由 exp01 滚动闭环检验；
    * 不做标定口径选型（quota_* / cap_x* 只是给定压缩档位，不是待选方法）；
    * 不做滚动到达、不做跨实验比较、不做显著性检验。

计时口径：单进程、单 seed、单次 perf_counter 观测；两臂共用同一 DEM 段长
记忆化缓存与同一惰性代价表构造，故**唯一自变量是「是否经过选择层」**。
墙钟只作观测，不作机制归因、不报告耗时倍数（跨格共享缓存会有先后偏倚）。

三段计时（与 README §3 一致）：
    T_hint + T_selection   第一阶段（最近可用机惰性边 + 选择）
    T_build                只对候选子集建三维矩阵（一阶段已算边命中缓存）
    T_muas                 第二阶段对候选集求解
    【参照】同上惰性表建全量矩阵并求解，但不调用选择层
"""

from __future__ import annotations

import hashlib
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
DATA_DIR = ROOT / "task_allocation" / "data"  # 场景注册表根；文件解析走 scenario_data

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(EXP_DIR) not in sys.path:
    sys.path.insert(0, str(EXP_DIR))

from accounting import accounting_from, assert_no_quality_claims, summarize  # noqa: E402
from task_allocation.common import scenario_data  # noqa: E402
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
from task_allocation.methods.muas.problem import (  # noqa: E402
    ConstraintConfig,
    MUASProblemConfig,
)
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)
from task_allocation.methods.selection import run as run_selection  # noqa: E402
from task_allocation.methods.utils.building_layer import BuildingLayer  # noqa: E402
from task_allocation.methods.selection import SelectionConfig  # noqa: E402
from task_allocation.methods.selection.capability import CapabilityScaleConfig  # noqa: E402
from task_allocation.methods.selection.feasibility import FeasibilityConfig  # noqa: E402
from task_allocation.methods.selection.marginal_gain import MarginalConfig  # noqa: E402
from task_allocation.methods.selection.priority import (  # noqa: E402
    PriorityConfig,
    PriorityWeights,
)


def _write_json_atomic(path: Path, obj: Any) -> None:
    """原子落盘：先写同名 ``.tmp`` 再替换，避免超时/中断留下半截 json。

    三份产物（effective_config / metrics / run_meta）都走这里，保证下游
    任何时刻读到的都是完整 json；配合「求解完成才落盘」，不会再出现
    「有配置快照、没有数值产物」的孤儿档。
    """
    payload = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


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
    # 选择封顶 max_ratio 显式从 yaml 的 selection.marginal 段读取：
    # 旧实现用 MarginalConfig 的代码默认 2.0，写成 config 键是「死键」
    # （写了也静默不生效），导致 ⌈max_ratio·K⌉=12 这个真正的瓶颈不在
    # effective_config 里、无法复现核对。显式给 2.0 与代码默认逐位相同。
    marg_d = d.get("marginal") or {}
    marginal = MarginalConfig(
        max_ratio=float(marg_d.get("max_ratio", 2.0)),
        current_time=current_time,
    )
    return SelectionConfig(
        priority=pc,
        feasibility=FeasibilityConfig(current_time=current_time),
        marginal=marginal,
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
        help="覆盖 config.yaml 任意键，如 --set solver.max_generations=500；支持多次。")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印生效配置与派生口径，不建场景、不求解、不落盘")
    args = ap.parse_args(argv)

    cfg_path = EXP_DIR / "config.yaml"
    cfg = load_config(cfg_path)
    _apply_dotted_set(cfg, args.set)

    # 有效配置快照：--set 覆盖后的**全量**配置 + 派生口径，供复现核对。
    # 落盘在 out_dir，与 metrics.json 同源；键路径必须与代码实际读取的一致。
    effective = {
        "config_path": str(cfg_path.relative_to(ROOT)),
        "cli_set": list(args.set),
        "config": cfg,
        "resolved": {
            "scenario": (cfg.get("data") or {}).get("scenario"),
            "K": (cfg.get("problem") or {}).get("n_uavs"),
            "n_nests": (cfg.get("problem") or {}).get("n_nests"),
            "uav_max_range_m": (cfg.get("problem") or {}).get("uav_max_range"),
            "uav_speed_mps": (cfg.get("problem") or {}).get("uav_speed"),
            "scales": list(cfg.get("scales") or []),
            "variants": [str(v.get("name")) for v in (cfg.get("variants") or [])],
            "selection_marginal_max_ratio": (
                (cfg.get("selection") or {}).get("marginal") or {}
            ).get("max_ratio", 2.0),
            "solver": dict(cfg.get("solver") or {}),
            "full_baseline_scales": list(
                (cfg.get("run") or {}).get("full_muas_baseline_scales") or []
            ),
        },
    }
    # 配置指纹：把 metrics / run_meta 与本次 effective_config 绑在一起，
    # 便于事后核对「这份数值产物到底是哪套配置跑出来的」。
    effective_sha256 = hashlib.sha256(
        json.dumps(effective, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if args.dry_run:
        print(json.dumps(effective, ensure_ascii=False, indent=2))
        return 0

    out_dir = EXP_DIR / cfg["experiment"]["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # 三份 json 在求解全部完成、分账门禁通过后同一时点原子落盘：
    # 旧实现在最开头就写 effective_config.json，场景构建/求解中途失败会
    # 留下「有配置快照、没有数值产物」的孤儿档，容易被误认成一次运行。
    t_start = time.perf_counter()

    # ── 场景：机巢 + DEM + 建筑包络 + 局部平面 + 估算器 + 统一距离尺子 ─
    # 2026-11-10 切 guangzhou：估算器必须挂建筑层，否则垂直切面代价只含地形，
    # 与 exp01 当前正式档的「DEM + 建筑屋顶包络」口径不一致。
    # 其余参数用类默认值（50 / 300 / 0.15 / 200），与 exp01 cost 段显式值逐位一致。
    data_cfg = cfg["data"]
    problem_cfg = cfg["problem"]
    scenario = build_nest_scenario(
        scenario_data.resolve_file(data_cfg["path"], data_cfg.get("scenario")),
        count=problem_cfg["n_nests"],
        capacity=problem_cfg["nest_capacity"],
        column=data_cfg.get("coordinate_column", "Coordinate"),
        dem_path=scenario_data.resolve_file(data_cfg["dem"], data_cfg.get("scenario")),
    )
    terrain = scenario.dem.to_metric_terrain(scenario.frame, step_m=200.0)

    buildings = None
    bname = data_cfg.get("buildings")
    if bname:
        bbase = scenario_data.scenario_dir(data_cfg.get("scenario")) / str(bname)
        bpaths = sorted(bbase.glob("*.shp")) if bbase.is_dir() else [bbase]
        if not bpaths:
            raise FileNotFoundError(f"建筑数据不存在：{bbase}")
        buildings = BuildingLayer.from_shapefiles(bpaths, scenario.frame)
        print(f"[exp02] 建筑层：{len(bpaths)} 个 shapefile", flush=True)
    estimator = VerticalSectionCostEstimator(dem_terrain=terrain, buildings=buildings)
    # 统一距离口径（与 horizon 同款）：带记忆化，跨变体/跨规模共享缓存
    distance_fn = make_distance_fn(estimator)

    n_uavs = int(problem_cfg["n_uavs"])
    max_range = float(problem_cfg.get("uav_max_range", 80000.0))
    speed = float(problem_cfg.get("uav_speed", 15.0))  # 与 exp01/exp05 build_uavs 同口径读 config
    # 最大飞行时间（秒）：显式 uav_max_time 优先；否则派生 = 航程/速度，
    # 与航程预算严格自洽（派生下 time 约束是 range 约束的等价重述，不新增淘汰）。
    max_time = problem_cfg.get("uav_max_time")
    if max_time is None and speed > 0:
        max_time = max_range / speed
    # UAV 驻留其机巢（exp01 build_uavs 同口径）：guangzhou 研究区内撒点、
    # 满电航程 10 km。c̃ 与 cap(t) 的实际量级由本次 metrics.json 的
    # selection.capability_cap 现算，不再沿用旧 lasa_chengguan 档的
    # 70km×87km / cap≈42 说明。
    nest_list = list(scenario.nests)
    uavs = [
        UAV(
            id=i,
            x=nest_list[i % len(nest_list)].x,
            y=nest_list[i % len(nest_list)].y,
            z=nest_list[i % len(nest_list)].z,
            nest_id=i % len(nest_list),
            remaining_range=max_range,
            speed=speed,
            max_time=max_time,
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
    # 与 exp01 当前正式档一致：单一加权标量 f（完成是约束、成本是目标），
    # 搜索用 f 不含 −m·F1、F3 已移除；覆盖门禁口径由 config 显式给出。
    problem_model = MUASProblemConfig(
        allow_unassigned=bool(m.get("allow_unassigned", True)),
        allow_idle_uav=bool(m.get("allow_idle_uav", True)),
        lambda_cost=float(m.get("lambda_cost", 1.0e-2)),
        w_penalty=float(m.get("w_penalty", 1.0)),
        w_violation=(
            None if m.get("w_violation") is None else float(m["w_violation"])
        ),
        w_nest_capacity=float(m.get("w_nest_capacity", 1.0)),
        big_m=(None if m.get("big_m") is None else float(m["big_m"])),
        late_reward_decay_seconds=(
            None if m.get("late_reward_decay_seconds") is None
            else float(m["late_reward_decay_seconds"])
        ),
        w_hard_violation=(
            None if m.get("w_hard_violation") is None
            else float(m["w_hard_violation"])
        ),
        hard_violation_kappa=float(m.get("hard_violation_kappa", 10.0)),
        # 覆盖门禁审计分账开关；兼容旧 yaml 键 lexicographic_coverage
        coverage_audit=bool(
            m.get("coverage_audit", m.get("lexicographic_coverage", False))
        ),
        w_skip_violation=(
            None if m.get("w_skip_violation") is None
            else float(m["w_skip_violation"])
        ),
        constraints=ConstraintConfig.from_dict(cfg.get("constraints")),
    )
    s = cfg.get("stage", {}) or {}
    stage_cfg = MUASStageConfig(
        prune_unprofitable=bool(s.get("prune_unprofitable", True)),
        prune_max_rounds=int(s.get("prune_max_rounds", 8)),
        prune_margin=float(s.get("prune_margin", 0.0)),
        prune_wait_bonus=float(s.get("prune_wait_bonus", 1.0)),
        wait_scale=float(s.get("wait_scale", 3600.0)),
        # 需求感知参与（生产口径）：贪心最大覆盖 + 容量感知补机
        participating_policy=str(s.get("participating_policy", "demand")),
        participation_cost=float(s.get("participation_cost", 0.0)),
        marginal_tour_cap=(
            None if s.get("marginal_tour_cap") is None
            else int(s["marginal_tour_cap"])
        ),
        demand_coverage_radius=(
            None if s.get("demand_coverage_radius") is None
            else float(s["demand_coverage_radius"])
        ),
        demand_range_factor=float(s.get("demand_range_factor", 0.5)),
        demand_provision_serving=bool(s.get("demand_provision_serving", True)),
        demand_serve_margin=float(s.get("demand_serve_margin", 0.95)),
        # 「可达即执行」救援池口径：扩到全可用机队
        reflow_rescue_scope=str(s.get("reflow_rescue_scope", "fleet")),
        polish_enabled=bool(s.get("polish_enabled", True)),
        polish_max_rounds=int(s.get("polish_max_rounds", 30)),
        polish_max_evals=int(s.get("polish_max_evals", 2000)),
        polish_eps=float(s.get("polish_eps", 1.0e-6)),
        # 覆盖门禁（默认关；完整口径 = coverage_gate + problem.lexicographic_coverage 同开）
        coverage_gate=bool(s.get("coverage_gate", False)),
        coverage_gate_max_rounds=int(s.get("coverage_gate_max_rounds", 8)),
        coverage_first=bool(s.get("coverage_first", False)),
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
            # 区域门禁（城关区 geojson）：相对场景数据目录（data/<scenario>/）
            region_geojson=str(scenario_data.resolve_file(
                data_cfg["region_geojson"], data_cfg.get("scenario")
            ))
            if data_cfg.get("region_geojson")
            else None,
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
            n_done = len(res.solution.executed_task_ids)
            row["muas"] = {
                "model_type": res.meta.get("model_type") if getattr(res, "meta", None) else None,
                "n_executed": n_done,
                "n_deferred": len(sel.selected) - n_done,
                # F1 只作事后指标（problem.py: 门禁口径下不进 f），
                # 此处保留为描述性观测，**不得**用作质量/优劣判据。
                "f1_reward_descriptive": round(res.solution.f1_reward, 3),
                "f2_cost": round(res.solution.f2_cost, 1),
                # n_evals 在 MUASStageResult 属性上（diagnostics 字典无此键）
                "n_evals": int(getattr(res, "n_evals", 0) or 0),
                "polish_evals": int(diag.get("polish_evals", 0)),
                "polish_accepted": int(sum((diag.get("polish_accepted") or {}).values())),
                "final_fitness": (
                    None if diag.get("final_fitness") is None
                    else round(float(diag["final_fitness"]), 1)
                ),
            }
            row["accounting"] = accounting_from(
                n_arrived=n_tasks,
                n_selected=len(sel.selected),
                n_done=n_done,
                selection_active=len(sel.selected) < n_tasks,
            )
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
                f"done={n_done} remain={row['accounting']['n_remain']}"
            )

        # 5) 参照臂：**只差「不调用选择层」**，其余与选择臂逐位同口径。
        #    旧实现用 eager 全量建表（不传 pairwise_source），而选择臂走惰性
        #    活视图 ⇒ 计时差里混了「建表策略」这个第二自变量，无法归因。
        #    现在两臂都先建惰性表、再用 pairwise_source 建矩阵；唯一差别是
        #    选择臂对 sel.selected 子集建表 + 多走 hint/selection 两步。
        if n_tasks in baseline_scales:
            lazy_full = make_lazy_pairwise(
                tasks, uavs, scenario.nests, estimator=estimator,
            )
            t0 = time.perf_counter()
            cb_full = build_pairwise_costs(
                tasks, uavs, scenario.nests, estimator=estimator,
                pairwise_source=lazy_full, distance_fn=distance_fn,
            )
            t_build = (time.perf_counter() - t0) * 1000.0
            t0 = time.perf_counter()
            res_full = run_selective_muas(
                tasks, uavs, scenario.nests, cb_full,
                problem_config=problem_model, stage_config=stage_cfg,
                solver_config=solver_cfg,
            )
            t_muas = (time.perf_counter() - t0) * 1000.0
            n_done_full = len(res_full.solution.executed_task_ids)
            diag_full = res_full.diagnostics or {}
            full_row: dict[str, Any] = {
                "n_tasks": n_tasks,
                "n_uav_avail": n_uavs,
                "variant": "full_baseline",
                "t_hint_ms": 0.0,
                "t_selection_ms": 0.0,
                "t_build_ms": t_build,
                "t_muas_ms": t_muas,
                # 参照臂没有第一阶段，端到端 = 建表 + 求解；与选择臂的
                # t_total 定义一致（四项之和，前两项为 0）。
                "t_total_ms": t_build + t_muas,
                "n_selected": n_tasks,
                "compression_ratio": 1.0,
                "lazy_edges": lazy_full.n_computed,
                "muas": {
                    "n_executed": n_done_full,
                    "n_deferred": n_tasks - n_done_full,
                    "f1_reward_descriptive": round(res_full.solution.f1_reward, 3),
                    "f2_cost": round(res_full.solution.f2_cost, 1),
                    "n_evals": int(getattr(res_full, "n_evals", 0) or 0),
                    "polish_evals": int(diag_full.get("polish_evals", 0)),
                    "polish_accepted": int(sum(
                        (diag_full.get("polish_accepted") or {}).values()
                    )),
                    "final_fitness": (
                        None if diag_full.get("final_fitness") is None
                        else round(float(diag_full["final_fitness"]), 1)
                    ),
                },
                "accounting": accounting_from(
                    n_arrived=n_tasks,
                    n_selected=n_tasks,
                    n_done=n_done_full,
                    selection_active=False,
                ),
            }
            results.append(full_row)
            print(
                f"[exp02] N={n_tasks:>4} {'full_baseline':<12} | "
                f"build={t_build:>7.0f} muas={t_muas:>8.0f}ms | "
                f"done={n_done_full} remain={full_row['accounting']['n_remain']}"
            )

    # ── 汇总表（终端速览：规模 × 档位的压缩 / 任务去向 / 墙钟）────────
    print("\n[exp02] === 汇总：N × 档位（sel=进入二阶段, done=交付, "
          "remain=未完成, T=端到端秒；均为描述性观测） ===")
    header = (f"{'N':>5} {'档位':<14} {'sel':>4} {'done':>5} "
              f"{'remain':>7} {'T/s':>9}")
    print(header)
    for row in results:
        a = row["accounting"]
        print(f"{row['n_tasks']:>5} {row['variant']:<14} {row['n_selected']:>4} "
              f"{a['n_done']:>5} {a['n_remain']:>7} "
              f"{row['t_total_ms'] / 1000.0:>9.1f}")

    # ── 产物 ─────────────────────────────────────────────────────
    acc_summary = summarize(results)
    if not acc_summary["identity_ok_all"]:
        # 门禁不是装饰：分账不闭合就禁止交付数值产物。
        raise RuntimeError(f"[exp02] 任务去向分账未闭合：{acc_summary}")
    metrics = {
        "status": "ok",
        "experiment": cfg["experiment"]["name"],
        "scope": {
            "kind": "single_cycle_scalability_observation",
            "answers": [
                "任务规模 N 与进入二阶段的任务数是否解耦",
                "分段墙钟与现算边数随 N 的变化",
                "同口径参照臂（不做选择）的单周期耗时量级",
            ],
            "does_not_answer": [
                "方法/档位优劣与质量高低",
                "完成约束是否满足（本实验是单周期快照，无后续周期）",
                "标定口径选型、滚动到达、跨实验比较、显著性",
            ],
            "wall_clock": (
                "单进程单 seed 单次 perf_counter 观测；跨格共享 DEM 段长"
                "记忆化缓存，先后执行存在偏倚。仅作量级描述，不作机制归因。"
            ),
            "f1_reward_descriptive": (
                "F1 是事后指标（门禁口径下不进搜索标量 f），仅登记原始值，"
                "不得用作质量或优劣判据。"
            ),
        },
        "K": n_uavs,
        "uav_max_range_m": max_range,
        "accounting_summary": acc_summary,
        "effective_config_sha256": effective_sha256,
        "scales": results,
    }
    run_meta = {
        "experiment": cfg["experiment"]["name"],
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str(cfg_path.relative_to(ROOT)),
        "seed": int(cfg["experiment"].get("seed", 42)),
        "scenario": data_cfg.get("scenario"),
        "scales": list(cfg.get("scales") or []),
        "variants": [str(v.get("name")) for v in variants],
        "full_baseline_scales": sorted(baseline_scales),
        "solver": dict(cfg.get("solver") or {}),
        "effective_config": "effective_config.json",
        "effective_config_sha256": effective_sha256,
        "n_rows": len(results),
        "wall_clock_s": round(time.perf_counter() - t_start, 3),
        "accounting_summary": acc_summary,
        "scope_kind": "single_cycle_scalability_observation",
    }
    # 三份产物同一时点原子落盘：任何一份都不会是半截 json，也不会出现
    # 「配置快照已写、数值产物未写」的孤儿档。
    _write_json_atomic(out_dir / "effective_config.json", effective)
    _write_json_atomic(out_dir / "metrics.json", metrics)
    _write_json_atomic(out_dir / "run_meta.json", run_meta)
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
            from plot.registry import FIGURES  # 图标题门禁

            # 门禁：任何一张图的标题/来源说明出现优劣类结论词
            # 即视为职责越界，直接失败，不静默出图。
            for fig in FIGURES:
                assert_no_quality_claims(fig.title, where=f"figure:{fig.id}")
                assert_no_quality_claims(fig.source, where=f"figure:{fig.id}")

            make_plots(metrics, {}, figures_dir, formats=formats, dpi=dpi)
        except Exception as exc:  # 出图失败不阻断数值产物
            print(f"[exp02][warn] 出图失败（json 已落盘）：{exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
