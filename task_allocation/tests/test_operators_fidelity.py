# -*- coding: utf-8 -*-
"""算子保真度核对：把 Zhao Ming 等(2017) DMDE 的公式与本实现逐条比对。

对照点（论文第 3 章）：
    式 (3-9)  动态交叉率 CR = 1 - (log(t)/log(S))^h, h=3
    式 (3-10) 混合差分：rand/1 与 best/2 由 CR 驱动二选一
    式 (3-11) 动态缩放因子 F = CR          (rand <= CR)
                        F = (1 - CR) / 2  (rand >  CR)
    式 (3-12) GMR = 1 - CR（原文无 δ 门限）

运行：PYTHONPATH=. .venv/Scripts/python.exe task_allocation/tests/test_operators_fidelity.py
"""

from __future__ import annotations

import numpy as np

from task_allocation.methods.muas.operators.crossover import (
    dynamic_crossover_rate,
    hybrid_differential_population,
)
from task_allocation.methods.muas.operators.extinction import gmr_rate, should_extinct
from task_allocation.methods.muas.operators.scale_factor import (
    dynamic_scale_factor,
    dynamic_scale_factor_batch,
)

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [OK] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        FAILS.append(name)


def test_cr_formula() -> None:
    """式 (3-9)：CR = 1 - (log(t)/log(S))^h"""
    print("\n=== 式 (3-9) 动态交叉率 CR ===")
    S = 500
    worst = 0.0
    for t in (1, 10, 50, 100, 250, 499):
        expect = 1.0 - (np.log(t) / np.log(S)) ** 3
        got = dynamic_crossover_rate(t, S, 3)
        worst = max(worst, abs(got - expect))
    check("CR 轨迹与论文式一致", worst < 1e-12, f"max|Δ|={worst:.3e}")

    # 边界：首代接近 1，末代趋近 0
    cr_first = dynamic_crossover_rate(1, S, 3)
    cr_last = dynamic_crossover_rate(S - 1, S, 3)
    check("CR 单调递减且落在 [0,1]", 0.0 <= cr_last < cr_first <= 1.0,
          f"first={cr_first:.4f} last={cr_last:.4f}")

    # 曲率语义：底数 r=log(t)/log(S) ∈ (0,1)，指数越大 r^h 越小 → CR 越高，
    # 即 ζ 越大 CR 在整个搜索期保持高位（探索期更长）。
    cr_z1 = dynamic_crossover_rate(100, S, 1)
    cr_z3 = dynamic_crossover_rate(100, S, 3)
    check("ζ=3 时 CR 高于 ζ=1（探索期更长）", cr_z3 > cr_z1,
          f"z1={cr_z1:.4f} z3={cr_z3:.4f}")


def test_f_formula() -> None:
    """式 (3-11)：F = CR (rand<=CR) / (1-CR)/2 (rand>CR)，取值必须 ∈ [0,1]"""
    print("\n=== 式 (3-11) 动态缩放因子 F ===")
    rng = np.random.default_rng(2024)
    samples = [dynamic_scale_factor(cr, rng) for cr in np.linspace(0.02, 0.98, 40)]
    check("F ∈ [0, 1]", all(0.0 <= f <= 1.0 for f in samples),
          f"min={min(samples):.4f} max={max(samples):.4f}")

    # 判据：强行给定 r 验证两个分支
    cr = 0.6
    # r <= cr -> F == cr
    class _FixedRng:
        def __init__(self, r: float) -> None:
            self._r = r

        def random(self) -> float:
            return self._r

    check("r <= CR 分支返回 CR",
          abs(dynamic_scale_factor(cr, _FixedRng(0.1)) - cr) < 1e-12)
    check("r > CR 分支返回 (1-CR)/2",
          abs(dynamic_scale_factor(cr, _FixedRng(0.9)) - (1.0 - cr) / 2.0) < 1e-12)

    # 批量版与标量版同一判据、同一分布
    b = dynamic_scale_factor_batch(0.6, 20000, np.random.default_rng(7))
    emp_hi = float((b > 0.5).mean())
    # 理论：P(F = CR=0.6) = P(r<=CR) = 0.6；P(F = 0.2) = 0.4
    check("批量版高值占比 ≈ CR", abs(emp_hi - 0.6) < 0.02, f"emp={emp_hi:.4f}")
    check("批量版取值 ⊂ {CR, (1-CR)/2}",
          np.all(np.isclose(b, 0.6) | np.isclose(b, 0.2)))
    check("批量版 ∈ [0,1]", bool(((b >= 0.0) & (b <= 1.0)).all()))


def test_hybrid_differential() -> None:
    """式 (3-10)：rand/1 与 best/2 由 CR 驱动二选一"""
    print("\n=== 式 (3-10) 混合差分策略 ===")
    rng = np.random.default_rng(1)
    pop = rng.random((12, 6))
    out_rand = hybrid_differential_population(pop, 0, np.full(12, 0.5), 0.0, rng)
    out_best = hybrid_differential_population(pop, 0, np.full(12, 0.5), 1.0, rng)
    check("CR=0 与 CR=1 产生不同的试验向量",
          not np.allclose(out_rand, out_best))
    check("试验向量与种群同形", out_rand.shape == pop.shape)
    check("CR=0 时结果落在种群凸包量级内（步长受控）",
          float(np.std(out_rand - pop.mean(axis=0))) < 5.0 * float(np.std(pop)))


def test_gmr_formula() -> None:
    """式 (3-12)：GMR = 1 - CR（默认无 δ 门限）"""
    print("\n=== 式 (3-12) GMR 灭绝率 ===")
    for cr in (0.1, 0.3, 0.5, 0.9):
        check(f"GMR(CR={cr}) = 1-CR", abs(gmr_rate(cr) - (1.0 - cr)) < 1e-12)

    check("默认 delta=None 即论文原式",
          abs(gmr_rate(0.5) - 0.5) < 1e-12)

    # GMR = 1 - CR：CR 越高灭绝概率越低（单调递减）
    check("GMR 随 CR 单调递减",
          gmr_rate(0.2) > gmr_rate(0.5) > gmr_rate(0.8),
          f"{gmr_rate(0.2):.2f} {gmr_rate(0.5):.2f} {gmr_rate(0.8):.2f}")

    # 可选 δ 门限仍可用（工程扩展）
    check("δ=0.3 门限：CR>=δ 时 GMR=0",
          gmr_rate(0.5, delta=0.3) == 0.0)
    check("δ=0.3 门限：CR<δ 时 GMR=1-CR",
          abs(gmr_rate(0.2, delta=0.3) - 0.8) < 1e-12)

    # 统计：GMR=1-CR 下灭绝确实主要发生在后期
    rng = np.random.default_rng(5)
    early = [should_extinct(0.9, None, rng) for _ in range(2000)]
    late = [should_extinct(0.1, None, rng) for _ in range(2000)]
    check("早期(CR=0.9)灭绝率低", sum(early) / len(early) < 0.2,
          f"rate={sum(early)/len(early):.3f}")
    check("晚期(CR=0.1)灭绝率高", sum(late) / len(late) > 0.8,
          f"rate={sum(late)/len(late):.3f}")


def main() -> None:
    print("算子保真度核对（对照 Zhao Ming 等 2017 DMDE 公式 3-9 ~ 3-12）")
    test_cr_formula()
    test_f_formula()
    test_hybrid_differential()
    test_gmr_formula()
    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        raise SystemExit(1)
    print("OK: 全部算子与论文公式一致")


if __name__ == "__main__":
    main()
