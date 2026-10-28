# -*- coding: utf-8 -*-
"""合并—删任务链路修复（结构性航程门 + 回流式航程修复）的常驻回归测试。

背景缺陷（exp01 实测暴露）：_consolidate 合并只看代价下降、不查航程，
合并出超航程长航次 → _repair_range 用删任务缩航程、删掉的任务不回流 →
物理可行任务被吞（exp01 周期 1：12 个任务单任务航次 8.4~68.2 km 全部
≤ 80 km 预算，却被延期 8 个）。

锁定的契约：

1. **结构性航程门**：合并后航次超受体航程预算 → 拒绝合并（可行性
   不依赖事后修复，与 MCF 溢出边结构性保底同一设计原则）；
2. **回流式修复**：超限航次移除的任务优先改派（插入现有航次 / 闲置机
   单任务航次），全部不可行才 y_j=0 暂缓；
3. **证明性暂缓**：真不可行（单任务航次即超预算）才延期。

三个用例的几何均为对抗性设计：任务两两分开 120°，单任务航次装得下、
合并航次必超预算、合并比分开省——修复前必失败。
"""

from __future__ import annotations

import math

from task_allocation.common.types import Nest, Task, UAV
from task_allocation.methods.muas.problem import (
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.muas_stage import (
    MUASAssignmentEvaluator,
    MUASStageConfig,
)

NEST_KEY = 1_000_000
RANGE = 80_000.0


def _task(tid: int, x: float, y: float) -> Task:
    return Task(id=tid, x=x, y=y, z=0.0, reward=10.0,
                earliest=0.0, latest=1e9, arrival_time=0.0)


def _uav(uid: int, remaining_range: float = RANGE) -> UAV:
    return UAV(id=uid, x=0.0, y=0.0, z=0.0, remaining_range=remaining_range,
               speed=10.0, nest_id=0)


def _pairwise(tasks, uavs, nests):
    pts = {t.id: (t.x, t.y, t.z) for t in tasks}
    pts.update({-(1 + u.id): (u.x, u.y, u.z) for u in uavs})
    pts.update({NEST_KEY + n.id: (n.x, n.y, n.z) for n in nests})
    keys = list(pts)
    out = {}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            out[(a, b)] = math.dist(pts[a], pts[b])
    return out


def _evaluator(tasks, uavs, nests) -> MUASAssignmentEvaluator:
    problem = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(),
    )
    return MUASAssignmentEvaluator(
        problem=problem,
        participating=list(uavs),
        tasks=list(tasks),
        nests=list(nests),
        fleet_uavs=list(uavs),
        config=MUASStageConfig(),
    )


