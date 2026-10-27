# -*- coding: utf-8 -*-
"""participating.py — 参与 UAV 选择（``U_t^exec`` 候选，论文 3.4 选机层）。

2026-10-27 自 ``muas_stage.py`` 拆出：负责「本周期派哪些机进精细规划」。

- :func:`select_participating_uavs` 三层裁剪：SRP 不变式（K ≤ N−1）→
  运营预算（sortie_budget / max_participating_uavs）→ 出动经济性；
- 参与策略：cheapest / first / marginal / demand / demand_marginal；
  选机只做**效率预筛**——「不漏可达任务」由解码端安全网保证
  （``reflow_rescue_scope="fleet"`` / 构造式原生覆盖）。
"""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

from task_allocation.common.types import UAV
from task_allocation.methods.muas.solvers.stage_config import (
    MUASStageConfig,
    POLICY_CHEAPEST,
    POLICY_DEMAND,
    POLICY_DEMAND_MARGINAL,
    POLICY_FIRST,
    POLICY_MARGINAL,
)

# 参与 UAV 选择


def select_participating_uavs(
    uavs: Sequence[UAV],
    n_tasks: int,
    config: MUASStageConfig,
    uav_target: np.ndarray | None = None,
    row_of_uav: Mapping[int, int] | None = None,
) -> list[UAV]:
    """挑选本周期参与规划的 UAV（``U_t^exec`` 的候选）。

    参与机数 K 的确定**与任务数解耦**，按三层裁剪（K ⊆ 可用机数）：

    1. **SRP 模型不变式**：``K ≤ N-1``（保证一任务一机且允许巡游；
       ``N=1`` 时 ``K=1``）。显式配置同样裁剪到该上界——旧实现只裁 auto
       不裁显式值，``K≥N`` 会走进 balanced/overloaded 路径；
    2. **运营预算**：``sortie_budget``（出动架次上限）与
       ``max_participating_uavs``（硬上限），与 N 无关地压低 K；
    3. **出动经济性**（``participating_policy="marginal"``）：按「该机是几个
       任务的最近机」降序逐个纳入，第 j 架带来的「容量受限覆盖代价」边际下降
       ≤ ``participation_cost`` γ 时**跳过该机继续试下一架**（不是停机——
       gain 非单调，零增益机之后仍可能有正增益机）。

    旧 auto 规则在机队充裕时总是顶格 ``K=N-1``，使 ``N-K`` 塌缩为常数、
    巡游顺序搜索维度名存实亡；解耦后 K 可显著小于 N-1，支撑 N≫K 的
    长航次巡游实例（顺序维度 = N-K 个巡游基因）。

    Args:
        uavs:             可用无人机（``U_t^avail``）。
        n_tasks:          候选任务数 N。
        config:           编排参数（含 K 决策旋钮）。
        uav_target:       ``(n_rows, n_tasks)`` 代价矩阵，供 cheapest/marginal 策略。
        row_of_uav:       ``{uav_id: 矩阵行号}``。

    Returns:
        参与规划的 UAV 列表（长度 K ≥ 1）。
    """
    avail = [u for u in uavs if u.is_available]
    if not avail or n_tasks <= 0:
        return []

    # 1) 上界裁剪：SRP 不变式 + 运营预算
    k_cap = max(1, n_tasks - 1) if n_tasks > 1 else 1
    if config.max_participating_uavs is not None:
        k_cap = min(k_cap, int(config.max_participating_uavs))
    if config.sortie_budget is not None:
        k_cap = min(k_cap, max(0, int(config.sortie_budget)))
    k = max(1, min(k_cap, len(avail)))

    # 2) 排序（marginal 需要代价序；无代价矩阵时退化为登记序）
    if config.participating_policy == POLICY_FIRST or uav_target is None:
        ordered = avail[:k]
    elif config.participating_policy == POLICY_DEMAND:
        # 需求感知：贪心最大覆盖（Fix④）。保证每个选中任务都有一架可达
        # 参与机，避免 cheapest 把机堆在稠密区、饿死离群稀疏任务。
        ordered = _greedy_demand_coverage(
            avail, n_tasks, config, uav_target, row_of_uav, k
        )
    elif config.participating_policy == POLICY_DEMAND_MARGINAL:
        # 三层选机（exp04 B2）：第1层设施选址预选（贪心最大覆盖）+
        # 第2层字典序边际精修。第3层安全网在解码端（reflow fleet /
        # 构造式原生覆盖），不在此处——「不漏可达任务」由解码保证，
        # 选机层只做效率预筛。
        # γ=0（未配出动代价）时预选即终选，保持可复现。
        preselected = _greedy_demand_coverage(
            avail, n_tasks, config, uav_target, row_of_uav, k
        )
        if (
            uav_target is not None
            and len(preselected) > 1
            and float(config.participation_cost) > 0.0
        ):
            return _select_by_lexicographic_marginal(
                preselected, config, uav_target, row_of_uav, n_tasks
            )
        return preselected
    else:
        def cheapest_cost(u: UAV) -> float:
            row = row_of_uav.get(u.id) if row_of_uav else None
            if row is None or row >= uav_target.shape[0]:
                return float("inf")
            return float(np.min(uav_target[row]))

        if config.participating_policy == POLICY_MARGINAL:
            # marginal 的排序键必须与边际贡献对齐：按「该机是几个任务的
            # 最近机」降序。旧键 cheapest_cost = min_t C(u,t) 度量的是「离
            # 我最近那个任务有多近」——一架机可以只因某个任务恰好在旁就排到
            # 前列，却对任何任务都不是最近机（gain≡0），把真正能贡献的机挤到
            # 后面（实测：0/20 argmin 的机排 #2，6/20 的机排 #5）。
            share = _argmin_share(uav_target, n_tasks)

            def marginal_key(u: UAV) -> tuple[int, float, int]:
                row = row_of_uav.get(u.id) if row_of_uav else None
                if row is None or row >= uav_target.shape[0]:
                    return (0, float("inf"), u.id)  # 无代价行 → 排最后
                return (-int(share[row]), cheapest_cost(u), u.id)

            ordered = sorted(avail, key=marginal_key)[:k]
        else:
            ordered = sorted(avail, key=lambda u: (cheapest_cost(u), u.id))[:k]

    # 3) 出动经济性：边际收益递减停机（仅 marginal 策略）
    if (
        config.participating_policy == POLICY_MARGINAL
        and uav_target is not None
        and len(ordered) > 1
    ):
        return _select_by_marginal_takeoff(
            ordered,
            float(config.participation_cost),
            uav_target,
            row_of_uav,
            n_tasks,
            config.marginal_tour_cap,
        )
    return ordered


