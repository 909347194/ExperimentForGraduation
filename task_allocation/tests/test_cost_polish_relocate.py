# -*- coding: utf-8 -*-
"""交付后成本 polish —— 跨机指派维度测试（2026-10-24 用户裁定转向）。

契约（组内 2-opt/Or-opt 之后的新维度，见 muas_stage._cost_polish_relocate）：

1. **压航程**：供体航次中的任务迁到更近的受体机（含空闲机）⇒ 系统总
   航程严格下降；
2. **执行集不变**：改派保持任务集（只是换机器）——「覆盖优先、成本随后」；
3. **航程门**：受体插入后超其剩余航程 ⇒ 拒绝，且 blocked_range 计数
   （几何更优但被航程堵死）；
4. **迟到门**：受体插入会制造新迟到 ⇒ 拒绝，且 blocked_late 计数；
5. **eps 门 / 开关**：改进小于阈值不采纳；``cost_polish_relocate_enabled
   =False`` 时整段跳过（逐位等于旧口径）；
6. **默认开启** + 配置校验。

动机背景：3 seed A/B 钉死组内维度已穷尽（63 个周期解仅 3 次接受、总增益
2262 m ≈ 0.014%，覆盖/按时逐位不变）⇒ 成本余量若存在应在归属维度。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import Nest, Task, UAV, UAVStatus  # noqa: E402
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASAssignmentEvaluator,
    MUASStageConfig,
)

NEST_KEY = 1_000_000


def _task(tid: int, x: float, reward: float = 5.0,
          earliest: float = 0.0, latest: float = 1e6) -> Task:
    return Task(id=tid, x=x, y=0.0, z=0.0, reward=reward,
                earliest=earliest, latest=latest, arrival_time=0.0)


def _uav(uid: int, x: float = 0.0, remaining_range: float = 1e7) -> UAV:
    return UAV(id=uid, x=x, y=0.0, z=0.0, remaining_range=remaining_range,
               speed=10.0, nest_id=0, status=UAVStatus.IDLE)


def _pairwise(tasks, uavs, nests):
    import math

    pts = {t.id: t.position for t in tasks}
    pts.update({-(1 + u.id): u.position for u in uavs})
    pts.update({NEST_KEY + n.id: n.position for n in nests})
    keys = list(pts)
    return {
        (a, b): math.dist(pts[a], pts[b])
        for i, a in enumerate(keys) for b in keys[i + 1:]
    }


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


def _handbuilt_solution(ev, routes: dict[int, list[int]],
                        ends: dict[int, int | None]):
    """按显式 routes/ends 组装 AllocationSolution（绕过修复链，供单元测）。"""
    from task_allocation.common.types import AllocationSolution, UAVTour

    tours = []
    executed: list[int] = []
    for uav in ev.problem.uavs:
        tids = list(routes.get(uav.id, []))
        end = ends.get(uav.id)
        if not tids:
            end = uav.nest_id
        executed.extend(tids)
        tours.append(UAVTour.from_uav(uav, task_ids=tids, end_nest_id=end))
    return AllocationSolution(
        tours=tours,
        candidate_task_ids=[t.id for t in ev.tasks],
        executed_task_ids=list(dict.fromkeys(executed)),
    )


# ---------------------------------------------------------------------------
# 1. 压航程 + 执行集不变（核心收益场景：任务迁往更近的空闲机）
# ---------------------------------------------------------------------------


def test_relocate_shrinks_path_and_keeps_exec_set():
    """U0=[T0(0),T1(300)]、U1 空闲于 250、巢在 0：

    - 基线：U0 0→0→300→0 = 600；U1 不起飞 = 0 ⇒ 600
    - 改派 T1→U1：U0 [T0] = 0；U1 250→300→0 = 350 ⇒ 350（−250）
    - 链式再改派 T0→U1 不成立（350 − 350 = 0，非严格下降）⇒ 收敛于此
    """
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0), _uav(1, 250.0)]
    tasks = [_task(0, 0.0), _task(1, 300.0)]
    prob, ev = _mk(tasks, uavs, nests)

    routes, ends, stats = ev._cost_polish_relocate({0: [0, 1]}, {0: 0, 1: 0})
    assert stats["n_relocate"] == 1, f"应接受 1 次跨机改派：{stats}"
    assert stats.get("blocked_range", 0) == 0
    assert stats.get("blocked_late", 0) == 0
    # 任务确已换机；执行集（任务集）不变
    assert sorted(routes.get(0, [])) == [0]
    assert sorted(routes.get(1, [])) == [1]
    sol = _handbuilt_solution(ev, routes, ends)
    assert set(sol.executed_task_ids) == {0, 1}
    # 航程严格下降（与算子内部估价同口径：350 < 600）
    total = sum(
        prob.tour_path_cost(t) for t in sol.tours
    )
    assert total == pytest.approx(350.0, abs=1e-6), total
    assert ends.get(0) == 0


def test_relocate_end_to_end_via_post_cost_polish():
    """e2e：post_cost_polish 对显式构造的次优归属解完成改派 + 联合重解。

    几何同测试 1（T0 在 U0 原点、T1 在 U1 附近）：基线 600 ⇒ 改派后 350。
    """
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0), _uav(1, 250.0)]
    tasks = [_task(0, 0.0), _task(1, 300.0)]
    prob, ev = _mk(tasks, uavs, nests)

    sol_before = _handbuilt_solution(ev, {0: [0, 1]}, {0: 0, 1: 0})
    path_before = prob.path_cost(sol_before)
    exec_before = set(sol_before.executed_task_ids)
    assert path_before == pytest.approx(600.0, abs=1e-6)

    sol_after, stats = ev.post_cost_polish(sol_before)
    assert stats["n_accepted"] > 0, f"未接受任何移动：{stats}"
    assert stats["n_relocate"] >= 1
    path_after = prob.path_cost(sol_after)
    assert path_after < path_before - 1.0, (
        f"航程未严格下降：{path_before} → {path_after}"
    )
    # 覆盖不侵蚀：执行集逐位不变；任务是「换机器」而非丢失/重复
    assert set(sol_after.executed_task_ids) == exec_before
    per_machine = [sorted(t.task_ids) for t in sol_after.tours if t.task_ids]
    assert sorted(t for m in per_machine for t in m) == [0, 1]
    # 硬违约不增（场景无硬约束）
    assert prob.hard_stats(sol_after)[0] == 0
    assert prob.soft_violation(sol_after) <= prob.soft_violation(sol_before) + 1e-9


def test_relocate_deterministic():
    """同输入两次调用逐位一致（无随机、按 id 升序扫描）。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0), _uav(1, 250.0)]
    tasks = [_task(0, 100.0), _task(1, 300.0)]
    _, ev = _mk(tasks, uavs, nests)
    r1, e1, s1 = ev._cost_polish_relocate({0: [0, 1]}, {0: 0})
    r2, e2, s2 = ev._cost_polish_relocate({0: [0, 1]}, {0: 0})
    assert r1 == r2 and e1 == e2 and s1 == s2


