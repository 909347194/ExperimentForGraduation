# -*- coding: utf-8 -*-
"""访问顺序进入进化搜索空间 —— Ming 2017 §4.1 Rule 1 + Rule 2(c) 常驻回归测试。

锁定的契约（历史缺陷，必须有常驻测试守住）：

    ``Individual.cost_vector`` 的巡游段 ``[K, N)`` 必须被反映射**真实消费** ——
    只改变该区段，解码出的「任务在航线内的访问顺序」必须随之改变。

历史缺陷：``_inverse_phi_srp`` 第二阶段此前只做「按任务编号顺序取任务 +
贪心最近追加」，从不读 ``cost_vector[K, N)``；随后 ``_recalculate_srp_tour_costs``
又把巡游基因的 cost 重算为实际前驱的真值，把这条通路彻底焊死。后果是 DE 的
``N`` 维中有 ``N-K`` 维写而不读，宣称的「归属 + 顺序统一编码」里顺序这一环
是虚的（实测敏感性 0/200）。

方案 A 口径：基因总数恒为 ``N``（终点机巢不进编码，由解码后内层最小费用流
精确求解），``cost_vector`` 布局为 ``[0, K)`` 起始 + ``[K, N)`` 巡游。
"""

from __future__ import annotations

import numpy as np

from task_allocation.methods.muas.representation.inverse_mapper import inverse_phi
from task_allocation.methods.muas.representation.repair_rules.terminal_nest import (
    split_segments,
)


def _routes_of(cv: np.ndarray, cm: np.ndarray, K: int, N: int, seed: int = 0, temperature: float = 0.0):
    """解码并按 UAV 下标返回规范化后的航线（tuple，便于比较）。"""
    ind = inverse_phi(
        np.asarray(cv, dtype=float).copy(), cm, K, N, "srp",
        rng=np.random.default_rng(seed), temperature=temperature,
    )
    routes = split_segments(ind.genes)
    return tuple((u, tuple(v)) for u, v in sorted(routes.items())), ind


def _random_cost_matrix(K: int, N: int, seed: int = 0) -> np.ndarray:
    """欧氏距离形状的 (K+N, N) 代价矩阵，保证代价非负且有限。"""
    rng = np.random.default_rng(seed)
    pts_r = rng.random((K + N, 2)) * 100.0
    pts_c = rng.random((N, 2)) * 100.0
    return np.asarray(
        np.linalg.norm(pts_r[:, None, :] - pts_c[None, :, :], axis=-1), dtype=float
    )


# ── 受控代价矩阵：用于逐位验证「最近邻匹配」真的选中指定巡游边 ──
K2, N2 = 2, 4
HIGH = 2000.0


def _controlled_cost_matrix() -> np.ndarray:
    """(K+N, N) = (6, 4) 矩阵，只有四条边的代价被刻意设置，其余为 HIGH。

    行 ``0..1``   UAV 起点行
    行 ``2..5``   任务行（任务 j → 行 K+j = 2+j）
    """
    cm = np.full((K2 + N2, N2), HIGH)
    cm[0, 0] = 10.0   # UAV0 → 任务0（配合 cv[0]=10）
    cm[1, 1] = 20.0   # UAV1 → 任务1（配合 cv[1]=20）
    # 巡游边：任务0/1 分别作为各自航线当前任务的出发行
    cm[2, 2] = 500.0  # 任务0 → 任务2
    cm[2, 3] = 900.0  # 任务0 → 任务3
    cm[3, 2] = 700.0  # 任务1 → 任务2
    cm[3, 3] = 300.0  # 任务1 → 任务3
    return cm