def _greedy_demand_coverage(
    avail: Sequence[UAV],
    n_tasks: int,
    config: "MUASStageConfig",
    uav_target: np.ndarray,
    row_of_uav: Mapping[int, int] | None,
    k: int,
) -> list[UAV]:
    """需求感知参与：贪心最大覆盖挑选参与机（Fix④）。

    每架可用 UAV 覆盖「代价矩阵中代价 ≤ 覆盖半径」的选中任务；依次挑选覆盖
    最多**尚未覆盖**任务的 UAV，直到所有选中任务被覆盖或达 K 上限 ``k``。
    覆盖为零的机不纳入（避免塞无用机挤占名额）。

    覆盖半径：``config.demand_coverage_radius``（显式，米）优先；否则
    ``demand_range_factor · remaining_range(u)``（默认 0.5，对应单任务往返
    航程 ≤ 剩余航程的保守可达性）。

    Args:
        avail:       可用 UAV（``U_t^avail``）。
        n_tasks:     选中任务数 N。
        config:      编排参数（含 demand 旋钮）。
        uav_target:  ``(n_rows, n_tasks)`` 代价矩阵，行 = 可用 UAV。
        row_of_uav:  ``{uav_id: 矩阵行号}``。
        k:           参与机数上限（SRP 不变式 + 运营预算裁剪后）。

    Returns:
        参与规划的 UAV 列表（长度 ≤ k，覆盖尽量多的选中任务）。
    """
    def _threshold(u: UAV) -> float:
        if config.demand_coverage_radius is not None:
            return float(config.demand_coverage_radius)
        return max(
            0.0, float(getattr(u, "remaining_range", 0.0) or 0.0)
        ) * float(config.demand_range_factor)

    def _cheapest_cost(u: UAV) -> float:
        row = row_of_uav.get(u.id) if row_of_uav else None
        if row is None or row >= uav_target.shape[0]:
            return float("inf")
        return float(np.min(uav_target[row]))

    # 每架机的覆盖任务集（列下标）
    cover: dict[int, set[int]] = {}
    n_cols = uav_target.shape[1]
    for u in avail:
        row = row_of_uav.get(u.id) if row_of_uav else None
        if row is None or row >= uav_target.shape[0]:
            cover[u.id] = set()
            continue
        thr = _threshold(u)
        covered = {
            t for t in range(n_tasks)
            if t < n_cols and np.isfinite(uav_target[row, t]) and uav_target[row, t] <= thr
        }
        cover[u.id] = covered

    picked: list[UAV] = []
    picked_ids: set[int] = set()
    covered_tasks: set[int] = set()
    all_tasks = set(range(n_tasks))
    pool = list(avail)

    while len(picked) < k and covered_tasks < all_tasks and pool:
        # 选覆盖「未覆盖任务」最多者；tie-break：更近（cheapest_cost 小）优先，再按 id
        best: UAV | None = None
        best_gain = 0
        for u in pool:
            gain = len(cover[u.id] - covered_tasks)
            if gain <= 0:
                continue
            # 优先：覆盖更多未覆盖任务；其次：更近（代价小）；再次：id 小
            if best is None or (gain, -_cheapest_cost(u), -u.id) > (
                best_gain, -_cheapest_cost(best), -best.id
            ):
                best, best_gain = u, gain
        if best is None:
            break  # 剩余机都无法覆盖任何未覆盖任务
        picked.append(best)
        picked_ids.add(best.id)
        covered_tasks |= cover[best.id]
        pool = [u for u in pool if u.id not in picked_ids]

    # ── Fix⑤：服务能力感知 provisioning ─────────────────────────────
    # 最小覆盖已保证每任务有「可达」参与机，但一架机的 80km 航程常不足以同时服务
    # 它覆盖的多个远任务（覆盖≠服务）。把过载任务（最近参与机往返代价 > 剩余航程×
    # 裕度）逐个剥给最近的空闲机，拉入参与，直到无过载或达 K 上限。
    if config.demand_provision_serving and n_tasks > 0 and picked:
        margin = float(config.demand_serve_margin)

        def _cost(u: UAV, t: int) -> float:
            row = row_of_uav.get(u.id) if row_of_uav else None
            if row is None or row >= uav_target.shape[0] or t >= n_cols:
                return float("inf")
            v = uav_target[row, t]
            return float(v) if np.isfinite(v) else float("inf")

        def _nearest_picked(t: int) -> UAV:
            return min(picked, key=lambda u: _cost(u, t))

        def _loads() -> dict[int, float]:
            ld: dict[int, float] = {u.id: 0.0 for u in picked}
            for t in range(n_tasks):
                u = _nearest_picked(t)
                c = _cost(u, t)
                if np.isfinite(c):
                    ld[u.id] += 2.0 * c  # 往返代价
            return ld

        # 最多迭代 len(avail) 次：每次至多拉入 1 架机，避免死循环
        for _ in range(len(avail)):
            if len(picked) >= k or not pool:
                break
            ld = _loads()
            # 找最过载的参与机
            over = [
                (u, ld[u.id])
                for u in picked
                if ld[u.id] > max(0.0, float(getattr(u, "remaining_range", 0.0) or 0.0)) * margin
            ]
            if not over:
                break  # 无过载：航程预算已够服务全部选中任务
            u, _ = max(over, key=lambda x: x[1])
            # 剥该机最远的归属任务，拉最近的空闲机来服务它
            my_tasks = [
                t for t in range(n_tasks)
                if np.isfinite(_cost(u, t)) and _nearest_picked(t).id == u.id
            ]
            if not my_tasks:
                break
            far = max(my_tasks, key=lambda t: _cost(u, t))
            nu = min(pool, key=lambda x: _cost(x, far))
            # 剩余机都服务不了该任务（代价 inf）时停止补机：拉入也服务不了，
            # 只会白占 K 名额（覆盖≠能服务，补机必须真的能接手才拉）。
            if not np.isfinite(_cost(nu, far)):
                break
            picked.append(nu)
            picked_ids.add(nu.id)
            pool = [x for x in pool if x.id not in picked_ids]

    return picked


