# -*- coding: utf-8 -*-
"""repair_rules 测试：三种分配模型的结构不变量（论文规则 3.1 / 3.2 / 3.3）。

    balanced   (K == M)  U 与 T 均不重复，一一对应
    overloaded (K >  M)  U 不重复、T 可重复、每个 T 至少出现一次
    srp        (K <  M)  U 可重复（巡游）、T 不重复、每个 U 至少出现一次

同时覆盖：
    - 屏蔽函数（mask_balanced / mask_overloaded / mask_srp_upper）
    - 温度自适应最近邻（temperature → top_k 单调）
    - 无效值修补（repair_invalid）
    - 终点机巢基因的提取（extract_end_nests，论文 §5(2)）
"""

from __future__ import annotations

import unittest

import numpy as np

from task_allocation.methods.muas.representation.encoder import Gene, PopulationEncoder
from task_allocation.methods.muas.representation.inverse_mapper import inverse_phi
from task_allocation.methods.muas.representation.repair_rules.invalid_mutator import (
    repair_invalid,
)
from task_allocation.methods.muas.representation.repair_rules.nearest_match import (
    nearest_match_adaptive,
    temperature_to_top_k,
)
from task_allocation.methods.muas.representation.repair_rules.terminal_nest import (
    extract_end_nests,
)
from task_allocation.methods.muas.representation.repair_rules.unique_filter import (
    mask_balanced,
    mask_overloaded,
    mask_srp_upper,
)


def build_cost_matrix(n_uavs: int, n_targets: int, n_nests: int = 0, seed: int = 0) -> np.ndarray:
    """构造 (K+M, M+B) 扩展代价矩阵（欧氏距离形状，保证非负有限）。"""
    rng = np.random.default_rng(seed)
    rows = n_uavs + n_targets
    cols = n_targets + n_nests
    pts_r = rng.random((rows, 2)) * 100.0
    pts_c = rng.random((cols, 2)) * 100.0
    d = np.linalg.norm(pts_r[:, None, :] - pts_c[None, :, :], axis=-1)
    return np.asarray(d, dtype=float)


def assignment_pairs(ind) -> list[tuple[int, int]]:
    return [(u, t) for u, t in ind.assignment if u >= 0]


