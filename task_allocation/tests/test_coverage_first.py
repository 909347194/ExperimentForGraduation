# -*- coding: utf-8 -*-
"""覆盖优先两段式解码测试（2026-10-23 用户裁定）。

用户裁定（解码/repair）：**先**按覆盖目标装到约定口径下的最大可行集，
**再**在不减少已装集合（不增加 shortfall）的约束下做成本 polish（跨机
relocate / 调序）。门禁证明「能装却不装」可被抹成 0，但不能单独承担
完成语义——E1E2（2026-10-23）实证：门禁单独上场时覆盖指标恒真而完成率
−5pp（「可装载」内生于当前解，成本-only 适应度占满航程/窗口 ⇒ 剩余
任务一阶判 shortfall ⇒ 淘汰）。

契约：

1. **覆盖优先装载**（``_load_coverage_fixed_point``）：EDD 升序把可行
   未装任务装到约定口径（航程 + 自身时间窗 + 开班容量，一阶判据）下的
   最大可行集；不动点（再跑零动作）；与默认路径的差别可观测
   （默认按 id 序、单趟；覆盖优先按 EDD、不动点）。
2. **单调成本 polish**（``_polish_routes_monotone``）四道门：航程门 /
   自身时间窗门 / **迟到数门**（不制造新违约）/ 成本门（严格下降）；
   移动不卸任务 ⇒ 覆盖只增不减。
3. **post_polish**：单调 polish → 覆盖补装 → 门禁，覆盖不减少。
4. **polish_assignment 覆盖守护**：覆盖盲判据（coverage_first /
   coverage_gate / lexicographic_coverage）下拒绝「卸任务换成本」的
   候选；非门禁口径逐位不变（stub 评测器直接验证）。
5. **默认路径逐位不变**：coverage_first=False 时不走装载/ polish 新
   逻辑（由既有 295 项测试 + 本节 EDD 对照共同保证）。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import (  # noqa: E402
    AllocationSolution,
    Nest,
    Task,
    UAV,
    UAVStatus,
    UAVTour,
)
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.local_search import (  # noqa: E402
    polish_assignment,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    EvalResult,
    MUASAssignmentEvaluator,
    MUASStageConfig,
)

NEST_KEY = 1_000_000


# ── 装配工具（与 test_polish_local_search / test_coverage_gate 同构）───


def _task(tid: int, x: float, y: float, *, reward: float = 5.0,
          earliest: float = 0.0, latest: float = 1e6) -> Task:
    return Task(id=tid, x=x, y=y, z=0.0, reward=reward,
                earliest=earliest, latest=latest, arrival_time=0.0)


def _uav(uid: int, x: float = 0.0, y: float = 0.0,
         remaining_range: float = 1e7) -> UAV:
    return UAV(id=uid, x=x, y=y, z=0.0, remaining_range=remaining_range,
               speed=10.0, nest_id=0, status=UAVStatus.IDLE)


def _pairwise(tasks, uavs, nests):
    pts = {t.id: (t.x, t.y, t.z) for t in tasks}
    pts.update({-(1 + u.id): (u.x, u.y, u.z) for u in uavs})
    pts.update({NEST_KEY + n.id: (n.x, n.y, n.z) for n in nests})
    keys = list(pts)
    return {(a, b): math.dist(pts[a], pts[b])
            for i, a in enumerate(keys) for b in keys[i + 1:]}


def _mk(tasks, uavs, nests, *, stage_cfg=None, **prob_kw):
    prob = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(w_violation=0.01, w_time=0.0, **prob_kw),
    )
    ev = MUASAssignmentEvaluator(
        prob, participating=list(uavs), tasks=list(tasks), nests=list(nests),
        fleet_uavs=list(uavs), config=stage_cfg or MUASStageConfig(),
    )
    return prob, ev


def _total_cost(ev, routes, ends) -> float:
    return sum(ev._tour_cost(u, t, ends.get(u)) for u, t in routes.items())


def _late_ids(ev, routes) -> list[int]:
    out: list[int] = []
    for u, tids in routes.items():
        for pos, tid in enumerate(tids):
            lat = ev._task_latest(tid)
            if lat is not None and ev._arrival_time_of(u, tids, pos) > lat + 1e-6:
                out.append(tid)
    return sorted(out)


# ── 1. 覆盖优先装载：EDD + 不动点 ─────────────────────────────────


def test_loader_edd_priority_and_fixed_point():
    """两个任务竞争单机槽位：EDD 先救紧急者；装载结果幂等。"""
    # 单任务往返各 40km ≤ 45km；同 tour 80km 超限 ⇒ 只能装一个
    tasks = [
        _task(0, -20.0, 0.0, latest=1e6),    # 宽限
        _task(1, 20.0, 0.0, latest=1000.0),  # 紧急
    ]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 0.0, 0.0, remaining_range=45.0)]
    cfg = MUASStageConfig(coverage_first=True)
    _, ev = _mk(tasks, uavs, nests, stage_cfg=cfg)

    work, ends, loaded = ev._load_coverage_fixed_point({0: []}, {})
    assert work[0] == [1], f"EDD 必须先装紧急任务 t1，实际 {work}"
    assert loaded == 1

    work2, ends2, loaded2 = ev._load_coverage_fixed_point(work, ends)
    assert work2 == work and ends2 == ends, "装载必须收敛到不动点"
    assert loaded2 == 0, f"不动点后再跑不得有新动作：loaded={loaded2}"


def test_build_solution_coverage_first_uses_edd():
    """同一输入：默认路径按 id 序补全，覆盖优先按 EDD——可观测差别。"""
    tasks = [
        _task(0, -20.0, 0.0, latest=1e6),
        _task(1, 20.0, 0.0, latest=1000.0),
    ]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 0.0, 0.0, remaining_range=45.0)]

    _, ev_def = _mk(tasks, uavs, nests)
    sol_def = ev_def.build_solution([])
    assert sol_def.executed_task_ids == [0], (
        f"默认路径按任务 id 序补全（先 t0），实际 {sol_def.executed_task_ids}"
    )

    _, ev_cf = _mk(tasks, uavs, nests,
                   stage_cfg=MUASStageConfig(coverage_first=True))
    sol_cf = ev_cf.build_solution([])
    assert sol_cf.executed_task_ids == [1], (
        f"覆盖优先按 EDD 补全（先紧急 t1），实际 {sol_cf.executed_task_ids}"
    )


def test_loader_never_loads_infeasible_task():
    """约定口径外（航程/窗口死）的任务不得被硬塞。"""
    tasks = [
        _task(0, 10_000.0, 0.0),                # 往返 20km ≤ 45 可行
        _task(1, 200_000.0, 0.0),               # 往返 400km > 45 航程死
        _task(2, 12_000.0, 0.0, latest=100.0),  # ETA=1200s > 100 窗口死
    ]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    uavs = [_uav(0, 0.0, 0.0, remaining_range=45_000.0)]
    cfg = MUASStageConfig(coverage_first=True)
    _, ev = _mk(tasks, uavs, nests, stage_cfg=cfg)

    work, _, loaded = ev._load_coverage_fixed_point({0: []}, {})
    assert work[0] == [0], f"只能装可行任务 t0，实际 {work}"
    assert loaded == 1


# ── 2. 单调成本 polish：四道门 ────────────────────────────────────


def test_monotone_polish_relocate_reduces_cost_keeps_coverage():
    """relocate 降成本：执行集不变、零新迟到、统计有记录。"""
    tA = _task(0, 100.0, 0.0)
    tC = _task(2, 55.0, 0.0)
    tB = _task(1, 60.0, 0.0)
    u0 = _uav(0, 0.0, 0.0)
    u1 = _uav(1, 50.0, 0.0)          # u1 出发点靠近 tB/tC
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    _, ev = _mk([tA, tC, tB], [u0, u1], nests)

    routes = {0: [0, 2], 1: [1]}
    ends = {0: 0, 1: 0}
    before_cost = _total_cost(ev, routes, ends)
    before_exec = {0, 1, 2}

    out_r, out_e, stats = ev._polish_routes_monotone(routes, ends)
    after_exec = {tid for tids in out_r.values() for tid in tids}

    assert after_exec == before_exec, (
        f"polish 不得改变已装集合：{before_exec} → {after_exec}"
    )
    assert _total_cost(ev, out_r, out_e) < before_cost - 1e-6, (
        "relocate 必须严格降成本"
    )
    assert _late_ids(ev, out_r) == [], "干净场景不得制造迟到"
    assert stats["n_relocate"] >= 1, f"应有 relocate 被接受：{stats}"


def test_monotone_polish_rejects_move_creating_new_late_task():
    """迟到数门：唯一降成本移动把守约任务推过 deadline ⇒ 拒绝。"""
    # u0=u1 同点；tB 紧窗（直飞 10.0s ≤ 10.2），tC 插到 tB 前会使 tB
    # 10.47s 迟到；tC 自身紧窗使「插到 tB 后」也迟到（11.41 > 11.0）。
    tA = _task(0, 100.0, 0.0)
    tB = _task(1, 0.0, 100.0, latest=10.2)
    tC = _task(2, 10.0, 90.0, latest=11.0)
    u0 = _uav(0, 0.0, 0.0)
    u1 = _uav(1, 0.0, 0.0)
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    _, ev = _mk([tA, tB, tC], [u0, u1], nests)

    routes = {0: [2, 0], 1: [1]}     # u0=[tC,tA]、u1=[tB]，全部守约
    ends = {0: 0, 1: 0}
    assert _late_ids(ev, routes) == [], "前提：基解全部守约"

    out_r, out_e, stats = ev._polish_routes_monotone(routes, ends)
    assert out_r == routes, (
        f"迟到数门必须拒绝唯一改进移动（routes 应不变）：{routes} → {out_r}"
    )
    assert stats["n_relocate"] == 0 and stats["n_position"] == 0, (
        f"不得接受任何移动：{stats}"
    )


def test_monotone_polish_rejects_range_violation():
    """航程门：降成本但超目标机剩余航程的 relocate 必须拒绝。"""
    tA = _task(0, 100.0, 0.0)
    tC = _task(2, 2.0, 60.0)
    u0 = _uav(0, 0.0, 0.0, remaining_range=1e7)
    u1 = _uav(1, 0.0, 55.0, remaining_range=60.0)   # u1[tC]=65.4 > 60
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    _, ev = _mk([tA, tC], [u0, u1], nests)

    routes = {0: [0, 2], 1: []}
    ends = {0: 0, 1: 0}
    out_r, _, stats = ev._polish_routes_monotone(routes, ends)
    assert out_r == routes, f"超航程 relocate 必须拒绝：{routes} → {out_r}"
    assert stats["n_relocate"] == 0, f"不得接受移动：{stats}"


def test_monotone_polish_position_reduces_cost():
    """位置移动（机内调序）降成本，任务集不变。"""
    t0 = _task(0, 0.0, 100.0)
    t1 = _task(1, 100.0, 0.0)
    t2 = _task(2, 100.0, 100.0)
    u0 = _uav(0, 0.0, 0.0)
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    _, ev = _mk([t0, t1, t2], [u0], nests)

    routes = {0: [0, 1, 2]}          # 482.8；最优 [1,2,0] = 400
    ends = {0: 0}
    before_cost = _total_cost(ev, routes, ends)

    out_r, _, stats = ev._polish_routes_monotone(routes, ends)
    assert {tid for tids in out_r.values() for tid in tids} == {0, 1, 2}, (
        f"调序不得改任务集：{out_r}"
    )
    assert _total_cost(ev, out_r, {0: 0}) < before_cost - 1e-6, (
        f"位置移动必须降成本：{before_cost} → {_total_cost(ev, out_r, {0: 0})}"
    )
    assert stats["n_position"] >= 1, f"应有 position 被接受：{stats}"


# ── 3. post_polish：补装 + 门禁 ───────────────────────────────────


def test_post_polish_reloads_feasible_task_and_keeps_gate_stats():
    """可行却没装的任务必须被补装；gate_stats 正常落盘。"""
    t0 = _task(0, 10.0, 0.0)
    t1 = _task(1, 20.0, 0.0)
    u0 = _uav(0, 0.0, 0.0, remaining_range=100.0)
    u1 = _uav(1, 0.0, 0.0, remaining_range=100.0)
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=4)]
    cfg = MUASStageConfig(coverage_first=True, coverage_gate=True)
    prob, ev = _mk([t0, t1], [u0, u1], nests, stage_cfg=cfg)

    sol = AllocationSolution(
        tours=[
            UAVTour.from_uav(u0, task_ids=[0], end_nest_id=0),
            UAVTour.from_uav(u1, task_ids=[], end_nest_id=None),
        ],
        candidate_task_ids=[0, 1],
        executed_task_ids=[0],
    )
    new_sol, stats = ev.post_polish(sol)
    assert set(new_sol.executed_task_ids) >= {0, 1}, (
        f"post_polish 必须补装可行任务 t1：{new_sol.executed_task_ids}"
    )
    assert stats.get("n_reloaded", 0) >= 1, f"应记录补装数：{stats}"
    gs = new_sol.meta.get("gate_stats")
    assert gs is not None and gs["n_skip"] == 0, (
        f"门禁分账必须落盘且无 voluntary_skip：{gs}"
    )


# ── 4. polish_assignment 覆盖守护（stub 评测器）────────────────────


class _StubEvaluator:
    """最小评测器：base 解 vs 全部候选（卸任务换成本）。"""

    def __init__(self, base_exec: set[int], cand_exec: set[int],
                 base_fitness: float, cand_fitness: float):
        self.participating = [_uav(0), _uav(1)]
        self.tasks = [_task(0, 0.0, 0.0), _task(1, 0.0, 0.0)]
        self.n_evals = 0
        self._base = (base_exec, base_fitness)
        self._cand = (cand_exec, cand_fitness)
        self._base_assignment = [(0, 0), (1, 1)]

    def evaluate(self, assignment, cost_matrix=None, n_uavs=None) -> EvalResult:
        self.n_evals += 1
        if list(assignment) == self._base_assignment:
            exec_ids, fit = self._base
        else:
            exec_ids, fit = self._cand
        sol = AllocationSolution(
            tours=[], candidate_task_ids=sorted(exec_ids),
            executed_task_ids=sorted(exec_ids),
        )
        return EvalResult(fitness=float(fit), solution=sol)


def _stub_result(ev: _StubEvaluator, assignment, config: MUASStageConfig):
    return polish_assignment(ev, list(assignment), config)


def test_polish_assignment_guard_rejects_coverage_loss_under_gate():
    """覆盖盲判据：卸任务换成本的候选必须被守护拒绝。"""
    ev = _StubEvaluator(base_exec={0, 1}, cand_exec={0},
                        base_fitness=100.0, cand_fitness=50.0)
    cfg = MUASStageConfig(coverage_first=True)   # 覆盖盲 ⇒ 守护开
    pr = _stub_result(ev, [(0, 0), (1, 1)], cfg)
    assert set(pr.best_solution.executed_task_ids) == {0, 1}, (
        f"守护必须拒绝减少已装集合的候选：{pr.best_solution.executed_task_ids}"
    )
    assert pr.n_accepted_total == 0, f"不得接受任何候选：{pr.n_accepted}"
    assert pr.best_fitness == 100.0, "交付解保持 base"


def test_polish_assignment_without_guard_accepts_improvement_when_not_blind():
    """非门禁口径（F1 在适应度里）：守护不生效，行为与历史一致。"""
    ev = _StubEvaluator(base_exec={0, 1}, cand_exec={0},
                        base_fitness=100.0, cand_fitness=50.0)
    cfg = MUASStageConfig()   # 三个开关全关 ⇒ 非覆盖盲
    pr = _stub_result(ev, [(0, 0), (1, 1)], cfg)
    assert pr.n_accepted_total >= 1, "非门禁口径应按适应度接受改进候选"
    assert pr.best_fitness == 50.0
