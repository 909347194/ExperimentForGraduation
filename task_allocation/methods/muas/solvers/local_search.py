# -*- coding: utf-8 -*-
"""local_search.py — 解码后分配解的局部搜索。

职责：DMDE 搜索结束后对 assignment 做局部优化：任务换机（relocate）、两机任务对换
（swap）、机内插入位移动（position）；候选经同一评价链，仅当适应度严格改进才接受，且
不减少已装任务集。全程无随机数。
边界：只优化已有解，不重新搜索分配。
相关：muas/solvers/muas_stage.py（调用 polish_assignment）、solvers/evaluator.py。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Sequence

if TYPE_CHECKING:  # 避免循环导入（muas_stage 反向依赖本模块）
    from task_allocation.methods.muas.solvers.muas_stage import (
        MUASAssignmentEvaluator,
        MUASStageConfig,
    )

MOVE_RELOCATE = "relocate"
MOVE_SWAP = "swap"
MOVE_POSITION = "position"
DEFAULT_MOVES: tuple[str, ...] = (MOVE_RELOCATE, MOVE_SWAP, MOVE_POSITION)


@dataclass
class PolishResult:
    """polish 输出与诊断。"""

    best_assignment: list[tuple[int, int]]
    base_fitness: float
    best_fitness: float
    best_solution: object  # AllocationSolution（已 evaluate + scalar_fitness）
    n_rounds: int = 0
    n_candidates: int = 0      # 候选尝试总数（含缓存命中）
    n_evals: int = 0           # 真实评估数（缓存未命中；预算按此计）
    n_accepted: dict[str, int] = field(default_factory=dict)
    converged: bool = False    # True = 完整邻域扫描无改进（自然收敛）
    budget_hit: bool = False   # True = 撞 max_rounds / max_evals 提前退出

    @property
    def n_accepted_total(self) -> int:
        return int(sum(self.n_accepted.values()))

    @property
    def fitness_gain(self) -> float:
        return float(self.base_fitness - self.best_fitness)


# ---------------------------------------------------------------------------
# assignment 规范化与邻域生成（纯函数，便于单测）
# ---------------------------------------------------------------------------


def normalize_assignment(
    assignment: Iterable[tuple[int, int]],
    n_uavs: int,
    n_tasks: int,
) -> list[tuple[int, int]]:
    """合法化 assignment：丢越界 pair、一任务一机去重（保留首次）、保序。

    与 ``MUASAssignmentEvaluator.build_solution`` 的口径逐位一致
    （越界跳过 + 重复任务保留首次），保证 normalize 后的 assignment
    经评估与原始 assignment 同值。
    """
    out: list[tuple[int, int]] = []
    seen: set[int] = set()
    for pair in assignment:
        u, t = int(pair[0]), int(pair[1])
        if not (0 <= u < n_uavs) or not (0 <= t < n_tasks):
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append((u, t))
    return out


def canonical_routes(
    assignment: Iterable[tuple[int, int]],
    n_uavs: int,
    n_tasks: int,
) -> dict[int, list[int]]:
    """assignment → ``{uav_idx: [task_idx, ...]}``（保出现序）。"""
    routes: dict[int, list[int]] = {}
    for u, t in normalize_assignment(assignment, n_uavs, n_tasks):
        routes.setdefault(u, []).append(t)
    return routes


def assignment_of(routes: dict[int, list[int]]) -> list[tuple[int, int]]:
    """canonical routes → assignment（uav 升序；机内保持列表序）。"""
    out: list[tuple[int, int]] = []
    for u in sorted(routes):
        for t in routes[u]:
            out.append((u, t))
    return out


def iter_relocate(
    routes: dict[int, list[int]], n_uavs: int
) -> Iterable[dict[int, list[int]]]:
    """relocate 邻域：每个任务 × 每架其它机（含空闲机）× 每个插入位。"""
    for u in sorted(routes):
        route = routes[u]
        for i, t in enumerate(route):
            rest = route[:i] + route[i + 1:]
            for u2 in range(n_uavs):
                if u2 == u:
                    continue
                target = routes.get(u2, [])
                for p in range(len(target) + 1):
                    new = dict(routes)
                    new[u] = list(rest)
                    new[u2] = target[:p] + [t] + target[p:]
                    yield new


def iter_swap(
    routes: dict[int, list[int]]
) -> Iterable[dict[int, list[int]]]:
    """swap 邻域：两架**不同**机的任务对换（同机对换 ≡ position 移动）。"""
    uavs = [u for u in sorted(routes) if routes[u]]
    for a in range(len(uavs)):
        for b in range(a + 1, len(uavs)):
            ua, ub = uavs[a], uavs[b]
            ra, rb = routes[ua], routes[ub]
            for i in range(len(ra)):
                for j in range(len(rb)):
                    new = {k: list(v) for k, v in routes.items()}
                    new[ua][i], new[ub][j] = new[ub][j], new[ua][i]
                    yield new


def iter_position(
    routes: dict[int, list[int]]
) -> Iterable[dict[int, list[int]]]:
    """position 邻域：任务在本机航次内移到其它插入位（改访问顺序）。"""
    for u in sorted(routes):
        route = routes[u]
        for i, t in enumerate(route):
            rest = route[:i] + route[i + 1:]
            for p in range(len(rest) + 1):
                if p == i:
                    continue
                new = dict(routes)
                new[u] = rest[:p] + [t] + rest[p:]
                yield new


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def polish_assignment(
    evaluator: "MUASAssignmentEvaluator",
    assignment: Sequence[tuple[int, int]],
    config: "MUASStageConfig",
) -> PolishResult:
    """对 DMDE 最优 assignment 做解码后局部搜索。

    Args:
        evaluator:  MUAS 评价器（候选一律走其 ``evaluate`` = 同一修复链）。
        assignment: DMDE 的 ``best_assignment``（局部下标 (uav, task) 序列）。
        config:     编排参数（读 ``polish_*`` 字段）。

    Returns:
        PolishResult——``best_assignment`` 即打磨后的 assignment（无改进时
        与输入等价）；``best_solution`` 是其经修复链重建并评价的解。
    """
    n_uavs = len(evaluator.participating)
    n_tasks = len(evaluator.tasks)
    # P4 L3b 统一总闸：读「有效移动集」（总闸关时只剩 position）。
    # 兼容非 MUASStageConfig 的替身配置：无 property 时退回旧字段口径。
    # 注意空 tuple 合法（不做任何邻域），不能再 `or DEFAULT_MOVES` 回退——
    # 那样总闸关会被静默绕过。
    eff_moves = getattr(config, "effective_polish_moves", None)
    if eff_moves is None:
        raw = getattr(config, "polish_moves", None)
        eff_moves = DEFAULT_MOVES if raw is None else tuple(raw)
    moves = tuple(eff_moves)
    eps = float(getattr(config, "polish_eps", 1e-6))
    max_rounds = max(1, int(getattr(config, "polish_max_rounds", 30)))
    max_evals = max(1, int(getattr(config, "polish_max_evals", 2000)))

    # 覆盖盲判据检测：门禁口径下 f 不含 −m·F1，成本-only
    # 适应度会欣然接受「卸任务换成本」的候选。守护 = executed 解集单调。
    problem_cfg = getattr(getattr(evaluator, "problem", None), "config", None)
    coverage_blind = (
        bool(getattr(config, "coverage_first", False))
        or bool(getattr(config, "coverage_gate", False))
        or bool(getattr(problem_cfg, "coverage_audit", False))
    )

    base = normalize_assignment(assignment, n_uavs, n_tasks)
    r0 = evaluator.evaluate(base)
    result = PolishResult(
        best_assignment=list(base),
        base_fitness=float(r0.fitness),
        best_fitness=float(r0.fitness),
        best_solution=r0.solution,
        n_accepted={m: 0 for m in moves},
    )
    if not getattr(config, "polish_enabled", False):
        result.converged = True
        return result

    routes = canonical_routes(base, n_uavs, n_tasks)
    best_exec = set(getattr(result.best_solution, "executed_task_ids", []) or [])
    real0 = int(evaluator.n_evals)

    while result.n_rounds < max_rounds:
        result.n_rounds += 1
        improved = False
        for move in moves:
            if move == MOVE_RELOCATE:
                gen = iter_relocate(routes, n_uavs)
            elif move == MOVE_SWAP:
                gen = iter_swap(routes)
            elif move == MOVE_POSITION:
                gen = iter_position(routes)
            else:
                raise ValueError(f"未知 polish 移动类型：{move!r}")
            for new_routes in gen:
                if result.n_evals >= max_evals:
                    result.budget_hit = True
                    break
                result.n_candidates += 1
                cand = assignment_of(new_routes)
                res = evaluator.evaluate(cand)
                result.n_evals = int(evaluator.n_evals) - real0
                if float(res.fitness) < result.best_fitness - eps:
                    # 覆盖守护：门禁/覆盖优先口径下，候选若
                    # 卸掉了已装任务（修复链删任务 → F1 不在适应度里看不
                    # 见），只降成本也拒绝——「不减少已装集合」。
                    # 修复：接受候选后把 best_exec 同步为当前
                    # 最优解的 executed 集，使守护升级为「⊇ 当前最优解集」而非
                    # 「⊇ 初始解集」。否则修复链在 polish 期间新增的任务会在
                    # 后续候选中因成本-only 适应度被静默丢弃，违反完成铁律。
                    cand_exec = set(
                        getattr(res.solution, "executed_task_ids", []) or []
                    )
                    if coverage_blind and not (cand_exec >= best_exec):
                        continue
                    # first-improvement：接受即重启邻域扫描（确定性，无随机）
                    routes = new_routes
                    result.best_assignment = cand
                    result.best_fitness = float(res.fitness)
                    result.best_solution = res.solution
                    best_exec = cand_exec  # 守护锚点随最优解前移（单调）
                    result.n_accepted[move] = result.n_accepted.get(move, 0) + 1
                    improved = True
                    break
            if improved or result.budget_hit:
                break
        if result.budget_hit:
            break
        if not improved:
            result.converged = True
            break
    return result