class TestModelTypeInvariants(unittest.TestCase):
    """三种模型的结构不变量（随机代价向量 × 多次解码）。"""

    N_SEEDS = 20

    def _decode_many(self, n_uavs, n_targets, model_type, n_nests=0):
        cm = build_cost_matrix(n_uavs, n_targets, n_nests, seed=7)
        rng = np.random.default_rng(42)
        out = []
        for _ in range(self.N_SEEDS):
            cv = rng.random(n_uavs + n_targets + (n_uavs if n_nests else 0))
            out.append(
                inverse_phi(cv, cm, n_uavs, n_targets, model_type, rng=rng, temperature=0.6, n_nests=n_nests)
            )
        return out

    def test_balanced_one_to_one(self):
        inds = self._decode_many(4, 4, "balanced")
        for ind in inds:
            pairs = assignment_pairs(ind)
            us = [u for u, _ in pairs]
            ts = [t for _, t in pairs]
            self.assertEqual(len(us), len(set(us)), "balanced: U 不重复")
            self.assertEqual(len(ts), len(set(ts)), "balanced: T 不重复")
            self.assertEqual(sorted(ts), [0, 1, 2, 3], "balanced: 一一对应全覆盖")

    def test_overloaded_uav_unique_targets_covered(self):
        inds = self._decode_many(6, 3, "overloaded")
        for ind in inds:
            pairs = assignment_pairs(ind)
            us = [u for u, _ in pairs]
            self.assertEqual(len(us), len(set(us)), "overloaded: U 不重复")
            self.assertEqual(sorted(set(t for _, t in pairs)), [0, 1, 2], "overloaded: 每个 T 至少出现一次")

    def test_srp_targets_unique_each_uav_present(self):
        inds = self._decode_many(3, 8, "srp")
        for ind in inds:
            pairs = assignment_pairs(ind)
            ts = [t for _, t in pairs]
            self.assertEqual(len(ts), len(set(ts)), "srp: T 不重复")
            routes = ind.srp_routes
            self.assertEqual(sorted(routes.keys()), [0, 1, 2], "srp: 每个 U 至少出现一次")
            total = sum(len(v) for v in routes.values())
            self.assertEqual(total, 8, "srp: 巡游覆盖全部任务")

    def test_srp_with_nests_emits_terminal_genes(self):
        cm = build_cost_matrix(3, 6, 2, seed=3)
        rng = np.random.default_rng(5)
        for _ in range(self.N_SEEDS):
            cv = rng.random(3 + 6 + 3)
            ind = inverse_phi(cv, cm, 3, 6, "srp", rng=rng, temperature=0.6, n_nests=2)
            ends = extract_end_nests(ind.genes)
            self.assertEqual(len(ends), 3, "每架 UAV 都应有终点机巢")
            for uav_id, nest_idx in ends.items():
                if nest_idx is not None:
                    self.assertIn(nest_idx, (0, 1), "终点必须是合法机巢下标")

    def test_decode_never_raises_on_random_vectors(self):
        for model_type, k, m in (("balanced", 5, 5), ("overloaded", 7, 4), ("srp", 3, 9)):
            cm = build_cost_matrix(k, m, seed=11)
            rng = np.random.default_rng(0)
            for _ in range(self.N_SEEDS):
                cv = rng.random(k + m)
                ind = inverse_phi(cv, cm, k, m, model_type, rng=rng)
                self.assertTrue(ind.assignment, f"{model_type} 解码不应产出空分配")

    def test_no_out_of_range_ids_regression(self):
        """回归：balanced 曾有近半数分配携带不存在的 uav_id（目标行未屏蔽）。"""
        for model_type, k, m, b in (
            ("balanced", 3, 3, 0),
            ("overloaded", 5, 2, 0),
            ("srp", 3, 8, 2),
            ("balanced", 1, 1, 0),
        ):
            cm = build_cost_matrix(k, m, b, seed=13)
            rng = np.random.default_rng(1)
            for i in range(self.N_SEEDS):
                cv = rng.random(k + m + (k if b else 0))
                cv[i % len(cv)] = -1.0  # 强制走 invalid_indices 修补分支
                ind = inverse_phi(cv, cm, k, m, model_type, rng=rng, temperature=0.3, n_nests=b)
                for u, t in ind.assignment:
                    self.assertTrue(0 <= u < k, f"{model_type}: uav_id {u} 越界 (K={k})")
                    self.assertTrue(0 <= t < m, f"{model_type}: target_id {t} 越界 (M={m})")


class TestMaskFunctions(unittest.TestCase):
    def test_mask_balanced_blocks_row_and_col(self):
        mask = np.zeros((3, 3), dtype=bool)
        mask_balanced(mask, 1, 2)
        self.assertTrue(mask[1, :].all(), "balanced 屏蔽整行（U 不重复）")
        self.assertTrue(mask[:, 2].all(), "balanced 屏蔽整列（T 不重复）")

    def test_mask_overloaded_blocks_row_only(self):
        mask = np.zeros((3, 3), dtype=bool)
        mask_overloaded(mask, 1, 2)
        self.assertTrue(mask[1, :].all(), "overloaded 屏蔽整行（U 不重复）")
        self.assertFalse(mask[0, 2] and mask[2, 2], "overloaded 不屏蔽列（T 可重复）")

    def test_mask_srp_upper_blocks_column(self):
        # mask_srp_upper 需要完整 (K+M, M+B) 矩阵：内部按 n_uavs+col 索引目标行
        cm = build_cost_matrix(1, 2, 0, seed=1)  # shape (3, 2)
        mask = np.zeros_like(cm, dtype=bool)
        mask_srp_upper(cm, mask, 0, 1, n_uavs=1)
        self.assertTrue(mask[:, 1].all(), "srp 屏蔽整列（T 不重复）")


