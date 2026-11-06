# -*- coding: utf-8 -*-
"""P2-a 测试：回写比例可配（repair_replacement）+ fitness 重算 + 四元组。

契约（定稿 §9.1 / §11.3 P2-a）：

1. **解析矩阵**：``repair_replacement`` 显式 > ``lamarckian``（True≡1.0 /
   False≡0.0）> ``lamarckian_patrol_writeback`` 保义 1.0 > 默认 0（纯 Baldwin，
   P2 方向性拍板）；求解器层同口径（无 patrol 字段）；
2. **fitness 重算**：回写基因必须按自身解码重评（修 ``re_encoded.fitness =
   child.fitness`` 直接赋值的双重错配）；表型复核失败（重评 ≠ S_repair
   适应度）⇒ 该次回写计失败、保留原基因 g_raw（Baldwin 兜底）；
3. **四元组**：select 事件携带 geno_id（g_raw 身份散列）与
   selection_object / inherited_object（§5.2 一致性契约）；
4. **方差护栏数据源**：台账 diversity_series 含 gen0 基线与逐代曲线；
5. **确定性**：比例 0 / 0.5 / 1 同 seed 两次运行结果逐位一致（0/1 不耗
   RNG；0.5 的抽签也由同一 rng 流确定）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from task_allocation.common.types import Nest, Task, UAV  # noqa: E402
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.problem import (  # noqa: E402
    MUASProblemConfig,
    SelectiveMUASProblem,
)
from task_allocation.methods.muas.representation.encoder import Gene, Individual  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import (  # noqa: E402
    DMDEConfig,
    DMDESolver,
    _geno_id,
)
from task_allocation.methods.muas.solvers.evaluator import (  # noqa: E402
    MUASAssignmentEvaluator,
)
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)

NEST_KEY = 1_000_000


# ---------------------------------------------------------------------------
# 1. 解析矩阵
# ---------------------------------------------------------------------------


def test_stage_config_resolution_matrix():
    # 拍板默认：0（纯 Baldwin）
    assert MUASStageConfig().effective_repair_replacement == 0.0
    # 旧布尔兼容：True ≡ 1.0 / False ≡ 0.0
    assert MUASStageConfig(lamarckian=True).effective_repair_replacement == 1.0
    assert MUASStageConfig(lamarckian=False).effective_repair_replacement == 0.0
    # 新参数优先
    assert MUASStageConfig(repair_replacement=0.5).effective_repair_replacement == 0.5
    assert (
        MUASStageConfig(repair_replacement=0.3, lamarckian=True)
        .effective_repair_replacement
        == 0.3
    )
    # patrol 保义：M2-1 借道 M1 调用门的历史语义不因默认翻转而静默失效
    assert (
        MUASStageConfig(lamarckian_patrol_writeback=True).effective_repair_replacement
        == 1.0
    )
    # 非法取值拒绝
    with pytest.raises(ValueError):
        MUASStageConfig(repair_replacement=1.5)
    with pytest.raises(ValueError):
        MUASStageConfig(repair_replacement=-0.1)


def test_dmde_config_resolution_matrix():
    assert DMDEConfig().effective_repair_replacement == 0.0
    assert DMDEConfig(lamarckian=True).effective_repair_replacement == 1.0
    assert DMDEConfig(lamarckian=False).effective_repair_replacement == 0.0
    assert DMDEConfig(repair_replacement=0.5).effective_repair_replacement == 0.5
    assert DMDEConfig(repair_replacement=0.7, lamarckian=False).effective_repair_replacement == 0.7
    with pytest.raises(ValueError):
        DMDEConfig(repair_replacement=2.0)


def test_lamarckian_sentinel_is_none_by_default():
    # 哨兵语义：未显式设置 = None（不是 True/False），由 repair_replacement 接管
    assert MUASStageConfig().lamarckian is None
    assert DMDEConfig().lamarckian is None


# ---------------------------------------------------------------------------
# 场地与求解工具
# ---------------------------------------------------------------------------


def _scenario():
    nests = [
        Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=2, available=True),
        Nest(id=1, x=4000.0, y=0.0, z=0.0, capacity=2, available=True),
    ]
    uavs = [
        UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=15000.0, speed=12.0, nest_id=0),
        UAV(id=1, x=4000.0, y=0.0, z=0.0, remaining_range=15000.0, speed=11.0, nest_id=1),
    ]
    tasks = [
        Task(id=0, x=800.0, y=200.0, z=0.0, reward=5.0, earliest=0.0, latest=1500.0),
        Task(id=1, x=1600.0, y=-300.0, z=0.0, reward=4.0, earliest=0.0, latest=2000.0),
        Task(id=2, x=2400.0, y=400.0, z=0.0, reward=6.0, earliest=0.0, latest=1800.0),
        Task(id=3, x=3100.0, y=-200.0, z=0.0, reward=3.0, earliest=0.0, latest=2200.0),
    ]
    return tasks, uavs, nests


def _make_ev(**stage_kwargs):
    tasks, uavs, nests = _scenario()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY)
    problem = SelectiveMUASProblem.from_entities(
        tasks, uavs, nests=nests, pairwise_cost=cb.provider.pairwise, config=MUASProblemConfig()
    )
    ev = MUASAssignmentEvaluator(
        problem=problem,
        participating=uavs,
        tasks=tasks,
        nests=nests,
        fleet_uavs=uavs,
        config=MUASStageConfig(**stage_kwargs),
    )
    return ev, cb, tasks, uavs, nests


def _solve(ev, cb, n_uavs=2, n_targets=4, **cfg_kwargs):
    cm = np.vstack([cb.uav_target, cb.task_task])  # (K+N, N)
    solver = DMDESolver(config=DMDEConfig(pop_size=6, max_generations=4, seed=1, **cfg_kwargs))
    return solver.solve(cm, n_uavs, n_targets, fitness_evaluator=ev)


def _run(stage_cfg, seed=7):
    tasks, uavs, nests = _scenario()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY)
    stage = run_selective_muas(
        tasks, uavs, nests, cb, fleet_uavs=uavs,
        problem_config=MUASProblemConfig(),
        stage_config=stage_cfg,
        solver_config=DMDEConfig(pop_size=10, max_generations=8, seed=seed),
        nest_offset=NEST_KEY, current_time=0.0,
    )
    sol = stage.solution
    return (
        sorted(sol.executed_task_ids),
        round(float(sol.f2_cost), 6),
        [(t.uav_id, tuple(t.task_ids), t.end_nest_id) for t in sol.tours],
        round(float(stage.diagnostics["final_fitness"]), 9),
    )


# ---------------------------------------------------------------------------
# 2. 回写事件：fitness 重算 + 表型复核 + 四元组
# ---------------------------------------------------------------------------


def test_writeback_ratio1_events_and_quadruple():
    ev, cb, *_ = _make_ev(lamarckian=True)
    res = _solve(ev, cb, lamarckian=True)
    assert np.isfinite(res.best_fitness)
    events = ev.ledger.events
    select_events = [e for e in events if e.layer == "select"]
    assert select_events, "应有 select 事件"
    # 四元组：select 事件全部携带 geno_id（g_raw 身份）与来源标签
    for e in select_events:
        assert e.geno_id is not None and len(e.geno_id) == 12
        assert e.selection_object == "S_repair"
        assert e.inherited_object in ("g_raw", "g_repaired_reencoded", "g_parent")
    wb = [e for e in events if e.action == "lamarck_writeback"]
    if wb:  # 场地足够小，回写发生与否取决于替换事件；发生了则必须合规
        for e in wb:
            # 表型复核通过的回写：S_repair 与重编码 fitness 逐位一致
            assert e.accepted is True
            assert str(e.from_state).split("=", 1)[1] == str(e.to_state).split("=", 1)[1]
            assert e.inherited_object == "g_repaired_reencoded"
            assert e.geno_id is not None
        # 回写后的 replace_parent 继承标签应为重编码基因
        replaced = [
            e for e in select_events
            if e.action == "replace_parent" and e.inherited_object == "g_repaired_reencoded"
        ]
        assert len(replaced) == len([e for e in wb if e.accepted])


def test_recheck_failure_reverts_to_g_raw():
    """表型复核失败 ⇒ 回写计失败、保留原基因（fitness 重算的守卫路径）。

    注：小场地上基因位移常被修复链归一成同一表型（表型碰撞），故不靠
    位移本身制造差异——在**评价端**对重编码 assignment 注入 +1000 偏离，
    模拟「重解码 ≠ S_repair 表型」的复核失败分支。
    """
    ev, cb, *_ = _make_ev(lamarckian=True)
    n_targets = 4

    class _Tampered:
        def __init__(self, inner):
            self._inner = inner
            self.ledger = inner.ledger
            self.reencode_calls = 0
            self._tampered_keys: set = set()

        def evaluate(self, assignment, cost_matrix=None, n_uavs=None):
            res = self._inner.evaluate(assignment, cost_matrix, n_uavs=n_uavs)

            class _R:
                pass

            r = _R()
            r.fitness = res.fitness
            r.solution = res.solution
            key = tuple((int(a), int(b)) for a, b in assignment)
            if key in self._tampered_keys:
                r.fitness = float(res.fitness) + 1000.0
            return r

        def lamarckian_reencode(self, child, solution, cost_matrix, n_uavs, n_targets_):
            self.reencode_calls += 1
            genes = [
                Gene(
                    uav_id=g.uav_id,
                    target_id=(g.target_id + 1) % n_targets,
                    cost=g.cost,
                )
                for g in child.genes
            ]
            ind = Individual(genes=genes, model_type=child.model_type)
            self._tampered_keys.add(tuple(ind.assignment))
            return ind

    tam = _Tampered(ev)
    res = _solve(tam, cb, lamarckian=True)
    assert np.isfinite(res.best_fitness)
    if tam.reencode_calls:
        wb = [e for e in ev.ledger.events if e.action == "lamarck_writeback"]
        assert wb, "发生了 reencode 调用则应有回写台账"
        for e in wb:
            # 全部复核失败：不得采纳（保留 g_raw），差额记台账不静默
            assert e.accepted is False
            assert e.inherited_object == "g_raw"
            assert "recheck_failed" in str(e.gate_reason)
        # 无任何 replace_parent 声称继承重编码基因
        replaced = [
            e for e in ev.ledger.events
            if e.action == "replace_parent"
            and e.inherited_object == "g_repaired_reencoded"
        ]
        assert not replaced


def test_default_baldwin_no_writeback_events():
    """拍板默认（比例 0）：纯 Baldwin，零回写事件、继承恒 g_raw。"""
    ev, cb, *_ = _make_ev()
    res = _solve(ev, cb)  # DMDEConfig 默认（哨兵 None → 0）
    assert np.isfinite(res.best_fitness)
    wb = [e for e in ev.ledger.events if e.action == "lamarck_writeback"]
    assert not wb, f"默认纯 Baldwin 不应发生回写：{wb}"
    for e in ev.ledger.events:
        if e.action == "replace_parent":
            assert e.inherited_object == "g_raw"


def test_diversity_series_recorded():
    ev, cb, *_ = _make_ev(lamarckian=True)
    _solve(ev, cb, lamarckian=True)
    ds = ev.ledger.diversity_series
    assert 0 in ds and ds[0] > 0.0, "应记录 gen0 基线多样性"
    assert len(ds) >= 2, "应逐代记录（gen0 + 至少一代）"
    snap = ev.ledger.snapshot()
    assert snap["diversity_initial"] == ds[0]
    assert snap["diversity_min_ratio"] is not None
    assert snap["diversity_min_ratio"] <= 1.0


def test_geno_id_stable_and_distinct():
    g1 = [Gene(uav_id=0, target_id=1, cost=1.0), Gene(uav_id=-1, target_id=2, cost=0.5)]
    g2 = [Gene(uav_id=0, target_id=1, cost=1.0), Gene(uav_id=-1, target_id=3, cost=0.5)]
    i1, i2, i3 = (
        Individual(genes=list(g1)),
        Individual(genes=list(g2)),
        Individual(genes=list(g1)),
    )
    assert _geno_id(i1) == _geno_id(i3)
    assert _geno_id(i1) != _geno_id(i2)


# ---------------------------------------------------------------------------
# 3. 确定性 + 端到端
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rr", [0.0, 0.5, 1.0])
def test_ratio_determinism_end_to_end(rr):
    # 同 seed 两次运行逐位一致（0/1 不耗 RNG；0.5 抽签走同一确定性 rng 流）
    def once():
        ev, cb, *_ = _make_ev()
        res = _solve(ev, cb, repair_replacement=rr)
        return round(float(res.best_fitness), 9), res.best_assignment

    a, b = once(), once()
    assert a == b


@pytest.mark.parametrize(
    "stage_kw",
    [
        dict(),                                   # 默认 0（Baldwin）
        dict(lamarckian=True),                    # 旧布尔 ≡ 1.0
        dict(repair_replacement=0.5),             # 连续比例
    ],
)
def test_run_selective_muas_ratio_end_to_end(stage_kw):
    sig1 = _run(MUASStageConfig(**stage_kw))
    sig2 = _run(MUASStageConfig(**stage_kw))
    assert sig1 == sig2, f"{stage_kw} 档应确定性"


def test_stage_forwarding_resolved_ratio():
    """stage 层解析（含 patrol 保义）经 repair_replacement 透传到求解器。"""
    # stage 层 lamarckian=True → 求解器收到 1.0（run_selective_muas 转发解析值）
    sig_lam = _run(MUASStageConfig(lamarckian=True))
    sig_rr1 = _run(MUASStageConfig(repair_replacement=1.0))
    # 两种设置方式解析结果一致 ⇒ 数值口径一致
    assert sig_lam == sig_rr1
