# -*- coding: utf-8 -*-
"""ablation_prune.py — 选择性剔除算子（消融专用，``AblationPruneMixin``）。

本模块的两个算子**不在生产路径**：生产口径（覆盖门禁）下「完成是约束」，
经济性剔除与完成约束冲突，相关开关默认关闭。它们仅用于消融对照与压力测试，
因此与主管线物理隔离，阅读 ``decode_repair`` 主链路时无需确认其开关。

- :meth:`_prune`              经济性剔除（``y_j=0``），门 ``prune_unprofitable``
- :meth:`_prune_zero_reward`  零奖励剔除，门 ``prune_zero_reward``

算子仍以 Mixin 形式共享评价器的 ``self`` 状态（reflow / 代价原语），
由 :class:`MUASAssignmentEvaluator` 组合继承。契约见
``docs/operator_contracts.md`` 第 5 节。
"""

from __future__ import annotations

from typing import Mapping, Sequence


class AblationPruneMixin:
    """选择性剔除算子组（消融专用，默认全部关闭）。"""

    def _prune(
        self,
        routes: Mapping[int, Sequence[int]],
        end_nests: Mapping[int, int | None],
    ) -> tuple[dict[int, list[int]], dict[int, int | None]]:
        """剔除执行代价高于收益的任务（``y_j = 0`` 选择性口径）。

        判据：移除任务 j 节省的路径代价 ``saving`` 满足

            λ · saving > R_j^eff · (1 + margin)

        则本周期不执行 j（保留至下一规划周期）。其中 ``R_j^eff`` 是含等待
        补偿的有效收益：任务滞留越久补偿越大，越不容易被剔除，避免偏远
        任务永久滞留。
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

        迟到折减 ``eff = R·max(0, 1 − L/W)`` 下，到达晚于 ``latest + W``
        的任务对 F1 贡献为 0，却仍占用航次与航程并计迟到惩罚。零奖励任务
        剔除不降 F1，是严格的适应度改进，还把机会留给下一周期。

        做法：逐航次按完成闸门同口径模拟 ETA，对第一个零奖励任务移除并
        :meth:`_reflow_task`（``full_window``：只接受「整条航次不新增迟到」
        的落点，不制造新违约；不做换机腾挪——改派不成立就应当暂缓）。
        每轮至少永久移除一个，迭代至无零奖励任务终止。
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