def _argmin_share(uav_target: np.ndarray, n_tasks: int) -> np.ndarray:
    """每行（UAV）是几个任务的「最近机」（代价最小的行），长度 = 行数。

    marginal 排序键用：``gain_j > 0`` 当且仅当新机对某个任务比已选机都近，
    即它必须是某任务的 argmin 行。按 argmin 份额降序可把「真有贡献」的机排到
    前面，避免零贡献机占位（详见 ``select_participating_uavs`` 的排序分支）。
    """
    if uav_target.size == 0 or n_tasks <= 0:
        return np.zeros(uav_target.shape[0], dtype=int)
    arg = np.argmin(uav_target[:, :n_tasks], axis=0)
    return np.bincount(arg, minlength=uav_target.shape[0]).astype(int)


def _resolve_tour_cap(
    ordered: Sequence[UAV],
    uav_target: np.ndarray,
    n_tasks: int,
    tour_cap: int | None,
) -> int | None:
    """解析 marginal 估计器的单机服务容量（None = 无容量 = 旧口径）。

    显式配置优先；否则按航程自动推导：串 c 个任务的航程 ≈ (c+1)×平均单程
    （含返航），故 ``cap = max(1, ⌊min_u 剩余航程 / 平均单程代价⌋ − 1)``。
    取机队内最小剩余航程（最弱成员决定有效容量）；推导不出（无航程信息或
    代价矩阵为空）时返回 None，估计器退化为无容量。
    """
    if tour_cap is not None:
        return max(1, int(tour_cap))
    if uav_target.size == 0 or n_tasks <= 0 or not ordered:
        return None
    ranges = [float(getattr(u, "remaining_range", 0.0) or 0.0) for u in ordered]
    rng = min(ranges) if ranges else 0.0
    mean_leg = float(np.mean(uav_target[:, :n_tasks]))
    if rng <= 0.0 or mean_leg <= 0.0:
        return None
    return max(1, int(rng / mean_leg) - 1)


