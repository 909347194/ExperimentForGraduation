# -*- coding: utf-8 -*-
"""多轮协调 / 失败记账测试：``max_replan_rounds`` 的边界与 ``failed`` 的留痕。

三个场景（都只用合成几何，不碰实验数据）：

1. **阻塞对把轮次推到第二轮**：一对两台全冻结的冲突（谁也动不了）＋一对可消解
   的新—旧式冲突。第一轮改得动的那台被改，阻塞对留着 → 第二轮再检测、再阻塞、
   没有 ``replanned`` → 以 ``CONFLICT_UNRESOLVED`` 收口，``rounds`` 记 2 轮。
2. **``max_rounds`` 是硬上限**：同一场景把上限压到 1，只许记 1 轮，
   ``detail["rounds_used"]`` 也必须是 1——不许靠"再多跑一轮"蒙过去。
3. **同时让的两台不会"让完又撞"**：两台同轮各改几何，候选在验收时已按对手
   *本轮新几何* 复核（``others`` 取的是更新后的 ``current``），所以第二轮不会
   因为它们互相让出新冲突——这正是"两机同时让、让完又撞"在证书式协调下
   被结构性排除的原因；本测试把这个性质钉成可执行断言。
"""

from __future__ import annotations

import unittest

from path_planning.common.plan_params import PlanParams
from path_planning.methods.conflict.conflict_detector import DetectionConfig
from path_planning.methods.coordination.global_recheck import coordinate_conflicts

from path_planning.tests.test_global_recheck import _field, _line, _nlp, _vehicle

__all__ = ["TestMultiRound"]


def _params() -> PlanParams:
    return PlanParams(n_nodes=30, max_iter=500, retreat_node_ladder=(20,))


class TestMultiRound(unittest.TestCase):
    def _blocked_pair_scenario(self):
        """1–2 全冻结的必撞对（z=30 上十字）＋ 3–4 一对可消解（z=10 同款十字）。

        两组差 20 m 高度（椭球竖轴 15 m），互不判冲突；可消解那组只让机 4 动，
        走的是 ``test_global_recheck`` 已验证可行的几何。
        """
        p = _params()
        a = _line(1, (0.0, 0.0, 30.0), (40.0, 0.0, 30.0), 4.0)
        b = _line(2, (20.0, -45.0, 30.0), (20.0, 45.0, 30.0), 8.0)
        c = _line(3, (0.0, 0.0, 10.0), (40.0, 0.0, 10.0), 4.0)
        d = _line(4, (20.0, -45.0, 10.0), (20.0, 45.0, 10.0), 8.0)
        return p, (a, b, c, d)

    def test_blocked_pair_forces_a_second_round(self) -> None:
        p, trajs = self._blocked_pair_scenario()
        out = coordinate_conflicts(
            list(trajs), field=_field(), params=p, vehicle=_vehicle(),
            detection=DetectionConfig.from_params(p, _vehicle()),
            nlp_for=lambda _i: _nlp(p), movable_ids=frozenset({4}),
        )
        self.assertFalse(out.ok)
        self.assertEqual(len(out.rounds), 2)
        first, second = out.rounds
        self.assertEqual(first.replanned, (4,))          # 可动的被改了
        self.assertEqual(first.failed, ())               # 改成功，不是失败
        self.assertEqual(second.replanned, ())           # 阻塞对改不动
        self.assertEqual(second.failed, ())
        self.assertEqual(out.reason.value, "CONFLICT_UNRESOLVED")
        self.assertEqual(out.detail["rounds_used"], 2)
        # 阻塞对每轮都会再被记一次（两轮 = 两条）——留痕不是"只记一次"
        self.assertEqual(out.detail["unresolvable_pairs"], [[1, 2], [1, 2]])
        # 冻结侧必须原对象未变：多轮不许偷偷改第二次
        self.assertIs(out.trajectories[1], trajs[0])
        self.assertIs(out.trajectories[2], trajs[1])

    def test_max_rounds_is_hard_cap(self) -> None:
        p, trajs = self._blocked_pair_scenario()
        out = coordinate_conflicts(
            list(trajs), field=_field(), params=p, vehicle=_vehicle(),
            detection=DetectionConfig.from_params(p, _vehicle()),
            nlp_for=lambda _i: _nlp(p), movable_ids=frozenset({4}), max_rounds=1,
        )
        self.assertFalse(out.ok)
        self.assertEqual(len(out.rounds), 1)
        self.assertEqual(out.detail["rounds_used"], 1)
        self.assertEqual(out.rounds[0].replanned, (4,))
        # 上限压到 1 后冲突没消完，但账必须照实：还剩几对写清楚
        self.assertGreater(out.detail["unresolved"], 0)

    def test_two_simultaneous_moves_are_certified_against_new_state(self) -> None:
        """同轮两台各改：验收按对手本轮新几何复核 → 一轮内归零，不会"让完又撞"。

        两对冲突（各一台冻结、一台可动）分在 z=10 / z=30 两层（竖轴 15 m 互不判冲突），
        都在第一轮里被消解：``replanned`` 记两台。若验收用的是对手的**旧**几何，
        第二台就会"照着旧位置让"并撞上新位置，这里就必须出现第二轮——测试没有
        第二轮，正是"证书按本轮新几何发"的直接证据。
        """
        p = _params()
        a = _line(1, (0.0, 0.0, 10.0), (40.0, 0.0, 10.0), 4.0)     # 冻结
        b = _line(2, (20.0, -45.0, 10.0), (20.0, 45.0, 10.0), 8.0)  # 可动
        c = _line(3, (0.0, 0.0, 30.0), (40.0, 0.0, 30.0), 4.0)     # 冻结
        d = _line(4, (20.0, -45.0, 30.0), (20.0, 45.0, 30.0), 8.0)  # 可动
        out = coordinate_conflicts(
            [a, b, c, d], field=_field(), params=p, vehicle=_vehicle(),
            detection=DetectionConfig.from_params(p, _vehicle()),
            nlp_for=lambda _i: _nlp(p), movable_ids=frozenset({2, 4}),
        )
        self.assertTrue(out.ok, out.reason)
        self.assertEqual(len(out.rounds), 1)
        self.assertEqual(out.rounds[0].replanned, (2, 4))
        self.assertEqual(out.rounds[0].conflicts_after, 0)
        for uid in (2, 4):
            self.assertTrue(out.detail["moves"].get(str(uid)))

    def test_no_replan_means_no_failed_noise(self) -> None:
        """无冲突场景：轮次记录只有一条 0→0，``failed`` 不许凭空有内容。"""
        p = _params()
        a = _line(1, (0.0, 0.0, 10.0), (40.0, 0.0, 10.0), 4.0)
        b = _line(2, (20.0, -25.0, 10.0), (20.0, 25.0, 10.0), 5.0, t0=200.0)
        out = coordinate_conflicts(
            [a, b], field=_field(), params=p, vehicle=_vehicle(),
            detection=DetectionConfig.from_params(p, _vehicle()),
            nlp_for=lambda _i: _nlp(p),
        )
        self.assertTrue(out.ok)
        self.assertEqual(out.rounds[0].replanned, ())
        self.assertEqual(out.rounds[0].failed, ())
