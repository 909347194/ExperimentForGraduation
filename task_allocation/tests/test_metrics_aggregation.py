# -*- coding: utf-8 -*-
"""exp01 指标汇总口径（2026-10-16 审查 I1/I2/I3/I4；2026-10-24 审查 A1/A2/A3/B1/B2/D）的聚焦测试。

背景（审查实证，详见 ``docs/review_exp01_20261016.md``）：

  I1  ``correct_exec_rate`` 把空转周期记 0 再平均 → 0.423（真实 0.846），
      与同摘要的出动池化正确执行率互相矛盾；且 ``zip`` 注释在列表变短后
      会把周期行与正确率配错位。修复：空转周期不入均值、按 cycle 键控注释。
      （该出动池化键 ``on_time_rate`` 已于 2026-10-24 A2 删除，正确执行率
      只保留逐周期均值 ``correct_exec_rate`` 与交付集条件率
      ``on_time_rate_final`` 两个口径。）
  I2  ``cycle_feasible_rate`` 把无解周期当 ``feasible=False`` → 1/8（真实 1/4）。
      修复：只统计真正求解过的周期（``feasible is not None``）。
  I3  ``total_cost = 路径(米) + w_penalty·惩罚(秒/单位)`` 混量纲，且报告口径
      冒充「总路径代价」。修复：拆成 ``total_cost``（纯路径）/ ``total_penalty``
      / ``total_f2``（原始口径，供追溯）。
  I4  「迟到≠废飞」下迟到 ≥ W 折减为 0 奖励的交付仍计完成 → 完成率虚高。
      修复：并列 ``n_zero_reward_delivery`` / ``effective_completion_rate``
      / ``reward_yield`` 有效完成口径。
  A1  naive 完成率（分母含物理不可达）与 final 双落盘 ⇒ 直读漂移。
      处置：exp01 定位正确性验证台，**删除** naive 键，只留 final 口径。
  A2  出动池化 ``on_time_rate`` 与 correct_exec_rate / on_time_rate_final
      三者近义误引。处置：删除池化键。
  A3  ``effective_completion_rate`` 分母对齐 ``n_final_denom``（与 final 可比）。
  B1  新增 ``infeasible_wasted_path_m``（废飞已飞航程，与计数同权）。
  B2  新增 ``cand_hit_rate``（Σn_sel/Σn_cand，验证候选池「该富」）。
  D   ``max_backlog`` 跨 seed 聚合取峰值（峰值不可平均）。

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
                 expired=(), unreachable=(), lateness=None,
                 selection_extra=None, pool_counts=None, tours=None):
        self.cycle = cycle
        self.current_time = 600.0 * cycle
        self.muas = dict(muas or {})
        self.selection = {"n_uav_avail": 10, **(selection_extra or {})}
        self.pool_counts = dict(pool_counts or {})
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
        self.tours = list(tours or [])
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


def _facts(records, tasks, completed, w_penalty=1.0, decay_w=600.0, arrivals=None):
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
            arrivals=(dict(arrivals) if arrivals is not None else {1: list(tasks)}),
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
    # A1（2026-10-24）：naive 完成率键已删，完成率只报 final 口径
    assert s["n_completed"] == 2 and abs(s["completion_rate_final"] - 1.0) < 1e-9, s
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


# ---------------------------------------------------------------------------
# v2 指标体系（2026-10-22）：主表 / 过程 / 方法诊断
# ---------------------------------------------------------------------------

def test_final_completion_rate_excludes_unreachable():
    """η_final 分母只排除物理不可达、淘汰（须为 0，进分母作缺陷信号）；按时率按最终交付口径。"""
    t0 = _FakeTask(0, 5.0, 0.0, 6000.0, completed_at=600.0)
    t1 = _FakeTask(1, 5.0, 0.0, 6000.0, completed_at=None)   # 淘汰（缺陷信号）→ 进分母
    t2 = _FakeTask(2, 5.0, 0.0, 6000.0, completed_at=None)   # 物理不可达 → 不进分母
    records = [
        _FakeRecord(1, muas={"feasible": True}, executed=[0],
                    expired=[1], unreachable=[2]),
    ]
    m = exp01_metrics.build_metrics(_facts(records, [t0, t1, t2], [t0]))
    s = m["summary"]
    assert abs(s["completion_rate_final"] - 0.5) < 1e-5, s   # 1 / (3−1)
    # A1（2026-10-24）：naive 完成率键已删除（正确性验证台不留双口径），
    # 旧断言 completion_rate == 1/3 随之移除；naive 值可由 n_completed/
    # n_arrived 复算，不进 summary。
    assert abs(s["elimination_rate"] - 0.5) < 1e-5, s        # 1 / (3−1)
    assert abs(s["unreachable_rate"] - 1 / 3) < 1e-5, s
    assert abs(s["on_time_rate_final"] - 1.0) < 1e-5, s      # 交付 1、按时 1
    assert abs(s["path_per_completion_m"] - s["total_path_m"]) < 1e-5, s
    # A2（2026-10-24）：出动池化 on_time_rate 键已删除
    assert "on_time_rate" not in s, s
    assert "completion_rate" not in s, s


def test_quota_utilization_backlog_and_s_coverage():
    """名额利用率（Σused/Σcap）、积压均值/峰值、S 内覆盖率分账。"""
    t0 = _FakeTask(0, 5.0, 0.0, 6000.0, completed_at=600.0)
    t1 = _FakeTask(1, 5.0, 0.0, 6000.0, completed_at=1200.0)
    records = [
        _FakeRecord(1, muas={"n_selected": 18, "final_n_hard": 1}, executed=[0],
                    selection_extra={"capability_n_sel": 20, "selected_ids": [0, 1]},
                    pool_counts={"n_total": 30, "n_executing": 2}),
        _FakeRecord(2, muas={"n_selected": 20}, executed=[1],
                    selection_extra={"capability_n_sel": 20, "selected_ids": [1]},
                    pool_counts={"n_total": 40, "n_executing": 3}),
    ]
    m = exp01_metrics.build_metrics(_facts(records, [t0, t1], [t0, t1]))
    s = m["summary"]
    assert abs(s["quota_utilization"] - 38 / 40) < 1e-5, s
    assert abs(s["quota_full_cycle_ratio"] - 0.5) < 1e-5, s
    assert abs(s["avg_backlog"] - (32 + 43) / 2) < 1e-2, s
    assert s["max_backlog"] == 43, s
    assert s["n_hard_violations_total"] == 1, s
    # 周期1：S={0,1} 交付{0} ⇒ 0.5；周期2：S={1} 交付{1} ⇒ 1.0；均值 0.75
    assert abs(s["s_coverage_rate"] - 0.75) < 1e-5, s


def test_deferred_delivery_cross_cycle():
    """跨周期推迟：交付周期 > 到达周期才算（“或早或晚”口径）。"""
    t0 = _FakeTask(0, 5.0, 0.0, 6000.0, completed_at=600.0)
    t1 = _FakeTask(1, 5.0, 0.0, 6000.0, completed_at=1200.0)
    records = [
        _FakeRecord(1, executed=[0]),
        _FakeRecord(2, executed=[1]),
    ]
    m = exp01_metrics.build_metrics(
        _facts(records, [t0, t1], [t0, t1], arrivals={1: [t0, t1]})
    )
    s = m["summary"]
    assert s["n_deferred_delivery"] == 1, s   # t1 周期1到达、周期2交付
    assert abs(s["deferred_delivery_ratio"] - 0.5) < 1e-5, s


# ---------------------------------------------------------------------------
# 2026-10-24 指标体系审查：A3（分母对齐）/ B1（废飞航程）/ B2（命中率）/ D（峰值聚合）
# ---------------------------------------------------------------------------

def test_effective_completion_rate_denominator_excludes_unreachable():
    """A3：有效完成率分母对齐 n_final_denom（剔除物理不可达）。

    t0 按时交付（eff=R）；t1 物理不可达（出 final 分母）；t2 迟到 1200s
    ≥ W=600 ⇒ eff=0 仍计完成。旧口径分母含 t1 ⇒ (2−1)/3≈0.333；新口径
    (2−1)/2=0.5，与 completion_rate_final 同分母——两者之差即零奖励
    交付的净贡献，不再混入分母差。
    """
    t0 = _FakeTask(0, 10.0, 0.0, 600.0, completed_at=600.0)
    t1 = _FakeTask(1, 10.0, 0.0, 6000.0, completed_at=None)      # 物理不可达
    t2 = _FakeTask(2, 10.0, 0.0, 600.0, completed_at=1800.0)     # 迟到 ≥ W
    records = [
        _FakeRecord(1, muas={"feasible": True}, executed=[0, 2], late=[2],
                    lateness={"2": 1200.0}, unreachable=[1]),
    ]
    m = exp01_metrics.build_metrics(
        _facts(records, [t0, t1, t2], [t0, t2], decay_w=600.0))
    s = m["summary"]
    assert abs(s["completion_rate_final"] - 1.0) < 1e-5, s      # 2 / (3−1)
    assert s["n_zero_reward_delivery"] == 1, s
    assert abs(s["effective_completion_rate"] - 0.5) < 1e-5, s  # (2−1) / (3−1)


def test_infeasible_wasted_path_attribution():
    """B1：废飞已飞航程按任务数比例归属；逐周期值落 cycles 行。

    周期1 航次A：3 任务 est 3000m、其中 1 个废飞 ⇒ 1000m；
    周期1 航次B：2 任务 est 1000m、无废飞 ⇒ 0；周期2 无废飞 ⇒ 0。
    """
    tasks = [_FakeTask(i, 5.0, 0.0, 6000.0) for i in range(5)]
    records = [
        _FakeRecord(1, muas={"feasible": False}, executed=[0, 1, 2, 3, 4],
                    infeasible=[1],
                    tours=[
                        {"task_ids": [0, 1, 2], "est_cost": 3000.0,
                         "feasible": False},
                        {"task_ids": [3, 4], "est_cost": 1000.0,
                         "feasible": True},
                    ]),
        _FakeRecord(2, muas={"feasible": True}, executed=[]),
    ]
    m = exp01_metrics.build_metrics(_facts(records, tasks, []))
    s = m["summary"]
    assert s["n_infeasible_delivery"] == 1, s
    assert abs(s["infeasible_wasted_path_m"] - 1000.0) < 1e-6, s
    rows = {row["cycle"]: row.get("infeasible_path_m") for row in m["cycles"]}
    assert rows == {1: 1000.0, 2: 0.0}, rows


def test_infeasible_wasted_path_zero_when_no_waste():
    """B1：无废飞时废飞航程恒 0（不依赖 tours 字段是否存在）。"""
    t0 = _FakeTask(0, 5.0, 0.0, 6000.0, completed_at=600.0)
    records = [_FakeRecord(1, muas={"feasible": True}, executed=[0])]
    m = exp01_metrics.build_metrics(_facts(records, [t0], [t0]))
    assert m["summary"]["infeasible_wasted_path_m"] == 0.0


def test_cand_hit_rate():
    """B2：候选池命中率 = Σselection.n_selected / Σcapability_n_cand（池化）。"""
    t0 = _FakeTask(0, 5.0, 0.0, 6000.0, completed_at=600.0)
    t1 = _FakeTask(1, 5.0, 0.0, 6000.0, completed_at=1200.0)
    records = [
        _FakeRecord(1, muas={"n_selected": 18}, executed=[0],
                    selection_extra={"capability_n_cand": 30, "n_selected": 18}),
        _FakeRecord(2, muas={"n_selected": 20}, executed=[1],
                    selection_extra={"capability_n_cand": 40, "n_selected": 20}),
    ]
    m = exp01_metrics.build_metrics(_facts(records, [t0, t1], [t0, t1]))
    assert abs(m["summary"]["cand_hit_rate"] - 38 / 70) < 1e-5, m["summary"]


def test_max_backlog_peak_aggregation():
    """D：max_backlog 跨 seed 聚合取峰值（mean 位=跨 seed max，std=0）。"""
    ss = exp01_metrics.build_seed_summary([
        {"max_backlog": 5, "n_completed": 10},
        {"max_backlog": 9, "n_completed": 12},
        {"max_backlog": 7, "n_completed": 11},
    ])
    assert ss["max_backlog"] == {"mean": 9.0, "std": 0.0}, ss["max_backlog"]
    # 普通键仍是 mean±std（ddof=0）：[10,12,11] ⇒ 11 ± sqrt(2/3)
    assert abs(ss["n_completed"]["mean"] - 11.0) < 1e-9, ss["n_completed"]
    assert abs(ss["n_completed"]["std"] - (2 / 3) ** 0.5) < 1e-6, ss["n_completed"]


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
