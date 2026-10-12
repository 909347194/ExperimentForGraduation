# -*- coding: utf-8 -*-
"""方案 B 两个落实项的常驻契约测试（pro 分支）。

锁定的契约（P2-3 / P3-3，2026-10-12 落实）：

**P2-3 编码终点按 UAV 粒度回退**（不再是「一损俱损」整批回退）：
  A. ``solve_end_nest_assignment(pre_assigned=...)``：预锁定终点直接占泊位
     且该 UAV 不进 MCF；预锁定 UAV 仍从原归属巢让出名额；
  B. ``MUASAssignmentEvaluator._choose_end_nests``：逐架校验编码终点
     （下标 / 禁用 / 可用 / 可达），容量竞争按 UAV 粒度——泊位满者
     **仅该架**解锁转内层 MCF，其余锁定不变；联合末态容量可行。

**P3-3 ``repair_terminal_genes`` 接入反映射**（不再是死代码）：
  C. 越界 / inf（不可达）终止基因改派可达最小代价巢，且 cost 与 target
     一致（历史 bug：``_safe_cost`` 把 inf 转 0.0 → 不可达巢被误选为零代价；
     越界改派后 cost 不更新 → cost 属于别的巢）；
  D. 空航次段（无起始基因）用该段 UAV 起点行判代价，不错认上一段；
  E. ``inverse_phi`` 端到端：n_nests>0 时所有终止基因下标合法且
     cost 与「末任务→该巢」真实代价一致。
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.muas.constraints.nest_capacity import (
    check_nest_capacity,
    solve_end_nest_assignment,
)
from task_allocation.methods.muas.representation.encoder import Gene
from task_allocation.methods.muas.representation.inverse_mapper import inverse_phi
from task_allocation.methods.muas.representation.repair_rules.terminal_nest import (
    TOUR_UAV_ID,
    repair_terminal_genes,
    split_segments,
)
from task_allocation.methods.muas.solvers.muas_stage import (
    MUASAssignmentEvaluator,
    MUASStageConfig,
)


def _uav(uid: int, nest_id: int | None) -> UAV:
    return UAV(id=uid, x=0.0, y=0.0, z=0.0, remaining_range=1e6, speed=10.0,
               nest_id=nest_id)


def _nest(nid: int, capacity: int, available: bool = True) -> Nest:
    n = Nest(id=nid, x=0.0, y=0.0, z=0.0, capacity=capacity)
    n.available = available
    return n


def _task(tid: int) -> Task:
    return Task(id=tid, x=100.0, y=100.0, z=0.0, reward=10.0,
                arrival_time=0.0, earliest=0.0, latest=1e5)


def _fake_problem(fleet: list[UAV], cost_table: dict[tuple[int, int], float],
                  *, reachability: bool = True) -> SimpleNamespace:
    """轻量伪造 SelectiveMUASProblem（仅 _choose_end_nests 用到的接口面）。"""

    def cost(a, b):
        return cost_table.get((a, b), float("inf"))

    return SimpleNamespace(
        uavs=fleet,
        config=SimpleNamespace(
            lambda_cost=1e-5,
            constraints=SimpleNamespace(end_nest_reachability=reachability),
        ),
        costs=SimpleNamespace(cost=cost, nest_key=lambda nid: f"nest:{nid}"),
    )


def _evaluator(fleet: list[UAV], nests: list[Nest], tasks: list[Task],
               cost_table: dict[tuple[int, int], float],
               **cfg) -> MUASAssignmentEvaluator:
    problem = _fake_problem(fleet, cost_table)
    return MUASAssignmentEvaluator(
        problem=problem,
        participating=fleet,
        tasks=tasks,
        nests=nests,
        fleet_uavs=fleet,
        config=MUASStageConfig(**cfg),
    )


# ══════════════════════════════════════════════════════════════════════
# A. MCF pre_assigned：预锁定占位 + 让出原巢
# ══════════════════════════════════════════════════════════════════════


def test_pre_assigned_occupies_slot_and_excluded_from_mcf():
    """预锁定 UAV 不进 MCF，但其占位计入容量账。

    巢 0 / 巢 1 均 cap=1；两架 UAV 候选相同且都强偏好巢 0。
    UAV0 预锁定巢 0 → UAV1 交 MCF：巢 0 泊位已被锁定占用，UAV1 必须去巢 1。
    改前（无预锁定占位）UAV1 会与 UAV0 同巢超卖。
    """
    nests = [_nest(0, 1), _nest(1, 1)]
    uavs = [_uav(0, nest_id=None), _uav(1, nest_id=None)]
    costs = {
        0: {0: 10.0, 1: 400.0},
        1: {0: 10.0, 1: 400.0},
    }
    got = solve_end_nest_assignment(
        uavs, nests, costs, planning_uav_ids=[0, 1], pre_assigned={0: 0},
    )
    assert got[0] == 0, "预锁定必须原样保留"
    assert got[1] == 1, "回退者不得与锁定者同巢超卖"
    rep = check_nest_capacity(uavs, nests, got)
    assert rep.violations == [] and rep.penalty == 0.0


def test_pre_assigned_releases_home_slot():
    """预锁定 UAV 仍从原归属巢让出名额（否则原巢被幽灵占用）。

    UAV0 原属巢 0（cap=1）。UAV0 预锁定巢 1 并起飞让出巢 0；
    UAV1 无归属、强偏好回巢 0（5 << 500）→ 必须可行。
    若预锁定不让出原巢，occ[巢0] 恒为 1，UAV1 被误挤到巢 1。
    """
    nests = [_nest(0, 1), _nest(1, 1)]
    uavs = [_uav(0, nest_id=0), _uav(1, nest_id=None)]
    costs = {1: {0: 5.0, 1: 500.0}}
    got = solve_end_nest_assignment(
        uavs, nests, costs, planning_uav_ids=[0, 1], pre_assigned={0: 1},
    )
    assert got == {0: 1, 1: 0}
    rep = check_nest_capacity(uavs, nests, got)
    assert rep.violations == [] and rep.penalty == 0.0


# ══════════════════════════════════════════════════════════════════════
# B. _choose_end_nests：编码终点按 UAV 粒度回退
# ══════════════════════════════════════════════════════════════════════


def test_capacity_contention_falls_back_per_uav():
    """两架同编码到 cap=1 的巢：一架锁定、仅另一架回退（非整批）。"""
    fleet = [_uav(0, nest_id=None), _uav(1, nest_id=None)]
    nests = [_nest(0, 1), _nest(1, 1)]
    tasks = [_task(0), _task(1)]
    # task0→巢0 近、task1→巢0 近；两架都想到巢 0
    table = {(0, "nest:0"): 10.0, (1, "nest:0"): 10.0,
             (0, "nest:1"): 900.0, (1, "nest:1"): 900.0}
    ev = _evaluator(fleet, nests, tasks, table)
    routes = {0: [0], 1: [1]}            # 真实 task_id
    coded = {0: 0, 1: 0}                 # 两架都编码到巢 0（局部下标）
    chosen = ev._choose_end_nests(routes, coded_ends=coded)
    # 按 UAV 顺序贪心：UAV0 锁定巢 0，UAV1 回退 MCF → 巢 1
    assert chosen[0] == 0
    assert chosen[1] == 1
    assign = {u.id: b for u, b in zip(fleet, [chosen[0], chosen[1]])}
    rep = check_nest_capacity(fleet, nests, assign)
    assert rep.violations == [] and rep.penalty == 0.0


def test_invalid_coded_end_falls_back_per_uav_only():
    """一架编码非法（越界）→ 仅该架回退，另一架锁定保留。"""
    fleet = [_uav(0, nest_id=None), _uav(1, nest_id=None)]
    nests = [_nest(0, 2), _nest(1, 2)]
    tasks = [_task(0), _task(1)]
    table = {(0, "nest:0"): 10.0, (1, "nest:0"): 20.0,
             (0, "nest:1"): 900.0, (1, "nest:1"): 800.0}
    ev = _evaluator(fleet, nests, tasks, table)
    routes = {0: [0], 1: [1]}
    coded = {0: 0, 1: 99}                # UAV1 编码越界
    chosen = ev._choose_end_nests(routes, coded_ends=coded)
    assert chosen[0] == 0, "合法编码终点必须锁定保留（不因同伴非法而整批回退）"
    # UAV1 回退 MCF：巢 0 还有 1 个泊位且更近 → 巢 0
    assert chosen[1] == 0
    assign = {0: chosen[0], 1: chosen[1]}
    rep = check_nest_capacity(fleet, nests, assign)
    assert rep.violations == [] and rep.penalty == 0.0


def test_unreachable_coded_end_falls_back_per_uav_only():
    """一架编码终点不可达（末任务→巢 inf）→ 仅该架回退。"""
    fleet = [_uav(0, nest_id=None), _uav(1, nest_id=None)]
    nests = [_nest(0, 2), _nest(1, 2)]
    tasks = [_task(0), _task(1)]
    # UAV1 的末任务(task1)→巢0 不可达（inf）；→巢1 可达
    table = {(0, "nest:0"): 10.0, (1, "nest:1"): 20.0,
             (0, "nest:1"): 900.0}
    ev = _evaluator(fleet, nests, tasks, table)
    routes = {0: [0], 1: [1]}
    coded = {0: 0, 1: 0}                 # UAV1 编码到不可达的巢 0
    chosen = ev._choose_end_nests(routes, coded_ends=coded)
    assert chosen[0] == 0
    assert chosen[1] == 1, "不可达编码终点必须按 UAV 粒度回退到可达巢"


def test_all_coded_ends_valid_no_mcf_needed():
    """全部编码终点合法且容量充足 → 全部锁定，无 pending 不进 MCF。"""
    fleet = [_uav(0, nest_id=None), _uav(1, nest_id=None)]
    nests = [_nest(0, 4), _nest(1, 4)]
    tasks = [_task(0), _task(1)]
    table = {(0, "nest:0"): 10.0, (1, "nest:0"): 20.0,
             (0, "nest:1"): 900.0, (1, "nest:1"): 800.0}
    ev = _evaluator(fleet, nests, tasks, table)
    routes = {0: [0], 1: [1]}
    coded = {0: 1, 1: 1}                 # 都想去巢 1（容量够）
    chosen = ev._choose_end_nests(routes, coded_ends=coded)
    assert chosen == {0: 1, 1: 1}


# ══════════════════════════════════════════════════════════════════════
# C. repair_terminal_genes：越界 / 不可达 / 重复 / 空段
# ══════════════════════════════════════════════════════════════════════

K, M, B = 2, 2, 3


def _cm(uav1_row: tuple[float, float, float],
        task0_row: tuple[float, float, float],
        task1_row: tuple[float, float, float]) -> np.ndarray:
    """(K+M, M+B) = (4, 5) 代价矩阵；只填机巢列用到的行。

    行约定：``0/1`` = UAV 起点行，``K+j`` = 任务 j 行；``M+b`` = 机巢 b 列。
    """
    cm = np.full((K + M, M + B), np.inf)
    cm[1, M:M + B] = uav1_row    # UAV1 起点行（空航次段前驱 = UAV 起点）
    cm[K + 0, M:M + B] = task0_row  # UAV1 航次 [0] 的末任务 0
    cm[K + 1, M:M + B] = task1_row  # UAV0 航次 [0,1] 的末任务 1
    return cm


def test_out_of_range_nest_reassigned_to_reachable():
    """越界下标（99 / -1）→ 改派可达最小代价巢，且 cost 与 target 一致。"""
    cm = _cm(
        uav1_row=(999.0, 999.0, 999.0),   # 空段用（本测试不涉及）
        task0_row=(100.0, 800.0, 300.0),  # UAV1 末任务 0：最近 nest0
        task1_row=(400.0, 200.0, 600.0),  # UAV0 末任务 1：最近 nest1
    )
    genes = [
        Gene(uav_id=0, target_id=0, cost=10.0),
        Gene(uav_id=TOUR_UAV_ID, target_id=1, cost=5.0),
        Gene(uav_id=-2, target_id=99, cost=400.0),   # 越界正
        Gene(uav_id=1, target_id=0, cost=20.0),
        Gene(uav_id=-2, target_id=-1, cost=100.0),   # 越界负
    ]
    out = repair_terminal_genes(genes, cm, K, M, B)
    terms = [g for g in out if g.uav_id == -2]
    assert len(terms) == 2
    # UAV0 段：末任务 1 → 行 K+1=3，最近可达巢 = nest1(200)
    assert terms[0].target_id == 1
    assert terms[0].cost == pytest.approx(cm[K + 1, M + 1]), "cost 必须与 target 一致"
    # UAV1 段：末任务 0 → 行 K+0=2，最近可达巢 = nest0(100)
    assert terms[1].target_id == 0
    assert terms[1].cost == pytest.approx(cm[K + 0, M + 0]), "cost 必须与 target 一致"


def test_inf_cost_terminal_reassigned():
    """下标合法但返航代价 inf（不可达）→ 改派可达巢。

    历史 bug：``_safe_cost`` 把 inf 转 0.0，argmin 反而选中不可达巢。
    """
    cm = _cm(
        uav1_row=(999.0, 999.0, 999.0),
        task0_row=(999.0, 999.0, 999.0),
        task1_row=(400.0, float("inf"), 600.0),  # nest1 不可达
    )
    genes = [
        Gene(uav_id=0, target_id=0, cost=10.0),
        Gene(uav_id=TOUR_UAV_ID, target_id=1, cost=5.0),
        Gene(uav_id=-2, target_id=1, cost=float("inf")),  # 合法下标但不可达
    ]
    out = repair_terminal_genes(genes, cm, K, M, B)
    terms = [g for g in out if g.uav_id == -2]
    assert len(terms) == 1
    assert terms[0].target_id == 0, "必须改派可达最小代价巢（nest0=400）"
    assert terms[0].cost == pytest.approx(400.0)
    assert np.isfinite(terms[0].cost)


def test_all_unreachable_keeps_original_for_downstream():
    """全部不可达时保留原值，交由 _choose_end_nests 的可达性判据处理。"""
    cm = _cm(
        uav1_row=(100.0, 800.0, 300.0),
        task0_row=(999.0, 999.0, 999.0),
        task1_row=(float("inf"),) * 3,   # 全部不可达
    )
    genes = [
        Gene(uav_id=0, target_id=0, cost=10.0),
        Gene(uav_id=TOUR_UAV_ID, target_id=1, cost=5.0),
        Gene(uav_id=-2, target_id=2, cost=float("inf")),
    ]
    out = repair_terminal_genes(genes, cm, K, M, B)
    terms = [g for g in out if g.uav_id == -2]
    assert terms[0].target_id == 2, "无可达候选时保留原值（不静默错选）"


def test_second_terminal_belongs_to_next_empty_segment():
    """当前段已有终止基因后再出现的终止基因归属下一空航次段（不丢弃）。

    normalize_srp_segments 保证每段至多一个终止基因，故「段内重复」在
    规范化输入中不存在；第二个终止基因必属下一空段（无起始基因）。
    repair 与 split_segments 必须用同一段归属逻辑，否则空段终点丢失
    或错挂。本测试锁定该一致性。
    """
    cm = _cm(
        uav1_row=(100.0, 800.0, 300.0),   # UAV1 起点行：最近 nest0
        task0_row=(999.0, 999.0, 999.0),  # UAV0 末任务 0（全有限）
        task1_row=(400.0, 200.0, 600.0),
    )
    genes = [
        Gene(uav_id=0, target_id=0, cost=10.0),
        Gene(uav_id=-2, target_id=0, cost=400.0),   # UAV0 段终止（合法）
        Gene(uav_id=-2, target_id=99, cost=7.0),    # UAV1 空段，越界
    ]
    out = repair_terminal_genes(genes, cm, K, M, B)
    terms = [g for g in out if g.uav_id == -2]
    assert len(terms) == 2, "第二个终止基因必须保留（归属空段），不得丢弃"
    # UAV0 段：末任务 0 → 行 K+0=2，target=0 合法且有限 → 原样保留
    assert terms[0].target_id == 0
    assert terms[0].cost == pytest.approx(cm[K + 0, M + 0])
    # UAV1 空段：行 = UAV1 起点行（行 1），越界 99 → 改派 nest0(100)
    assert terms[1].target_id == 0
    assert terms[1].cost == pytest.approx(cm[1, M + 0])
    routes, ends = split_segments(out)
    assert routes == {0: [0], 1: []}, f"空段应归属 UAV1：{routes}"
    assert ends[0] == 0 and ends[1] == 0


def test_empty_segment_terminal_uses_uav_row():
    """空航次段（无起始基因）→ 用该段 UAV 起点行判代价，不错认上一段。"""
    cm = _cm(
        uav1_row=(100.0, 800.0, 300.0),   # UAV1 起点行：最近 nest0
        task0_row=(999.0, 999.0, 999.0),
        task1_row=(400.0, 200.0, 600.0),
    )
    # UAV0 段有任务；随后一个孤立终止基因属于 UAV1 的空航次段
    # （normalize 后空段形态：无起始基因，只有终止基因）
    genes = [
        Gene(uav_id=0, target_id=0, cost=10.0),
        Gene(uav_id=TOUR_UAV_ID, target_id=1, cost=5.0),
        Gene(uav_id=-2, target_id=2, cost=600.0),
        Gene(uav_id=-2, target_id=99, cost=7.0),   # UAV1 空段，越界
    ]
    out = repair_terminal_genes(genes, cm, K, M, B)
    terms = [g for g in out if g.uav_id == -2]
    assert len(terms) == 2
    # 空段的越界终止基因应按 UAV1 起点行（行 1）改派 → nest0(100)
    assert terms[1].target_id == 0, "空航次段必须用该段 UAV 起点行"
    assert terms[1].cost == pytest.approx(100.0)
    routes, ends = split_segments(out)
    # 空段在 routes 中体现为空列表（键存在），终点不错挂到 UAV0
    assert routes == {0: [0, 1], 1: []}
    assert ends[0] == 2, "UAV0 的终点不得被空段基因覆盖"
    assert ends[1] == 0


# ══════════════════════════════════════════════════════════════════════
# E. 端到端：inverse_phi 后所有终止基因合法且 cost 一致
# ══════════════════════════════════════════════════════════════════════


def test_inverse_phi_terminal_genes_always_legal():
    """随机代价矩阵（含 inf 块）+ 随机 cost_vector：终止基因必须合法。

    锁 P3-3 接入效果：越界 / inf 在反映射末端即被修复，不会以非法形态
    进入评价（被当成合法返航边计入 F2）。逐项核对：

    1. 终止基因数 = K（每架 UAV 恰一个）；
    2. 下标合法（``0 <= target < B``）；
    3. cost 与「末任务 → 该巢」真实代价一致（改派后重算、未改派时匹配
       值即真实代价，故恒相等——历史上越界改派不更新 cost 曾违反本条）。
    """
    rng = np.random.default_rng(20261012)
    K, M, B = 3, 7, 4
    for trial in range(60):
        cm = rng.uniform(1.0, 500.0, size=(K + M, M + B))
        # 随机把一些「任务→巢」边设为不可达
        inf_mask = rng.random((K + M, B)) < 0.25
        cm[:, M:] = np.where(inf_mask, np.inf, cm[:, M:])
        # SRP 每段非空 → 末任务必为某任务；保证每个任务行至少一个可达巢，
        # 否则 repair 保留 inf 属预期行为（保留下游处理），不适用本断言
        for j in range(M):
            if not np.isfinite(cm[K + j, M:]).any():
                cm[K + j, M + int(rng.integers(B))] = float(rng.uniform(1.0, 500.0))
        cv = rng.uniform(1.0, 500.0, size=M + K)
        ind = inverse_phi(
            cv, cm, K, M, "srp",
            rng=np.random.default_rng(int(rng.integers(1 << 30))),
            temperature=float(rng.choice([0.0, 0.4, 0.9])),
            n_nests=B,
        )
        routes, ends = split_segments(ind.genes)
        terms = [g for g in ind.genes if g.uav_id == -2]
        assert len(terms) == K, f"trial={trial}: 每架 UAV 应恰有一个终止基因"
        assert sorted(routes) == list(range(K)), (
            f"trial={trial}: 每段应非空，实际 {sorted(routes)}"
        )
        # 段按序排列、每段终止基因在段末 → 第 i 个终止基因属于第 i 段
        for i, uid in enumerate(range(K)):
            g = terms[i]
            b = ends[uid]
            assert b is not None and 0 <= b < B, (
                f"trial={trial}: UAV{uid} 终止下标非法 b={b}"
            )
            assert g.target_id == b, (
                f"trial={trial}: UAV{uid} 终止基因 target={g.target_id} 与 "
                f"split_segments 解析 {b} 不一致"
            )
            row = K + routes[uid][-1]
            assert g.cost == pytest.approx(float(cm[row, M + b])), (
                f"trial={trial}: UAV{uid} 终止基因 cost={g.cost} 与 "
                f"「末任务→巢{b}」真实代价 {cm[row, M + b]} 不一致"
            )
