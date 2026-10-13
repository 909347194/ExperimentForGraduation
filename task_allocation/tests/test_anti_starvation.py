# -*- coding: utf-8 -*-
"""防饥饿（Fix①②）与配置接线契约测试。

背景：任务只要能力可达、不违反约束就应**最终**被执行（字典序：完成数优先，
成本次之）。滚动时域下允许跨周期推迟，因此必须有兜底保证任务不会被无限期
推迟——这正是防饥饿的职责：

  Fix① ``marginal.age_gamma``    ：等待时长对终刀接受判据的乘性软加权
                                   ``eff = ratio·(1 + gamma·age)``
  Fix② ``AntiStarvationConfig``  ：等待超 ``force_after_seconds`` 或逼近
                                   ``latest``（剩余 ≤ ``deadline_slack``）的
                                   任务**强制纳入**候选并获强优先

历史缺陷（本测试锁定的回归）：``selection_config_from_dict`` 曾遗漏读取
``marginal.age_gamma`` / ``marginal.wait_scale`` / ``anti_starvation.*`` /
``capability.fallback_when_disabled``，导致 yaml 里显式写了这些旋钮却静默
退回默认值、机制从未生效。故此处同时锁定「yaml → 配置对象」的接线契约。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import ScoredTask, Task, UAV  # noqa: E402
from task_allocation.methods.rolling.horizon import RollingHorizon  # noqa: E402
from task_allocation.methods.selection.marginal_gain import (  # noqa: E402
    MarginalConfig,
    select_by_marginal_gain,
)
from task_allocation.methods.selection.pipeline import (  # noqa: E402
    AntiStarvationConfig,
    SelectionConfig,
    run,
)


def _task(
    tid: int,
    x: float,
    y: float,
    reward: float = 5.0,
    arrival: float = 0.0,
    latest: float = 1e6,
) -> Task:
    return Task(
        id=tid, x=x, y=y, z=0.0, reward=reward,
        earliest=0.0, latest=latest, arrival_time=arrival,
    )


def _uav(uid: int, rng: float = 200_000.0, x: float = 0.0, y: float = 0.0) -> UAV:
    return UAV(id=uid, x=x, y=y, z=0.0, remaining_range=rng, speed=10.0, nest_id=0)


def _sel_cfg(
    *,
    enabled: bool,
    current_time: float = 0.0,
    force_after: float = 1200.0,
    deadline_slack: float = 300.0,
    age_gamma: float = 0.0,
    max_selected: int = 1,
) -> SelectionConfig:
    """构造一个「规模可控」的选择配置：关掉能力上界，名额由 max_selected 定。"""
    cfg = SelectionConfig(
        current_time=current_time,
        marginal=MarginalConfig(
            max_selected=max_selected,
            age_gamma=age_gamma,
            wait_scale=3600.0,
        ),
        anti_starvation=AntiStarvationConfig(
            enabled=enabled,
            force_after_seconds=force_after,
            deadline_slack=deadline_slack,
        ),
    )
    cfg.capability.enabled = False  # 排除 cap(t) 干扰，聚焦防饥饿语义
    return cfg


# ── 接线契约（yaml → 配置对象）──────────────────────────────────────
def test_from_dict_wires_anti_starvation_and_age_gamma():
    """Fix①② 旋钮必须能从 yaml 段读入 —— 曾因漏接线静默退回默认值。"""
    sc = RollingHorizon.selection_config_from_dict(
        {
            "marginal": {"age_gamma": 1.5, "wait_scale": 600.0, "max_ratio": 2.0},
            "anti_starvation": {
                "enabled": True,
                "force_after_seconds": 900.0,
                "deadline_slack": 120.0,
            },
            "capability": {"fallback_when_disabled": True},
        }
    )
    assert sc.marginal.age_gamma == 1.5, "age_gamma 未接线 → 静默退回 0.0"
    assert sc.marginal.wait_scale == 600.0, "wait_scale 未接线"
    assert sc.anti_starvation.enabled is True, "anti_starvation.enabled 未接线"
    assert sc.anti_starvation.force_after_seconds == 900.0
    assert sc.anti_starvation.deadline_slack == 120.0
    assert sc.capability.fallback_when_disabled is True, "fallback_when_disabled 未接线"


def test_from_dict_defaults_keep_mechanism_off():
    """缺省时必须保持关闭：防饥饿默认不得意外改变既有消融基线。"""
    sc = RollingHorizon.selection_config_from_dict({})
    assert sc.anti_starvation.enabled is False
    assert sc.marginal.age_gamma == 0.0
    assert sc.capability.fallback_when_disabled is False


# ── Fix① 等待软加权 ────────────────────────────────────────────────
def test_age_gamma_prioritizes_long_waiting_task():
    """Fix①：等得久的任务在终刀排序中应更靠前（同名额下挤掉「近但新」的任务）。

    几何：t_near 近(10km)、t_far 较远(20km)，同收益 → 基础 ratio 上 t_near 胜
    （约 2 倍）。t_near 刚到达(waited=0)、t_far 已等 3000s，开启 age_gamma 后
    t_far 的 eff = ratio·(1+γ·age) 被放大，应反超 t_near。

    注：age_gamma 是**有界软加权**（age∈[0,1]），几何代价差一个数量级时不足以
    翻盘——这正是必须再配 Fix② 硬性兜底的原因，本用例只验证「适度差异下生效」。
    """
    t_near = _task(0, 10_000.0, 0.0, arrival=3_000.0)         # 刚到
    t_far = _task(1, 20_000.0, 0.0, arrival=0.0)              # 等了 3000s
    uavs = [_uav(0)]
    base = dict(current_time=3_000.0, max_selected=1)

    got_off = run([t_near, t_far], uavs, _sel_cfg(enabled=False, age_gamma=0.0, **base))
    got_on = run([t_near, t_far], uavs, _sel_cfg(enabled=False, age_gamma=8.0, **base))

    assert [t.id for t in got_off.selected] == [0], "关闭加权时应选更近的 t_near"
    assert [t.id for t in got_on.selected] == [1], (
        "age_gamma 开启后，久等的 t_far 应凭等待加权反超"
    )


# ── Fix② 硬性不停饿兜底 ────────────────────────────────────────────
def test_anti_starvation_forces_long_waiting_task():
    """Fix②：等待超阈值的任务被强制纳入，即便几何代价远劣于新任务。"""
    t_new = _task(0, 1_000.0, 0.0, arrival=2_000.0)           # 新且近
    t_old = _task(1, 60_000.0, 0.0, arrival=0.0)              # 老且远（waited=2000）
    uavs = [_uav(0)]
    base = dict(current_time=2_000.0, force_after=1_200.0, max_selected=1)

    off = run([t_new, t_old], uavs, _sel_cfg(enabled=False, **base))
    on = run([t_new, t_old], uavs, _sel_cfg(enabled=True, **base))

    assert [t.id for t in off.selected] == [0], "关闭兜底时应选近的 t_new"
    assert [t.id for t in on.selected] == [1], (
        "开启兜底后，等待 2000s > 1200s 的 t_old 应被强制纳入"
    )


def test_anti_starvation_forces_near_deadline_task():
    """Fix②：即便等待未达阈值，逼近 latest（剩余 ≤ slack）也应强制纳入。

    注意 forced 只绕过 Top-αK **预筛**，仍须过可行性过滤：故 t_urgent 必须是
    时间窗可行的（2km / 10m·s⁻¹ = 200s，1000+200=1200 ≤ latest 1250），否则
    被 prefilter 正确淘汰 —— 强制纳入不等于强制执行不可行任务。
    """
    now = 1_000.0
    # 等待仅 1000s < force_after(1200)，但 latest=1250 → 剩余 250 ≤ slack(300)
    t_urgent = _task(0, 2_000.0, 0.0, reward=0.01, arrival=0.0, latest=1_250.0)
    t_relax = _task(1, 1_000.0, 0.0, arrival=0.0, latest=1e6)
    uavs = [_uav(0)]
    base = dict(current_time=now, force_after=1_200.0, deadline_slack=300.0, max_selected=1)

    off = run([t_urgent, t_relax], uavs, _sel_cfg(enabled=False, **base))
    on = run([t_urgent, t_relax], uavs, _sel_cfg(enabled=True, **base))

    assert [t.id for t in off.selected] == [1], "关闭兜底时应选近的 t_relax"
    assert [t.id for t in on.selected] == [0], "临期任务应被强制纳入"


def test_forced_task_overrides_low_marginal_stop():
    """强制任务应能突破 ``min_marginal_ratio`` 的终止条件（不停饿的核心）。"""
    t_bad = _task(0, 80_000.0, 0.0, reward=0.001, arrival=0.0)  # ratio 极低
    uavs = [_uav(0)]
    cfg = MarginalConfig(min_marginal_ratio=1e9, max_selected=1)  # 阈值高到必停
    cands = [ScoredTask(task=t_bad, priority=1.0)]

    got = select_by_marginal_gain(cands, uavs, cfg, forced_task_ids={0})
    assert [s.task.id for s in got] == [0], (
        "forced 任务应无视 min_marginal_ratio 被纳入，否则会永久滞留"
    )


def test_selection_run_does_not_mutate_shared_marginal():
    """滚动时域下每周期应独立重算：run() 不得就地改写调用方共享的 marginal。

    horizon 每周期以共享的 ``sel_cfg.marginal`` 构造 SelectionConfig，若 run()
    就地写入 forced 集，语义上等于让「上周期强制过的任务」永久保持强制。
    """
    cfg = _sel_cfg(enabled=True, current_time=2_000.0, force_after=1_200.0)
    before_forced = set(cfg.marginal.forced_task_ids)
    before_wait = cfg.marginal.wait_scale

    run([_task(0, 1_000.0, 0.0, arrival=0.0)], [_uav(0)], cfg)

    assert cfg.marginal.forced_task_ids == before_forced, (
        "run() 不应就地改写传入的 marginal.forced_task_ids"
    )
    assert cfg.marginal.wait_scale == before_wait, "run() 不应就地改写 wait_scale"