def _covering_cost(sub: np.ndarray, n_tasks: int, cap: int | None) -> float:
    """j 架机（``sub`` 的行）覆盖 ``n_tasks`` 个任务的最小总代价。

    ``cap=None``：退化为无容量口径 ``Σ_t min_u C(u,t)``（每架机可「同时」
    覆盖全部任务）。

    ``cap=k``：每架机至多串 k 个任务——按代价升序贪心分配（便宜的先占位），
    装不下的任务按「需另派机」记往返惩罚 ``2×min_u C(u,t)``。这样估计器才
    反映航程/时间窗约束下的真实服务能力。
    """
    if cap is None:
        return float(np.min(sub[:, :n_tasks], axis=0).sum())
    j = sub.shape[0]
    if j == 0 or n_tasks <= 0:
        return 0.0
    pairs = sorted(
        (float(sub[u, t]), u, t) for u in range(j) for t in range(n_tasks)
    )
    load = [0] * j
    done = [False] * n_tasks
    total = 0.0
    for c, u, t in pairs:
        if done[t] or load[u] >= cap:
            continue
        done[t] = True
        load[u] += 1
        total += c
    for t in range(n_tasks):
        if not done[t]:
            total += float(np.min(sub[:, t])) * 2.0
    return total


def _select_by_marginal_takeoff(
    ordered: Sequence[UAV],
    gamma: float,
    uav_target: np.ndarray,
    row_of_uav: Mapping[int, int] | None,
    n_tasks: int,
    tour_cap: int | None = None,
) -> list[UAV]:
    """边际出动决策：容量受限覆盖代价的边际下降决定出动架次。

    估计器 ``est(j)`` = 「j 架机、每架至多 ``cap`` 个任务」的最小总覆盖代价
    （见 ``_covering_cost``），第 j 架的边际贡献 ``gain_j = est(j-1) − est(j)``。

    **停机规则**：``gain_j ≤ γ`` 时**跳过该机、继续试下一架**（不是 break）。
    依据：``est(j)`` 单调非增只保证 ``gain_j ≥ 0``，并**不**保证 gain 单调——
    一架零增益机（对任何任务都不比已选机近）之后仍可能有正增益机。旧实现
    在第一个零增益机处过早停机，导致 K 远小于可用机数、完成率受损。

    **容量**：``tour_cap`` 为单机服务容量（None=按航程自动推导，见
    ``_resolve_tour_cap``）。无容量的旧口径把每架机当成能同时覆盖全部任务，
    gain 与「多派一架的真实收益」脱节，γ 因此从不咬合。

    第一架无条件纳入（保证非空）。
    """
    cap = _resolve_tour_cap(ordered, uav_target, n_tasks, tour_cap)
    picked: list[UAV] = []
    rows: list[int] = []
    prev = 0.0
    for u in ordered:
        row = row_of_uav.get(u.id) if row_of_uav else None
        if row is None or row >= uav_target.shape[0]:
            continue
        if not picked:
            picked.append(u)
            rows.append(row)
            prev = _covering_cost(uav_target[rows][:, :n_tasks], n_tasks, cap)
            continue
        cur = _covering_cost(uav_target[rows + [row]][:, :n_tasks], n_tasks, cap)
        if prev - cur <= gamma:
            continue  # 零/低增益机：跳过继续，不是停机
        picked.append(u)
        rows.append(row)
        prev = cur
    return picked


