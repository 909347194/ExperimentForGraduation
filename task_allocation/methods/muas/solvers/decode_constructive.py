# -*- coding: utf-8 -*-
"""decode_constructive.py — 构造式解码（``ConstructiveDecodeMixin``，exp04 B1）。

2026-10-27 自 ``muas_stage.py`` 拆出。``decode_mode="constructive"`` 对照臂：
把「救援」前置为主解码，逐任务做可行性内联插入；与 repair_chain 共用
``tour_cost`` 原语与 ``_choose_end_nests`` / ``_reorder_for_deadlines`` /
``_assemble``，口径一致。三条红线见 ``MUASStageConfig.decode_mode`` 注释。
"""

from __future__ import annotations

from typing import Iterable, Sequence


class ConstructiveDecodeMixin:
    """构造式解码算子组（混入 :class:`MUASAssignmentEvaluator`）。"""

    def _feasible_insert(
        self,
        uav_id: int,
        seq: Sequence[int],
        tid: int,
    ) -> list[int] | None:
        """``tid`` 插入 ``seq`` 的最优**可行**位；不可行返回 None。

        位置按插入代价最小选（归属由基因组定、位置可重算——与 repair_chain
        的 ``_reorder_for_deadlines`` 同性质，是 B1 对照臂的语义差异之一）；
        返航腿按最近巢保守估（终点巢此时未定，实际只会更远或相等）。
        可行判据 = 航程（窗口是软约束，2026-10-24 口径：迟到计价、计完成，
        不否决可装性）。
        """
        best_pos: int | None = None
        best_cost = float("inf")
        limit = self._range_limit(uav_id)
        for pos in range(len(seq) + 1):
            cand = list(seq[:pos]) + [tid] + list(seq[pos:])
            c = self._tour_cost(uav_id, cand, None)
            if c > limit + 1e-6:
                continue
            if c < best_cost - 1e-12:
                best_cost, best_pos = c, pos
        if best_pos is None:
            return None
        return list(seq[:best_pos]) + [tid] + list(seq[best_pos:])

    def _rescue_task(
        self,
        tid: int,
        routes: dict[int, list[int]],
        exclude: set[int],
    ) -> bool:
        """构造式解码的出列任务救援（第3层安全网，Fix⑥ 内化）。

        顺序即优先级：① 插入其它**非空**航次的最优可行位（代价最小）；
        ② 闲置机开单任务航次——先参与集内、再（``reflow_rescue_scope="fleet"``
        时）全可用机队。可达即执行（存在量化），但不得制造新违约；
        全部不可行返回 False（调用方记 y_j=0 证明性暂缓，不谎报完成）。
        与 ``_reflow_task`` 的区别：本函数只做「插入 + 闲置开单」两级
        （换机腾位 Fix⑦ 留给 repair_chain 的 _reflow_task，构造式臂不复制
        该补丁——少一层打补丁正是 B1 要检验的假设）。
        """
        # ① 现有非空航次（参与机；exclude 外的）
        best: tuple[float, int, list[int]] | None = None
        for uid in sorted(routes):
            if uid in exclude or not routes.get(uid):
                continue
            new_seq = self._feasible_insert(
                uid, routes[uid], tid
            )
            if new_seq is None:
                continue
            c = self._tour_cost(uid, new_seq, None)
            if best is None or c < best[0] - 1e-12:
                best = (c, uid, new_seq)
        if best is not None:
            routes[best[1]] = best[2]
            return True
        # ② 闲置机开单：参与集优先，其次全机队（fleet 口径）
        part_ids = {u.id for u in self.participating}
        pools: list[Sequence[UAV]] = [self.participating]
        if str(self.config.reflow_rescue_scope).lower() == "fleet":
            extra = [u for u in self.available_uavs if u.id not in part_ids]
            if extra:
                pools.append(extra)
        for pool in pools:
            for u in sorted(pool, key=lambda x: x.id):
                uid = u.id
                if uid in exclude or routes.get(uid):
                    continue
                if not getattr(u, "is_available", True):
                    continue
                if (
                    self._tour_cost(uid, [tid], None)
                    <= self._range_limit(uid) + 1e-6
                ):
                    routes[uid] = [tid]
                    return True
        return False

    def _build_constructive(
        self, assignment: Iterable[tuple[int, int]]
    ) -> AllocationSolution:
        """构造式解码（exp04 B1，``decode_mode="constructive"``）。

        与 repair_chain 的分工：repair_chain 先按基因组装航次、再反应式修复
        （合并→删→救→重排→删→闸 六道 pass）；本函数把「救援」前置为**主
        解码**，逐任务做可行性内联插入：

        1. 初始航次 = assignment 的逐机顺序——**完整保留 DMDE 结构信号**
           （归属与顺序先验；红线1：不把 cost_vector 当排序键）；
        2. 逐任务重建航次：仅当「插入后航程 ≤ 预算」才留下（位置按代价
           最优，与 repair_chain 的 _reorder 同性质），不可行的任务出列；
        3. 出列任务救援：其它参与机最优可行位 → 全机队闲置机开单（第3层
           安全网）→ 仍不可行 y_j=0 证明性暂缓（不谎报完成）；
        4. 终点机巢（内层 MCF）与时间窗是**耦合子问题**：≤3 轮小迭代
           （选巢 → 超航程则剔「节省最多」任务重救 → 重排 → 再闸）；
           红线2：不承诺单趟。

        与 repair_chain 共用同一批 helper，口径一致；产出航次全部满足
        航程约束，时间窗由 _reorder_for_deadlines 同款处理。
        """
        # 1) 基因组 → 逐机航次（保留 assignment 顺序；一任务一机去重）
        routes_local: dict[int, list[int]] = {}
        seen: set[int] = set()
        for uav_idx, task_idx in assignment:
            uav_idx, task_idx = int(uav_idx), int(task_idx)
            if uav_idx < 0 or uav_idx >= len(self.participating):
                continue
            if task_idx < 0 or task_idx >= len(self.tasks):
                continue
            if task_idx in seen:
                continue
            seen.add(task_idx)
            routes_local.setdefault(uav_idx, []).append(task_idx)
        # assignment 未覆盖的选中任务（极端情形）进待救队列，保「候选即候选」
        # （转任务 ID：滚动场景下 t.id 是跨周期全局计数，≠ self.tasks 下标）
        pending = [
            self._task_ids[i] for i in range(len(self.tasks)) if i not in seen
        ]

        routes: dict[int, list[int]] = {}
        for uav_idx, tids in routes_local.items():
            routes[self.participating[uav_idx].id] = [
                self._task_ids[i] for i in tids
            ]

        deferred: list[int] = []

        # 2) 逐任务重建航次（航程可行性内联；不可行出列救援）
        for uid in sorted(routes):
            kept: list[int] = []
            for tid in list(routes[uid]):
                new_kept = self._feasible_insert(uid, kept, tid)
                if new_kept is None:
                    if not self._rescue_task(tid, routes, exclude={uid}):
                        deferred.append(tid)
                else:
                    kept = new_kept
            routes[uid] = kept

        # 3) 待救任务（未被 assignment 覆盖者）
        for tid in pending:
            if not self._rescue_task(tid, routes, exclude=set()):
                deferred.append(tid)

        # 4) 巢 + 时间窗小迭代（耦合子问题，≤3 轮；不动点即收敛）
        ends: dict[int, int | None] = (
            self._choose_end_nests(routes) if routes else {}
        )
        prev_state: tuple | None = None
        for _ in range(3):
            for uid in sorted(routes):
                if not routes.get(uid):
                    continue
                limit = self._range_limit(uid)
                while (
                    routes[uid]
                    and self._tour_cost(uid, routes[uid], ends.get(uid))
                    > limit + 1e-6
                ):
                    best_pos: int | None = None
                    best_saving = float("-inf")
                    for pos in range(len(routes[uid])):
                        saving = self._removal_saving(
                            uid, routes[uid], pos, ends.get(uid)
                        )
                        if saving is not None and saving > best_saving:
                            best_saving, best_pos = saving, pos
                    if best_pos is None:
                        break
                    tid = routes[uid].pop(best_pos)
                    if not self._rescue_task(tid, routes, exclude={uid}):
                        deferred.append(tid)
            routes = self._reorder_for_deadlines(routes)
            ends = self._choose_end_nests(routes) if routes else {}
            state = (
                tuple((k, tuple(v)) for k, v in sorted(routes.items())),
                tuple(sorted((k, str(v)) for k, v in ends.items())),
            )
            if state == prev_state:
                break
            prev_state = state

        # deferred 全程持有**任务 ID**（steps 2-4 均从 routes 取 tid）；
        # 严禁再经 _task_ids 二次映射——滚动场景 t.id 是全局计数，越界。
        self.last_constructive_deferred = list(dict.fromkeys(deferred))
        return self._assemble(routes, ends)

    def greedy_constructive_routes(
        self, participating_ids: Sequence[int]
    ) -> dict[int, list[int]]:
        """无基因组的构造式航线（exp04 B3 枚举参照的固定解码器）。

        与 :meth:`_build_constructive` 同机制但**不读基因组**：每个选中
        任务在给定参与子集上按「最优可行插入代价」全局贪心
        （facility-location 式），不可行走与构造式解码相同的救援链
        （参与机 → 全机队闲置）。用于枚举参照——把「选哪些机」的质量
        从 DMDE 搜索中隔离出来。

        注意（B3 边界）：本方法度量**选机质量**（固定解码器下的代理适应
        度），不是全流程质量；DMDE 的搜索增量由 B1/B2 主指标体现。
        """
        routes: dict[int, list[int]] = {int(uid): [] for uid in participating_ids}
        for tid in self._task_ids:
            best: tuple[float, int, list[int]] | None = None
            for uid in sorted(routes):
                new_seq = self._feasible_insert(uid, routes[uid], tid)
                if new_seq is None:
                    continue
                c = self._tour_cost(uid, new_seq, None)
                if best is None or c < best[0] - 1e-12:
                    best = (c, uid, new_seq)
            if best is not None:
                routes[best[1]] = best[2]
                continue
            # 安全网：参与集内闲置 → 全机队闲置（fleet 口径）
            self._rescue_task(tid, routes, exclude=set())
        return {uid: seq for uid, seq in routes.items() if seq}

