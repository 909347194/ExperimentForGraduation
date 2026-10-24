# -*- coding: utf-8 -*-
"""exp01_smoke 产物组装：指标汇总、运行元信息、分配结果与终端报表。

只做「内存对象 → 可机读 dict」与打印，不跑算法、不解析 yaml。
三个产物的分工（见 ``experiments/README.md``）：

    results/metrics.json    数值指标：summary + 各周期诊断（不含分配结构）
    results/run_meta.json   运行元信息：seed、时间戳、配置路径、DEM 网格
    results/solution.json   分配结构：任务表 + 机巢表 + 各周期航次与收敛轨迹
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

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
    # ── 代价口径拆分（2026-10-16 审查 I3）──────────────────────────
    # f2_cost = 路径(米) + w_penalty·惩罚(秒/单位)：量纲不同不能混加后当
    # 「总路径代价」报。按 w_penalty 反解拆开：total_cost 只留米制路径
    # （与 tables/plot 的「总三维路径代价」标签一致），惩罚单列 total_penalty，
    # 原始 f2 合计留 total_f2 供追溯。注意：优化口径是 λ·path + w_violation·
    # penalty（解耦），报告口径与之独立、但互相不再冒充。
    w_penalty = float(getattr(f.bundle.problem_model, "w_penalty", 1.0) or 0.0)
    total_f2 = sum(float(r.muas.get("f2_cost", 0.0) or 0.0) for r in records)
    total_penalty = sum(float(r.muas.get("penalty", 0.0) or 0.0) for r in records)
    # ── 纯路径：直读而非反解（2026-10-24 修复 P1-3）────────────────
    # final_path_m = problem.path_cost(final)，每周期落盘（muas_stage 诊断），
    # 不含惩罚。旧口径从 f2 反解（f2 − w_penalty·penalty），两者实测逐位
    # 相等（三 seed 差 0.000000），但反解依赖「终态 evaluate 以 penalty=None
    # 调用」这一未写进代码的隐含前提——gate / polish / coverage_first 任一
    # 路径传入显式 penalty，反解就静默偏，而报告仍标「总三维路径代价」。
    # 现改为直读；反解值保留作交叉校验（残差落盘，异常即可见）。
    total_path_direct = sum(
        float(r.muas.get("final_path_m", 0.0) or 0.0) for r in records
    )
    total_cost_inferred = total_f2 - w_penalty * total_penalty
    has_direct = any(r.muas.get("final_path_m") is not None for r in records)
    total_cost = total_path_direct if has_direct else total_cost_inferred
    path_split_residual = (
        abs(total_path_direct - total_cost_inferred) if has_direct else 0.0
    )
    total_reward = sum(float(r.muas.get("f1_reward", 0.0) or 0.0) for r in records)
    solve_ms = sum(float(r.muas.get("solve_seconds", 0.0) or 0.0) for r in records) * 1000.0
    avail_counts = [r.selection.get("n_uav_avail", 0) for r in records]
    # 实际跑的周期数：收敛早停后可能 < 配置的 n_cycles 安全上限；吞吐/报告
    # 一律以真实周期数为准（跑满时 len(records) 与配置一致，旧口径不变）。
    n_cycles = len(records) or int(f.bundle.horizon.n_cycles)
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
    # ── 有效完成口径（2026-10-16 审查 I4）──────────────────────────
    # 「迟到≠废飞」下迟到交付计完成，但迟到 ≥ W 的交付有效奖励折减为 0。
    # 完成率若不并列这个口径就会虚高（本跑 12 个迟到交付中 7 个实得 0）。
    # 有效奖励 eff = R·max(0, 1−L/W)，迟到秒数取闭环实际交付迟到
    # （record.lateness_by_task，非规划端预估），与 total_reward 口径互验。
    decay_w = getattr(f.bundle.problem_model, "late_reward_decay_seconds", None)
    lateness_actual: dict[int, float] = {}
    for r in records:
        for k, v in (r.lateness_by_task or {}).items():
            lateness_actual[int(k)] = float(v)
    reward_possible = sum(max(float(t.reward), 0.0) for t in tasks)
    reward_effective = 0.0
    n_zero_reward_delivery = 0
    for t in completed:
        r_full = max(float(t.reward), 0.0)
        late = lateness_actual.get(int(t.id), 0.0)
        eff = r_full
        if decay_w is not None and float(decay_w) > 0 and late > 0:
            eff = r_full * max(0.0, 1.0 - late / float(decay_w))
        reward_effective += eff
        if r_full > 0 and eff <= 0.0:
            n_zero_reward_delivery += 1
    # 静态物理不可达（「过于偏远」）：所有机巢往返航程超过满电可及范围，
    # 入池判定一次性出池。用户原则（2026-10-15）下唯一合法的未完成原因，
    # 与超期淘汰分列——后者对物理可行任务是兜底失灵的信号，应趋 0。
    n_unreachable = sum(len(r.unreachable_ids) for r in records)
    # 只统计真正求解过的周期（2026-10-16 审查 I2：空转周期 muas 无解，
    # 旧口径把它们当 feasible=False 摊薄，1/4 被记成 1/8）
    feas_flags = [
        bool(r.muas.get("feasible"))
        for r in records
        if r.muas.get("feasible") is not None
    ]
    cycle_feasible_rate = (sum(feas_flags) / len(feas_flags)) if feas_flags else 0.0
    # 活跃周期（有出动）计数：吞吐的另一个分母（I6：全周期分母含空转兜底周期，
    # 跨配置对比会被收敛节奏差异污染）
    n_active_cycles = sum(1 for r in records if len(r.executed_ids) > 0)
    # ── 覆盖门禁分账（2026-10-22，合法解 = 物理硬约束 + 覆盖门禁）────
    # voluntary_skip = S 内能装却没装（gate 修复下应恒 0）；
    # capacity_shortfall = 一阶检验能力不足（允许不交付）；
    # gate_rescued = 门禁修复救回数。逐周期 gate_stats 累加，
    # 缺字段 = 未插桩/未开启（schema 容缺约定）按 0 计。
    def _gate_sum(key: str) -> int:
        return sum(
            int(((r.muas or {}).get("gate_stats") or {}).get(key, 0) or 0)
            for r in records
        )
    n_voluntary_skip = _gate_sum("n_skip")
    n_capacity_shortfall = _gate_sum("n_shortfall")
    n_gate_rescued = _gate_sum("n_rescued")
    # ── 交付后成本 polish 汇总（2026-10-24，组内 2-opt/Or-opt）─────────
    # gain = 各周期采纳的交付航程下降量合计（米）；移动数/拒绝态同理。
    # 未开启/旧数据缺字段按 0 计（schema 容缺约定）。
    def _cp(key: str) -> float:
        return sum(
            float((r.muas or {}).get(f"cost_polish_{key}", 0.0) or 0.0)
            for r in records
        )
    cp_gain_m = _cp("path_gain_m")
    cp_n_2opt = int(_cp("n_2opt"))
    cp_n_oropt = int(_cp("n_oropt"))
    # 跨机改派（2026-10-24，归属维度）：采纳数 + 被航程门/迟到门拦死的
    # 几何更优改派数。blocked_*>0 且 n_relocate=0 ⇒ 有余量被约束堵住。
    cp_n_relocate = int(_cp("n_relocate"))
    cp_blocked_range = int(_cp("relocate_blocked_range"))
    cp_blocked_late = int(_cp("relocate_blocked_late"))
    cp_rejected = sum(
        int((r.muas or {}).get("cost_polish_rejected", 0) or 0) for r in records
    )
    # ── 论文主表口径 v2（2026-10-22 指标体系）────────────────────────
    # 「可完成的最终都应完成，只是或早或晚；在此前提下控成本与时效」。
    # η_final 分母排除物理不可达（问题固有、不归算法能力），
    # **保留超期淘汰**（可做却永久丢失=失败，淘汰率单列正因如此）。
    n_final_denom = n_arrived - n_unreachable
    completion_rate_final = (len(completed) / n_final_denom) if n_final_denom else 0.0
    # 按时率（最终交付口径）：迟到交付计完成、不计按时（与“做没做完”分开）
    on_time_rate_final = (
        (len(completed) - n_late) / len(completed) if completed else 0.0
    )
    path_per_completion_m = (total_cost / len(completed)) if completed else 0.0
    elimination_rate = (n_eliminated / n_final_denom) if n_final_denom else 0.0
    unreachable_rate = (n_unreachable / n_arrived) if n_arrived else 0.0

    # ── 过程指标 v2：名额利用率 / 积压 / 跨周期推迟 ────────────────────
    sel_caps = [int((r.selection or {}).get("capability_n_sel") or 0) for r in records]
    sel_used = [int((r.muas or {}).get("n_selected") or 0) for r in records]
    quota_cap_total = sum(sel_caps)
    quota_utilization = (sum(sel_used) / quota_cap_total) if quota_cap_total else 0.0
    n_quota_cycles = sum(1 for c in sel_caps if c > 0)
    quota_full_cycle_ratio = (
        sum(1 for u, c in zip(sel_used, sel_caps) if c > 0 and u >= c)
        / n_quota_cycles
    ) if n_quota_cycles else 0.0
    # 积压 = 每周期在池未终态（规划池 + 执行中）
    backlog_sizes = [
        int(((getattr(r, "pool_counts", None) or {}).get("n_total", 0)))
        + int(((getattr(r, "pool_counts", None) or {}).get("n_executing", 0)))
        for r in records
    ]
    avg_backlog = (sum(backlog_sizes) / len(backlog_sizes)) if backlog_sizes else 0.0
    max_backlog = max(backlog_sizes) if backlog_sizes else 0
    # 跨周期推迟（“或早或晚”的直接证据）：交付周期 > 到达周期。
    # 到达周期取自 batch.arrivals 的周期映射，交付周期 = executed_ids 所在周期。
    arrive_cycle_of = {
        t.id: cyc
        for cyc, ts in (getattr(f.batch, "arrivals", None) or {}).items()
        for t in ts
    }
    deliver_cycle_of = {tid: r.cycle for r in records for tid in r.executed_ids}
    # 分子限定为**已完成**任务（2026-10-24 修复 P1-1）：executed_ids ⊋ completed
    # ——废飞回池的任务也在 executed 里（审计 C 项判据即断言
    # infeasible ⊆ executed），全量统计会把废飞算成「跨周期推迟」，而分母是
    # completed ⇒ 废飞非 0 时比率虚高、甚至 >1。本跑废飞恒 0 故未暴露。
    completed_ids = {int(t.id) for t in completed}
    n_deferred_delivery = sum(
        1 for tid, dc in deliver_cycle_of.items()
        if tid in completed_ids and arrive_cycle_of.get(tid, dc) < dc
    )
    deferred_delivery_ratio = (
        n_deferred_delivery / len(completed) if completed else 0.0
    )
    # ── 方法诊断 v2：硬违约累计 + S 内覆盖率 ─────────────────────────
    n_hard_violations_total = sum(
        int((r.muas or {}).get("final_n_hard") or 0) for r in records
    )
    # S 内覆盖率（逐周期均值）：交付∩S / 可装的 S；分母 = |S| − 一阶 shortfall。
    # gate 开启时按构造恒 1.0（验证指标，非观测指标）。
    s_cov_rates: list[float] = []
    for r in records:
        sel_ids = set((r.selection or {}).get("selected_ids") or [])
        if not sel_ids:
            continue
        short = int(((r.muas or {}).get("gate_stats") or {}).get("n_shortfall", 0) or 0)
        denom = len(sel_ids) - short
        if denom <= 0:
            continue
        s_cov_rates.append(len(set(r.executed_ids) & sel_ids) / denom)
    s_coverage_rate = (sum(s_cov_rates) / len(s_cov_rates)) if s_cov_rates else 0.0

    # ── 长程吞吐协议指标（2026-10-14 用户定口径，2026-10-15 修订）──────
    # 完成率不是目标；真实目标 = 持续到达下「尽可能多且正确地完成任务」。
    # 正确执行率 = 当周期按时出动 / 当周期出动（迟到交付与物理不可行交付
    # 都不算「正确」，但迟到仍计入完成与吞吐——迟到不是废飞）；
    # 吞吐 = 累计完成（含迟到）/ 周期数。
    # 废飞（n_infeasible_delivery）= 物理不可行的弹回交付：UAV 已飞航程、
    # 位置已更新，但任务解绑回池不计完成——总航程下降若靠它换来则是净
    # 亏损，必须与吞吐同权报告。迟到单独以 n_late / 按时率 / 平均迟到报告。
    # 每活跃周期正确率，按 cycle 键控（2026-10-16 审查 I1：空转周期不入均值；
    # 同时修复旧口径 zip 对齐错位——列表变短后行与率会配错周期）
    per_cycle_correct: dict[int, float] = {}
    for r in records:
        n_exec = len(r.executed_ids)
        if not n_exec:
            # 空转周期（池空/无出动）不入均值：旧口径记 0 再平均，
            # 4 个空周期把 0.846 摊成 0.423，与 on_time_rate 矛盾
            continue
        n_on_time = n_exec - len(r.late_ids) - len(r.infeasible_ids)
        per_cycle_correct[r.cycle] = max(n_on_time, 0) / n_exec
    correct_exec_rate = (
        sum(per_cycle_correct.values()) / len(per_cycle_correct)
        if per_cycle_correct else 0.0
    )
    n_executed_total = sum(len(r.executed_ids) for r in records)
    on_time_rate = (
        (n_executed_total - n_late - n_infeasible_delivery) / n_executed_total
        if n_executed_total else 0.0
    )
    avg_lateness = (sum(all_lateness) / len(all_lateness)) if all_lateness else 0.0
    max_lateness = max(all_lateness) if all_lateness else 0.0
    throughput = (len(completed) / n_cycles) if n_cycles else 0.0
    cycles_payload = [r.as_dict() for r in records]
    for row in cycles_payload:
        rate = per_cycle_correct.get(row.get("cycle"))
        # 空转周期显式置 None（JSON null），不再伪装成 0.0 的「错误率」
        row["correct_exec_rate"] = None if rate is None else round(rate, 6)

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
            # 罚权自描述（2026-10-24）：口径类数值一律随数据落盘，图/表直读
            # 而不再回源 config.yaml——否则 --set 改了这些键，算法按新值跑、
            # 图仍按 yaml 原文标注（图会说谎，与 10-23 修的 lexicographic
            # 透传是同一类分裂，当时只修了算法侧）。
            "lambda_cost": float(
                getattr(f.bundle.problem_model, "lambda_cost", 0.0) or 0.0
            ),
            "w_violation": getattr(f.bundle.problem_model, "w_violation", None),
            "w_penalty": float(
                getattr(f.bundle.problem_model, "w_penalty", 1.0) or 0.0
            ),
            "w_time": float(getattr(f.bundle.problem_model, "w_time", 0.0) or 0.0),
            # 覆盖门禁口径自描述（2026-10-22）：审计/跨配置对比直读
            "lexicographic_coverage": bool(
                getattr(f.bundle.problem_model, "lexicographic_coverage", False)
            ),
            "coverage_gate": bool(
                getattr(getattr(f.bundle, "stage_model", None), "coverage_gate", False)
            ),
            # 交付后成本 polish 开关自描述（2026-10-24）：消融对比直读，
            # 不回源 yaml（同 lambda_cost 的自描述理由）
            "cost_polish_enabled": bool(
                getattr(
                    getattr(f.bundle, "stage_model", None),
                    "cost_polish_enabled",
                    False,
                )
            ),
            # 论文主表必报列序（2026-10-22 指标体系 v2）：随数据自描述，
            # 表格生成按此序取数，不靠文档记忆
            "paper_main_table": [
                "completion_rate_final",
                "on_time_rate_final",
                "path_per_completion_m",
                "total_path_m",
                "elimination_rate",
                "unreachable_rate",
            ],
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
            "n_active_cycles": n_active_cycles,
            "throughput_per_active_cycle": round(
                (len(completed) / n_active_cycles) if n_active_cycles else 0.0, 6
            ),
            # 有效完成口径（I4）：零奖励交付 / 有效完成率 / 奖励产出率
            "n_zero_reward_delivery": n_zero_reward_delivery,
            "effective_completion_rate": round(
                ((len(completed) - n_zero_reward_delivery) / n_arrived)
                if n_arrived else 0.0,
                6,
            ),
            "reward_yield": round(
                (reward_effective / reward_possible) if reward_possible else 0.0, 6
            ),
            "avg_wait": (sum(waits) / len(waits)) if waits else 0.0,
            "total_reward": round(total_reward, 4),
            # total_cost = 纯三维路径代价（米，I3 拆分后）；惩罚单列 total_penalty
            "total_cost": round(total_cost, 3),
            "total_penalty": round(total_penalty, 3),
            "total_f2": round(total_f2, 3),
            # 直读 vs 反解的交叉校验残差（米）：非 0 说明 f2 里的惩罚与
            # record.penalty 不同源（终态 evaluate 被传入显式 penalty）。
            "path_split_residual_m": round(path_split_residual, 6),
            "solve_time_ms": round(solve_ms, 2),
            "K_mean": (sum(avail_counts) / len(avail_counts)) if avail_counts else 0.0,
            "n_deferred_last": records[-1].muas.get("n_deferred", 0) if records else 0,
            # 覆盖门禁三分账（2026-10-22）：skip 应恒 0（否则门禁未生效/有 bug）
            "n_voluntary_skip": n_voluntary_skip,
            "n_capacity_shortfall": n_capacity_shortfall,
            "n_gate_rescued": n_gate_rescued,
            # 交付后成本 polish 汇总（2026-10-24）：总航程下降量（米）与
            # 移动数；rejected>0 表示有周期候选未达采纳判据（执行集减少/
            # 硬违约增/航程未严格降），交付解保持原样。
            "cost_polish_gain_m": round(cp_gain_m, 3),
            "cost_polish_n_2opt": cp_n_2opt,
            "cost_polish_n_oropt": cp_n_oropt,
            # 跨机改派（归属维度，2026-10-24）：采纳数与被门拦死的几何更优
            # 改派数。blocked_*>0 且 n_relocate=0 ⇒ 有余量、被约束堵住。
            "cost_polish_n_relocate": cp_n_relocate,
            "cost_polish_relocate_blocked_range": cp_blocked_range,
            "cost_polish_relocate_blocked_late": cp_blocked_late,
            "cost_polish_rejected": cp_rejected,
            # ── 论文主表 v2（η_final 口径：分母排除不可达、保留淘汰）──
            "completion_rate_final": round(completion_rate_final, 6),
            "on_time_rate_final": round(on_time_rate_final, 6),
            "path_per_completion_m": round(path_per_completion_m, 3),
            "total_path_m": round(total_cost, 3),
            "elimination_rate": round(elimination_rate, 6),
            "unreachable_rate": round(unreachable_rate, 6),
            # ── 过程指标 v2 ──
            "quota_utilization": round(quota_utilization, 6),
            "quota_full_cycle_ratio": round(quota_full_cycle_ratio, 6),
            "avg_backlog": round(avg_backlog, 3),
            "max_backlog": int(max_backlog),
            "n_deferred_delivery": n_deferred_delivery,
            "deferred_delivery_ratio": round(deferred_delivery_ratio, 6),
            # ── 方法诊断 v2 ──
            "n_hard_violations_total": n_hard_violations_total,
            "s_coverage_rate": round(s_coverage_rate, 6),
            "n_remaining_pool": len(f.pool),
            "wall_seconds": round(f.wall_seconds, 3),
        },
        "fleet": f.fleet.snapshot_json(),
        "pool": f.pool.snapshot_json(),
    }


def config_fingerprint(raw_cfg: Mapping[str, Any]) -> str:
    """当前**生效**配置（含 ``--set`` 覆盖）的稳定指纹。

    与 exp05_baselines 同口径（2026-10-23 移植）：exp01 反复用作「口径
    标定台」，换 λ / 代数 / 门禁开关后跑进同一个 ``results/`` 目录会覆盖
    旧口径产物，事后无法区分某份 metrics.json 是哪套配置跑出来的
    （旧数据已因口径不可比被整体清空过一次）。落盘到 run_meta 后可按
    指纹回溯每份产物的生效配置。

    seed 类字段**不进**指纹（2026-10-24 修复 P2-2）：随机种子是重复维度
    而非口径维度，同口径换 seed 应得同一指纹，否则无法按指纹把「同口径」
    产物归类（exp05 每 (arm,seed) 单独落盘故无此问题，exp01 只在代表
    seed 上落一个 run_meta，指纹含 seed 就有歧义）。
    """
    cfg = copy.deepcopy(dict(raw_cfg))
    for section, key in (
        ("experiment", "seed"), ("experiment", "seeds"), ("solver", "seed"),
    ):
        node = cfg.get(section)
        if isinstance(node, dict):
            node.pop(key, None)
    blob = json.dumps(cfg, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


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
        # 生效配置指纹（含 --set）：产物与配置的一一对应凭据
        "config_fingerprint": config_fingerprint(f.bundle.raw),
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
    "n_active_cycles",
    "throughput_per_active_cycle",
    "n_zero_reward_delivery",
    "effective_completion_rate",
    "reward_yield",
    "total_penalty",
    "total_f2",
    "avg_wait",
    "total_reward",
    "total_cost",
    "solve_time_ms",
    "K_mean",
    "n_deferred_last",
    "n_voluntary_skip",
    "n_capacity_shortfall",
    "n_gate_rescued",
    "cost_polish_gain_m",
    "cost_polish_n_2opt",
    "cost_polish_n_oropt",
    "cost_polish_n_relocate",
    "cost_polish_relocate_blocked_range",
    "cost_polish_relocate_blocked_late",
    "cost_polish_rejected",
    "completion_rate_final",
    "on_time_rate_final",
    "path_per_completion_m",
    "total_path_m",
    "elimination_rate",
    "unreachable_rate",
    "quota_utilization",
    "quota_full_cycle_ratio",
    "avg_backlog",
    "max_backlog",
    "n_deferred_delivery",
    "deferred_delivery_ratio",
    "n_hard_violations_total",
    "s_coverage_rate",
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
                # 适应度权重快照（本周期实际使用的 m/W_hard/w_late/λ 等）：
                # 与 cost_history 同盘落定，使交付解适应度可用同一组
                # 权重独立重算（治 auto big_m 漂移；审计 N 项校验）。
                "fitness_weights": dict(
                    (r.muas or {}).get("fitness_weights") or {}
                ),
                # 覆盖门禁分账（2026-10-22）：W_skip 层的 n_skip 成分与
                # 一阶检验归因，供审计 N2 重算与三分账表。
                "gate_stats": dict((r.muas or {}).get("gate_stats") or {}),
                # 交付解自身适应度与成分（与搜索期 best_fitness 口径分离：
                # Lamarckian 只回写起始基因通道，回写后基因解码重建的解与
                # 搜索期被评价解可不同）。缺省空值（旧数据/空转周期）。
                "final_fitness": (r.muas or {}).get("final_fitness"),
                "final_f1": (r.muas or {}).get("final_f1"),
                "final_path_m": (r.muas or {}).get("final_path_m"),
                "final_late_s": (r.muas or {}).get("final_late_s"),
                "final_bottleneck_s": (r.muas or {}).get("final_bottleneck_s"),
                "final_n_hard": (r.muas or {}).get("final_n_hard"),
                "final_excess_m": (r.muas or {}).get("final_excess_m"),
                # 解码后局部搜索 polish 诊断（2026-10-19）：
                # before=DMDE 交付解适应度，after=打磨后，gain=严格改进量。
                # 缺省空值（旧数据/空转周期）。
                "polish_enabled": (r.muas or {}).get("polish_enabled"),
                "polish_rounds": (r.muas or {}).get("polish_rounds"),
                "polish_candidates": (r.muas or {}).get("polish_candidates"),
                "polish_evals": (r.muas or {}).get("polish_evals"),
                "polish_accepted": (r.muas or {}).get("polish_accepted"),
                "polish_fitness_before": (r.muas or {}).get("polish_fitness_before"),
                "polish_fitness_after": (r.muas or {}).get("polish_fitness_after"),
                "polish_gain": (r.muas or {}).get("polish_gain"),
                "polish_converged": (r.muas or {}).get("polish_converged"),
                "polish_budget_hit": (r.muas or {}).get("polish_budget_hit"),
                # memetic M1 精英档案诊断（2026-10-19）：搜索期内每 K 代
                # 对精英 polish，档案轨迹与 cost_history 逐代对齐，供
                # 「搜索期内路由在降」出图。缺省空值（旧数据/空转周期）。
                "memetic_enabled": (r.muas or {}).get("memetic_enabled"),
                "memetic_K": (r.muas or {}).get("memetic_K"),
                "memetic_n_polish": (r.muas or {}).get("memetic_n_polish"),
                "memetic_n_accepted": (r.muas or {}).get("memetic_n_accepted"),
                "memetic_n_failed": (r.muas or {}).get("memetic_n_failed"),
                "memetic_first_fitness": (r.muas or {}).get("memetic_first_fitness"),
                "memetic_final_fitness": (r.muas or {}).get("memetic_final_fitness"),
                "memetic_gain": (r.muas or {}).get("memetic_gain"),
                "memetic_last_improve_gen": (r.muas or {}).get("memetic_last_improve_gen"),
                "memetic_trajectory": (r.muas or {}).get("memetic_trajectory"),
                # 交付后成本 polish 诊断（2026-10-24，组内 2-opt/Or-opt）：
                # path_gain_m = 本周期交付航程的绝对下降量（米；0 = 无改进/
                # 未开启/被拒）。缺省空值（旧数据/空转周期）。
                "cost_polish_enabled": (r.muas or {}).get("cost_polish_enabled"),
                "cost_polish_rounds": (r.muas or {}).get("cost_polish_rounds"),
                "cost_polish_n_2opt": (r.muas or {}).get("cost_polish_n_2opt"),
                "cost_polish_n_oropt": (r.muas or {}).get("cost_polish_n_oropt"),
                # 跨机改派（归属维度，2026-10-24）：n_relocate = 本周期采纳的
                # 跨机单任务迁移数；blocked_* = 几何更优但被航程门/迟到门
                # 拦死的改派数（均 >0 而 n_relocate=0 ⇒ 余量被约束堵住）。
                "cost_polish_n_relocate": (r.muas or {}).get(
                    "cost_polish_n_relocate"
                ),
                "cost_polish_relocate_blocked_range": (r.muas or {}).get(
                    "cost_polish_relocate_blocked_range"
                ),
                "cost_polish_relocate_blocked_late": (r.muas or {}).get(
                    "cost_polish_relocate_blocked_late"
                ),
                "cost_polish_converged": (r.muas or {}).get("cost_polish_converged"),
                "cost_polish_rejected": (r.muas or {}).get("cost_polish_rejected"),
                "cost_polish_path_before_m": (r.muas or {}).get(
                    "cost_polish_path_before_m"
                ),
                "cost_polish_path_after_m": (r.muas or {}).get(
                    "cost_polish_path_after_m"
                ),
                "cost_polish_path_gain_m": (r.muas or {}).get(
                    "cost_polish_path_gain_m"
                ),
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
            f"K={r.selection.get('n_uav_avail', 0)} | "
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
            f"零奖励交付 {sm.get('n_zero_reward_delivery', 0)}"
            f"（有效完成率 {sm.get('effective_completion_rate', 0.0):.1%}，"
            f"奖励产出率 {sm.get('reward_yield', 0.0):.1%}），"
            f"废飞(物理不可行) {sm['n_infeasible_delivery']}，"
            f"不可达(过于偏远) {sm.get('n_unreachable', 0)}，"
            f"淘汰 {sm['n_eliminated']}"
        )
    print(f"[exp01] wrote {Path(out_dir) / 'metrics.json'}")
