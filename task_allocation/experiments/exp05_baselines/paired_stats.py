# -*- coding: utf-8 -*-
"""exp05 v3 配对统计：同 seed 配对差 + 精确 Student t + 三态结论。

数值实现按「实验独立性」约定**复制**自 exp08_gain_decomposition/metrics.py，
不跨实验 import、不抽公共包：改这里不会悄悄动到 exp08 的结果。

口径（见 docs/exp05_redesign_2026-11-09.md §5）：
- p 用正则化不完全 beta 精确求值，不用正态近似、不依赖 scipy；
- 零方差 / 配对不足 ⇒ ``p_reason`` 显式说明，**不得写成「不显著」**；
- 三态结论 ``supported / not_supported / inconclusive``，判据写死；
- ``inconclusive`` 一律表述为「本实验未检出差异」，禁写「无差异 / 等价」。
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

__all__ = [
    "two_sided_p",
    "t_critical",
    "paired_stats",
    "paired_difference",
    "verdict",
    "CAVEATS",
]

#: 随每个结论一起落盘的限制声明（不得删，删了就等于偷偷加强结论）
CAVEATS = (
    "单场景、单到达流、5 seed；效应量与名义 p 值未做多重比较校正",
    "跨实验（exp05 v3 与 P4 的 [7,11,23] 协议）禁止配对 t 检验",
    "冒烟 / mid 档结论不可外推正式档",
    "inconclusive 只能写「本实验未检出差异」，禁写「无差异 / 等价」",
    "禁写「独立收益」「协同效应」「双双验证」「性能提升 / SOTA」",
)


# ---------------------------------------------------------------------------
# 精确 p 值（Numerical Recipes 正则化不完全 beta）
# ---------------------------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    """不完全 beta 的连分式。"""
    maxit, eps, fpmin = 300, 3.0e-14, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """正则化不完全 beta 函数 I_x(a, b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log(1.0 - x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def two_sided_p(t: float, df: int) -> float | None:
    """Student t 双尾 p 的精确值 ``I_{df/(df+t²)}(df/2, 1/2)``。"""
    if df <= 0 or not math.isfinite(t):
        return None
    return float(_betai(df / 2.0, 0.5, df / (df + t * t)))


def t_critical(df: int, alpha: float = 0.05) -> float | None:
    """双尾临界值：二分求 ``two_sided_p(t, df) = alpha`` 的正根。

    只为 95% CI 服务，避免用 1.96 正态近似冒充 t 分位数。
    """
    if df <= 0:
        return None
    lo, hi = 0.0, 1.0
    while two_sided_p(hi, df) > alpha and hi < 1.0e6:
        hi *= 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if two_sided_p(mid, df) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# 配对统计与结论
# ---------------------------------------------------------------------------


def paired_stats(diffs: Sequence[float]) -> dict[str, Any]:
    """逐 seed 差值 → 完整配对统计；零方差不检验并显式给理由。"""
    xs = [float(d) for d in diffs]
    n = len(xs)
    mu = sum(xs) / n if n else 0.0
    sd = math.sqrt(sum((x - mu) ** 2 for x in xs) / (n - 1)) if n >= 2 else 0.0
    se = sd / math.sqrt(n) if n >= 2 and sd > 0 else None
    t = mu / se if se else None
    p = two_sided_p(t, n - 1) if t is not None else None
    if n < 2:
        reason = "n_pairs_lt_2"
    elif sd == 0.0:
        reason = "zero_variance_no_test"
    elif p is None:
        reason = "p_undefined"
    else:
        reason = "computed"
    crit = t_critical(n - 1) if n >= 2 else None
    ci95 = (
        [mu - crit * se, mu + crit * se]
        if crit is not None and se is not None else None
    )
    return {
        "per_seed": [round(x, 6) for x in xs],
        "n_pairs": n,
        "mean": round(mu, 6),
        "std": round(sd, 6),
        "t_stat": round(t, 6) if t is not None else None,
        "p_two_sided": round(p, 6) if p is not None else None,
        "p_reason": reason,
        "p_method": "student_t_incomplete_beta_exact",
        "ci95": [round(v, 6) for v in ci95] if ci95 else None,
        "cohens_dz": round(mu / sd, 6) if sd > 0 else None,
        "significant_at_0_05": (bool(p < 0.05) if p is not None else None),
    }


def paired_difference(
    rows_a: Mapping[int, Mapping[str, Any]],
    rows_b: Mapping[int, Mapping[str, Any]],
    key: str,
    seeds: Sequence[int],
) -> tuple[list[float], list[int], list[int]]:
    """取同 seed 配对差 ``a - b``；返回 (差值, 已配对 seed, 缺失 seed)。

    缺配对不静默丢弃：调用方必须把 ``unpaired`` 落盘并视为门槛失败。
    """
    paired = [int(s) for s in seeds if int(s) in rows_a and int(s) in rows_b]
    unpaired = [int(s) for s in seeds if int(s) not in paired]
    diffs = [
        float(rows_a[s].get(key, 0.0) or 0.0) - float(rows_b[s].get(key, 0.0) or 0.0)
        for s in paired
    ]
    return diffs, paired, unpaired


def verdict(
    stats: Mapping[str, Any],
    *,
    expected: str = "negative",
    min_dz: float = 0.5,
) -> dict[str, Any]:
    """三态结论；判据写死，禁止事后调整。

    Args:
        expected: 主张预期方向 ``negative``（差值 < 0）/ ``positive`` / ``any``。
        min_dz: ``supported`` 所需的效应量下界。
    """
    p = stats.get("p_two_sided")
    mean = float(stats.get("mean", 0.0) or 0.0)
    dz = stats.get("cohens_dz")
    sig = p is not None and p < 0.05
    if expected == "negative":
        matches = mean < 0
        opposite = mean > 0
    elif expected == "positive":
        matches = mean > 0
        opposite = mean < 0
    else:
        matches, opposite = True, False

    if sig and matches and dz is not None and abs(float(dz)) >= min_dz:
        state = "supported"
    elif sig and opposite:
        state = "not_supported"
    else:
        state = "inconclusive"

    return {
        "state": state,
        "expected_direction": expected,
        "min_abs_dz": min_dz,
        "significant": bool(sig),
        "wording": {
            "supported": "在本实验条件下，配对差方向与主张一致且达到显著",
            "not_supported": "配对差方向与主张相反且达到显著，须修订主张措辞",
            "inconclusive": "本实验未检出差异（含 p 不显著 / 零方差 / 配对不足 / 效应量过小）",
        }[state],
        "caveats": list(CAVEATS),
    }