# ---------------------------------------------------------------------------
# 2. 航程门：受体装不下 ⇒ 拒绝并计数
# ---------------------------------------------------------------------------


def test_relocate_range_gate_blocks_and_counts():
    """几何更优但受体剩余航程不足 ⇒ 拒绝，blocked_range=1。"""
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0), _uav(1, 250.0, remaining_range=100.0)]  # 需 350
    tasks = [_task(0, 100.0), _task(1, 300.0)]
    _, ev = _mk(tasks, uavs, nests)

    routes, ends, stats = ev._cost_polish_relocate({0: [0, 1]}, {0: 0})
    assert stats["n_relocate"] == 0, "超航程改派不得被接受"
    assert stats["blocked_range"] == 1, f"应记 1 次航程门拦截：{stats}"
    assert stats["blocked_late"] == 0
    assert routes == {0: [0, 1]}, "路由不得被改动"


# ---------------------------------------------------------------------------
# 3. 迟到门：受体插入制造新迟到 ⇒ 拒绝并计数
# ---------------------------------------------------------------------------


def test_relocate_late_gate_blocks_and_counts():
    """几何更优但受体（原空闲）插入即迟到 ⇒ 拒绝，blocked_late=1。

    构造：T1 latest=4s。供体 U0 [T0,T1] 上 T1 到达 30s（已迟到 26s）；
    受体 U1(250) 插入 T1 到达 5s > 4s ⇒ 由 (0,0) 变 (1,1) ⇒ 迟到门拦截。
    """
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0), _uav(1, 250.0)]
    tasks = [_task(0, 100.0), _task(1, 300.0, latest=4.0)]
    _, ev = _mk(tasks, uavs, nests)

    routes, ends, stats = ev._cost_polish_relocate({0: [0, 1]}, {0: 0})
    assert stats["n_relocate"] == 0, "制造新迟到的改派不得被接受"
    assert stats["blocked_late"] == 1, f"应记 1 次迟到门拦截：{stats}"
    assert stats["blocked_range"] == 0
    assert routes == {0: [0, 1]}, "路由不得被改动"


def test_relocate_late_gate_allows_on_time_move():
    """对照：同一几何、时间窗放宽 ⇒ 改派应被接受（证明上门真的只拦迟到）。

    几何同测试 1（T0 在原点、T1 在 300）：链式二次改派不成立
    （350 − 350 = 0）⇒ 恰好 1 次接受。
    """
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0), _uav(1, 250.0)]
    tasks = [_task(0, 0.0), _task(1, 300.0, latest=100.0)]  # 5s ≤ 100s
    _, ev = _mk(tasks, uavs, nests)

    routes, ends, stats = ev._cost_polish_relocate({0: [0, 1]}, {0: 0, 1: 0})
    assert stats["n_relocate"] == 1
    assert stats["blocked_late"] == 0


# ---------------------------------------------------------------------------
# 4. 开关与默认
# ---------------------------------------------------------------------------


def test_relocate_switch_off_skips_dimension():
    """cost_polish_relocate_enabled=False ⇒ 跳过归属维度（逐位旧口径）。

    场景只有改派能改进（组内顺序已最优：反转序 600 不优于 600）⇒ 关闭后
    零接受、原样返回。
    """
    nests = [Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=10, available=True)]
    uavs = [_uav(0, 0.0), _uav(1, 250.0)]
    tasks = [_task(0, 0.0), _task(1, 300.0)]
    _, ev = _mk(
        tasks, uavs, nests,
        stage_cfg=MUASStageConfig(cost_polish_relocate_enabled=False),
    )
    sol_before = _handbuilt_solution(ev, {0: [0, 1]}, {0: 0, 1: 0})
    sol_after, stats = ev.post_cost_polish(sol_before)
    assert stats.get("n_relocate", 0) == 0
    assert stats.get("n_accepted", 0) == 0
    assert sol_after is sol_before, "关闭时必须原样返回"


def test_relocate_default_on():
    cfg = MUASStageConfig()
    assert cfg.cost_polish_relocate_enabled is True, "生产口径默认开启"