class TestNearestMatch(unittest.TestCase):
    def test_top_k_increases_with_temperature(self):
        ks = [temperature_to_top_k(t) for t in (0.0, 0.25, 0.5, 0.75, 1.0)]
        self.assertEqual(ks, sorted(ks), "温度越高 top_k 越大（探索）")
        self.assertGreaterEqual(ks[0], 1)

    def test_low_temperature_picks_argmin(self):
        cm = np.array([[5.0, 1.0, 9.0]])
        mask = np.zeros_like(cm, dtype=bool)
        rng = np.random.default_rng(0)
        hits = set()
        for _ in range(30):
            r = nearest_match_adaptive(0.0, cm, mask, temperature=0.01, rng=rng)
            hits.add(r[1])
        self.assertEqual(hits, {1}, "低温应稳定取最小代价列")

    def test_returns_none_when_all_masked(self):
        cm = np.ones((1, 2))
        mask = np.ones_like(cm, dtype=bool)
        self.assertIsNone(nearest_match_adaptive(0.0, cm, mask, rng=np.random.default_rng(0)))


class TestRepairInvalid(unittest.TestCase):
    def test_returns_reachable_pairs(self):
        cm = build_cost_matrix(3, 3, seed=2)
        mask = np.zeros_like(cm, dtype=bool)
        out = repair_invalid(cm, mask, [0, 1], "balanced", n_uavs=3)
        self.assertIsInstance(out, list)
        for uav_id, tgt_id, cost in out:
            self.assertTrue(0 <= uav_id < 3, f"uav_id 越界：{uav_id}")
            self.assertTrue(0 <= tgt_id < 3, f"target_id 越界：{tgt_id}")
            self.assertTrue(np.isfinite(cost))
            self.assertGreaterEqual(cost, 0.0)

    def test_never_emits_out_of_range_ids(self):
        """回归：repair_invalid 曾从目标行/机巢列取位置，产出越界 id。"""
        for model, k, m in (("balanced", 3, 3), ("overloaded", 5, 2)):
            cm = build_cost_matrix(k, m, seed=4)
            for seed in range(50):
                rng_mask = np.zeros_like(cm, dtype=bool)
                import random as _r
                _r.seed(seed)
                out = repair_invalid(cm, rng_mask, [0, 1, 2], model, n_uavs=k)
                for uav_id, tgt_id, _ in out:
                    self.assertLess(uav_id, k, f"{model}: uav_id {uav_id} >= {k}")
                    self.assertLess(tgt_id, m, f"{model}: target_id {tgt_id} >= {m}")


class TestTerminalGenes(unittest.TestCase):
    def test_extract_without_terminal_genes(self):
        genes = [Gene(uav_id=0, target_id=1, cost=1.0), Gene(uav_id=-1, target_id=2, cost=1.0)]
        ends = extract_end_nests(genes)
        self.assertTrue(all(v is None for v in ends.values()))

    def test_terminal_gene_carries_nest(self):
        genes = [
            Gene(uav_id=0, target_id=1, cost=1.0),
            Gene(uav_id=-2, target_id=0, cost=2.0),
        ]
        ends = extract_end_nests(genes)
        self.assertEqual(ends.get(0), 0, "终止基因应携带终点机巢下标")

    def test_population_encoder_generates_terminal_genes(self):
        cm = build_cost_matrix(2, 5, 3, seed=9)
        enc = PopulationEncoder(cm, 2, 5, n_nests=3)
        for ind in enc.generate(10, seed=1):
            ends = extract_end_nests(ind.genes)
            self.assertEqual(len(ends), 2)
            for v in ends.values():
                if v is not None:
                    self.assertIn(v, (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