def test_tour_slot_selects_cost_nearest_to_cv():
    """巡游位的 cv 精确选中代价最接近的巡游边（Rule 1 的直接验证）。

    第一阶段已被 cv[0]=10 / cv[1]=20 钉成 UAV0→任务0、UAV1→任务1，
    剩余任务为 {2, 3}。候选巡游边代价矩阵（行=当前任务，列=候选任务）::

                任务2   任务3
        任务0    500     900
        任务1    700     300

    cv[K+0] 取不同值应分别命中不同格子。
    """
    cm = _controlled_cost_matrix()

    # cv[2]=700 → |700-700|=0 命中「任务1→任务2」，随后 cv[3]=900 命中「任务0→任务3」
    routes_a, _ = _routes_of([10.0, 20.0, 700.0, 900.0], cm, K2, N2)
    assert routes_a == ((0, (0, 3)), (1, (1, 2))), f"cv[2]=700 时应为 U0=[0,3] / U1=[1,2]，实际 {routes_a}"

    # cv[2]=290 → 最近的是「任务1→任务3」(300)，随后 cv[3]=500 命中「任务0→任务2」
    routes_b, _ = _routes_of([10.0, 20.0, 290.0, 500.0], cm, K2, N2)
    assert routes_b == ((0, (0, 2)), (1, (1, 3))), f"cv[2]=290 时应为 U0=[0,2] / U1=[1,3]，实际 {routes_b}"

    # 两者必须不同，否则说明巡游段没有真正生效
    assert routes_a != routes_b, "巡游段未能改变访问顺序，反映射已退化"


def test_tour_segment_materially_changes_routes():
    """只改 cost_vector[K, N)，访问顺序必须高比例响应（历史 0/200 → 现应 >50%）。"""
    K, N = 3, 8
    cm = _random_cost_matrix(K, N, seed=3)
    base_cv = np.random.default_rng(11).uniform(1.0, 150.0, size=N)

    base_routes, _ = _routes_of(base_cv, cm, K, N)
    rng = np.random.default_rng(12)
    changed = 0
    trials = 200
    for _ in range(trials):
        v = base_cv.copy()
        v[K:N] = rng.uniform(1.0, 150.0, size=N - K)  # 只动巡游段
        r, _ = _routes_of(v, cm, K, N)
        if r != base_routes:
            changed += 1

    assert changed > 0.5 * trials, (
        f"巡游段敏感性过低：{changed}/{trials} —— cost_vector[K, N) 未被消费"
    )


def test_distinct_tour_vectors_yield_many_distinct_routes():
    """不同的巡游向量应产生大量不同航线组合（排除退化为单一结果的可能）。"""
    K, N = 3, 8
    cm = _random_cost_matrix(K, N, seed=5)
    rng = np.random.default_rng(13)
    seen = set()
    for _ in range(200):
        cv = rng.uniform(1.0, 150.0, size=N)
        routes, _ = _routes_of(cv, cm, K, N)
        seen.add(routes)
    assert len(seen) >= 20, f"200 个随机 cost_vector 只产生 {len(seen)} 种航线，多样性不足"


def test_temperature_zero_is_deterministic():
    """temperature < 0.1 时同一 cost_vector 必须解码出完全相同的结果。"""
    K, N = 3, 9
    cm = _random_cost_matrix(K, N, seed=7)
    cv = np.random.default_rng(21).uniform(1.0, 150.0, size=N)

    a, _ = _routes_of(cv, cm, K, N, seed=0, temperature=0.0)
    b, _ = _routes_of(cv, cm, K, N, seed=99, temperature=0.0)
    assert a == b, "temperature=0 下结果必须确定，与 rng 种子无关"


def test_invalid_tour_value_falls_back_to_cheapest_edge():
    """巡游位为 nan / 负数时退化为最小代价边（Rule 3 兜底语义）。

    候选矩阵以众 ``任务0/1 → 任务2/3`` 的最小值 300（任务1→任务3）为首选项。
    """
    cm = _controlled_cost_matrix()
    for bad in (float("nan"), -5.0):
        routes, _ = _routes_of([10.0, 20.0, bad, bad], cm, K2, N2)
        # 第一步取全局最小边 300（任务1→任务3），第二步剩下任务2 落到 UAV0
        assert routes == ((0, (0, 2)), (1, (1, 3))), (
            f"无效 cv={bad} 应退回最小代价边，实际 {routes}"
        )


