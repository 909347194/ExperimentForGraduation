# -*- coding: utf-8 -*-
"""decode_repair.py — repair_chain 解码与修复算子组（``DecodeRepairMixin``）。

2026-10-27 自 ``muas_stage.py`` 拆出。对应论文 §5(4) 约束处理：
:func:`build_solution` 按固定管线调用下列算子、修到不动点后装配交付解。
算子一览（括号内为管线角色）：

- :func:`_consolidate`               合并修复（闲置合法化，任务只改派不删）
- :func:`_prune`                     经济性剔除 y_j=0（门禁口径下恒关）
- :func:`_repair_range`              航程修复（移除→reflow 改派→证明性暂缓）
- :func:`_choose_end_nests`          终点机巢（内层最小费用流精确分配）
- :func:`_complete_coverage`         覆盖补全（单趟，可达即执行）
- :func:`_load_coverage_fixed_point` 覆盖优先装载（EDD 序、修到不动点）
- :func:`_reorder_for_deadlines`     时间窗重排（纯调序、不改任务集）
- :func:`_repair_sequence`           时序修复（同链按 ``sequence_order`` 升序归位）
- :func:`_prune_zero_reward`         零奖励剔除（默认关）
- :func:`_reflow_task`               改派救援（插入 / 开班 / 换机腾位）

各算子的接受纪律：不删除可达任务（「可达即执行」的存在量化口径）、
修复不制造**新**违约、不可行才记 y_j=0 证明性暂缓。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from task_allocation.methods.muas.constraints.nest_capacity import (
    solve_end_nest_assignment,
)
from task_allocation.methods.muas.coverage_gate import audit_coverage, repair_coverage


class DecodeRepairMixin:
    """repair_chain 解码与修复算子组（混入 :class:`MUASAssignmentEvaluator`）。"""

    def build_solution(
        self,
        assignment: Iterable[tuple[int, int]],
    ) -> AllocationSolution:
        """局部下标的 assignment → AllocationSolution（含 π / y / z）。

        方案 A：终点机巢 z_ub 不在编码内，由 :meth:`_choose_end_nests`
        经内层最小费用流精确求解。
        """
        # exp04 B1：解码模式分流。默认 repair_chain（生产口径，bit-exact）；
        # constructive 为 gated 对照臂（构造式解码，救援前置为主解码）。
        if str(self.config.decode_mode).lower() == "constructive":
            return self._build_constructive(assignment)
        routes_local: dict[int, list[int]] = {}
        seen: set[int] = set()
        for uav_idx, task_idx in assignment:
            uav_idx = int(uav_idx)
            task_idx = int(task_idx)
            if uav_idx < 0 or uav_idx >= len(self.participating):
                continue
            if task_idx < 0 or task_idx >= len(self.tasks):
                continue
            if task_idx in seen:
                continue  # 一任务一机：重复出现只保留首次
            seen.add(task_idx)
            routes_local.setdefault(uav_idx, []).append(task_idx)

        # 局部下标 → 真实 id
        routes: dict[int, list[int]] = {}
        for uav_idx, tids in routes_local.items():
            routes[self.participating[uav_idx].id] = [self._task_ids[i] for i in tids]

        # 航线与终点机巢必须**联合收敛**后才能定稿，顺序不能颠倒：
        # 只有真正有航次的 UAV 才会起飞让出原机巢名额。若先选巢再做剪枝 /
        # 航程修复，被清空的航次实际并未起飞，让出的名额是虚的 → 落地期超卖。
        end_nests: dict[int, int | None] = {}
        prev_state = None
        # 字典序（收益优先）下经济性剔除与目标冲突（丢正收益任务必降 F1），
        # 自动关闭；任务暂缓仅由可行性修复产生。legacy 加权和保留选择性剔除
        # （论文 §5(3) y_j=0 的消融对比口径）。coverage_first（覆盖优先解码，
        # 2026-10-23）下同样强制关闭——「完成是约束」，经济剔除与覆盖目标
        # 直接冲突。
        economic_prune = (
            self.config.prune_unprofitable
            and not self.config.coverage_first
            and not getattr(self.problem.config, "lexicographic", True)
        )
        for _ in range(6):
            # 合并修复：任务少时向更少机合并（闲置合法，U_exec ⊆ U_avail）
            routes, end_nests = self._consolidate(routes, end_nests)
            if economic_prune:
                routes, end_nests = self._prune(routes, end_nests)
            routes, end_nests = self._repair_range(routes, end_nests)
            end_nests = self._choose_end_nests(routes)
            state = (
                tuple((k, tuple(v)) for k, v in sorted(routes.items())),
                tuple(sorted(end_nests.items(), key=lambda kv: str(kv[0]))),
            )
            if state == prev_state:
                break
            prev_state = state

        # Fix⑥ 覆盖补全：把「可达却被留在解外」的选中任务补进航次。
        # 注意 _reflow_task 是**被动**救援（仅在航程修复移除任务时触发），
        # 救不了压根没被 DMDE 编入航次的任务——DMDE 的搜索空间锁死在参与集
        # 内，无法自己引入参与集外的可用机（exp01 的 T10 正是此情形）。
        #
        # coverage_first（2026-10-23 用户裁定，解码/repair 第一段）：改走
        # **覆盖优先装载**——EDD 升序、修到不动点（装载 → 重排 → 换巢 →
        # 航程修复多轮），把 S 内未交付任务装到约定口径（一阶判据）下的
        # 最大可行集；比 _complete_coverage 单趟多两层：不动点 + EDD。
        # 完成语义由这一段承担，门禁退居事后校验（不能单独承担完成语义——
        # E1E2 实证：门禁指标恒真而完成率 −5pp）。
        if self.config.coverage_first:
            routes, end_nests, _n_loaded = self._load_coverage_fixed_point(
                routes, end_nests
            )
        else:
            routes, end_nests = self._complete_coverage(routes, end_nests)
        # 航次内访问顺序的时间窗修复（不改任务集，只调先后）
        routes = self._reorder_for_deadlines(routes)
        # 时序修复：同链任务按 sequence_order 升序归位（硬约束，构造性满足）。
        # 必须在航程闸之前 —— 调序会改变航程，后续 _repair_range 按新序再闸。
        routes = self._repair_sequence(routes)
        # 零奖励剔除：ETA ≥ latest+W 的任务 eff≈0——带着飞只赔迟到惩罚
        # （T2），暂缓把机会留给下周期（2026-10-18 四层适应度配套）。
        # coverage_first 下强制跳过：零奖励是**经济**判据，卸任务与
        # 「完成是约束」冲突；迟交按完成计（w_violation 单一定价），
        # 是否值得飞由成本目标层裁决，不由解码端预先剔除。
        if not self.config.coverage_first:
            routes, end_nests = self._prune_zero_reward(routes, end_nests)
        # 重排按「迟到数, 迟到量, 航程」字典序挑顺序，末任务换人会拉长返航腿，
        # 可能把航次推过剩余航程。先按当前终点闸一次；被移除任务由
        # _reflow_task 改派（全航程口径校验），全部不可行才证明性暂缓。
        routes, end_nests = self._repair_range(routes, end_nests)
        if routes:
            # 新增航次后终点机巢需重新联合分配（容量约束下精确求解）
            end_nests = self._choose_end_nests(routes)
        # 终点机巢在容量竞争下未必是最近巢，改派会再次拉长航程。交付前最后
        # 闸一次：否则完成闸门按「起点→任务→终点巢」全航程判 range_uav_*
        # 弹回——航程/位置已更新却解绑回池不计完成（废飞）。
        routes, end_nests = self._repair_range(routes, end_nests)

        # coverage_first（2026-10-23）：终态**再装载一轮**。前面的换巢 /
        # 重排 / 航程闸可能释放新的装载空间（终点巢改近、顺序调整腾出
        # 时间），覆盖优先口径要求把「约定口径下的最大可行集」做终——
        # 无新可装任务时一轮集合运算即返回（不动点）。
        if self.config.coverage_first:
            routes, end_nests, _n_loaded2 = self._load_coverage_fixed_point(
                routes, end_nests
            )

        # ── 覆盖门禁（2026-10-22）终态挂点：最后一次 _repair_range 之后、
        # _assemble 之前（其后仅 problem.evaluate 不改 routes，终态安全）。
        #   coverage_gate=True  → 能救必救修到不动点（skip 构造归零）；
        #   否则 lexicographic_coverage=True → 纯审计分账（W_skip 层消费）。
        # coverage_first 下门禁退居**事后校验**：完成语义由覆盖优先装载
        # 承担（E1E2 实证门禁单独上场覆盖指标恒真而完成率 −5pp）。
        gate_stats: dict[str, Any] | None = None
        if bool(getattr(self.config, "coverage_gate", False)):
            routes, end_nests, gs = repair_coverage(
                self.problem, routes, end_nests,
                max_rounds=int(getattr(self.config, "coverage_gate_max_rounds", 8)),
            )
            gate_stats = gs.to_dict()
        elif bool(getattr(self.problem.config, "lexicographic_coverage", False)):
            gate_stats = audit_coverage(self.problem, routes, end_nests).to_dict()

        # 终态兜底：门禁/装载救援往航次里插入任务时只按可达与成本挑位置，
        # 不会顾及链内顺序。此处**仅在确实存在逆序时**归位（调序会改变航程，
        # 无违规就不动，避免给已完成闸门放行的解引入新的航程风险）。
        if self._sequence_enabled():
            routes = {
                uid: (self._sequence_fix(tids) if self._seq_violation(tids) else list(tids))
                for uid, tids in routes.items()
            }

        sol = self._assemble(routes, end_nests)
        if gate_stats is not None:
            sol.meta["gate_stats"] = gate_stats
        return sol

    def _window_enabled(self) -> bool:
        cons = getattr(self.problem.config, "constraints", None)
        return bool(getattr(cons, "time_window", True))

    def _sequence_enabled(self) -> bool:
        """时序（执行顺序）约束是否启用（``constraints.sequence``）。"""
        cons = getattr(self.problem.config, "constraints", None)
        return bool(getattr(cons, "sequence", True))

    def _seq_order_of(self, tid: int) -> tuple[Any, float]:
        """任务的 ``(sequence_group, sequence_order)``；无链时 ``(None, 0)``。"""
        task = self._task_by_id.get(tid)
        if task is None:
            return (None, 0.0)
        grp = getattr(task, "sequence_group", None)
        if grp is None:
            return (None, 0.0)
        order = getattr(task, "sequence_order", None)
        return (grp, float(order if order is not None else tid))

    def _seq_violation(self, seq: Sequence[int]) -> bool:
        """该访问序是否违反同链顺序（链内出现「序号下降」即为逆序）。"""
        seen: dict[Any, float] = {}
        for tid in seq:
            grp, order = self._seq_order_of(tid)
            if grp is None:
                continue
            prev = seen.get(grp)
            if prev is not None and order < prev - 1e-9:
                return True
            seen[grp] = order if prev is None else max(prev, order)
        return False

    def _sequence_fix(self, seq: Sequence[int]) -> list[int]:
        """把一条航次内同链任务按 ``sequence_order`` 升序归位（最小位移重排）。

        做法：每个链占据的**位置槽位**保持不变，只把链内成员按序号升序
        重新填回这些槽位 —— 链外任务的相对位置完全不动，改动量最小。

        为什么不在最后靠惩罚压制：时序是**硬约束**（违反即判物理不可行），
        交付解若不主动归位，硬违约会让整批解被判废，与「完成是约束」冲突。
        归位只调序、不增删任务，不改变执行集。
        """
        out = list(seq)
        if len(out) <= 1:
            return out
        groups: dict[Any, list[int]] = {}
        for pos, tid in enumerate(out):
            grp, _order = self._seq_order_of(tid)
            if grp is not None:
                groups.setdefault(grp, []).append(pos)
        if not groups:
            return out
        for _grp, positions in groups.items():
            if len(positions) <= 1:
                continue
            members = [out[p] for p in positions]
            members.sort(key=lambda t: (self._seq_order_of(t)[1], t))
            for slot, tid in zip(positions, members):
                out[slot] = tid
        return out

    def _repair_sequence(
        self, routes: Mapping[int, Sequence[int]]
    ) -> dict[int, list[int]]:
        """航次内时序修复（不改任务集，只调先后）。

        挂点：``_reorder_for_deadlines`` 之后、航程闸之前 —— 顺序变化会改变
        航程，必须让后续的 ``_repair_range`` 按新顺序再闸一次。
        """
        if not self._sequence_enabled():
            return {uid: list(tids) for uid, tids in routes.items()}
        return {uid: self._sequence_fix(tids) for uid, tids in routes.items()}

    def _reorder_for_deadlines(
        self, routes: Mapping[int, Sequence[int]]
    ) -> dict[int, list[int]]:
        """在不丢任务的前提下重排每个航次的访问顺序，减少时间窗违约。

        为什么必须有这一步：DMDE 搜索的是「UAV 归属 + 访问顺序」，但解码后的
        修复链（``_consolidate`` / ``_repair_range`` / ``_complete_coverage``）
        会往航次里增删任务，顺序却沿用编码原序 —— 于是「本来得及」的任务被
        排到后面而迟到。统一 DEM 距离口径后该缺陷被放大（exp01 完成率
        95% → 70%）：实测 6 个未完成任务**直飞全部来得及**（余量 638~3291 s），
        零个物理不可达，纯粹是被排在了多任务航次的后段。

        访问顺序不改变任务集，是纯可行性问题；在「可达即执行」的方法论下属于
        必须修复项，航程（成本）只是同分时的次级判据。

        候选集是常数个（不是 n! 枚举，避免拖垮每代解码）：
            原序 / EDD(latest 升序) / earliest 升序 / 松弛量升序 / 最近邻
        判据字典序：``(迟到任务数, 迟到总量, 航程)``，并剔除超出剩余航程的候选。
        """
        if not self._window_enabled():
            return {uid: list(tids) for uid, tids in routes.items()}

        out: dict[int, list[int]] = {}
        for uid, tids in routes.items():
            seq = list(tids)
            if len(seq) <= 1:
                out[uid] = seq
                continue
            out[uid] = self._best_order(uid, seq)
        return out

    def _best_order(self, uav_id: int, tids: Sequence[int]) -> list[int]:
        """为单个航次挑最优访问顺序。"""
        by_id = {tid: self._task_by_id.get(tid) for tid in tids}
        if any(t is None for t in by_id.values()):
            return list(tids)

        uav = self._uav_by_id.get(uav_id)
        budget = float(getattr(uav, "remaining_range", float("inf")) or float("inf"))
        start = (
            (float(uav.x), float(uav.y), float(uav.z)) if uav is not None else (0.0, 0.0, 0.0)
        )

        def latest_of(tid: int) -> float:
            lst = getattr(by_id[tid], "latest", None)
            return float(lst) if lst is not None else float("inf")

        def earliest_of(tid: int) -> float:
            est = getattr(by_id[tid], "earliest", None)
            return float(est) if est is not None else 0.0

        candidates: list[list[int]] = [list(tids)]
        candidates.append(sorted(tids, key=lambda t: (latest_of(t), earliest_of(t), t)))
        candidates.append(sorted(tids, key=lambda t: (earliest_of(t), latest_of(t), t)))
        if any(latest_of(t) != float("inf") for t in tids):
            candidates.append(
                sorted(tids, key=lambda t: (latest_of(t) - earliest_of(t), latest_of(t), t))
            )
        # 最近邻（从 UAV 起点贪心）：航程最短的候选，成本维度的对照
        remaining = list(tids)
        nn: list[int] = []
        cur = start
        while remaining:
            nxt = min(
                remaining,
                key=lambda t: (
                    self.problem.costs.travel(cur, by_id[t].position),
                    t,
                ),
            )
            nn.append(nxt)
            cur = by_id[nxt].position
            remaining.remove(nxt)
        candidates.append(nn)

        # 时序优先：候选序先剔掉违反同链顺序的（硬约束），让「最优」只在
        # 合法序里挑——先挑后修会把迟到/航程指标算在非法序上，修完又变。
        # 若所有候选都违反（罕见：链内任务被硬塞进同一航次），保留全部候选
        # 交由下游 _repair_sequence 归位，不在此处丢任务。
        if self._sequence_enabled():
            legal = [c for c in candidates if not self._seq_violation(c)]
            if legal:
                candidates = legal

        def _evaluate(seq: Sequence[int]) -> tuple[int, float, float] | None:
            """返回 ``(迟到数, 迟到量, 航程)``；超剩余航程 → None（不可行）。"""
            n_late = 0
            late_total = 0.0
            length = 0.0
            prev = start
            for pos, tid in enumerate(seq):
                task = by_id[tid]
                length += self.problem.costs.travel(prev, task.position)
                prev = task.position
                if length > budget + 1e-6:
                    return None
                arrive = self._arrival_time_of(uav_id, seq, pos)
                lst = getattr(task, "latest", None)
                if lst is not None and arrive > float(lst) + 1e-6:
                    n_late += 1
                    late_total += arrive - float(lst)
            # 返航腿必须计入航程预算。完成闸门（check_range_constraint →
            # problem.tour_path_cost）与 _repair_range/_tour_cost/_reflow_task
            # 用的都是「起点 → 各任务 → 终点巢」全航程；只查外出链会把
            # 返航后超限的航次放行，漏到交付即被完成闸门弹回。
            # 终点机巢此刻尚未定稿（_choose_end_nests 在重排之后才跑），取
            # 最近可用巢作为返航下界——实际终点只会更远或相等，保守。
            length += self._return_leg_cost(seq[-1], None)
            if length > budget + 1e-6:
                return None
            return (n_late, round(late_total, 6), round(length, 6))

        base_key = _evaluate(list(tids))
        if base_key is not None and base_key[0] == 0:
            # 原早退：零迟到 → 直接返回原序（每代每体都要解码一次，不早退
            # 会把求解时间从 ~72 s 抬到 ~121 s，见实测）。
            #
            # 2026-10-24 修复：早退不能连「航程」一起退掉。候选判据是
            # ``(迟到数, 迟到量, 航程)`` 字典序，航程排第 3；原序零迟到时
            # 直接返回 ⇒ 航程永远进不了比较。exp01 各周期 final_late_s 常
            # 态为 0 ⇒ **航程从未被优化过**，实测 polish 增益 100% 来自 F1
            # （反证：若来自路径需 Δpath 达 154 万 km，实测 435 km）。
            # 证据见 docs/fitness_path_optimization_diagnosis_2026-10-24.md §3。
            #
            # 改为：零迟到时仍评估「最近邻」这一唯一以航程为目标的候选
            # （candidates[-1]），仅在「仍零迟到 且 航程严格更短」时采纳。
            # 语义 = 覆盖优先、成本随后：执行集不变（F1 不动）、不引入迟到、
            # 不超剩余航程，纯成本改进 ⇒ 不触碰主适应度阶梯。
            # 性能：只多一次 _evaluate（全候选评估 72→121 s，单候选约其 1/4）。
            if not self.config.reorder_range_optimize:
                return list(tids)
            nn_seq = candidates[-1]
            nn_key = _evaluate(nn_seq) if list(nn_seq) != list(tids) else None
            if (
                nn_key is not None
                and nn_key[0] == 0
                and nn_key[2] < base_key[2] - self.config.reorder_range_eps
            ):
                return list(nn_seq)
            return list(tids)

        best_seq = list(tids)
        best_key = base_key
        for seq in candidates[1:]:
            key = _evaluate(seq)
            if key is None:
                continue
            if best_key is None or key < best_key:
                best_key = key
                best_seq = list(seq)
        return best_seq

    def _choose_end_nests(
        self,
        routes: Mapping[int, Sequence[int]],
    ) -> dict[int, int | None]:
        """为有航次的 UAV 选终点机巢（含飞行中也要占名额）。

        方案 A（master 主线）：终点机巢由内层子问题
        :func:`solve_end_nest_assignment` 在末态容量约束下**精确求解**，
        联合考虑所有 UAV 的终点竞争（不是各抢最近巢、先到先得）；返航代价
        进入 F2 完整评价，间接作用于进化方向。

        「终点进编码」的联合变体（编码终点优先、精确分配兜底）已从 master
        移除，在 pro 分支实现。
        """
        if not self.nests:
            return {uid: None for uid in routes}

        # 事件驱动：被标记不可用的机巢不可选为终点（多机巢异巢终止的容错分支）
        excluded = self.excluded_nest_ids
        cand_nests = [n for n in self.nests if n.id not in excluded]
        # 终点机巢可达性约束开关：关闭时改派不再过滤不可达巢
        # （与 problem.check_feasible 的 end_nest_reachability 共用判据）
        check_reach = bool(
            getattr(self.problem.config, "constraints", None)
            and self.problem.config.constraints.end_nest_reachability
        )

        # 内层精确分配（方案 A）：为有航次的 UAV 联合选终点机巢
        last_cost: dict[int, dict[int, float]] = {}
        for uav_id, tids in routes.items():
            if not tids:
                continue
            last_node = tids[-1]
            costs: dict[int, float] = {}
            for nest in cand_nests:
                if not nest.available or nest.capacity <= 0:
                    continue
                c = float(
                    self.problem.costs.cost(
                        last_node, self.problem.costs.nest_key(nest.id)
                    )
                )
                if check_reach and not np.isfinite(c):
                    # 末任务→机巢不可达（地形遮挡 / 禁飞）：排除该候选，
                    # 不纳入容量感知改派的候选集（可达性约束落地）
                    continue
                costs[nest.id] = c
            if costs:
                last_cost[uav_id] = costs

        if not last_cost:
            return {uid: None for uid in routes}

        chosen = solve_end_nest_assignment(
            self.fleet_uavs,
            cand_nests,
            last_cost,
            planning_uav_ids=list(last_cost),
            excluded_nest_ids=excluded,
        )
        return chosen

    def _prune(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None]]:
        """剔除执行代价高于收益的任务（论文 §4.2 的 ``y_j = 0``）。

        判据：移除任务 j 节省的路径代价 ``saving`` 满足

            λ · saving > R_j^eff · (1 + margin)

        则本周期不执行 j（保留至下一规划周期）。其中 ``R_j^eff`` 是含等待
        补偿的有效收益（论文 §3.2 的 ``W_i(t)``）：任务滞留越久，补偿越大，
        越不容易被剔除，从而避免偏远任务永久滞留。
        """
        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)
        lam = self._lambda
        margin = float(self.config.prune_margin)

        for _ in range(max(1, self.config.prune_max_rounds)):
            removed = False
            for uav_id in list(work):
                tids = work[uav_id]
                if not tids:
                    continue
                for pos in range(len(tids)):
                    tid = tids[pos]
                    saving = self._removal_saving(uav_id, tids, pos, ends.get(uav_id))
                    reward = self._eff_reward.get(tid, 0.0)
                    if saving is None:
                        continue
                    if lam * saving > reward * (1.0 + margin) + 1e-9:
                        tids.pop(pos)
                        removed = True
                        break
                if removed:
                    break
            if not removed:
                break

        # 航次被清空的 UAV 转为空闲，回到原归属机巢（释放名额）
        for uav_id, tids in work.items():
            if not tids:
                ends[uav_id] = None

        return work, ends

    def _prune_zero_reward(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None]]:
        """剔除「必然零奖励」的任务（ETA ≥ latest + W ⇒ eff≈0，y_j=0 暂缓）。

        动机（2026-10-18）：迟到折减 ``eff = R·max(0, 1 − L/W)`` 下，到达
        晚于 ``latest + W`` 的任务对 F1 的贡献为 0，却仍占用航次与航程、
        并计迟到惩罚——经济上等价于废飞，却按「完成」计。字典序下
        ``_prune``（经济性剔除）整体关闭（丢正收益任务必降 F1），而零奖励
        任务**不降 F1**：剔除它是严格的适应度改进（省 T2 迟到惩罚），
        还把机会留给下一周期（可能按时交付）。与「尽可能多且**正确地**
        完成任务」的长程目标一致。

        做法：逐航次按完成闸门同口径模拟 ETA，对第一个零奖励任务移除并
        :meth:`_reflow_task`（``full_window``：只接受「整条航次不新增迟到」
        的落点，不制造新违约；不做换机腾挪——剔除场景下改派不成立就应当暂缓）。移除只会让后续任务更早，迭代至
        无零奖励任务即终止（每轮至少永久移除一个）。
        """
        decay = getattr(self.problem.config, "late_reward_decay_seconds", None)
        if not decay or float(decay) <= 0.0:
            return {k: list(v) for k, v in routes.items()}, dict(end_nests)
        if not self.config.prune_zero_reward or not self._window_enabled():
            return {k: list(v) for k, v in routes.items()}, dict(end_nests)
        W = float(decay)
        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)

        # 每轮至少永久移除一个任务，轮数上界 = 任务数 + 4
        for _ in range(len(self._task_ids) + 4):
            changed = False
            for uav_id in sorted(work):
                tids = work[uav_id]
                if not tids:
                    continue
                for pos in range(len(tids)):
                    tid = tids[pos]
                    latest = self._task_latest(tid)
                    if latest is None:
                        continue
                    if self._arrival_time_of(uav_id, tids, pos) < latest + W - 1e-9:
                        continue
                    # 零奖励：移除；改派到「整航次不新增迟到」处则保住，否则暂缓
                    tids.pop(pos)
                    self._reflow_task(
                        tid, work, ends,
                        active_completion=True, full_window=True, allow_evict=False,
                    )
                    changed = True
                    break
                if changed:
                    break
            if not changed:
                break

        # 航次被清空的 UAV 转为空闲，回到原归属机巢（释放名额）
        for uav_id, tids in work.items():
            if not tids:
                ends[uav_id] = None
        return work, ends

    def _consolidate(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None]]:
        """合并修复：任务少时把航次并入更少的机（闲置合法化，解码端表达）。

        背景：编码层「每架参与 UAV 至少一个任务」是 DE 映射的布局不变量
        （cost_vector 的 K 个起始槽位，见 ``_inverse_phi_srp``）；而"任务少时
        少派更省"要求解能表达闲置。本修复在解码端补上该表达（方案原文：
        "在编码**或解码**中增加'不参与本轮任务'的表达方式"）：

        - 整航次迁移：把供体航次的任务逐个插入受体航次的最优位置，
          仅当「起始腿 + 任务链 + 返航腿（近似）」总代价下降时合并；
        - 合并后供体航次置空 → 该 UAV 本轮闲置（U_exec ⊆ U_avail），
          空航次不计返航义务、不起飞让位（与 _repair_range 同语义）；
        - 不删任务（区别于 _prune 的 y_j=0 暂缓），只改派；
        - 合并带**结构性航程门**：合并后航次超受体航程预算则不合并，
          从源头杜绝"合并制造不可行、修复再删任务"的吞任务链路。
        """
        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)

        improved = True
        while improved:
            improved = False
            carriers = [u for u, t in work.items() if t]
            for src in carriers:
                best: tuple[float, int, list[int]] | None = None
                for dst in carriers:
                    if dst == src or not work[dst]:
                        continue
                    merged = self._cheapest_insertion(
                        dst, work[dst], work[src], ends.get(dst)
                    )
                    new_cost = self._tour_cost(dst, merged, ends.get(dst))
                    old_cost = self._tour_cost(
                        dst, work[dst], ends.get(dst)
                    ) + self._tour_cost(src, work[src], ends.get(src))
                    # 结构性航程门（可行性不依赖事后修复）：合并后的航次必须
                    # 落入受体航程预算，否则不合并——合并修复不得制造超航程航次，
                    # 否则下游 _repair_range 只能靠删任务擦除，吞掉本可执行的任务。
                    if (
                        new_cost < old_cost - 1e-9
                        and new_cost <= self._range_limit(dst) + 1e-6
                    ):
                        gain = old_cost - new_cost
                        if best is None or gain > best[0]:
                            best = (gain, dst, merged)
                if best is not None:
                    _gain, dst, merged = best
                    work[dst] = merged
                    work[src] = []
                    ends[src] = None
                    improved = True
                    break  # 受体已变，重算下一轮
        return work, ends

    def _cheapest_insertion(
        self,
        uav_id: int,
        base: Sequence[int],
        extra: Sequence[int],
        end_nest_id: int | None,
    ) -> list[int]:
        """把 ``extra`` 的任务逐个按最优位置插入 ``base``（航次代价贪心）。"""
        merged = list(base)
        for tid in extra:
            best_pos, best_cost = 0, float("inf")
            for pos in range(len(merged) + 1):
                cand = merged[:pos] + [tid] + merged[pos:]
                c = self._tour_cost(uav_id, cand, end_nest_id)
                if c < best_cost:
                    best_cost, best_pos = c, pos
            merged.insert(best_pos, tid)
        return merged

    def _repair_range(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None]]:
        """航程约束修复（论文 §5(4) 约束处理）。

        与机巢容量同理，只靠 penalty 兜底不够——DMDE 会在不可行个体上浪费
        代数。这里对超航程的航次逐个移除「移除后节省最多」的任务，
        直到航次代价落入剩余航程内；被移除的任务本周期不执行（y_j = 0）。
        """
        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)

        for uav_id in sorted(work):
            uav = self._uav_by_id.get(uav_id)
            if uav is None:
                continue
            limit = float(uav.remaining_range)
            while work[uav_id]:
                if self._tour_cost(uav_id, work[uav_id], ends.get(uav_id)) <= limit + 1e-6:
                    break
                best_pos: int | None = None
                best_saving = float("-inf")
                for pos in range(len(work[uav_id])):
                    saving = self._removal_saving(
                        uav_id, work[uav_id], pos, ends.get(uav_id)
                    )
                    if saving is not None and saving > best_saving:
                        best_saving = saving
                        best_pos = pos
                if best_pos is None:
                    break
                tid = work[uav_id].pop(best_pos)
                # 只改派不删任务：先回流到其它可行位置（现有航次 / 闲置机
                # 单任务航次），全部不可行才本轮暂缓（y_j=0，证明性暂缓）。
                self._reflow_task(tid, work, ends)

            if not work[uav_id]:
                ends[uav_id] = None

        return work, ends

    def _complete_coverage(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None]]:
        """Fix⑥ 覆盖补全：**可达即执行**（存在量化口径）。

        DMDE 只在 ``participating`` 内搜索，参与集外的可用机它无从调度；而
        ``_reflow_task`` 是被动救援（仅在航程修复**移除**任务时触发），对
        「压根没被编入航次」的任务不会调用。两者叠加，就出现物理可达却被留在
        解外的任务（exp01 的 T10：参与集外的 UAV1/6/8 单任务航次 67.3/71.0/
        76.1km 均 ≤ 80km，却从未拿到航次）。

        这里主动补：对每个未分配的选中任务，按 :meth:`_reflow_task` 的顺序
        尝试「插入现有航次的最优可行位置 → 拉入闲置可用机（**全机队**）开单
        任务航次」。只要航程可行就执行——可达性是存在量化，不是方案内判定。

        真不可达的任务不会被硬塞：**航程/巢容量等硬约束**不可达（任何机
        单任务航次都超限，如 exp01 的 T19，最小单任务航次 82.4km）时
        _reflow_task 返回 False，保持 y_j=0。窗口是软约束（2026-10-24
        口径：迟到计价、计完成），「飞得到但赶不到 deadline」的任务
        **必须装**（如 exp01 的 T10：迟到交付＝完成，不是白飞）。
        """
        # 门禁口径（唯一主口径，2026-10-24）下经济性剔除已永久关闭：原 legacy
        # 加权和（lexicographic=False）路径已从代码移除，而门禁口径「完成是约束」
        # 不容许丢弃可达任务。故强制补全**始终执行**（可达即执行）——这正是本修复
        # 「可达即执行、但不得违反约束」的方法论前提。
        # （2026-10-27 清理死分支：原 `economic_prune = False` + 恒假 return 已删，
        # 语义逐位不变；build_solution 处的 economic_prune（真开关）不受影响。）

        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)

        assigned = {tid for tids in work.values() for tid in tids}
        for tid in self._task_ids:
            if tid in assigned:
                continue
            # 窗口是软约束（2026-10-24：迟到计价、计完成），主动补全不查窗。
            if self._reflow_task(tid, work, ends, active_completion=True):
                assigned.add(tid)

        return work, ends

    def _load_coverage_fixed_point(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None], int]:
        """覆盖优先装载：把 S 内未交付任务装到**约定口径下的最大可行集**。

        用户 2026-10-23 裁定（解码/repair 第一段）：先按覆盖目标装，再
        在不减少已装集合的约束下做成本 polish。本函数即第一段。

        约定口径（与覆盖门禁同源，2026-10-24 软窗）：**一阶判据** =
        航程（全航程 ≤ 剩余航程）+ 开班机巢容量；窗口不否决（迟到
        软约束，迟到计价、计完成）；救援通道含插入现有航次、闲置机开班
        （参与集 → 全机队 fleet 口径）、换机腾位（Fix⑦，depth<2，
        迁移任务受全航次单调纪律约束——不制造**新**迟到）。不含
        「两个未覆盖任务拼班才装得下」的组合可行——与 ``gate_stats``
        的 shortfall 声明同边界。

        顺序 EDD（deadline 升序，最紧急先装）——与
        :func:`coverage_gate.repair_coverage` 一致；每轮装载后重排 +
        重选终点巢 + 航程修复（装载改变航次结构，可能释放或占用后续
        任务的装载空间），修到不动点或轮数上限。只增不减：任务移除
        仅由 :meth:`_repair_range` 的硬约束触发，且被移除任务当场改派
        （reflow），全部不可行才是证明性暂缓（与门禁 shortfall 同义）。

        与 ``_complete_coverage`` 的区别：① EDD 序（最紧急先装，装载
        优先级确定）；② 不动点多轮（装载 → 重排 → 换巢 → 修复），
        把后续 pass 释放的空间用尽；③ 与 ``_polish_routes_monotone``
        组成两段式，服务于 coverage_first 口径。
        """
        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)
        loaded = 0
        for _ in range(max(1, int(self.config.coverage_first_max_rounds))):
            assigned = {tid for tids in work.values() for tid in tids}
            deferred = [tid for tid in self._task_ids if tid not in assigned]
            if not deferred:
                break
            # EDD：deadline 升序（无 deadline 排最后），同刻按 id 保确定
            deferred.sort(
                key=lambda t: (self._task_latest(t) if self._task_latest(t) is not None else float("inf"), t)
            )
            round_loaded = 0
            for tid in deferred:
                # active_completion=True：主动补全（启用换机腾位 Fix⑦，
                # 救援强度高于门禁的插入/开班两级）；窗口是软约束，不查窗。
                if self._reflow_task(tid, work, ends, active_completion=True):
                    round_loaded += 1
            loaded += round_loaded
            if round_loaded == 0:
                break  # 不动点：一阶口径下无更多可装任务
            # 装载改变了航次结构 → 重排 + 重选终点巢 + 航程闸，可能释放
            # 新的装载空间（也可能把某航次推超航程 → 修复时改派）。
            work = self._reorder_for_deadlines(work)
            ends = self._choose_end_nests(work) if work else {}
            work, ends = self._repair_range(work, ends)
        return work, ends, loaded

    def _reflow_task(
        self,
        task_id: int,
        work: dict[int, list[int]],
        ends: dict[int, int | None],
        active_completion: bool = False,
        allow_evict: bool = True,
        depth: int = 0,
        full_window: bool = False,
    ) -> bool:
        """把从超限航次移除的任务改派到其它可行位置；全部不可行才暂缓。

        顺序与字典序评价同构——**可行性优先、代价次之**：

        1. 插入现有航次的最优可行位置（插入后航次 ≤ 该机航程预算）；
        2. 给闲置参与 UAV 开单任务航次（闲置合法，U_exec ⊆ U_avail）；
        2b. Fix⑥：救援池扩到全可用机队（参与集外的闲置机同样可用）；
        2c. Fix⑦ 换机：某架机的单任务航次 ``[task_id]`` 可行，但它已被
            占用 → 尝试把它的现有任务整体迁到别处，空出该机飞 ``task_id``。
        （仅 ``active_completion`` 下启用，即主动覆盖补全；被动救援不动别人
        的任务，保持修复链最小干预。）
        3. 均不可行 → 返回 False：任务本轮 y_j=0，属**证明性暂缓**，
           而非旧实现的"顺手删除"（任务物理可行却因邻座航次装不下而被吞）。

        为什么需要 2c：exp01 的 T2 三个周期全部 defer——实测全程只有
        UAV2 能飞它（单航次 47.0 km ≤ 80 km），其余 9 架航程超限；而
        UAV2 每周期都被 DMDE 派了别的航次。只做「插入 + 拉闲置」判不出
        可达，必须允许换机。

        全程确定性（无随机、按 id 升序遍历），评估缓存安全。
        ``depth``：换机链深度上限（``depth < 2`` 才允许 2c）——被腾出的
        任务迁往别处时自身也可能需要换机（exp01 的 T7），一层不够；
        但无限递归会震荡且拖慢 evaluate，故限制为两层。
        ``full_window``：为 True 时插入判断用 :meth:`_window_ok_all`
        （整条航次所有任务不得新增迟到）。2c 的迁移调用置 True——换机是
        事后强制补全，不看 fitness，只认可行性与**单调修复纪律**：不得
        把已守约的任务推成迟到（exp01 换机版实测 95%→85%，3 个新 tw
        违约全源于此）。注意：迟到本身是软约束（T2 计价、计完成），
        可装判据不查窗；此处仅约束「修复不得制造**新**迟到」。
        """
        # 1) 插入现有航次（含原航次——换个位置能装下就不用暂缓）
        best: tuple[float, int, list[int]] | None = None
        for uid in sorted(work):
            if not work[uid]:
                continue
            limit = self._range_limit(uid)
            base = work[uid]
            for pos in range(len(base) + 1):
                cand = base[:pos] + [task_id] + base[pos:]
                c = self._tour_cost(uid, cand, ends.get(uid))
                if (
                    c <= limit + 1e-6
                    # 窗口是软约束（2026-10-24 口径：迟到计价、计完成），
                    # 可装判据只查航程——「飞得到但赶不到」的任务必须装
                    # （迟到交付＝完成，不是白飞）。full_window：换机迁移
                    # 保留全航次单调纪律（不得把同航次后续任务搞迟到——
                    # 修复不制造**新**迟到，与 cost_polish 四道门同纪律）。
                    and (self._window_ok_all(uid, cand) if full_window else True)
                    and (best is None or c < best[0] - 1e-9)
                ):
                    best = (c, uid, cand)
        if best is not None:
            work[best[1]] = best[2]
            return True
        # 2) 闲置 UAV 单任务航次：先在参与集内找（历史行为，优先保持原解结构）
        for u in sorted(self.participating, key=lambda x: x.id):
            if work.get(u.id):
                continue
            if (
                self._tour_cost(u.id, [task_id], ends.get(u.id)) <= self._range_limit(u.id) + 1e-6
            ):
                work[u.id] = [task_id]
                return True
        # 2b) Fix⑥：救援池扩到全可用机队。参与集是按「最小距离覆盖」挑出的子集，
        # 它之外的闲置机物理上同样可用；只在参与集内找，会让可达任务因「参与集
        # 恰好全满」被判 y_j=0（假暂缓）。把它们纳入救援池，才真正落实
        # 「可达即执行」——可达性是存在量化，不是方案内判定。
        # 注：无需把新机并入 self.participating——Lamarckian 回写对参与集外的
        # UAV 本就安全跳过（``local_uav_idx.get(...) is None``），且终点机巢
        # 选择与装配都按 routes 遍历，新机同样被正确处理。
        if str(self.config.reflow_rescue_scope).lower() == "fleet":
            part_ids = {u.id for u in self.participating}
            for u in sorted(self.available_uavs, key=lambda x: x.id):
                if u.id in part_ids or work.get(u.id):
                    continue
                if not getattr(u, "is_available", True):
                    continue
                if (
                    self._tour_cost(u.id, [task_id], ends.get(u.id)) <= self._range_limit(u.id) + 1e-6
                ):
                    work[u.id] = [task_id]
                    return True
        # 2c) Fix⑦ 换机：把「能单飞 task_id 但已被占用」的机腾出来。
        # 仅主动补全（active_completion）时启用；迁移允许有限深度递归换机。
        if active_completion and allow_evict and depth < 2:
            for uid in sorted(work):
                if not work[uid]:
                    continue
                if not (
                    self._tour_cost(uid, [task_id], ends.get(uid))
                    <= self._range_limit(uid) + 1e-6
                ):
                    continue
                moved = list(work[uid])
                trial = {k: list(v) for k, v in work.items()}
                trial[uid] = []
                ok = True
                for tid in moved:
                    if not self._reflow_task(
                        tid, trial, ends, active_completion=True,
                        allow_evict=True, depth=depth + 1, full_window=True,
                    ):
                        ok = False
                        break
                if ok and not trial.get(uid):
                    # 迁移可能把任务回填到 uid 自己（腾空机的唯一去处），
                    # 此时 uid 并未真正腾空；若无条件覆盖 trial[uid]，
                    # 回填的任务会被直接丢弃（exp01 测试实测暴露）。
                    # 回填＝没腾空 → 本架换机失败，继续试下一架。
                    trial[uid] = [task_id]
                    work.clear()
                    work.update(trial)
                    return True
        return False

