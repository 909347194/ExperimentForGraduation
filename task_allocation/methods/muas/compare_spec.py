# -*- coding: utf-8 -*-
"""compare_spec.py — 统一的候选比较算子。

职责：把 DE 替换等场景的"谁更优"判定收敛到 CompareSpec，作用在内部选择标量上，支持
penalty / deb / epsilon / lagaros 等模式。
边界：只做比较，不改问题目标函数。
相关：muas/problem.py（目标与适应度）、solvers/dmde_solver.py（进化选择时调用）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CandidateFeatures",
    "CompareSpec",
    "calibrate_lambda_cov",
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
    """比较所需的特征（f 恒为问题目标标量，绝不被重标定）。

    ``n_skip`` 是覆盖保护项（P2-d）：可行解比较时若 `CompareSpec.lambda_cov`
    非 None，则有效标量为 ``f + λ_cov·n_skip``——保证搜索不靠「少做任务」
    换取边际成本下降（定稿 §9 / P2-d）。
    """

    fitness: float                  # f（问题目标，搜索适应度）
    n_hard: int = 0                 # 硬违约条数
    violation: float = 0.0          # 违背量合计（hard_violation）
    n_violations: int = 0           # 违背标签条数（p_violation 的条数项）
    n_skip: int = 0                 # voluntary_skip 数（覆盖门禁口径；gate 修复下恒 0）


def extract_features(fitness: float, solution: Any) -> CandidateFeatures:
    """从被评价解的 ``meta`` 提取比较特征。

    ``solution is None`` 或缺 meta 时退化为 (fitness, 0, 0, 0, 0)——
    deb/epsilon/lagaros 退化为纯 f 比较（与 penalty 同判）。

    ``n_skip`` 来自 ``meta["gate_stats"]["n_skip"]``（覆盖门禁口径的
    voluntary_skip 计数，见 problem.py scalar_fitness）；缺省 0。
    """
    if solution is None:
        return CandidateFeatures(fitness=float(fitness))
    meta = getattr(solution, "meta", None) or {}
    feas = meta.get("feasibility") or {}
    n_hard = int(feas.get("n_hard", 0) or 0)
    violation = float(feas.get("hard_violation", 0.0) or 0.0)
    tags = feas.get("violations") or []
    gate = meta.get("gate_stats") or {}
    n_skip = int(gate.get("n_skip", 0) or 0)
    return CandidateFeatures(
        fitness=float(fitness),
        n_hard=n_hard,
        violation=violation,
        n_violations=len(tags),
        n_skip=n_skip,
    )


@dataclass
class CompareSpec:
    """统一比较算子（P1：DE 替换；P3 扩到邻域/档案采纳）。

    Attributes:
        mode: penalty | deb | epsilon | lagaros。
        epsilon0 / epsilon_tc:**废弃**（P2-e 前的占位线性衰减）；
            Eq.12 状态机改用 epsilon_gamma/tc_frac/cp/tau/alpha。
        epsilon_gamma / epsilon_tc_frac / epsilon_cp / epsilon_tau /
        epsilon_alpha: Eq.12（Fan 2018）调度参数。
        epsilon_max_fes: maxFEs（求解器注入；None 时 TC 退化为 1）。
        lagaros_variant: 8a | 8b | 8c | 8d（lagaros 模式）。
        n_constraints: 约束总数（8b/8c/8d 的条数项必需；缺省用 8a）。
        lambda_cov: 覆盖保护系数（P2-d；**仅非 penalty 档生效**）。None/0 = 关。
            两可行候选比较有效标量 ``f + λ_cov·n_skip``；保证搜索不靠少做任务
            换边际成本。penalty 档恒不作用（与 method-phase1 逐位等价）。
    """

    mode: str = MODE_PENALTY
    # 废弃：占位线性衰减（P2-e 起由 Eq.12 取代；保留字段以维持旧 config 兼容）
    epsilon0: float = 0.0
    epsilon_tc: int = 1
    # Eq.12（Fan et al. 2018）调度参数（定稿 §9 第 2 项拍板值）
    epsilon_gamma: float = 0.5       # θ = γ·Npop（θ 大违反量取 ε(0)）
    epsilon_tc_frac: float = 0.4     # TC = tc_frac · maxFEs
    epsilon_cp: float = 5.0          # 衰减曲率
    epsilon_tau: float = 0.0         # 放松分支幅度 (1+τ)
    epsilon_alpha: float = 0.5       # 可行比例阈值 α（rk ≥ α 触发放松）
    epsilon_max_fes: int | None = None  # maxFEs（求解器注入；None ⇒ TC=1）
    lagaros_variant: str = "8a"
    n_constraints: int | None = None
    lambda_cov: float | None = None
    # lagaros Eq. 9b 的 f_best_feasible 参照（求解器喂入；未喂时退化 max(f_cand,f_cur)）
    _f_best_feasible: float | None = None
    # Eq.12 内部状态（非 init，可变）
    _eps: float | None = field(default=None, init=False, repr=False)
    _eps_gen: int = field(default=-1, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.mode not in _VALID_MODES:
            raise ValueError(f"compare mode 必须是 {_VALID_MODES}，当前 {self.mode!r}")
        if self.epsilon0 < 0:
            raise ValueError("epsilon0 不能为负（已废弃字段，仅作兼容校验）")
        if self.epsilon_tc < 1:
            raise ValueError("epsilon_tc 必须 >= 1（已废弃字段，仅作兼容校验）")
        if not (0.0 < self.epsilon_gamma <= 1.0):
            raise ValueError("epsilon_gamma 必须 ∈ (0, 1]")
        if not (0.0 < self.epsilon_tc_frac <= 1.0):
            raise ValueError("epsilon_tc_frac 必须 ∈ (0, 1]")
        if self.epsilon_cp <= 0:
            raise ValueError("epsilon_cp 必须 > 0")
        if self.epsilon_tau < 0:
            raise ValueError("epsilon_tau 不能为负")
        if not (0.0 <= self.epsilon_alpha <= 1.0):
            raise ValueError("epsilon_alpha 必须 ∈ [0, 1]")
        if self.lagaros_variant not in ("8a", "8b", "8c", "8d"):
            raise ValueError("lagaros_variant 必须是 8a/8b/8c/8d")
        if self.lambda_cov is not None and self.lambda_cov < 0:
            raise ValueError("lambda_cov 不能为负")

    # ---- 参照喂入（lagaros Eq. 9b）----

    def set_best_feasible(self, f: float | None) -> None:
        self._f_best_feasible = None if f is None else float(f)

    # ---- ε 调度（Eq.12，Fan et al. 2018，P2-e 实装）----

    def set_epsilon_max_fes(self, n: int) -> None:
        """注入 maxFEs（求解器：``max_generations · pop_size``）。"""
        self.epsilon_max_fes = int(n) if n else None

    def reset_epsilon(self, phi_theta: float) -> None:
        """Eq.12 初始化：``ε(0) = ϕ_θ``（θ = γ·Npop 第 θ 大违反量）。

        仅 ``epsilon`` 模式生效；非 epsilon 模式为 no-op（保留调用安全）。
        """
        if self.mode != MODE_EPSILON:
            return
        self._eps = float(phi_theta)
        self._eps_gen = 0

    def tick_epsilon(self, fes: int, rk: float, phi_max: float) -> float:
        """Eq.12 状态机：推进到当前代 ``ε(k)`` 并返回。

        FEs 计数口径（定稿 §9 第 2 项）：``fes = gen · Npop``（本代替换决策
        前的累计函数评价数；gen 为 1-based 进化代数）。

        - ``FEs < TC`` 且 ``rk < α``：``ε(k) = ε(k-1)·(1 − FEs/TC)^cp``（收紧）
        - ``FEs < TC`` 且 ``rk ≥ α``：``ε(k) = (1+τ)·ϕ_max``（放松反馈，
          防搜索塌进可行域——对症精英冻结病理）
        - ``FEs ≥ TC``：``ε(k) = 0``

        其中 ``TC = epsilon_tc_frac · maxFEs``。未 reset（直接调用）或非
        epsilon 模式返回 0.0（与占位行为一致，保证旧调用不崩）。
        """
        if self.mode != MODE_EPSILON or self._eps is None:
            return 0.0
        tc = max(1, int(round(self.epsilon_tc_frac * (self.epsilon_max_fes or 1))))
        fes = max(0, int(fes))
        if fes < tc:
            if float(rk) < self.epsilon_alpha:
                decay = max(0.0, 1.0 - fes / tc) ** self.epsilon_cp
                self._eps = self._eps * decay
            else:
                self._eps = (1.0 + self.epsilon_tau) * max(0.0, float(phi_max))
        else:
            self._eps = 0.0
        self._eps_gen += 1
        return self._eps

    def epsilon_at(self, gen: int = 0) -> float:
        """返回当前 ε（Eq.12 状态机内部值；``gen`` 参数保留兼容，不再驱动衰减）。"""
        if self.mode != MODE_EPSILON:
            return 0.0
        return float(self._eps) if self._eps is not None else 0.0

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
            # 可行（或 penalty 模式）：覆盖保护只在可行侧生效（P2-d）
            return feat.fitness + self._cov_shift(feat)
        ref = self._f_best_feasible
        if ref is None:
            ref = feat.fitness
        return max(float(ref), feat.fitness + self._cov_shift(feat)) * self.p_violation(feat)

    # ---- 覆盖保护（P2-d）----

    def _cov_shift(self, feat: CandidateFeatures) -> float:
        """覆盖保护位移：``λ_cov·n_skip``（仅当 ``lambda_cov`` 非 None）。

        penalty 档不调用本方法（should_replace 走纯 f 分支），故 penalty
        路径仍与 method-phase1 逐位等价；λ_cov 的生效面严格限定在
        deb/epsilon/lagaros（定稿 §9 / P2-d）。
        """
        if self.lambda_cov is None:
            return 0.0
        return float(self.lambda_cov) * int(feat.n_skip)

    def _eff(self, feat: CandidateFeatures) -> float:
        """可行比较有效标量：``f + λ_cov·n_skip``。"""
        return feat.fitness + self._cov_shift(feat)

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
                return self._eff(cand) < self._eff(cur)  # 两可行比目标(+覆盖保护)
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
            return self._eff(cand) < self._eff(cur)

        # lagaros：F_sel 乘积式重标定后按标量比较（可行时 F_sel = f）
        return self.f_sel(cand) < self.f_sel(cur)


def make_compare_spec(
    *,
    mode: str = MODE_PENALTY,
    epsilon0: float = 0.0,
    epsilon_tc: int = 1,
    epsilon_gamma: float = 0.5,
    epsilon_tc_frac: float = 0.4,
    epsilon_cp: float = 5.0,
    epsilon_tau: float = 0.0,
    epsilon_alpha: float = 0.5,
    lagaros_variant: str = "8a",
    n_constraints: int | None = None,
    lambda_cov: float | None = None,
) -> CompareSpec:
    return CompareSpec(
        mode=mode,
        epsilon0=epsilon0,
        epsilon_tc=epsilon_tc,
        epsilon_gamma=epsilon_gamma,
        epsilon_tc_frac=epsilon_tc_frac,
        epsilon_cp=epsilon_cp,
        epsilon_tau=epsilon_tau,
        epsilon_alpha=epsilon_alpha,
        lagaros_variant=lagaros_variant,
        n_constraints=n_constraints,
        lambda_cov=lambda_cov,
    )


def calibrate_lambda_cov(
    lambda_cost: float,
    w_late: float,
    max_path_delta: float,
    max_late_delta: float,
    margin: float = 1.1,
) -> float:
    """推导 ``λ_cov`` 的严格下界（定稿 §9 / P2-d）。

    少做一个任务的收益上界 = ``λ·Δpath_max + w_late·ΔL_max``（省下的航程
    计价 + 省下的迟到计价）。``λ_cov`` 须**严格**超过此上界，才能使「两可行
    比较」恒不奖励跳过任务（``f + λ_cov·n_skip`` 对 ``n_skip ≥ 1`` 的惩罚
    大于单任务边际收益）。

    Args:
        lambda_cost: 问题目标中的航程权重 ``λ``（config.lambda_cost，默认 0.1）。
        w_late: 迟到权重 ``w_late``（config.w_violation，设计率 0.01/s）。
        max_path_delta: 单任务最大可能航程增量（米；取代价矩阵最大边）。
        max_late_delta: 单任务最大可能迟到增量（秒；取时间窗跨度上限）。
        margin: 安全裕度。``<=1`` 返回裸上界；``>1`` 返回带裕度的可配置值。

    Returns:
        建议配置的 ``λ_cov``（≥ 上界 × margin）。
    """
    bound = float(lambda_cost) * float(max_path_delta) + float(w_late) * float(max_late_delta)
    if margin <= 1.0:
        return bound
    return bound * float(margin)
