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
    mutate_best_1,
    mutate_rand_1,
)
from task_allocation.methods.muas.operators.extinction import gmr_rate, should_extinct
from task_allocation.methods.muas.operators.scale_factor import (
    dynamic_scale_factor,
    dynamic_scale_factor_batch,
)

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    """核对一项契约。

    失败时**必须抛出**，不能只打印：pytest 收集的是 ``test_*`` 函数，
    ``main()`` 不会被自动执行，若此处仅打印则断言失败也只会表现为
    ``passed`` —— 契约等于没被锁住（历史缺陷：本文件 4 个测试曾全部空转）。
    """
    if cond:
        print(f"  [OK] {name}")
        return
    msg = f"{name} {detail}".strip()
    print(f"  [FAIL] {name} {detail}")
    FAILS.append(name)
    raise AssertionError(msg)


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


def test_small_population_never_hangs() -> None:
    """小种群下差分变异不得挂起/抛错（死循环回归）。

    历史缺陷：``hybrid_differential_population`` 原实现为
    ``rng.choice(pop_size, size=4, replace=False)`` 后接
    ``while i in indices`` 重试。当 ``pop_size == 4`` 时无放回抽全部必然
    命中 ``i``，重试条件恒真 → **死循环**（进程无提示挂起）；
    ``pop_size < 4`` 时 ``choice`` 直接抛 ValueError。
    ``mutate_rand_1`` / ``mutate_best_1`` 分别在 ``pop_size == 3`` / ``== 2``
    有同样的死循环。

    修复口径：先从「不含 i」的候选池抽取（互异且排除自身天然成立），
    可用个体不足时缺失差分项用首个可用向量补齐（差分退化，不挂起）。
    """
    print("\n=== 小种群差分变异（不得挂起）===")
    for ps in (1, 2, 3, 4, 5, 6, 10):
        pop = np.random.default_rng(0).uniform(0.0, 100.0, size=(ps, 6))
        fv = np.full(ps, 0.5)
        out = hybrid_differential_population(
            pop, 0, fv, 0.5, rng=np.random.default_rng(1)
        )
        check(f"hybrid pop_size={ps} 同形且有限",
              out.shape == pop.shape and bool(np.all(np.isfinite(out))))
        out_r1 = mutate_rand_1(pop, 0, fv, 0.5, rng=np.random.default_rng(2))
        check(f"rand/1 pop_size={ps} 同形且有限",
              out_r1.shape == pop.shape and bool(np.all(np.isfinite(out_r1))))
        out_b1 = mutate_best_1(pop, 0, fv, 0.5, rng=np.random.default_rng(3))
        check(f"best/1 pop_size={ps} 同形且有限",
              out_b1.shape == pop.shape and bool(np.all(np.isfinite(out_b1))))


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
    test_small_population_never_hangs()
    test_gmr_formula()
    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)}): " + ", ".join(FAILS))
        raise SystemExit(1)
    print("OK: 全部算子与论文公式一致")


if __name__ == "__main__":
    main()
