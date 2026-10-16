# -*- coding: utf-8 -*-
"""exp01 指标汇总口径（2026-10-16 审查 I1/I2/I3/I4）的聚焦测试。

背景（审查实证，详见 ``docs/review_exp01_20261016.md``）：

  I1  ``correct_exec_rate`` 把空转周期记 0 再平均 → 0.423（真实 0.846），
      与同摘要的 ``on_time_rate=0.80`` 互相矛盾；且 ``zip`` 注释在列表变短后
      会把周期行与正确率配错位。修复：空转周期不入均值、按 cycle 键控注释。
  I2  ``cycle_feasible_rate`` 把无解周期当 ``feasible=False`` → 1/8（真实 1/4）。
      修复：只统计真正求解过的周期（``feasible is not None``）。
  I3  ``total_cost = 路径(米) + w_penalty·惩罚(秒/单位)`` 混量纲，且报告口径
      冒充「总路径代价」。修复：拆成 ``total_cost``（纯路径）/ ``total_penalty``
      / ``total_f2``（原始口径，供追溯）。
  I4  「迟到≠废飞」下迟到 ≥ W 折减为 0 奖励的交付仍计完成 → 完成率虚高。
      修复：并列 ``n_zero_reward_delivery`` / ``effective_completion_rate``
      / ``reward_yield`` 有效完成口径。

运行：``python3 task_allocation/tests/test_metrics_aggregation.py``
（无 pytest 环境可直接跑；有 pytest 时同名 test_* 函数会被自动收集。）
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
EXP_DIR = REPO / "task_allocation" / "experiments" / "exp01_smoke"
for p in (str(REPO), str(EXP_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import metrics as exp01_metrics  # noqa: E402
from metrics import RunFacts  # noqa: E402


# ---------------------------------------------------------------------------
# 脚手架：最小可跑的 RunFacts（不动真实算法对象）
# ---------------------------------------------------------------------------


class _FakeTask:
    def __init__(self, tid, reward, arrival, latest, completed_at=None):
        self.id = tid
        self.x = self.y = self.z = 0.0
        self.reward = float(reward)
        self.arrival_time = float(arrival)
        self.earliest = 0.0
        self.latest = float(latest)
        self.status = SimpleNamespace(value="done" if completed_at is not None else "pending")
        self.meta = {"completed_at": completed_at} if completed_at is not None else {}


class _FakeRecord:
    def __init__(self, cycle, muas=None, executed=(), late=(), infeasible=(),
                 expired=(), unreachable=(), lateness=None):
        self.cycle = cycle
        self.current_time = 600.0 * cycle
        self.muas = dict(muas or {})
        self.selection = {"n_uav_avail": 10}
        self.executed_ids = list(executed)
        self.late_ids = list(late)
        self.infeasible_ids = list(infeasible)
        self.expired_ids = list(expired)
        self.unreachable_ids = list(unreachable)
        self.lateness_by_task = dict(lateness or {})
        self.completed_ids = []
        self.deferred_ids = []
        self.released_by_fault = []
        self.replan = False
        self.tours = []
        self.cost_history = []

    def as_dict(self):
        return {"cycle": self.cycle, "muas": dict(self.muas)}


class _FakePool:
    def __init__(self, finished, remaining):
        self._finished = finished
        self._remaining = remaining

    def finished_tasks(self):
        return list(self._finished)

    def __len__(self):
        return self._remaining

    def snapshot_json(self):
        return {"counts": {}}


def _facts(records, tasks, completed, w_penalty=1.0, decay_w=600.0):
    bundle = SimpleNamespace(
        experiment={"name": "test"},
        problem={"uav_speed": 15.0, "uav_max_range": 80000.0},
        solver=SimpleNamespace(pop_size=50, max_generations=100),
        problem_model=SimpleNamespace(
            lexicographic=True,
            constraints=SimpleNamespace(time_window=True),
            w_penalty=w_penalty,
            late_reward_decay_seconds=decay_w,
        ),
        data={"dem": None},
        cost={"dem_step_m": 200.0},
        horizon=SimpleNamespace(n_cycles=60),
    )
    return RunFacts(
        bundle=bundle,
        scenario=SimpleNamespace(frame=SimpleNamespace(lat0=0.0, lon0=0.0)),
        nests=[SimpleNamespace(id=0, x=0.0, y=0.0, z=0.0, capacity=4,
                               meta={"lat": None, "lon": None})],
        terrain=None,
        uavs=[SimpleNamespace(id=i) for i in range(2)],
        batch=SimpleNamespace(
            initial=list(tasks),
            all_tasks=lambda: list(tasks),
            per_cycle_counts=lambda: {1: len(tasks)},
        ),
        records=list(records),
        pool=_FakePool(completed, remaining=len(tasks) - len(completed)),
        fleet=SimpleNamespace(snapshot_json=lambda: {}),
        wall_seconds=1.0,
    )


# ---------------------------------------------------------------------------
# I1：空转周期不入正确率均值；周期行与率不错位
# ---------------------------------------------------------------------------


def test_correct_exec_rate_skips_idle_cycles():
    tasks = [_FakeTask(0, 5.0, 0.0, 6000.0, completed_at=600.0),
             _FakeTask(1, 5.0, 0.0, 6000.0, completed_at=1200.0)]
    records = [
        _FakeRecord(1, muas={"feasible": True}, executed=[0]),
        _FakeRecord(2, muas={"feasible": True}, executed=[0, 1], late=[1]),
        _FakeRecord(3),  # 空转
        _FakeRecord(4),  # 空转
    ]
    m = exp01_metrics.build_metrics(_facts(records, tasks, tasks))
    # 活跃周期均值 = (1.0 + 0.5) / 2 = 0.75；旧口径 = 1.5/4 = 0.375
    assert abs(m["summary"]["correct_exec_rate"] - 0.75) < 1e-9, m["summary"]
    # 周期行：空转周期显式 None，活跃周期按各自 cycle 键控（不错位）
    rows = {row["cycle"]: row.get("correct_exec_rate") for row in m["cycles"]}
    assert rows == {1: 1.0, 2: 0.5, 3: None, 4: None}, rows


# ---------------------------------------------------------------------------
# I2：无解周期不入可行率
# ---------------------------------------------------------------------------


def test_cycle_feasible_rate_skips_no_solution_cycles():
    tasks = [_FakeTask(0, 5.0, 0.0, 6000.0, completed_at=600.0)]
    records = [
        _FakeRecord(1, muas={"feasible": True}, executed=[0]),
        _FakeRecord(2, muas={"feasible": False}, executed=[0]),
        _FakeRecord(3),  # 无解
    ]
    m = exp01_metrics.build_metrics(_facts(records, tasks, tasks))
    # (1 True + 1 False) / 2 = 0.5；旧口径 1/3
    assert abs(m["summary"]["cycle_feasible_rate"] - 0.5) < 1e-9, m["summary"]


# ---------------------------------------------------------------------------
# I3：路径与惩罚拆开，total_cost 只留米制路径
# ---------------------------------------------------------------------------


def test_total_cost_splits_penalty_from_path():
    tasks = [_FakeTask(0, 5.0, 0.0, 6000.0, completed_at=600.0)]
    records = [
        _FakeRecord(1, muas={"feasible": False, "f2_cost": 1000.0,
                             "penalty": 100.0, "f1_reward": 5.0}, executed=[0]),
    ]
    m = exp01_metrics.build_metrics(_facts(records, tasks, tasks, w_penalty=1.0))
    s = m["summary"]
    assert abs(s["total_cost"] - 900.0) < 1e-9, s        # 纯路径
    assert abs(s["total_penalty"] - 100.0) < 1e-9, s     # 惩罚单列
    assert abs(s["total_f2"] - 1000.0) < 1e-9, s         # 原始口径可追溯


def test_total_cost_respects_w_penalty_scale():
    tasks = [_FakeTask(0, 5.0, 0.0, 6000.0, completed_at=600.0)]
    records = [
        _FakeRecord(1, muas={"feasible": False, "f2_cost": 1000.0,
                             "penalty": 100.0, "f1_reward": 5.0}, executed=[0]),
    ]
    m = exp01_metrics.build_metrics(_facts(records, tasks, tasks, w_penalty=2.0))
    # f2 = path + 2×100 → path = 800
    assert abs(m["summary"]["total_cost"] - 800.0) < 1e-9, m["summary"]


# ---------------------------------------------------------------------------
# I4：零奖励交付并列报，完成率不再独占叙事
# ---------------------------------------------------------------------------


def test_zero_reward_delivery_accounting():
    # T0 按时交付（eff=10）；T1 迟到 1200s ≥ W=600 → eff=0 仍计完成
    tasks = [_FakeTask(0, 10.0, 0.0, 600.0, completed_at=600.0),
             _FakeTask(1, 10.0, 0.0, 600.0, completed_at=1800.0)]
    records = [
        _FakeRecord(1, muas={"feasible": False, "f1_reward": 10.0,
                             "f2_cost": 500.0, "penalty": 0.0},
                    executed=[0, 1], late=[1], lateness={"1": 1200.0}),
    ]
    m = exp01_metrics.build_metrics(_facts(records, tasks, tasks, decay_w=600.0))
    s = m["summary"]
    assert s["n_completed"] == 2 and abs(s["completion_rate"] - 1.0) < 1e-9, s
    assert s["n_zero_reward_delivery"] == 1, s
    assert abs(s["effective_completion_rate"] - 0.5) < 1e-9, s   # 1/2 有效
    assert abs(s["reward_yield"] - 0.5) < 1e-9, s                # 10/20 折减后


def test_zero_reward_accounting_disabled_decay():
    # W=None（历史口径，无折减）→ 不应误报零奖励交付
    tasks = [_FakeTask(0, 10.0, 0.0, 600.0, completed_at=1800.0)]
    records = [
        _FakeRecord(1, muas={"feasible": False, "f1_reward": 10.0,
                             "f2_cost": 500.0, "penalty": 0.0},
                    executed=[0], late=[0], lateness={"0": 1200.0}),
    ]
    m = exp01_metrics.build_metrics(_facts(records, tasks, tasks, decay_w=None))
    s = m["summary"]
    assert s["n_zero_reward_delivery"] == 0, s
    assert abs(s["effective_completion_rate"] - 1.0) < 1e-9, s
    assert abs(s["reward_yield"] - 1.0) < 1e-9, s


if __name__ == "__main__":
    import traceback

    failed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"PASS {_name}")
            except Exception:  # noqa: BLE001
                failed += 1
                print(f"FAIL {_name}")
                traceback.print_exc()
    sys.exit(1 if failed else 0)