def test_srp_invariants_hold_after_rule_2c():
    """Rule 2(c) 不得破坏 srp 结构不变量：任务不重不漏、每机至少一段、长度 N。

    方案 A 口径：终点机巢不进编码，代价矩阵只有任务列 ``(K+N, N)``，
    基因总数恒为 ``N``（无终止基因）。
    """
    for K, N in ((2, 5), (3, 8), (4, 9)):
        cm = _random_cost_matrix(K, N, seed=K * 10 + N)

        rng = np.random.default_rng(31)
        for _ in range(25):
            cv = rng.uniform(1.0, 150.0, size=N)
            temperature = float(rng.choice([0.0, 0.4, 0.9]))
            ind = inverse_phi(
                cv, cm, K, N, "srp",
                rng=np.random.default_rng(int(rng.integers(1 << 30))),
                temperature=temperature,
            )
            routes = split_segments(ind.genes)
            targets = [t for seg in routes.values() for t in seg]
            assert sorted(targets) == list(range(N)), (
                f"K={K},N={N}: 任务必须恰好覆盖一次，实际 {sorted(targets)}"
            )
            assert sorted(routes.keys()) == list(range(K)), (
                f"K={K},N={N}: 每架 UAV 都应有航次段，实际 {sorted(routes.keys())}"
            )
            assert len(ind.genes) == N, (
                f"K={K},N={N}: 基因数应为 {N}，实际 {len(ind.genes)}"
            )


# ── 不可达（inf）候选：不得污染 softmax、不得崩溃 ──────────────────────

def test_unreachable_targets_never_crash_softmax():
    """某 UAV 对**所有**未分配任务都不可达时，第一阶段 softmax 不得产生 NaN。

    历史缺陷：``_inverse_phi_srp`` 第一阶段直接对 ``|cm[row, available] - cv|``
    做 softmax。若该行全为 ``inf``，则 ``logits`` 全 ``-inf``，
    ``logits.max() = -inf`` → ``(-inf) - (-inf) = nan`` → 概率全 NaN →
    ``rng.choice`` 抛 ``ValueError: Probabilities contain NaN``。

    同族缺陷在 ``nearest_match_adaptive`` 中已有 inf 防护（standard 路径），
    SRP 第一阶段此前漏掉 —— 本测试锁住两处口径一致。

    可达性的裁决权在解码/修复端（``_match_tour_edge`` 的 finite 过滤与
    ``muas_stage._repair_range`` 改派），采样端只须健壮兜底、不崩。
    """
    K, N = 2, 4
    cm = np.array(
        [
            [100.0, 200.0, 300.0, 400.0],  # UAV0 → t0..t3（全可达）
            [np.inf] * N,                   # UAV1 对全部任务不可达
            [0.0, 10.0, 20.0, 30.0],       # 任务行 t0..t3
            [10.0, 0.0, 15.0, 25.0],
            [20.0, 15.0, 0.0, 5.0],
            [30.0, 25.0, 5.0, 0.0],
        ]
    )
    cv = np.array([150.0, 150.0, 12.0, 8.0])
    for temperature in (0.9, 0.5, 0.2, 0.05):
        ind = inverse_phi(
            cv.copy(), cm, K, N, "srp",
            rng=np.random.default_rng(3), temperature=temperature,
        )
        routes = split_segments(ind.genes)
        targets = [t for seg in routes.values() for t in seg]
        assert sorted(targets) == list(range(N)), (
            f"temperature={temperature}: 任务必须恰好覆盖一次，实际 {sorted(targets)}"
        )
        assert len(ind.genes) == N


def test_unreachable_targets_excluded_from_sampling():
    """可达任务必须优先于不可达任务被选中（不可达不得因代价 0 而受偏爱）。

    UAV0 到 t0 不可达（inf）、到 t1/t2/t3 可达且 t1 最近。
    ``cv`` 取 t1 的代价 → 必须选中可达的 t1，不得被 inf 污染成 NaN 或误选 t0。
    """
    K, N = 1, 4
    cm = np.array(
        [
            [np.inf, 100.0, 500.0, 900.0],  # UAV0：t0 不可达，t1 最近
            [0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 0.0],
        ]
    )
    cv = np.array([100.0, 0.0, 0.0, 0.0])  # cv[0]=100 → 最近的是 t1
    for seed in range(12):
        ind = inverse_phi(
            cv.copy(), cm, K, N, "srp",
            rng=np.random.default_rng(seed), temperature=0.8,
        )
        starts = [g.target_id for g in ind.genes if g.uav_id >= 0]
        assert starts, "至少应有一个起始基因"
        assert starts[0] != 0, (
            f"seed={seed}: UAV0 对 t0 不可达，起始任务不得选 t0，实际 {starts}"
        )
