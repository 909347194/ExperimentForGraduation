# -*- coding: utf-8 -*-
"""L3b 统一总闸（2026-11-06 用户裁定②）——配置解析 + 行为边界测试。

裁定原文：新增统一 ``l3b_enabled``（默认 False）**同时约束两个通道**
+ 旧 config 映射。两个受控通道：

- 通道 A ``polish_assignment`` 的 relocate / swap（``polish_moves``）；
- 通道 B 交付后成本 polish 的 ``_cost_polish_relocate``。

契约：
1. 默认档总闸关；显式 ``l3b_enabled=True`` 两通道同时恢复；
2. 旧 config 保义映射：未设新键但显式写了旧键且开启 ⇒ 总闸开，
   数值与「显式开」逐位一致（``--set``/yaml 未改的历史实验不被静默改数）；
3. 新键优先：显式 False + 旧键显式 True ⇒ 关，诊断留痕不抛错；
4. L3a 纯几何（2-opt/Or-opt/终点巢 MCF/position）不受总闸约束；
5. ``replace()`` 幂等（property 无状态，``current_time``/memetic 重入不翻闸）；
6. 完成率铁律：关总闸不得降低完成数 / 制造硬违约 / 触发交付闸；
7. 第三来源 ``coverage_first`` 单调 polish 的跨机 relocate **不受**总闸约束
   （默认 coverage_first=False ⇒ 默认档恒 0），单列 ``l3b_ungated_monotone_relocate``
   如实记账，是否纳入总闸属 P4 待裁定项。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # task_allocation/
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from task_allocation.common.types import Nest, Task, UAV  # noqa: E402
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs  # noqa: E402
from task_allocation.methods.muas.problem import MUASProblemConfig  # noqa: E402
from task_allocation.methods.muas.solvers.dmde_solver import DMDEConfig  # noqa: E402
from task_allocation.methods.muas.solvers.local_search import (  # noqa: E402
    DEFAULT_MOVES,
    MOVE_POSITION,
)
from task_allocation.methods.muas.solvers.stage_config import (  # noqa: E402
    MUASStageConfig,
)
from task_allocation.methods.muas.solvers.stage_runner import (  # noqa: E402
    run_selective_muas,
)

NEST_KEY = 1_000_000


# ---------------------------------------------------------------------------
# 合成场景（与 test_p3_l3_split 同口径，便于交叉对照）
# ---------------------------------------------------------------------------


def _scenario():
    nests = [
        Nest(id=0, x=0.0, y=0.0, z=0.0, capacity=2, available=True),
        Nest(id=1, x=4000.0, y=0.0, z=0.0, capacity=2, available=True),
    ]
    uavs = [
        UAV(id=0, x=0.0, y=0.0, z=0.0, remaining_range=15000.0,
            speed=12.0, nest_id=0),
        UAV(id=1, x=4000.0, y=0.0, z=0.0, remaining_range=15000.0,
            speed=11.0, nest_id=1),
    ]
    tasks = [
        Task(id=0, x=1000.0, y=0.0, z=0.0, reward=10.0,
             earliest=0.0, latest=200.0),
        Task(id=1, x=2000.0, y=0.0, z=0.0, reward=10.0,
             earliest=0.0, latest=200.0),
        Task(id=2, x=3000.0, y=0.0, z=0.0, reward=10.0,
             earliest=0.0, latest=200.0),
        Task(id=3, x=4500.0, y=0.0, z=0.0, reward=10.0,
             earliest=0.0, latest=200.0),
    ]
    return tasks, uavs, nests


def _run(stage_cfg=None, seed=7):
    tasks, uavs, nests = _scenario()
    cb = build_pairwise_costs(
        tasks, uavs, nests=nests, estimator=None, nest_offset=NEST_KEY
    )
    return run_selective_muas(
        tasks, uavs, nests, cb, fleet_uavs=uavs,
        problem_config=MUASProblemConfig(),
        stage_config=stage_cfg or MUASStageConfig(),
        solver_config=DMDEConfig(pop_size=10, max_generations=8, seed=seed),
        nest_offset=NEST_KEY, current_time=0.0,
    )


# ---------------------------------------------------------------------------
# 1. 配置解析：默认 / 显式 / 旧键映射 / 冲突
# ---------------------------------------------------------------------------


def test_default_gate_is_off_and_only_l3a_moves_remain():
    cfg = MUASStageConfig()
    assert cfg.l3b_enabled is None, "None 哨兵 = 未显式设置"
    assert cfg.effective_l3b_enabled is False, "拍板默认：关"
    assert cfg.l3b_source == "default"
    assert cfg.l3b_legacy_conflict is False
    assert cfg.effective_cost_polish_relocate is False, "通道 B 关"
    assert cfg.effective_polish_moves == (MOVE_POSITION,), "通道 A 只剩 L3a"


def test_explicit_on_restores_both_channels():
    cfg = MUASStageConfig(l3b_enabled=True)
    assert cfg.effective_l3b_enabled is True
    assert cfg.l3b_source == "explicit"
    assert cfg.effective_cost_polish_relocate is True
    assert cfg.effective_polish_moves == DEFAULT_MOVES


def test_legacy_config_mapping_preserves_old_behavior():
    """旧 config 显式写全旧键 ⇒ 映射为开（exp01_smoke/config.py 属此类）。"""
    cfg = MUASStageConfig(
        cost_polish_relocate_enabled=True,
        polish_moves=("relocate", "swap", "position"),
    )
    assert cfg.effective_l3b_enabled is True
    assert cfg.l3b_source == "legacy_on"
    assert cfg.effective_cost_polish_relocate is True
    assert cfg.effective_polish_moves == DEFAULT_MOVES
    # 只写通道 B 旧键 / 只写通道 A 旧键，任一开启都足以保义为开
    assert MUASStageConfig(
        cost_polish_relocate_enabled=True
    ).effective_l3b_enabled is True
    assert MUASStageConfig(
        polish_moves=("relocate", "swap", "position")
    ).effective_l3b_enabled is True
    # 旧键显式全关 ⇒ 关，且来源如实标 legacy_off
    off = MUASStageConfig(
        cost_polish_relocate_enabled=False, polish_moves=("position",)
    )
    assert off.effective_l3b_enabled is False
    assert off.l3b_source == "legacy_off"


def test_explicit_new_key_wins_over_legacy_keys_without_raising():
    """显式关 + 旧键显式开 ⇒ 新键优先，留痕（便于 --set 覆盖旧 yaml）。"""
    cfg = MUASStageConfig(
        l3b_enabled=False,
        cost_polish_relocate_enabled=True,
        polish_moves=DEFAULT_MOVES,
    )
    assert cfg.effective_l3b_enabled is False
    assert cfg.effective_cost_polish_relocate is False
    assert cfg.effective_polish_moves == (MOVE_POSITION,)
    assert cfg.l3b_source == "explicit"
    assert cfg.l3b_legacy_conflict is True


def test_fine_grained_legacy_keys_still_work_when_gate_is_on():
    """总闸开时，旧细粒度键仍能单独关掉通道 B（AND 语义）。"""
    cfg = MUASStageConfig(
        l3b_enabled=True, cost_polish_relocate_enabled=False
    )
    assert cfg.effective_l3b_enabled is True
    assert cfg.effective_cost_polish_relocate is False
    assert cfg.effective_polish_moves == DEFAULT_MOVES
    # 总闸关时旧键 True 也开不了归属维度
    assert MUASStageConfig(
        l3b_enabled=False, cost_polish_relocate_enabled=True
    ).effective_cost_polish_relocate is False


def test_replace_is_idempotent():
    """stage_runner 的 current_time / memetic replace 不得翻转总闸。"""
    for cfg in (
        MUASStageConfig(),
        MUASStageConfig(l3b_enabled=True),
        MUASStageConfig(cost_polish_relocate_enabled=True),
        MUASStageConfig(l3b_enabled=False, polish_moves=DEFAULT_MOVES),
    ):
        r1 = replace(cfg, current_time=3.0)
        r2 = replace(r1, polish_max_rounds=6, polish_max_evals=500)
        for c in (r1, r2):
            assert c.effective_l3b_enabled == cfg.effective_l3b_enabled
            assert c.l3b_source == cfg.l3b_source
            assert c.effective_polish_moves == cfg.effective_polish_moves
            assert c.effective_cost_polish_relocate == (
                cfg.effective_cost_polish_relocate
            )


# ---------------------------------------------------------------------------
# 2. 行为边界：默认关 vs 显式开（同 seed）
# ---------------------------------------------------------------------------


def test_gate_off_diagnostics_and_completion_are_clean():
    off = _run(MUASStageConfig(), seed=7).diagnostics
    assert off["l3b_enabled"] is False
    assert off["l3b_enabled_observed"] is False
    assert off["l3b_source"] == "default"
    assert off["l3b_legacy_conflict"] is False
    assert off["l3b_relocate_actions"] == 0, "两个受控通道都不得改归属"
    assert off["l3b_ungated_monotone_relocate"] == 0, "coverage_first 默认关"
    assert off["n_task_to_uav_changes"] == 0, "L3a 纯几何不应改 task→uav"
    # 完成率铁律：关总闸不得降低完成量 / 制造硬违约 / 触发交付闸
    assert off["n_executed"] + off["n_deferred"] == off["n_tasks"]
    assert off["final_n_hard"] == 0
    assert off["repair_status"] == "REPAIRED"
    assert off["delivery_refused"] is False
    # L3a 几何动作仍可用（总闸只管归属维度）
    assert off["l3a_geometric_actions"] >= 0


def test_gate_on_restores_both_channels_in_diagnostics():
    on = _run(MUASStageConfig(l3b_enabled=True), seed=7).diagnostics
    assert on["l3b_enabled"] is True
    assert on["l3b_enabled_observed"] is True
    assert on["l3b_source"] == "explicit"
    assert on["l3b_relocate_actions"] >= 0
    assert on["final_n_hard"] == 0
    assert on["repair_status"] == "REPAIRED"


def test_legacy_mapping_is_bit_identical_to_explicit_on():
    """旧 config 保义映射与显式开逐位一致（排除来源标签本身）。"""
    a = _run(MUASStageConfig(l3b_enabled=True), seed=11)
    b = _run(
        MUASStageConfig(
            cost_polish_relocate_enabled=True,
            polish_moves=DEFAULT_MOVES,
        ),
        seed=11,
    )
    assert a.solution.assignment_pairs() == b.solution.assignment_pairs()
    keys = (
        "final_fitness", "final_path_m", "final_late_s", "final_n_hard",
        "final_excess_m", "n_executed", "n_deferred",
        "n_task_to_uav_changes", "l3a_geometric_actions",
        "l3b_relocate_actions", "cost_polish_n_relocate",
        "cost_polish_n_2opt", "cost_polish_n_oropt",
        "polish_accepted", "best_fitness",
    )
    for k in keys:
        assert a.diagnostics[k] == b.diagnostics[k], k
    assert a.diagnostics["l3b_source"] == "explicit"
    assert b.diagnostics["l3b_source"] == "legacy_on"


def test_legacy_conflict_is_recorded_in_diagnostics():
    d = _run(
        MUASStageConfig(
            l3b_enabled=False,
            cost_polish_relocate_enabled=True,
            polish_moves=DEFAULT_MOVES,
        ),
        seed=23,
    ).diagnostics
    assert d["l3b_enabled"] is False
    assert d["l3b_legacy_conflict"] is True
    assert d["l3b_relocate_actions"] == 0
