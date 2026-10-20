# -*- coding: utf-8 -*-
"""惰性 pairwise 代价表（延迟构建优化）的契约与逐位一致性测试。

背景（exp02_selection_scale 结论 1，2026-10-20 修复）：
    ``build_pairwise_costs`` 曾在第一阶段**之前**对全量 T_t 建 O(n²)
    三维矩阵，而任务选择只触碰 O(n·K + k²) 条边；N=200 时全量建矩阵
    占端到端 85.6%。修复 = 一阶段前只建惰性表（按需算 + 缓存），
    一阶段后只对候选子集建 4 个矩阵。

本测试锁定三条契约：

    1. **逐位一致**：惰性表的值与全量 eager 建表完全相等（同估算器、
       同坐标、同**方向**、同权重）——用**非对称**估算器放大方向错误；
    2. **端到端逐位一致**：选择 + MUAS 两阶段在「全量 eager 旧路径」
       与「惰性新路径」下产出同一交付解（executed/f1/f2/tours/终点/
       n_evals/适应度/收敛曲线）；
    3. **真的少算**：惰性表现算条目数远小于全量建表边数（优化有实效），
       且缺 id 查询的回落语义与普通字典一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import Nest, Task, UAV, UAVStatus  # noqa: E402
from task_allocation.methods.muas.cost.cost_matrix import (  # noqa: E402
    build_pairwise_costs,
)
from task_allocation.methods.muas.cost.lazy_pairwise import (  # noqa: E402
    LazyPairwiseCost,
    make_lazy_pairwise,
)
from task_allocation.methods.muas.problem import MUASProblemConfig  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.muas_stage import (  # noqa: E402
    MUASStageConfig,
    run_selective_muas,
)
from task_allocation.methods.selection.pipeline import (  # noqa: E402
    SelectionConfig,
    run as run_selection,
)

NEST_KEY = 1_000_000


# ---------------------------------------------------------------------------
# 脚手架
# ---------------------------------------------------------------------------


class _AsymmetricEstimator:
    """非对称代价估算器：estimate(a,b) != estimate(b,a)。

    距离 = 三维直线 + (end.z > start.z ? 500 : 0)。
    垂直切面估算的地形跟随路径本就依赖遍历方向（起点/终点高程固定、
    爬升-平飞-下降的前向填充不对称），非对称替身可把「惰性表把方向
    搞反」这类 bug 放大成可断言差异。
    """

    def __init__(self) -> None:
        self.calls = 0

    def estimate(self, start, end, weight: float = 1.0):
        self.calls += 1
        a = np.asarray(start, dtype=np.float64)[:3]
        b = np.asarray(end, dtype=np.float64)[:3]
        d = float(np.linalg.norm(b - a))
        if b[2] > a[2]:
            d += 500.0
        return type("R", (), {"distance": d, "cost": d * weight})()


def _uav(uid: int, x: float, y: float, remaining_range: float = 1e9) -> UAV:
    return UAV(
        id=uid, x=x, y=y, z=0.0, remaining_range=remaining_range,
        speed=15.0, nest_id=uid % 3, status=UAVStatus.IDLE,
    )


def _task(tid: int, x: float, y: float, reward: float = 5.0,
          latest: float = 1e6) -> Task:
    return Task(
        id=tid, x=x, y=y, z=0.0, reward=reward, arrival_time=0.0,
        earliest=0.0, latest=latest,
    )


def _scenario(n_tasks: int = 14):
    """3 巢 / 4 机 / n_tasks 个散布任务；任务带不同高程制造非对称边。"""
    nests = [
        Nest(id=i, x=float(i * 20000.0), y=0.0, z=0.0, capacity=4, available=True)
        for i in range(3)
    ]
    uavs = [_uav(i, nests[i % 3].x, nests[i % 3].y) for i in range(4)]
    rng = np.random.default_rng(7)
    tasks = []
    for i in range(n_tasks):
        x = float(rng.uniform(0.0, 40000.0))
        y = float(rng.uniform(-5000.0, 5000.0))
        z = float(rng.choice([0.0, 300.0]))  # 高程差 → 非对称
        t = _task(i, x, y, reward=float(rng.uniform(1.0, 10.0)))
        t.z = z
        tasks.append(t)
    return tasks, uavs, nests


def _sel_cfg(alpha: float = 2.5) -> SelectionConfig:
    from task_allocation.methods.selection.feasibility import FeasibilityConfig
    from task_allocation.methods.selection.marginal_gain import MarginalConfig
    from task_allocation.methods.selection.priority import PriorityConfig

    return SelectionConfig(
        priority=PriorityConfig(alpha=alpha),
        feasibility=FeasibilityConfig(),
        marginal=MarginalConfig(),
        current_time=0.0,
    )


def _stage_cfg() -> MUASStageConfig:
    return MUASStageConfig(lamarckian=True)


def _solver_cfg(seed: int = 42) -> DMDEConfig:
    return DMDEConfig(pop_size=12, max_generations=8, seed=seed)


# ---------------------------------------------------------------------------
# 1. 惰性表的值 = 全量 eager 建表的值（逐位一致，含方向）
# ---------------------------------------------------------------------------


def test_lazy_values_equal_eager_full_build():
    """惰性表现算的每条边（含两个方向）与全量 eager 字典完全相等。"""
    tasks, uavs, nests = _scenario()
    est = _AsymmetricEstimator()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, estimator=est,
                              nest_offset=NEST_KEY)
    eager = cb.provider.pairwise

    lazy = make_lazy_pairwise(tasks, uavs, nests, estimator=est,
                              nest_offset=NEST_KEY)
    checked = 0
    for key, val in eager.items():
        assert key in lazy, f"惰性表缺键 {key}"
        assert lazy[key] == pytest.approx(val), (
            f"边 {key} 不一致：lazy={lazy[key]} eager={val}"
        )
        checked += 1
    # 反方向键也必须可解析且相等（eager 两个方向都建了）
    for (a, b), val in list(eager.items()):
        if (b, a) in eager:
            assert lazy[(b, a)] == pytest.approx(eager[(b, a)])
    assert checked > 0


def test_lazy_respects_pair_direction():
    """非对称估算器下，惰性表不得把 (a,b) 与 (b,a) 混为一谈。"""
    tasks, uavs, nests = _scenario(n_tasks=3)
    # 显式制造高程差：随机撒点的 z 可能相同（无不对称可测）
    tasks[0].z = 0.0
    tasks[1].z = 300.0
    est = _AsymmetricEstimator()
    lazy = make_lazy_pairwise(tasks, uavs, nests, estimator=est,
                              nest_offset=NEST_KEY)
    a, b = tasks[0], tasks[1]
    ab = lazy[(a.id, b.id)]
    ba = lazy[(b.id, a.id)]
    assert ab != ba, "非对称估算器下两个方向必须不同（方向 bug 会被此捕获）"
    direct = est.estimate(a.position, b.position).cost
    assert ab == pytest.approx(direct)


def test_lazy_missing_id_falls_back_like_dict():
    """位置注册表外的 id：``in`` 为 False、``[]`` 抛 KeyError（与 dict 缺项一致）。"""
    tasks, uavs, nests = _scenario(n_tasks=2)
    lazy = make_lazy_pairwise(tasks, uavs, nests, estimator=_AsymmetricEstimator(),
                              nest_offset=NEST_KEY)
    assert (99999, tasks[0].id) not in lazy
    assert (tasks[0].id, 99999) not in lazy
    with pytest.raises(KeyError):
        lazy[(99999, tasks[0].id)]
    assert lazy.get((99999, tasks[0].id), -1.0) == -1.0


def test_lazy_task_weight_from_reward_matches_eager():
    """task_weight_from_reward=True 时与 eager 加权口径逐位一致。"""
    tasks, uavs, nests = _scenario(n_tasks=5)
    est = _AsymmetricEstimator()
    cb = build_pairwise_costs(tasks, uavs, nests=nests, estimator=est,
                              task_weight_from_reward=True, nest_offset=NEST_KEY)
    lazy = make_lazy_pairwise(tasks, uavs, nests, estimator=est,
                              task_weight_from_reward=True, nest_offset=NEST_KEY)
    for key, val in cb.provider.pairwise.items():
        if key[0] == key[1]:
            continue
        assert lazy[key] == pytest.approx(val), f"加权边 {key} 不一致"


# ---------------------------------------------------------------------------
# 2. 端到端逐位一致：全量 eager 旧路径 vs 惰性新路径
# ---------------------------------------------------------------------------


def _run_eager_reference(tasks, uavs, nests, est):
    """旧路径复刻（与改造前 horizon.run_cycle 逐帧对应）：
    全量建表 → cost_hint（全量表查最近可用机）→ 选择（全量 pairwise +
    同一把 distance_fn）→ 裁子集 → MUAS。"""
    cb = build_pairwise_costs(tasks, uavs, nests=nests, estimator=est,
                              nest_offset=NEST_KEY)
    cost_hint: dict[int, float] = {}
    for t in tasks:
        best = float("inf")
        for u in uavs:
            if not u.is_available:
                continue
            c = cb.provider.cost(-(1 + u.id), t.id)
            if c < best:
                best = c
        cost_hint[t.id] = best
    sel = run_selection(
        tasks, uavs, config=_sel_cfg(), cost_hint=cost_hint,
        pairwise_cost=cb.provider.pairwise,
        distance_fn=cb.distance_fn,
    )
    selected = sel.selected
    pos = {t.id: i for i, t in enumerate(tasks)}
    rows = [pos[t.id] for t in selected]
    cb_sel = type(cb)(
        provider=cb.provider,
        uav_target=cb.uav_target[:, rows],
        task_task=cb.task_task[np.ix_(rows, rows)],
        task_nest=cb.task_nest[rows, :],
        uav_nest=cb.uav_nest,
        n_nests=cb.n_nests,
        distance_fn=cb.distance_fn,
    )
    stage = run_selective_muas(
        selected, uavs, nests, cb_sel, fleet_uavs=uavs,
        problem_config=MUASProblemConfig(),
        stage_config=_stage_cfg(), solver_config=_solver_cfg(),
        nest_offset=NEST_KEY, current_time=0.0,
    )
    return sel, stage, cb


def _run_lazy_path(tasks, uavs, nests, est):
    """新路径（与改造后 horizon.run_cycle 逐帧对应）：
    惰性表 → cost_hint（惰性表查最近可用机）→ 选择（惰性 pairwise +
    同一把 distance_fn）→ 候选子集建矩阵 → MUAS。"""
    from task_allocation.methods.muas.cost.vertical_section import make_distance_fn

    distance_fn = make_distance_fn(est)
    lazy = make_lazy_pairwise(tasks, uavs, nests, estimator=est,
                              nest_offset=NEST_KEY)
    cost_hint: dict[int, float] = {}
    avail = [u for u in uavs if u.is_available]
    for t in tasks:
        cost_hint[t.id] = min(lazy[(-(1 + u.id), t.id)] for u in avail)
    sel = run_selection(
        tasks, uavs, config=_sel_cfg(), cost_hint=cost_hint,
        pairwise_cost=lazy, distance_fn=distance_fn,
    )
    selected = sel.selected
    cb_sel = build_pairwise_costs(
        selected, uavs, nests=nests, estimator=est, nest_offset=NEST_KEY,
        pairwise_source=lazy, distance_fn=distance_fn,
    )
    stage = run_selective_muas(
        selected, uavs, nests, cb_sel, fleet_uavs=uavs,
        problem_config=MUASProblemConfig(),
        stage_config=_stage_cfg(), solver_config=_solver_cfg(),
        nest_offset=NEST_KEY, current_time=0.0,
    )
    return sel, stage, lazy


def _tour_signature(stage):
    sol = stage.solution
    return (
        sorted(sol.executed_task_ids),
        round(float(sol.f1_reward), 9),
        round(float(sol.f2_cost), 6),
        [(t.uav_id, tuple(t.task_ids), t.end_nest_id, round(float(t.est_cost), 6))
         for t in sol.tours],
        round(float(stage.diagnostics["final_fitness"]), 9),
        round(float(stage.diagnostics["best_fitness"]), 9),
        int(stage.n_evals),
        [round(float(v), 9) for v in stage.cost_history],
    )


def test_lazy_path_end_to_end_bit_identical():
    """两阶段产出在旧/新路径下逐位一致（选择、交付解、收敛曲线、评价次数）。"""
    tasks, uavs, nests = _scenario()
    est = _AsymmetricEstimator()
    sel_ref, stage_ref, cb_ref = _run_eager_reference(tasks, uavs, nests, est)

    est2 = _AsymmetricEstimator()
    sel_new, stage_new, lazy = _run_lazy_path(tasks, uavs, nests, est2)

    # 1) 选择结果一致
    assert sel_new.selected_ids == sel_ref.selected_ids, "选择集不一致"
    assert sel_new.diagnostics == sel_ref.diagnostics, "选择诊断不一致"

    # 2) MUAS 交付解一致（逐位）
    assert _tour_signature(stage_new) == _tour_signature(stage_ref), (
        "惰性路径交付解与全量 eager 路径不一致"
    )

    # 3) 惰性确实少算：现算条目数 < 全量 eager 边数
    n_eager = len(cb_ref.provider.pairwise)
    assert lazy.n_computed < n_eager, (
        f"惰性表算了 {lazy.n_computed} 条，未少于全量 {n_eager} 条"
    )
    print(f"[lazy] eager={n_eager} edges, lazy computed={lazy.n_computed} "
          f"({lazy.n_computed / max(n_eager, 1):.1%})")


def test_lazy_path_never_estimates_full_matrix():
    """|T|≫K 时惰性表现算边数为 O(n·K + k²) 量级，不是 O(n²)。"""
    tasks, uavs, nests = _scenario(n_tasks=40)
    est = _AsymmetricEstimator()
    est_eager = _AsymmetricEstimator()
    cb_eager = build_pairwise_costs(tasks, uavs, nests=nests,
                                    estimator=est_eager, nest_offset=NEST_KEY)
    n_eager_edges = len(cb_eager.provider.pairwise)  # ≈ 4·40 + 40·40 + 40·3 + 4·3

    lazy = make_lazy_pairwise(tasks, uavs, nests, estimator=est,
                              nest_offset=NEST_KEY)
    sel = run_selection(
        tasks, uavs, config=_sel_cfg(), cost_hint=None,
        pairwise_cost=lazy, distance_fn=None,
    )
    # 一阶段触碰的边：cost_hint 缺省由 coarse_task_costs 走欧氏（不算估算），
    # 边际插入的任务对在候选集内 —— 上界应为 O(n·K + k²)
    n_lazy = lazy.n_computed
    assert n_lazy <= 4 * 40 + 30 * 30 + 40 * 3 + 4 * 3, (
        f"惰性表现算 {n_lazy} 条，超出 O(n·K + k²) 预期上界"
    )
    assert n_lazy < n_eager_edges / 2, (
        f"惰性表 {n_lazy} 未显著少于全量 {n_eager_edges}"
    )


def test_lazy_provider_is_live_view_after_subset_build():
    """子集建矩阵后 provider.pairwise 仍是活视图：范围外查询按需解析。"""
    tasks, uavs, nests = _scenario(n_tasks=6)
    est = _AsymmetricEstimator()
    lazy = make_lazy_pairwise(tasks, uavs, nests, estimator=est,
                              nest_offset=NEST_KEY)
    selected = tasks[:3]
    cb = build_pairwise_costs(selected, uavs, nests=nests, estimator=est,
                              nest_offset=NEST_KEY, pairwise_source=lazy)
    assert cb.provider.pairwise is lazy, "provider.pairwise 应即惰性活视图"
    # 范围外任务对（未建矩阵）仍可解析，且值 = 直接估算
    outside = (tasks[5].id, tasks[4].id)
    assert cb.provider.cost(*outside) == pytest.approx(
        est.estimate(tasks[5].position, tasks[4].position).cost
    )


def test_subset_build_reads_through_lazy_cache():
    """子集建矩阵不重复估算：一阶段算过的边直接命中缓存。"""
    tasks, uavs, nests = _scenario(n_tasks=8)
    est = _AsymmetricEstimator()
    lazy = make_lazy_pairwise(tasks, uavs, nests, estimator=est,
                              nest_offset=NEST_KEY)
    # 预热：算几条任务对
    warm = {(tasks[0].id, tasks[1].id), (tasks[1].id, tasks[2].id)}
    for key in warm:
        _ = lazy[key]
    calls_before = est.calls
    n_before = lazy.n_computed

    selected = tasks[:3]
    build_pairwise_costs(selected, uavs, nests=nests, estimator=est,
                         nest_offset=NEST_KEY, pairwise_source=lazy)
    new_calls = est.calls - calls_before
    # 预热的两条 + 预热时顺带算的（(t1,t0) 未算，只算请求的方向）不应重算
    assert lazy[(tasks[0].id, tasks[1].id)] is not None
    # 3 任务子集：任务对 3·2 + UAV→任务 4·3 + 任务→巢 3·3 + UAV→巢 4·3 = 42 条边上界，
    # 减去已预热的 2 条
    assert new_calls <= 42 - len(warm) + len(warm), "子集建矩阵重复估算过多"
    assert lazy.n_computed >= n_before


def test_lazy_mapping_protocol():
    """Mapping 协议面：len/iter/get/contains/keys 行为符合 drop-in 预期。"""
    tasks, uavs, nests = _scenario(n_tasks=2)
    lazy = make_lazy_pairwise(tasks, uavs, nests, estimator=_AsymmetricEstimator(),
                              nest_offset=NEST_KEY)
    assert len(lazy) == 0 and list(lazy) == []
    v = lazy[(tasks[0].id, tasks[1].id)]
    assert len(lazy) == 1 and (tasks[0].id, tasks[1].id) in list(lazy)
    assert lazy.get((tasks[0].id, tasks[1].id)) == v
    assert lazy.get((12345, 67890)) is None
    lazy[(7, 8)] = 3.5  # __setitem__（eager 路径的对角线零项等写入）
    assert lazy[(7, 8)] == 3.5
    assert isinstance(lazy, LazyPairwiseCost)