def _fixture_2uav_2task():
    """巢在原点；t1/t2 距巢各 30 km、相距约 52 km（120° 夹角）。

    单任务航次 60 km（≤ 80）；合并航次 0→t1→t2→0 ≈ 112 km（> 80）；
    合并 (112) 比分开 (120) 省——正是吞任务链路的触发几何。
    """
    tasks = [_task(0, 30_000.0, 0.0), _task(1, -15_000.0, 26_000.0)]
    uavs = [_uav(0), _uav(1)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    return tasks, uavs, nests


# ---------------------------------------------------------------------------
# 1. 结构性航程门：合并不得制造超航程航次
# ---------------------------------------------------------------------------


def test_consolidate_rejects_range_infeasible_merge():
    tasks, uavs, nests = _fixture_2uav_2task()
    ev = _evaluator(tasks, uavs, nests)
    # 解码：一机一任务（本可全部执行）
    sol = ev.build_solution([(0, 0), (1, 1)])
    executed = set(sol.executed_task_ids)
    carriers = [t for t in sol.tours if t.task_ids]
    assert executed == {0, 1}, f"任务被吞：executed={executed}"
    assert len(carriers) == 2, (
        f"合并制造了超航程航次并被修复吞任务：{[(t.uav_id, t.task_ids) for t in carriers]}"
    )


# ---------------------------------------------------------------------------
# 2. 回流式修复：装不下的任务改派给闲置机，而非删除
# ---------------------------------------------------------------------------


def test_repair_reflows_to_idle_uav():
    tasks, uavs, nests = _fixture_2uav_2task()
    ev = _evaluator(tasks, uavs, nests)
    # 解码：两任务全压在 uav0（超航程），uav1 闲置
    sol = ev.build_solution([(0, 0), (0, 1)])
    executed = set(sol.executed_task_ids)
    assert executed == {0, 1}, f"回流失败，任务被删：executed={executed}"
    carriers = [t for t in sol.tours if t.task_ids]
    assert len(carriers) == 2, (
        f"应改派到闲置机：{[(t.uav_id, t.task_ids) for t in carriers]}"
    )


# ---------------------------------------------------------------------------
# 3. 证明性暂缓：真不可行才 y_j=0
# ---------------------------------------------------------------------------


def test_defers_only_genuinely_infeasible():
    tasks = [_task(0, 30_000.0, 0.0), _task(1, 400_000.0, 0.0)]  # t1 单航次 800km
    uavs = [_uav(0), _uav(1)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    ev = _evaluator(tasks, uavs, nests)
    sol = ev.build_solution([(0, 0), (0, 1)])
    executed = set(sol.executed_task_ids)
    assert executed == {0}, f"真不可行任务不应被执行或误改派：executed={executed}"
    # 可行的 t0 仍应保留（不因邻座不可行被连坐）
    assert any(t.task_ids == [0] for t in sol.tours)


# ---------------------------------------------------------------------------
# 4. Fix⑥「可达即执行」：救援池扩到全可用机队
# ---------------------------------------------------------------------------


def _evaluator_scoped(tasks, uavs, nests, participating_ids, available, scope):
    """构造「参与集 ⊂ 可用机队」的 evaluator（Fix⑥ 救援池口径对照）。"""
    problem = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests,
        pairwise_cost=_pairwise(tasks, uavs, nests),
        config=MUASProblemConfig(),
    )
    part = [u for u in uavs if u.id in participating_ids]
    return MUASAssignmentEvaluator(
        problem=problem,
        participating=part,
        tasks=list(tasks),
        nests=list(nests),
        fleet_uavs=list(uavs),
        config=MUASStageConfig(reflow_rescue_scope=scope),
        available_uavs=list(available),
    )


def test_reflow_rescues_with_idle_uav_outside_participating_set():
    """Fix⑥：参与集外的闲置机应能救援可达任务（可达性＝存在量化）。

    几何：机巢与两架机同在原点；t0/t1 各距原点 30km（单任务航次 60km），
    两任务相距约 42.4km，故合并航次 ≈ 102.4km。
      u0 航程 70km → 装得下单个（60 ≤ 70）、装不下两个（102.4 > 70），
                     且它是**唯一**参与机；
      u1 航程 80km → 装得下（60 ≤ 80），但**不在**参与集（闲置可用）。
    历史口径（participating）只在参与集内找闲置机 → 唯一参与机已占用 → 假暂缓；
    Fix⑥（fleet）扩到全可用机队 → u1 接手 → 两任务均执行。
    这正是 exp01 中 T10 的处境（非参与闲置机 67.3/71.0/76.1km 均 ≤80km）。
    """
    tasks = [_task(0, 30_000.0, 0.0), _task(1, 0.0, 30_000.0)]
    uavs = [_uav(0, remaining_range=70_000.0), _uav(1, remaining_range=RANGE)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]

    ev_old = _evaluator_scoped(tasks, uavs, nests, {0}, uavs, "participating")
    sol_old = ev_old.build_solution([(0, 0), (0, 1)])
    assert len(set(sol_old.executed_task_ids)) == 1, (
        f"历史口径只在参与集内救援，应有 1 个被暂缓：{sol_old.executed_task_ids}"
    )

    ev_new = _evaluator_scoped(tasks, uavs, nests, {0}, uavs, "fleet")
    sol_new = ev_new.build_solution([(0, 0), (0, 1)])
    assert set(sol_new.executed_task_ids) == {0, 1}, (
        f"可达任务应被执行（存在量化）：{sol_new.executed_task_ids}"
    )
    assert any(t.uav_id == 1 and t.task_ids for t in sol_new.tours), (
        f"应由参与集外的闲置机 u1 接手："
        f"{[(t.uav_id, t.task_ids) for t in sol_new.tours]}"
    )


def test_reflow_fleet_scope_still_defers_infeasible():
    """Fix⑥ 不得把真不可达任务硬塞进来：救援池扩大仍需过航程门。

    t1 在 400km 外（单任务航次 800km），即便把全机队都当救援池也装不下 →
    应只执行 t0。保证「可达即执行」不会退化成「无条件执行」。
    """
    tasks = [_task(0, 30_000.0, 0.0), _task(1, 400_000.0, 0.0)]
    uavs = [_uav(0, remaining_range=70_000.0), _uav(1, remaining_range=RANGE)]
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10)]
    ev = _evaluator_scoped(tasks, uavs, nests, {0}, uavs, "fleet")
    sol = ev.build_solution([(0, 0), (0, 1)])
    assert set(sol.executed_task_ids) == {0}, (
        f"真不可达任务不应因救援池扩大而被执行：{sol.executed_task_ids}"
    )


# ---------------------------------------------------------------------------
# 5. 重排的航程预算必须含返航腿（uav_9「边际航程弹回」链路根因）
# ---------------------------------------------------------------------------


def _task_at(tid: int, x: float, y: float, latest: float = 1e9) -> Task:
    return Task(id=tid, x=x, y=y, z=0.0, reward=10.0,
                earliest=0.0, latest=latest, arrival_time=0.0)


