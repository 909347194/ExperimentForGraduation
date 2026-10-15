# -*- coding: utf-8 -*-
"""exp01_smoke 产物组装：指标汇总、运行元信息、分配结果与终端报表。

只做「内存对象 → 可机读 dict」与打印，不跑算法、不解析 yaml。
三个产物的分工（见 ``experiments/README.md``）：

    results/metrics.json    数值指标：summary + 各周期诊断（不含分配结构）
    results/run_meta.json   运行元信息：seed、时间戳、配置路径、DEM 网格
    results/solution.json   分配结构：任务表 + 机巢表 + 各周期航次与收敛轨迹
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

import paths  # noqa: F401  # sys.path 引导：挂载仓库根，供 task_allocation.* 导入

from task_allocation.common.types import Nest, Task, UAV

from build import TaskBatch
from config import ConfigBundle

__all__ = [
    "RunFacts",
    "build_metrics",
    "build_run_meta",
    "build_solution",
    "write_json",
    "print_cycle_report",
    "print_summary",
]


# ---------------------------------------------------------------------------
# 一次运行的全部事实
# ---------------------------------------------------------------------------


@dataclass
class RunFacts:
    """滚动求解结束后，组装产物所需的全部上下文。

    把散落的实体收在一处，避免 ``build_*`` 函数出现十几个位置参数。

    注意两套机队对象：``uavs`` 是求解后的机队（已被 ``UAVFleet`` 原位
    修改为终态）；``initial_uavs`` 是 t=0 快照。凡落盘「初始分布」
    （solution.json 的 uavs 表、geo_overview 图）一律用 ``initial_uavs``。
    """

    bundle: ConfigBundle
    scenario: Any
    nests: list[Nest]
    terrain: Any
    uavs: list[UAV]
    batch: TaskBatch
    records: list[Any]
    pool: Any
    fleet: Any
    wall_seconds: float
    initial_uavs: list[UAV] | None = None


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def write_json(path: Path, payload: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _nest_rows(nests: Sequence[Nest]) -> list[dict[str, Any]]:
    return [
        {
            "id": n.id,
            "x": round(n.x, 3),
            "y": round(n.y, 3),
            "z": round(n.z, 3),
            "capacity": n.capacity,
            "lat": n.meta.get("lat"),
            "lon": n.meta.get("lon"),
        }
        for n in nests
    ]


def _task_row(t: Task) -> dict[str, Any]:
    return {
        "id": int(t.id),
        "x": round(float(t.x), 3),
        "y": round(float(t.y), 3),
        "z": round(float(t.z), 3),
        "reward": round(float(t.reward), 4),
        "arrival_time": round(float(t.arrival_time), 3),
        "earliest": t.earliest,
        "latest": t.latest,
        "status": t.status.value,
        "completed_at": t.meta.get("completed_at"),
    }


# ---------------------------------------------------------------------------
# 产物
# ---------------------------------------------------------------------------


def build_metrics(f: RunFacts) -> dict[str, Any]:
    """汇总指标（不含分配结构与收敛轨迹，那些进 solution.json）。"""
    records = f.records
    finished = f.pool.finished_tasks()
    completed = [t for t in finished if t.meta.get("completed_at") is not None]
    waits = [float(t.meta["completed_at"]) - float(t.arrival_time) for t in completed]

    tasks = f.batch.all_tasks()
    n_arrived = len(tasks)
    total_cost = sum(float(r.muas.get("f2_cost", 0.0)) for r in records)
    total_reward = sum(float(r.muas.get("f1_reward", 0.0)) for r in records)
    solve_ms = sum(float(r.muas.get("solve_seconds", 0.0)) for r in records) * 1000.0
    k_avail = [r.selection.get("n_uav_avail", 0) for r in records]
    n_cycles = int(f.bundle.horizon.n_cycles)
    # 完成闸门：被判**物理不可行**、未真正交付而回池的任务（不计完成、
    # 不计奖励）。2026-10-15 协议「迟到不是废飞」后，废飞只剩这一类
    # （航程/续航/终点机巢/结构类违约）；迟到交付计入完成。
    n_infeasible_delivery = sum(len(r.infeasible_ids) for r in records)
    # 迟到交付：完成但到达晚于 latest 的任务数（计入完成与吞吐）
    n_late = sum(len(r.late_ids) for r in records)
    all_lateness = [
        float(v) for r in records for v in r.lateness_by_task.values()
    ]
    # 超期淘汰：deadline 已过仍在池中、被 expire 出池的任务
    n_eliminated = sum(len(r.expired_ids) for r in records)
    # 静态物理不可达（「过于偏远」）：所有机巢往返航程超过满电可及范围，
    # 入池判定一次性出池。用户原则（2026-10-15）下唯一合法的未完成原因，
    # 与超期淘汰分列——后者对物理可行任务是兜底失灵的信号，应趋 0。
    n_unreachable = sum(len(r.unreachable_ids) for r in records)
    feas_flags = [bool(r.muas.get("feasible", False)) for r in records]
    cycle_feasible_rate = (sum(feas_flags) / len(feas_flags)) if feas_flags else 0.0

    # ── 长程吞吐协议指标（2026-10-14 用户定口径，2026-10-15 修订）──────
    # 完成率不是目标；真实目标 = 持续到达下「尽可能多且正确地完成任务」。
    # 正确执行率 = 当周期按时出动 / 当周期出动（迟到交付与物理不可行交付
    # 都不算「正确」，但迟到仍计入完成与吞吐——迟到不是废飞）；
    # 吞吐 = 累计完成（含迟到）/ 周期数。
    # 废飞（n_infeasible_delivery）= 物理不可行的弹回交付：UAV 已飞航程、
    # 位置已更新，但任务解绑回池不计完成——总航程下降若靠它换来则是净
    # 亏损，必须与吞吐同权报告。迟到单独以 n_late / 按时率 / 平均迟到报告。
    per_cycle_correct = []
    for r in records:
        n_exec = len(r.executed_ids)
        n_on_time = n_exec - len(r.late_ids) - len(r.infeasible_ids)
        per_cycle_correct.append(max(n_on_time, 0) / n_exec if n_exec else 0.0)
    correct_exec_rate = (sum(per_cycle_correct) / len(per_cycle_correct)) \
        if per_cycle_correct else 0.0
    n_executed_total = sum(len(r.executed_ids) for r in records)
    on_time_rate = (
        (n_executed_total - n_late - n_infeasible_delivery) / n_executed_total
        if n_executed_total else 0.0
    )
    avg_lateness = (sum(all_lateness) / len(all_lateness)) if all_lateness else 0.0
    max_lateness = max(all_lateness) if all_lateness else 0.0
    throughput = (len(completed) / n_cycles) if n_cycles else 0.0
    cycles_payload = [r.as_dict() for r in records]
    for row, rate in zip(cycles_payload, per_cycle_correct):
        row["correct_exec_rate"] = round(rate, 6)

    return {
        "status": "ok",
        "experiment": f.bundle.experiment.get("name"),
        "n_cycles": n_cycles,
        "problem": {
            "n_tasks_initial": len(f.batch.initial),
            "n_arrived": n_arrived,
            "n_arrived_per_cycle": {
                str(k): v for k, v in f.batch.per_cycle_counts().items()
            },
            "n_uavs": len(f.uavs),
            "n_nests": len(f.nests),
            "nest_capacity": f.nests[0].capacity if f.nests else None,
            # —— 以下字段供结果表 tab1 直读：配置改动后表自动跟着变，不说谎 ——
            "uav_speed": float(f.bundle.problem.get("uav_speed", 0.0)),
            "uav_max_range_km": float(f.bundle.problem.get("uav_max_range", 0.0)) / 1000.0,
            "dmde_population": int(f.bundle.solver.pop_size),
            "dmde_max_iter": int(f.bundle.solver.max_generations),
            "scalarization": (
                "字典序（big-M，收益优先）"
                if f.bundle.problem_model.lexicographic else "加权和 −F1+λ·F2"
            ),
            "time_window": bool(f.bundle.problem_model.constraints.time_window),
            "dem_file": str(f.bundle.data.get("dem") or "未接入"),
            "dem_step_m": float(f.bundle.cost.get("dem_step_m", 0.0)),
        },
        "local_frame": {
            "lat0": f.scenario.frame.lat0,
            "lon0": f.scenario.frame.lon0,
        },
        "nests": _nest_rows(f.nests),
        "cycles": cycles_payload,
        "summary": {
            "n_arrived": n_arrived,
            "n_completed": len(completed),
            "completion_rate": (len(completed) / n_arrived) if n_arrived else 0.0,
            "cycle_feasible_rate": round(cycle_feasible_rate, 6),
            "n_infeasible_delivery": n_infeasible_delivery,
            "n_eliminated": n_eliminated,
            "n_unreachable": n_unreachable,
            "n_late": n_late,
            "on_time_rate": round(on_time_rate, 6),
            "avg_lateness_s": round(avg_lateness, 3),
            "max_lateness_s": round(max_lateness, 3),
            "correct_exec_rate": round(correct_exec_rate, 6),
            "throughput_per_cycle": round(throughput, 6),
            "avg_wait": (sum(waits) / len(waits)) if waits else 0.0,
            "total_reward": round(total_reward, 4),
            "total_cost": round(total_cost, 3),
            "solve_time_ms": round(solve_ms, 2),
            "K_avail_mean": (sum(k_avail) / len(k_avail)) if k_avail else 0.0,
            "n_deferred_last": records[-1].muas.get("n_deferred", 0) if records else 0,
            "n_remaining_pool": len(f.pool),
            "wall_seconds": round(f.wall_seconds, 3),
        },
        "fleet": f.fleet.snapshot_json(),
        "pool": f.pool.snapshot_json(),
    }


def build_run_meta(
    f: RunFacts, *, cfg_path: Path, root: Path = paths.ROOT
) -> dict[str, Any]:
    """运行元信息：复现实验所需的环境与配置快照。"""
    cost_cfg = f.bundle.cost
    terrain_meta = None
    if f.terrain is not None:
        terrain_meta = {
            "width": f.terrain.meta.width,
            "height": f.terrain.meta.height,
            "step_m": float(cost_cfg.get("dem_step_m", 200.0)),
            "bounds": [round(v, 1) for v in f.terrain.meta.bounds],
        }

    return {
        "experiment": f.bundle.experiment.get("name"),
        "seed": f.bundle.seed,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str(Path(cfg_path).relative_to(root)),
        "repo_root": str(root),
        "dem_terrain": terrain_meta,
    }


# 需要跨 seed 聚合的数值型指标键（取自各次运行的 summary）
_SEED_AGG_KEYS = (
    "n_completed",
    "completion_rate",
    "cycle_feasible_rate",
    "n_infeasible_delivery",
    "n_eliminated",
    "n_unreachable",
    "n_late",
    "on_time_rate",
    "avg_lateness_s",
    "correct_exec_rate",
    "throughput_per_cycle",
    "avg_wait",
    "total_reward",
    "total_cost",
    "solve_time_ms",
    "K_avail_mean",
    "n_deferred_last",
    "n_remaining_pool",
    "wall_seconds",
    "n_arrived",
)


def build_seed_summary(per_seed_summaries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """把多次随机种子运行的 summary 聚合成 mean±std。

    返回 ``{指标: {"mean": ..., "std": ...}}``；std 用总体标准差（ddof=0），
    单个样本时 std=0。
    """
    import numpy as np

    out: dict[str, Any] = {}
    for k in _SEED_AGG_KEYS:
        vals = [float(s.get(k, 0.0)) for s in per_seed_summaries if isinstance(s, dict)]
        if not vals:
            out[k] = {"mean": 0.0, "std": 0.0}
            continue
        arr = np.asarray(vals, dtype=float)
        out[k] = {
            "mean": round(float(arr.mean()), 6),
            "std": round(float(arr.std(ddof=0)), 6),
        }
    return out


def _uav_rows(uavs: Sequence[UAV]) -> list[dict[str, Any]]:
    """全量 UAV 初始分布（t=0），供地理示意图画「初始无人机分布」。

    注意：调用方必须传入**运行前**的快照（``RunFacts.initial_uavs``）。
    ``UAVFleet`` 在滚动求解中原位修改 UAV 对象的 x/y/z/nest_id/status，
    若直接把求解后的 ``RunFacts.uavs`` 传进来，得到的是终态位置——
    周期 1 的起点巢会被周期 1 的落点覆盖（历史 bug：曾据终态表误读为
    「巢 4 里驻留 UAV0」，实际周期 1 起点巢 4 驻留的是 UAV4）。
    """
    rows: list[dict[str, Any]] = []
    for u in uavs:
        pos = u.position
        rows.append(
            {
                "id": int(u.id),
                "x": float(pos[0]),
                "y": float(pos[1]),
                "z": float(pos[2]) if len(pos) > 2 else 0.0,
                "nest_id": int(u.nest_id) if u.nest_id is not None else None,
                "available": bool(getattr(u, "is_available", True)),
            }
        )
    return rows


def _dem_payload(terrain, max_dim: int = 128) -> dict[str, Any] | None:
    """把 DEM 高程降采样后进 solution.json，供 2D 地理示意图作底图。

    降采样用分块均值，最长边压到 ``max_dim`` 以内，既保证底图轮廓清晰，
    又把 JSON 体积控制在可接受范围（约 max_dim² 个浮点数）。
    """
    if terrain is None:
        return None
    elev = np.asarray(getattr(terrain, "_elevation", None), dtype=float)
    if elev is None or elev.ndim != 2 or elev.size == 0:
        return None
    h, w = elev.shape
    step = max(1, int(np.ceil(max(h, w) / max_dim)))
    h2, w2 = h // step, w // step
    if h2 < 1 or w2 < 1:
        data = elev
    else:
        trim = elev[: h2 * step, : w2 * step]
        data = trim.reshape(h2, step, w2, step).mean(axis=(1, 3))
    out = np.asarray(data, dtype=float)
    return {
        "bounds": [float(v) for v in terrain.meta.bounds],  # (left, bottom, right, top)
        "shape": [int(out.shape[0]), int(out.shape[1])],
        "elevation": out.tolist(),
    }


def build_solution(f: RunFacts) -> dict[str, Any]:
    """分配结构：任务表 + 机巢表 + 各周期航次与 DMDE 收敛轨迹 + 地理底图。"""
    return {
        "experiment": f.bundle.experiment.get("name"),
        "local_frame": {
            "lat0": f.scenario.frame.lat0,
            "lon0": f.scenario.frame.lon0,
        },
        "nests": _nest_rows(f.nests),
        "tasks": [_task_row(t) for t in f.batch.all_tasks()],
        "uavs": _uav_rows(f.initial_uavs if f.initial_uavs is not None else f.uavs),
        "dem": _dem_payload(f.terrain),
        "cycles": [
            {
                "cycle": r.cycle,
                "current_time": round(float(r.current_time), 3),
                "tours": list(r.tours),
                "cost_history": list(r.cost_history),
            }
            for r in f.records
        ],
    }


# ---------------------------------------------------------------------------
# 终端报表
# ---------------------------------------------------------------------------


def print_cycle_report(records: Sequence[Any]) -> None:
    for r in records:
        muas = r.muas
        print(
            f"[exp01] 周期 {r.cycle} @t={r.current_time:7.1f}s | "
            f"T_t={r.pool_counts.get('n_total', 0)}"
            f"(remain {r.pool_counts.get('n_remain', 0)}/"
            f"new {r.pool_counts.get('n_new', 0)}/"
            f"release {r.pool_counts.get('n_release', 0)}) "
            f"K_avail={r.selection.get('n_uav_avail', 0)} | "
            f"T_sel={muas.get('n_selected', 0)} → "
            f"T_exec={muas.get('n_executed', 0)}"
            f"(保留 {muas.get('n_deferred', 0)}) | "
            f"F1={muas.get('f1_reward', 0.0):.2f} "
            f"F2={muas.get('f2_cost', 0.0):.0f}m | "
            f"{muas.get('solve_seconds', 0.0):.2f}s"
        )


def print_summary(metrics: dict[str, Any], n_arrived: int, out_dir: Path) -> None:
    seeds = metrics.get("seeds")
    if seeds and len(seeds) > 1 and metrics.get("seed_summary"):
        # 多 seed 复现：报告均值±标准差
        ss = metrics["seed_summary"]
        _m = ss.get("n_completed", {})
        _r = ss.get("completion_rate", {})
        _c = ss.get("total_cost", {})
        _w = ss.get("avg_wait", {})
        _t = ss.get("solve_time_ms", {})
        print(
            f"[exp01] 多 seed 汇总（{len(seeds)} 次，seed={seeds}）："
            f"完成 {_m.get('mean',0):.2f}±{_m.get('std',0):.2f} / {n_arrived}"
            f"({_r.get('mean',0):.1%}±{_r.get('std',0):.1%})，"
            f"平均等待 {_w.get('mean',0):.0f}±{_w.get('std',0):.0f}s，"
            f"总代价 {_c.get('mean',0):.0f}±{_c.get('std',0):.0f}m，"
            f"求解 {_t.get('mean',0):.0f}±{_t.get('std',0):.0f}ms"
        )
    else:
        sm = metrics["summary"]
        print(
            f"[exp01] 汇总：完成 {sm['n_completed']}/{n_arrived}"
            f"({sm['completion_rate']:.1%})，平均等待 {sm['avg_wait']:.0f}s，"
            f"总代价 {sm['total_cost']:.0f}m，求解 {sm['solve_time_ms']:.0f}ms，"
            f"池内剩余 {sm['n_remaining_pool']}"
        )
        print(
            f"[exp01] 长程口径：吞吐 {sm['throughput_per_cycle']:.3f}/周期，"
            f"按时率 {sm.get('on_time_rate', 0.0):.1%}，"
            f"迟到交付 {sm.get('n_late', 0)}（平均 {sm.get('avg_lateness_s', 0.0):.0f}s），"
            f"废飞(物理不可行) {sm['n_infeasible_delivery']}，"
            f"不可达(过于偏远) {sm.get('n_unreachable', 0)}，"
            f"淘汰 {sm['n_eliminated']}"
        )
    print(f"[exp01] wrote {Path(out_dir) / 'metrics.json'}")
