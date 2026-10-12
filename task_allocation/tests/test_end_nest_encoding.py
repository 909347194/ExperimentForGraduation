# -*- coding: utf-8 -*-
"""终点机巢进编码（论文 §5(1)(2)）的常驻回归测试。

锁定的契约（曾出过 bug，必须有常驻测试守住）：

    ``Individual.cost_vector`` 的 DE 侧布局 = ``[起点 K][巡游 M-K][终点 K]``，
    UAV i 的终止代价槽位于 ``cost_vector[M + i]``（M = 任务数）；
    ``inverse_mapper._select_end_nests`` 必须**从该槽取值**与机巢列做匹配。

旧缺陷：基因在个体中是**交错式**的（``[start_i, tour_i…, term_i, …]``，
总长 M+K），而反映射按「末尾连续 K 个终止槽」去读 ``cost_vector[K+M+i]``，
该下标恒越界 → 取到 nan → ``_pick_nest_by_cost`` 退回最近机巢 →
终点机巢**根本没被 DE 搜索**，退化成解码后的贪心补丁。

本测试用「把终止槽设成**非最近**机巢的代价」来区分两种行为：
修复后会选到那个非最近巢；退化时会选到最近巢。
"""

from __future__ import annotations

import numpy as np

from task_allocation.methods.muas.representation.inverse_mapper import inverse_phi
from task_allocation.methods.muas.representation.repair_rules.terminal_nest import (
    extract_end_nests,
)

K, M, B = 2, 4, 3  # 2 架机 / 4 个任务 / 3 个机巢
HIGH = 900.0


def _cost_matrix() -> np.ndarray:
    """构造可控的 (K+M, M+B) 代价矩阵，使 decode 结果完全确定。

    行：``[0,K)`` UAV 起点行，``[K,K+M)`` 任务行（任务 j → 行 K+j）
    列：``[0,M)`` 任务列，``[M,M+B)`` 机巢列（机巢 b → 列 M+b）
    """
    cm = np.full((K + M, M + B), HIGH)

    # UAV 起点行：UAV0 最近的未分配任务是 0，UAV1 是 2
    cm[0, 0] = 10.0
    cm[1, 2] = 20.0

    # 任务行 → 任务列：任务1 挂 UAV0（从任务0 出发最近），任务3 挂 UAV1
    cm[K + 0, 1] = 50.0
    cm[K + 2, 3] = 50.0

    # 末任务 → 机巢列（关键）
    # UAV0 航次 [0,1] 的末任务是 1 → 行 K+1 = 3：最近巢 = nest1(200)
    cm[K + 1, M + 0] = 400.0
    cm[K + 1, M + 1] = 200.0  # 最近
    cm[K + 1, M + 2] = 600.0
    # UAV1 航次 [2,3] 的末任务是 3 → 行 K+3 = 5：最近巢 = nest0(100)
    cm[K + 3, M + 0] = 100.0  # 最近
    cm[K + 3, M + 1] = 800.0
    cm[K + 3, M + 2] = 300.0
    return cm


def _base_cost_vector() -> np.ndarray:
    """``[起点 K][巡游 M-K][终点 K]``。

    起点分量 10.0 / 20.0：强制 UAV0→任务0、UAV1→任务2。
    巡游分量 50.0 / 50.0：匹配矩阵里那两条 50.0 的巡游边，把航线钉成
    UAV0=[0,1]、UAV1=[2,3]。这一步是必需的 —— 反映射在 §4.1 Rule 2(c)
    下由 cost_vector 的巡游段决定访问顺序，进而决定「末任务」是谁、
    以及最终用哪一行去匹配终点机巢。原先此处填 HIGH 依赖的是历史实现的
    「按任务编号顺序」行为，语义变更后必须显式钉住。
    终点分量由各测试自行覆盖。
    """
    return np.array([10.0, 20.0, 50.0, 50.0, 600.0, 300.0], dtype=float)


def test_end_nest_follows_cost_vector_not_nearest():
    """终止槽 = 非最近巢代价 → 终点选到该巢（证明 z_ub 真的被 DE 搜索）。"""
    rng = np.random.default_rng(0)
    cm = _cost_matrix()
    cv = _base_cost_vector()

    # UAV0 末任务行(3) 上 nest2 = 600；UAV1 末任务行(5) 上 nest2 = 300
    # 两者都不是各自的最近巢（nest1=200 / nest0=100）
    cv[M + 0] = 600.0
    cv[M + 1] = 300.0
    ind = inverse_phi(cv, cm, K, M, "srp", rng=rng, temperature=0.0, n_nests=B)
    ends = extract_end_nests(ind.genes)

    assert ends.get(0) == 2, f"UAV0 终点应为 nest2（代价 600），实际 {ends}"
    assert ends.get(1) == 2, f"UAV1 终点应为 nest2（代价 300），实际 {ends}"
    # 反证：若退化成「最近机巢贪心」，会分别是 nest1 / nest0
    assert (ends.get(0), ends.get(1)) != (1, 0)


def test_end_nest_slot_layout_is_terminal_block():
    """改终止槽 → 终点随之改变（终点确实由 cv[M+i] 决定）。

    注意：终点槽只是**输入的一半**。终点是在「末任务行」上匹配的，而末任务是
    巡游段按 Rule 2(c) 匹配出来的结果，因此巡游段也会间接改变最终的末任务行。
    本测试通过 :func:`_base_cost_vector` 把巡游段钉死，隔离出终止槽这一个变量。
    """
    rng = np.random.default_rng(1)
    cm = _cost_matrix()

    cv_a = _base_cost_vector()
    cv_a[M + 0] = 400.0  # UAV0 → nest0 的代价
    ends_a = extract_end_nests(
        inverse_phi(cv_a, cm, K, M, "srp", rng=rng, temperature=0.0, n_nests=B).genes
    )

    cv_b = _base_cost_vector()
    cv_b[M + 0] = 200.0  # UAV0 → nest1 的代价
    ends_b = extract_end_nests(
        inverse_phi(cv_b, cm, K, M, "srp", rng=rng, temperature=0.0, n_nests=B).genes
    )

    assert ends_a.get(0) == 0, f"终止槽=400 应还原 nest0：{ends_a}"
    assert ends_b.get(0) == 1, f"终止槽=200 应还原 nest1：{ends_b}"


def test_nan_terminal_value_falls_back_to_nearest():
    """终止槽为 nan 时退回最近机巢（记录兜底语义，也是旧 bug 的表现）。"""
    rng = np.random.default_rng(2)
    cm = _cost_matrix()
    cv = _base_cost_vector()
    cv[M + 0] = float("nan")
    cv[M + 1] = float("nan")
    ind = inverse_phi(cv, cm, K, M, "srp", rng=rng, temperature=0.0, n_nests=B)
    ends = extract_end_nests(ind.genes)
    # 最近巢：UAV0 末任务行 3 → nest1(200)；UAV1 末任务行 5 → nest0(100)
    assert ends.get(0) == 1 and ends.get(1) == 0, f"nan 应退回最近机巢：{ends}"