def _capacity_served_count(sub: np.ndarray, n_tasks: int, cap: int | None) -> int:
    """容量受限下 ``sub`` 的 j 架机最多能服务几个任务（贪心：便宜的先占位）。

    ``cap=None``：无容量口径——有任一有限代价行即算可服务。
    ``cap=k``：每架至多串 k 个——按 (代价, 行, 列) 升序贪心分配，不可达
    （inf）行不占位。与 :func:`_covering_cost` 同一分配语义，只是把
    「总代价」换成「可服务计数」，供字典序边际的第一维使用。
    """
    if sub.shape[0] == 0 or n_tasks <= 0:
        return 0
    if cap is None:
        return int(sum(1 for t in range(n_tasks) if np.isfinite(sub[:, t]).any()))
    j = sub.shape[0]
    pairs = sorted(
        (float(sub[u, t]), u, t) for u in range(j) for t in range(n_tasks)
    )
    load = [0] * j
    done = [False] * n_tasks
    served = 0
    for c, u, t in pairs:
        if done[t] or load[u] >= cap or not np.isfinite(c):
            continue
        done[t] = True
        load[u] += 1
        served += 1
    return served


def _select_by_lexicographic_marginal(
    ordered: Sequence[UAV],
    config: "MUASStageConfig",
    uav_target: np.ndarray,
    row_of_uav: Mapping[int, int] | None,
    n_tasks: int,
) -> list[UAV]:
    """字典序边际出动决策（exp04 B2 第2层精修）。

    边际贡献按**字典序** ``(Δ可服务任务数, −Δ覆盖代价)`` 度量——与四层
    适应度同构：先看多派一架能多服务几个任务（T1 收益层），同数时才看
    覆盖代价下降（T3 成本层）。跳过边际 ``≤ (0, γ)`` 的机（γ =
    ``participation_cost``，折算进成本维；第一架无条件纳入保非空）。
    gain 非单调 ⇒ 跳过该机**继续试下一架**，不停机（同
    ``_select_by_marginal_takeoff`` 的教训）。

    与纯成本边际（``_select_by_marginal_takeoff``）的区别：可服务数放在
    代价之前，避免「省一点航程但不多服务任何任务」的机被误派——这是
    「承诺该紧」在选机层的落点：多派一架必须换来可服务的增量。
    """
    cap = _resolve_tour_cap(ordered, uav_target, n_tasks, config.marginal_tour_cap)
    gamma = float(config.participation_cost)
    picked: list[UAV] = []
    rows: list[int] = []
    for u in ordered:
        row = row_of_uav.get(u.id) if row_of_uav else None
        if row is None or row >= uav_target.shape[0]:
            continue
        if not picked:
            picked.append(u)
            rows.append(row)
            continue
        prev = uav_target[rows][:, :n_tasks]
        cur = uav_target[rows + [row]][:, :n_tasks]
        d_served = _capacity_served_count(cur, n_tasks, cap) - _capacity_served_count(
            prev, n_tasks, cap
        )
        d_cost = _covering_cost(prev, n_tasks, cap) - _covering_cost(
            cur, n_tasks, cap
        )
        if d_served > 0 or (d_served == 0 and d_cost > gamma):
            picked.append(u)
            rows.append(row)
    return picked

