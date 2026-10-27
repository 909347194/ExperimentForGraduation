# -*- coding: utf-8 -*-
"""route_polish.py — 交付解成本 polish 算子组（``RoutePolishMixin``）。

2026-10-27 自 ``muas_stage.py`` 拆出。三组算子 + 两个编排入口：

- :func:`_polish_routes_monotone`  覆盖优先口径的单调成本 polish（relocate/调序）
- :func:`_cost_polish_routes`      组内 2-opt / Or-opt（顺序维度）
- :func:`_cost_polish_relocate`    跨机单任务改派（归属维度）
- :func:`post_polish`              coverage_first 口径的终态后处理编排
- :func:`post_cost_polish`         生产口径的交付后成本 polish 编排

接受纪律（四道门，各算子同构）：航程门（不超剩余航程）、迟到门
（不制造新违约、不加深旧违约）、执行集门（覆盖只增不减）、
成本门（严格下降 > eps）。全程无随机，同输入逐位可复现。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from task_allocation.common.types import AllocationSolution
from task_allocation.methods.muas.coverage_gate import audit_coverage, repair_coverage


class RoutePolishMixin:
    """交付解成本 polish 算子组（混入 :class:`MUASAssignmentEvaluator`）。"""

    def _polish_routes_monotone(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None], dict[str, int]]:
        """单调成本 polish：跨机 relocate / 调序，**覆盖只增不减**。

        用户 2026-10-23 裁定（解码/repair 第二段）：覆盖优先装载之后，
        成本 polish 必须在不减少已装集合（不增加 shortfall）的约束下
        进行。relocate / 调序移动本身不卸任务（任务集由移动结构保持），
        候选仍须过四道门：

        1. **航程门**：涉及航次（改派双方）全航程 ≤ 各自剩余航程；
        2. **自身时间窗门**：插入位任务自身到达 ≤ deadline；
        3. **迟到数门**：插入航次的迟到任务数不得增加（不制造新违约；
           移除侧任务只可能更早，无需校验）；
        4. **成本门**：涉及航次代价合计严格下降（first-improvement，
           接受即重启邻域扫描；无随机，逐位可复现）。

        relocate 目标含**空闲机**（routes 键即真实 uav id，
        ``_tour_cost`` / ``_window_ok`` 等 helper 均按真实 id 口径，
        参与集外的可用机可直接作为目标——与覆盖装载的 fleet 口径一致）。
        位置移动只改访问顺序（同 :meth:`_reorder_for_deadlines` 的候选
        性质，但这里以成本为唯一目标、迟到数门兜底）。
        """
        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)
        eps = float(self.config.monotone_polish_eps)
        max_rounds = max(1, int(self.config.monotone_polish_max_rounds))
        stats = {"n_relocate": 0, "n_position": 0, "n_rounds": 0, "converged": 0}
        # relocate 目标 = 全部 UAV（含当前空闲机；work 缺键时按需补）
        all_uav_ids = [u.id for u in self.problem.uavs]

        def cost_of(uid: int, tids: Sequence[int]) -> float:
            return self._tour_cost(uid, tids, ends.get(uid))

        def arrivals_of(uid: int, tids: Sequence[int]) -> list[float]:
            # 2026-10-27 去重：原地一份逐位等价的拷贝已删，统一走 tour_cost
            # 的 _eta_list（口径唯一，防两处实现漂移；测试锁逐位相等）。
            return self._eta_list(uid, tids)

        def late_count(uid: int, tids: Sequence[int]) -> int:
            # 同上：迟到键唯一来源 tour_cost._late_key（取其计数分量）。
            return self._late_key(uid, tids)[0]

        for round_idx in range(max_rounds):
            stats["n_rounds"] = round_idx + 1
            improved = False
            # ── 位置移动（机内调序）：单航次、代价严格降、迟到数不增
            for uid in sorted(work):
                if improved:
                    break  # first-improvement：接受即重启（下一轮从头扫）
                tids = work[uid]
                if len(tids) <= 1:
                    continue
                limit = self._range_limit(uid)
                base_cost = cost_of(uid, tids)
                base_late = late_count(uid, tids)
                for i in range(len(tids)):
                    if improved:
                        break
                    rest = tids[:i] + tids[i + 1:]
                    for p in range(len(rest) + 1):
                        if p == i:
                            continue
                        cand = rest[:p] + [tids[i]] + rest[p:]
                        if not self._window_ok(uid, cand, p):
                            continue
                        c = cost_of(uid, cand)
                        if c > limit + 1e-6:
                            continue
                        if late_count(uid, cand) > base_late:
                            continue
                        if c < base_cost - eps:
                            work[uid] = cand
                            stats["n_position"] += 1
                            improved = True
                            break
            # ── 跨机 relocate：任务 × 目标机（含空闲）× 插入位
            if not improved:
                for uid in sorted(work):
                    if improved:
                        break  # first-improvement：接受即重启
                    tids = work[uid]
                    if not tids:
                        continue
                    limit_src = self._range_limit(uid)
                    for i, tid in enumerate(tids):
                        if improved:
                            break
                        rest = tids[:i] + tids[i + 1:]
                        src_after_cost = cost_of(uid, rest)
                        for u2 in all_uav_ids:
                            if improved:
                                break
                            if u2 == uid:
                                continue
                            target = work.get(u2, [])
                            limit_dst = self._range_limit(u2)
                            base_dst_cost = cost_of(u2, target)
                            base_dst_late = late_count(u2, target)
                            for p in range(len(target) + 1):
                                cand = target[:p] + [tid] + target[p:]
                                if not self._window_ok(u2, cand, p):
                                    continue
                                c2 = cost_of(u2, cand)
                                if c2 > limit_dst + 1e-6:
                                    continue
                                if late_count(u2, cand) > base_dst_late:
                                    continue
                                # 成本门：双方航次合计严格下降（源侧移除
                                # 任务只降不升，仍精确计算保稳健）
                                if (
                                    src_after_cost + c2
                                    < cost_of(uid, tids) + base_dst_cost - eps
                                ):
                                    work[uid] = list(rest)
                                    work[u2] = cand
                                    stats["n_relocate"] += 1
                                    improved = True
                                    break
                # first-improvement：接受即结束本轮（下一轮从头重启邻域
                # 扫描——确定性、无随机；n_rounds ≈ 接受移动数 + 1）。
                if improved:
                    continue
            if not improved:
                stats["converged"] = 1
                break
        return work, ends, stats

    def _cost_polish_routes(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None], dict[str, int]]:
        """交付解的组内 2-opt / Or-opt 成本重排（**覆盖不侵蚀**）。

        只改访问顺序、不改归属与任务集（航次内置换），四道门：

        1. **航程门**：候选全航程（起点→任务→终点巢，:meth:`_tour_cost`）
           ≤ 该机剩余航程（与 ``_consolidate`` 的结构性航程门同口径）；
        2. **迟到门**：候选迟到键 ``(数, 总量)`` 逐分量不差于基线——
           不制造新违约、不加深旧违约（⇒ ``soft_violation`` 不增）；
        3. **执行集门**：置换由构造保持任务集（采纳端再复核）；
        4. **成本门**：航程严格下降 > ``cost_polish_eps`` 才采纳。

        策略：first-improvement + 接受即重启邻域扫描；整轮无改进即收敛。
        全程无随机，同输入逐位可复现。终点机巢在调用方重选（见
        :meth:`post_cost_polish`），这里只按当前 ``ends`` 估价。
        """
        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)
        eps = float(self.config.cost_polish_eps)
        max_rounds = max(1, int(self.config.cost_polish_max_rounds))
        stats = {"n_2opt": 0, "n_oropt": 0, "n_rounds": 0, "converged": 0}

        def cost_of(uid: int, tids: Sequence[int]) -> float:
            return self._tour_cost(uid, tids, ends.get(uid))

        for round_idx in range(max_rounds):
            stats["n_rounds"] = round_idx + 1
            improved = False
            for uid in sorted(work):
                if improved:
                    break  # first-improvement：接受即重启（下一轮从头扫）
                tids = work[uid]
                n = len(tids)
                if n < 2:
                    continue
                limit = self._range_limit(uid)
                base_cost = cost_of(uid, tids)
                base_late = self._late_key(uid, tids)

                # ── 2-opt：反转段 [i, j]（i<j）──────────────────────
                for i in range(n - 1):
                    if improved:
                        break
                    for j in range(i + 1, n):
                        cand = tids[:i] + tids[i:j + 1][::-1] + tids[j + 1:]
                        c = cost_of(uid, cand)
                        if c > limit + 1e-6:
                            continue
                        if c >= base_cost - eps:
                            continue
                        late_n, late_t = self._late_key(uid, cand)
                        if late_n > base_late[0] or late_t > base_late[1] + 1e-6:
                            continue
                        work[uid] = cand
                        stats["n_2opt"] += 1
                        improved = True
                        break

                # ── Or-opt：段长 1~3 迁往其它插入位 ──────────────────
                if not improved:
                    for seg in range(1, 4):
                        if improved or seg > n:
                            break
                        for i in range(n - seg + 1):
                            if improved:
                                break
                            rest = tids[:i] + tids[i + seg:]
                            moving = tids[i:i + seg]
                            for p in range(len(rest) + 1):
                                if p == i:
                                    continue  # 恒等移动
                                cand = rest[:p] + moving + rest[p:]
                                c = cost_of(uid, cand)
                                if c > limit + 1e-6:
                                    continue
                                if c >= base_cost - eps:
                                    continue
                                late_n, late_t = self._late_key(uid, cand)
                                if late_n > base_late[0] or late_t > base_late[1] + 1e-6:
                                    continue
                                work[uid] = cand
                                stats["n_oropt"] += 1
                                improved = True
                                break
            if not improved:
                stats["converged"] = 1
                break
        return work, ends, stats

    def _cost_polish_relocate(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None], dict[str, int]]:
        """交付解的**跨机单任务改派**成本 polish（归属维度，2026-10-24）。

        组内 2-opt/Or-opt（:meth:`_cost_polish_routes`）只改访问顺序；
        本算子改**归属**：把单任务从供体机 ``src`` 迁到受体机 ``dst``
        （含可用但当前空闲的机——空闲机起终点都按当前位置估价，终点巢
        由 :meth:`post_cost_polish` 下游 :meth:`_choose_end_nests` 重选）。

        对每个 (src, 位置 pos, 任务 tid, dst) 计算机械最优插入位的净变化::

            Δ = tour_cost(src 移除 tid) + tour_cost(dst 插入 tid)
              − tour_cost(src) − tour_cost(dst)

        四道门（与 :meth:`_cost_polish_routes` 同纪律，逐机判定）：

        1. **航程门**：供体移除后、受体插入后各自的全程代价分别 ≤ 两机
           剩余航程（受体超限的插入位直接跳过）；
        2. **迟到门**：两侧迟到键 ``(数, 总量)`` 逐分量不差于基线——
           不制造新违约、不加深旧违约（⇒ ``soft_violation`` 不增）；
        3. **执行集门**：改派保持任务集（采纳端再以 executed ⊇ 旧解复核）；
        4. **成本门**：Δ < −``cost_polish_eps`` 才采纳。

        策略：first-improvement + 接受即重启邻域扫描；无随机、按 id 升序，
        逐位可复现。诊断另计 ``blocked_range`` / ``blocked_late``：几何上
        更便宜但被航程门/迟到门拦死的改派数——用于区分「无余量」与
        「有余量被约束堵住」（后者才是搜索期/约束口径的问题）。
        """
        work: dict[int, list[int]] = {k: list(v) for k, v in routes.items()}
        ends: dict[int, int | None] = dict(end_nests)
        eps = float(self.config.cost_polish_eps)
        max_rounds = max(1, int(self.config.cost_polish_max_rounds))
        all_uavs = sorted(self._uav_by_id)
        stats = {
            "n_relocate": 0,
            "n_rounds": 0,
            "converged": 0,
            "blocked_range": 0,
            "blocked_late": 0,
        }

        for round_idx in range(max_rounds):
            stats["n_rounds"] = round_idx + 1
            improved = False
            for src in sorted(work):
                if improved:
                    break  # first-improvement：接受即重启（下一轮从头扫）
                tids = work[src]
                if not tids:
                    continue
                limit_src = self._range_limit(src)
                base_src_cost = self._tour_cost(src, tids, ends.get(src))
                base_src_late = self._late_key(src, tids)
                for pos in range(len(tids)):
                    if improved:
                        break
                    tid = tids[pos]
                    src_new = tids[:pos] + tids[pos + 1:]
                    src_cost_new = self._tour_cost(src, src_new, ends.get(src))
                    if src_cost_new > limit_src + 1e-6:
                        continue  # 移除任务不可能超限；防御性保留
                    src_late_new = self._late_key(src, src_new)
                    if (
                        src_late_new[0] > base_src_late[0]
                        or src_late_new[1] > base_src_late[1] + 1e-6
                    ):
                        continue  # 供体迟到键不劣化（移除只可能更好；仍验证）
                    for dst in all_uavs:
                        if dst == src:
                            continue
                        dst_tids = work.get(dst, [])
                        limit_dst = self._range_limit(dst)
                        end_dst = ends.get(dst)
                        base_dst_cost = (
                            self._tour_cost(dst, dst_tids, end_dst)
                            if dst_tids
                            else 0.0
                        )
                        base_dst_late = (
                            self._late_key(dst, dst_tids) if dst_tids else (0, 0.0)
                        )
                        # 全部插入位中通过两道门的最便宜者（并列取最前位）
                        best_cost = float("inf")
                        best_cand: list[int] | None = None
                        cheap_cost = float("inf")  # 仅几何最便宜（归因用）
                        for p in range(len(dst_tids) + 1):
                            cand = dst_tids[:p] + [tid] + dst_tids[p:]
                            c = self._tour_cost(dst, cand, end_dst)
                            if c < cheap_cost:
                                cheap_cost = c
                            if c > limit_dst + 1e-6:
                                continue  # 航程门
                            ln, lt = self._late_key(dst, cand)
                            if ln > base_dst_late[0] or lt > base_dst_late[1] + 1e-6:
                                continue  # 迟到门
                            if c < best_cost - 1e-12:
                                best_cost = c
                                best_cand = cand
                        if best_cand is None:
                            # 归因：几何更便宜但被门拦死才计数
                            if (
                                cheap_cost < float("inf")
                                and src_cost_new + cheap_cost
                                < base_src_cost + base_dst_cost - eps
                            ):
                                if cheap_cost > limit_dst + 1e-6:
                                    stats["blocked_range"] += 1
                                else:
                                    stats["blocked_late"] += 1
                            continue
                        if (
                            src_cost_new + best_cost
                            >= base_src_cost + base_dst_cost - eps
                        ):
                            continue  # 成本门：非严格下降
                        # ── 采纳 ─────────────────────────────────────
                        work[src] = src_new
                        work[dst] = list(best_cand)
                        if not src_new:
                            ends[src] = None  # 供体清空 ⇒ 本轮闲置
                        stats["n_relocate"] += 1
                        improved = True
                        break
            if not improved:
                stats["converged"] = 1
                break
        return work, ends, stats

    def post_cost_polish(
        self, solution: AllocationSolution
    ) -> tuple[AllocationSolution, dict[str, int]]:
        """交付解的「跨机改派 + 组内 2-opt/Or-opt → 终态联合重解」后处理。

        对已构建的解（DMDE 原始交付解 / assignment polish 后 / memetic
        档案 / coverage_first 后处理，whichever 胜出）取 routes 做：

        1. :meth:`_cost_polish_relocate`——跨机单任务改派（归属维度），
           四道门（两侧航程 / 两侧迟到 / 执行集由构造保持 / 净航程严格
           下降）；``cost_polish_relocate_enabled=False`` 时整段跳过；
        2. :meth:`_cost_polish_routes`——组内 2-opt/Or-opt（顺序维度），
           四道门，执行集与迟到不劣化，只压航程；
        3. ①→② 交替至一个联合轮次零接受（上限 3 轮：改派会改末任务、
           改末任务会改插入代价，两维度有二阶交互）；
        4. 全零接受 ⇒ 原样返回（逐位不变，零开销）；
        5. 有改进 ⇒ 与 :meth:`build_solution` 终态同序的下游联合重解：
           重排（时间窗）→ 航程闸 → 终点巢精确重解（改序/改派会改末任务
           ⇒ 返航腿与巢容量竞争都变）→ 航程闸 → 重装配。

        调用方按「执行集 ⊇ 旧解 且 硬违约不增 且 交付航程严格下降」
        择优采纳（见 ``run_selective_muas``）；被拒则交付解保持原样。
        """
        routes = {
            t.uav_id: list(t.task_ids) for t in solution.tours if t.task_ids
        }
        ends = {t.uav_id: t.end_nest_id for t in solution.tours}
        if not routes:
            return solution, {}
        stats: dict[str, int] = {}
        n_accepted = 0
        # 联合局部搜索：改派（归属）↔ 重排（顺序）交替，各自收敛后若无
        # 新接受即停。上界 3 轮——两维度交互是二阶项，exp01 量级下 1~2
        # 轮即收敛；上限只是防御性边界。
        for joint in range(3):
            before = n_accepted
            if self.config.cost_polish_relocate_enabled:
                routes, ends, rstats = self._cost_polish_relocate(routes, ends)
                for k in ("n_relocate", "blocked_range", "blocked_late"):
                    stats[k] = int(stats.get(k, 0)) + int(rstats.get(k, 0))
                n_accepted += int(rstats.get("n_relocate", 0))
            routes, ends, ostats = self._cost_polish_routes(routes, ends)
            for k in ("n_2opt", "n_oropt"):
                stats[k] = int(stats.get(k, 0)) + int(ostats.get(k, 0))
            n_accepted += int(ostats.get("n_2opt", 0)) + int(ostats.get("n_oropt", 0))
            if n_accepted == before:
                break  # 一个完整联合轮次零接受 ⇒ 两维度均局部最优
        stats["n_accepted"] = int(n_accepted)
        if n_accepted == 0:
            return solution, stats
        # 下游联合重解（与 build_solution 末段同序）：重排/改派改了末任务
        # ⇒ 返航腿/终点巢竞争变，必须重选终点巢再闸一次航程，否则交付解
        # 的 path_cost 口径与 polish 内的估价脱节。
        routes = self._reorder_for_deadlines(routes)
        routes, ends = self._repair_range(routes, ends)
        if routes:
            ends = self._choose_end_nests(routes)
        routes, ends = self._repair_range(routes, ends)
        sol = self._assemble(routes, ends)
        return sol, stats

    def post_polish(
        self, solution: AllocationSolution
    ) -> tuple[AllocationSolution, dict[str, int]]:
        """交付解的「单调成本 polish → 覆盖优先装载 → 门禁」后处理。

        对已构建的解（DMDE 原始交付解 / assignment polish 后 / memetic
        档案）取 routes 做：

        1. :meth:`_polish_routes_monotone`——跨机 relocate / 调序压成本
           （四道门，覆盖不侵蚀）；
        2. :meth:`_load_coverage_fixed_point`——补回 polish 期间新变为
           可行集的任务（覆盖只增）；
        3. 重排 → 航程闸 → 终点巢精确重解 → 航程闸（与
           :meth:`build_solution` 终态同序）；
        4. 覆盖门禁（coverage_gate 修复 / lexicographic_coverage 审计，
           与 :meth:`build_solution` 末段同判据）；
        5. 重新装配，``gate_stats`` 落 ``solution.meta``。

        覆盖只增不减：polish 移动不卸任务、装载只加任务、门禁修复只加
        任务。调用方按「覆盖 ⊇ 旧解 且 硬违约不增 且 适应度更优」择优
        采纳（见 ``run_selective_muas``）。
        """
        routes = {
            t.uav_id: list(t.task_ids) for t in solution.tours if t.task_ids
        }
        ends = {t.uav_id: t.end_nest_id for t in solution.tours}
        if not routes:
            return solution, {}
        polish_stats: dict[str, int] = {}
        if self.config.coverage_first:
            routes, ends, polish_stats = self._polish_routes_monotone(routes, ends)
        routes, ends, loaded = self._load_coverage_fixed_point(routes, ends)
        polish_stats["n_reloaded"] = int(loaded)
        routes = self._reorder_for_deadlines(routes)
        routes, ends = self._repair_range(routes, ends)
        if routes:
            ends = self._choose_end_nests(routes)
        routes, ends = self._repair_range(routes, ends)
        gate_stats: dict[str, Any] | None = None
        if bool(getattr(self.config, "coverage_gate", False)):
            routes, ends, gs = repair_coverage(
                self.problem, routes, ends,
                max_rounds=int(getattr(self.config, "coverage_gate_max_rounds", 8)),
            )
            gate_stats = gs.to_dict()
        elif bool(getattr(self.problem.config, "lexicographic_coverage", False)):
            gate_stats = audit_coverage(self.problem, routes, ends).to_dict()
        sol = self._assemble(routes, ends)
        if gate_stats is not None:
            sol.meta["gate_stats"] = gate_stats
        return sol, polish_stats

