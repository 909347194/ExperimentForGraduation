# -*- coding: utf-8 -*-
"""compare_spec.py — CompareSpec：统一比较/选择算子（第二阶段 P1）。

来源：定稿 §6 要素 2「可行性优先的 DMDE 竞争」+ §5.2 一致性契约：
DE 替换 / 邻域接受 / 档案择优最终全部走同一比较算子。P1 落地 **DE 替换**
一处（要素 2 范围）；邻域/档案采纳统一在 P3（定稿 §4.2）。

四种模式（全部作用在**内部选择标量** ``F_sel``，问题目标 ``f`` 一律不动，
定稿 §1 / §7 勘误 3）：

- ``penalty``（默认，= 现行硬贪婪 ``child.fitness < parent.fitness``）：
  与 `method-phase1` 逐位可比；big-M 标定下可行优于不可行自动成立；
- ``deb``：Deb 三规则——可行优于不可行 / 两可行比 f / 两不可行比违反量
  （违反量同则比违约条数、再比 f）。定稿 §6 要素 2 的候选默认档；
- ``epsilon``：ε-constrained（Lagaros §3.3；原始调度 Takahama & Sakai
  Eq.11，新变体 Fan et al. 2018 Eq.12）。⚠️ 当前实现为**线性衰减占位**
  （``eps0 → 0`` 于 ``tc`` 代内），Eq.12 的 ``rk/TC/cp/τ/α`` 参数
  **P2 开工前定稿**（§9 第 2 项），占位实现不得用于正式实验；
- ``lagaros``：FeasRulesNEW 乘积式 F_sel 重标定（Eq. 9b：
  ``F_sel(x) = max(f_best_feasible, f(x)) × p_violation``；可行时 F_sel=f）。
  ``p_violation`` 默认 Eq. 8a（最大违反量，原文不含违反条数）；
  8b/8c/8d（最大/均值/中位数 ×(1 + 违反条数/约束总数)）需显式声明
  ``n_constraints``。⚠️ 勘误 3：原文是乘性**适应度重标定**（评价层），
  不是纯比较算子；此处只作用于 ``F_sel``。

诚实口径（§2.3）：「剥掉解码期优化 + 换比较器 ⇒ 精英更新上升」仍是
**待验证假设**，由 P1 前置闸门（step1 口径）实测裁定；本模块不保证收益。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "CandidateFeatures",
    "CompareSpec",
    "extract_features",
    "make_compare_spec",
]

MODE_PENALTY = "penalty"
MODE_DEB = "deb"
MODE_EPSILON = "epsilon"
MODE_LAGAROS = "lagaros"
_VALID_MODES = (MODE_PENALTY, MODE_DEB, MODE_EPSILON, MODE_LAGAROS)


@dataclass(frozen=True)
class CandidateFeatures:
    """比较所需的特征三元组（f 恒为问题目标标量，绝不被重标定）。"""

    fitness: float                  # f（问题目标，搜索适应度）
    n_hard: int = 0                 # 硬违约条数
    violation: float = 0.0          # 违背量合计（hard_violation）
    n_violations: int = 0           # 违背标签条数（p_violation 的条数项）


def extract_features(fitness: float, solution: Any) -> CandidateFeatures:
    """从被评价解的 ``meta["feasibility"]`` 提取比较特征。

    ``solution is None`` 或缺 meta 时退化为 (fitness, 0, 0, 0)——
    deb/epsilon/lagaros 退化为纯 f 比较（与 penalty 同判）。
    """
    if solution is None:
        return CandidateFeatures(fitness=float(fitness))
    meta = getattr(solution, "meta", None) or {}
    feas = meta.get("feasibility") or {}
    n_hard = int(feas.get("n_hard", 0) or 0)
    violation = float(feas.get("hard_violation", 0.0) or 0.0)
    tags = feas.get("violations") or []
    return CandidateFeatures(
        fitness=float(fitness),
        n_hard=n_hard,
        violation=violation,
        n_violations=len(tags),
    )


@dataclass
class CompareSpec:
    """统一比较算子（P1：DE 替换；P3 扩到邻域/档案采纳）。

    Attributes:
        mode: penalty | deb | epsilon | lagaros。
        epsilon0: ε 初值（epsilon 模式；占位线性衰减）。
        epsilon_tc: ε 衰减到 0 的代数（epsilon 模式）。
        lagaros_variant: 8a | 8b | 8c | 8d（lagaros 模式）。
        n_constraints: 约束总数（8b/8c/8d 的条数项必需；缺省用 8a）。
    """

    mode: str = MODE_PENALTY
    epsilon0: float = 0.0
    epsilon_tc: int = 1
    lagaros_variant: str = "8a"
    n_constraints: int | None = None
    # lagaros Eq. 9b 的 f_best_feasible 参照（求解器喂入；未喂时退化 max(f_cand,f_cur)）
    _f_best_feasible: float | None = None

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(f"compare mode 必须是 {_VALID_MODES}，当前 {self.mode!r}")
        if self.mode == MODE_EPSILON and self.epsilon0 < 0:
            raise ValueError("epsilon0 不能为负")
        if self.epsilon_tc < 1:
            raise ValueError("epsilon_tc 必须 >= 1")
        if self.lagaros_variant not in ("8a", "8b", "8c", "8d"):
            raise ValueError("lagaros_variant 必须是 8a/8b/8c/8d")

    # ---- 参照喂入（lagaros Eq. 9b）----

    def set_best_feasible(self, f: float | None) -> None:
        self._f_best_feasible = None if f is None else float(f)

    # ---- ε 调度（占位：线性衰减；正式参数 P2 定稿）----

    def epsilon_at(self, gen: int) -> float:
        if self.mode != MODE_EPSILON or self.epsilon0 <= 0.0:
            return 0.0
        g = max(0, int(gen))
        tc = max(1, int(self.epsilon_tc))
        return float(self.epsilon0) * max(0.0, 1.0 - g / tc)

    # ---- p_violation（Lagaros Eq. 8a–d）----

    def p_violation(self, feat: CandidateFeatures) -> float:
        if feat.violation <= 0.0 and feat.n_violations == 0:
            return 0.0
        v = float(feat.violation)
        if self.lagaros_variant == "8a" or not self.n_constraints:
            return v
        ratio = 1.0 + feat.n_violations / max(1, int(self.n_constraints))
        if self.lagaros_variant == "8b":
            # 8b 原文 = 最大违反量 × ratio；特征仅携带合计量（聚合口径限制），
            # 此处以合计代最大（近似，勿用于正式实验；P2 逐样本特征可消除）
            return v * ratio
        # 8c = 均值违反量 × ratio（以合计/条数 近似均值）；
        # 8d = 中位数 × ratio——不可由聚合特征还原，声明为 8c 近似（同上）
        mean_v = v / max(1, feat.n_violations)
        return mean_v * ratio

    # ---- F_sel 变换（lagaros Eq. 9b）----

    def f_sel(self, feat: CandidateFeatures) -> float:
        if self.mode != MODE_LAGAROS or (feat.violation <= 0.0 and feat.n_violations == 0):
            return feat.fitness
        ref = self._f_best_feasible
        if ref is None:
            ref = feat.fitness
        return max(float(ref), feat.fitness) * self.p_violation(feat)

    # ---- 比较 ----

    def should_replace(
        self,
        cand: CandidateFeatures,
        cur: CandidateFeatures,
        *,
        gen: int = 0,
    ) -> bool:
        """候选是否替换当前个体（True = 替换）。"""
        if self.mode == MODE_PENALTY:
            return cand.fitness < cur.fitness

        if self.mode == MODE_DEB:
            c_ok, r_ok = cand.n_hard == 0, cur.n_hard == 0
            if c_ok != r_ok:
                return c_ok                       # 可行优于不可行
            if c_ok:
                return cand.fitness < cur.fitness  # 两可行比目标
            if cand.violation != cur.violation:    # 两不可行比违反量
                return cand.violation < cur.violation
            if cand.n_hard != cur.n_hard:
                return cand.n_hard < cur.n_hard
            return cand.fitness < cur.fitness

        if self.mode == MODE_EPSILON:
            eps = self.epsilon_at(gen)

            def eff(v: float) -> float:
                return 0.0 if v <= eps else v

            cv, rv = eff(cand.violation), eff(cur.violation)
            if cv != rv:
                return cv < rv
            return cand.fitness < cur.fitness

        # lagaros：F_sel 乘积式重标定后按标量比较（可行时 F_sel = f）
        return self.f_sel(cand) < self.f_sel(cur)


def make_compare_spec(
    *,
    mode: str = MODE_PENALTY,
    epsilon0: float = 0.0,
    epsilon_tc: int = 1,
    lagaros_variant: str = "8a",
    n_constraints: int | None = None,
) -> CompareSpec:
    return CompareSpec(
        mode=mode,
        epsilon0=epsilon0,
        epsilon_tc=epsilon_tc,
        lagaros_variant=lagaros_variant,
        n_constraints=n_constraints,
    )