def _uav9_rebound_fixture():
    """uav_9「边际航程弹回」的对抗性微型几何。

    exp01 实测（results_phys_60，seed 42）：uav_9 周期 3–6 交付航次固定
    ``[47,59,40,55]``、终点 nest 4、全航程 80 027.58 m，超满电 80 000 m
    仅 27.58 m（0.034%），四周期共 16 次弹回，标签全是
    ``range_uav_9`` + ``tour_infeasible_uav_9``。

    根因（插桩复现，build_solution 四阶段探针对照）::

        post-loop-entry    tour=[59,40,55,47] cost=77686.91  OVER=False
        after-complete     tour=[59,40,55,47] cost=77686.91  OVER=False
        after-reorder      tour=[47,59,40,55] cost=80027.58  OVER=True
        after-final-choose tour=[47,59,40,55] cost=80027.58  OVER=True

    ``_reorder_for_deadlines → _best_order → _evaluate`` 只累计「起点 →
    各任务」的**外出链**（48 713.62 m ≤ 80 000 m，过关），**漏掉返航腿**
    （末任务 55 → 终点巢的 31 313.96 m）；而完成闸门（check_range_constraint
    → problem.tour_path_cost）与 ``_repair_range``/``_tour_cost``/
    ``_reflow_task`` 用的都是「起点 → 任务 → 终点巢」**全航程**。且
    post-loop 的 ``_choose_end_nests`` 之后没有航程复闸，越限航次直接漏到
    交付 → 判 range_uav_9 弹回（航程/位置已更新却解绑回池不计完成＝废飞）。

    本几何复现同一结构（uav9 起点 (0,0)，满电 80 km，nest 0 近 / nest 1 极远）：

    ============  =====================  ============================  ========
    序            外出链                  全航程（返航到**最近**巢）      结论
    ============  =====================  ============================  ========
    ``[p, q]``    28 + 35 = 63 km        63 + 15 = 78 km               可行
    ``[q, p]``    10 + 35 = 45 km        45 + 47.6 = 92.6 km           越限
    ============  =====================  ============================  ========

    ``q`` 近巢（15 km）、``p`` 远巢（47.6 km），故把 ``p`` 排到末位返航腿
    暴涨。时间窗开启但 ``q.latest=6000 s``：原序 ``[p,q]`` 下 ``q`` 到达
    6 300 s 迟到，反向序 ``[q,p]`` 零迟到——字典序「迟到数, 迟到量, 航程」
    下反向序胜出，这正是重排的动机。修复前 ``_evaluate`` 按外出链放行
    ``[q,p]``，交付即越限。
    """
    tasks = [
        _task_at(47, -17_050.0, 22_210.4),             # p：远巢（47.6 km）
        _task_at(59, 10_000.0, 0.0, latest=6_000.0),   # q：近巢（15 km）
    ]
    uavs = [_uav(9)]                                   # 起点 (0,0)，满电 80 km
    nests = [
        Nest(id=0, x=25_000.0, y=0.0, z=0.0, capacity=10),      # 近 q
        Nest(id=1, x=0.0, y=-100_000.0, z=0.0, capacity=10),    # 极远（对照）
    ]
    return tasks, uavs, nests


def test_reorder_range_budget_includes_return_leg():
    """_best_order 的航程预算必须含返航腿，否则越限序会被放行。"""
    tasks, uavs, nests = _uav9_rebound_fixture()
    ev = _evaluator(tasks, uavs, nests)
    # 原序 [p, q]（本地 [47, 59]）：q 迟到 → 触发候选重排
    order = ev._best_order(9, [47, 59])
    cost = ev._tour_cost(9, order, None)  # 终点未定 → 最近巢（保守下界）
    assert cost <= RANGE + 1e-6, (
        f"_best_order 返回全航程越限的序 {order}（{cost:.1f} m > {RANGE:.0f} m）："
        "返航腿未计入航程预算，重排会把可行航次改成越限航次"
    )


def test_delivered_tours_never_exceed_range_after_reorder():
    """交付解不得含超过剩余航程的航次（完成闸门同口径，防废飞弹回）。"""
    tasks, uavs, nests = _uav9_rebound_fixture()
    ev = _evaluator(tasks, uavs, nests)
    sol = ev.build_solution([(0, 0), (0, 1)])  # 两任务都派给 uav9
    assert any(t.task_ids for t in sol.tours), "应有非空闲航次"
    for tour in sol.tours:
        if not tour.task_ids:
            continue
        cost = ev.problem.tour_path_cost(tour)  # 与完成闸门同口径
        limit = ev._uav_by_id[tour.uav_id].remaining_range
        assert cost <= limit + 1e-6, (
            f"uav{tour.uav_id} 航次 {tour.task_ids} 全航程 {cost:.1f} m 超剩余 "
            f"航程 {limit:.1f} m —— 会被完成闸门判 range_uav_{tour.uav_id} "
            "弹回（航程/位置已更新却解绑回池不计完成＝废飞）"
        )
