# -*- coding: utf-8 -*-
"""exp05 v3 重新设计的纯函数回归测试（不跑求解、不写产物）。

守护口径：精确 p 值 / 零方差不检验 / 三态判据 / 两把尺子的有向逐腿重算 /
门槛恒等式 / 独立重评 failures 分类 / 主张报告结构。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # task_allocation/
REPO = ROOT.parent
EXP05 = ROOT / "experiments" / "exp05_baselines"
for p in (str(REPO), str(EXP05)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

from claims import CLAIMS, build_claims_report, check_invariants  # noqa: E402
from paired_stats import paired_stats, t_critical, two_sided_p, verdict  # noqa: E402
from post_eval import recompute_two_rulers, summarize_audits  # noqa: E402


def test_exact_student_t_p_value():
    assert two_sided_p(2.776445, 4) == pytest.approx(0.05, abs=1e-5)
    assert t_critical(4) == pytest.approx(2.776445, abs=1e-4)
    assert two_sided_p(float("nan"), 4) is None


def test_paired_stats_and_ci():
    st = paired_stats([1.0, 2.0, 3.0, 4.0, 5.0])
    assert st["mean"] == pytest.approx(3.0)
    assert st["t_stat"] == pytest.approx(4.242641, abs=1e-5)
    assert st["p_two_sided"] == pytest.approx(0.013236, abs=1e-5)
    assert st["ci95"][0] == pytest.approx(1.036757, abs=1e-5)
    assert st["cohens_dz"] == pytest.approx(1.897367, abs=1e-5)
    assert st["p_method"] == "student_t_incomplete_beta_exact"


def test_zero_variance_is_not_insignificance():
    st = paired_stats([2.0, 2.0, 2.0])
    assert st["p_two_sided"] is None
    assert st["p_reason"] == "zero_variance_no_test"
    assert st["significant_at_0_05"] is None
    assert verdict(st, expected="negative")["state"] == "inconclusive"
    assert "未检出差异" in verdict(st)["wording"]


def test_verdict_three_states():
    assert verdict(paired_stats([-3.0, -4.0, -5.0]), expected="negative")["state"] == "supported"
    assert verdict(paired_stats([3.0, 4.0, 5.0]), expected="negative")["state"] == "not_supported"
    assert verdict(paired_stats([-1.0, 2.0, -1.0]), expected="negative")["state"] == "inconclusive"


def test_two_rulers_are_directional_and_count_legs():
    sol = {
        "tasks": [
            {"id": 1, "xyz": [10.0, 0.0, 0.0]},
            {"id": 2, "xyz": [20.0, 0.0, 0.0]},
        ],
        "cycles": [{
            "tours": [{
                "start": [0.0, 0.0, 0.0],
                "start_nest_id": 0,
                "end_nest_id": 0,
                "task_ids": [1, 2],
            }]
        }],
    }
    # 有向：爬升方向多算 10 m，反向不算；若实现把 a/b 对调就会少算。
    def dem(a, b):
        return 100.0 + (10.0 if float(b[0]) > float(a[0]) else 0.0)

    r = recompute_two_rulers(
        sol,
        dem_distance_fn=dem,
        euclid_distance_fn=lambda a, b: 10.0,
        nest_xyz={0: (0.0, 0.0, 0.0)},
    )
    assert r.n_legs == 3                  # 起点→T1→T2→终点巢
    assert r.n_missing_end_nest == 0
    assert r.path_m_audit == pytest.approx(320.0)   # 110 + 110 + 100
    assert r.path_m_euclid3d == pytest.approx(30.0)
    assert r.ruler_gap_m == pytest.approx(290.0)


def test_two_rulers_missing_end_nest_is_counted():
    sol = {
        "tasks": [{"id": 1, "xyz": [1.0, 0.0, 0.0]}],
        "cycles": [{"tours": [{
            "start": [0.0, 0.0, 0.0],
            "start_nest_id": None,
            "end_nest_id": None,
            "task_ids": [1],
        }]}],
    }
    r = recompute_two_rulers(
        sol,
        dem_distance_fn=lambda a, b: 1.0,
        euclid_distance_fn=lambda a, b: 1.0,
        nest_xyz={},
    )
    assert r.n_missing_end_nest == 1
    assert r.n_legs == 1                 # 只算到最后一个任务，返航腿不猜 0


def test_invariants_identity_and_eliminating():
    good = {
        "n_arrived": 60, "n_completed": 58, "n_unreachable": 1,
        "n_dormant_end": 0, "n_remaining_pool": 1,
        "completion_rate_final": 1.0, "n_eliminated": 0,
        "n_hard_violations_total": 0,
    }
    assert check_invariants(good)["passed"] is True
    bad = dict(good, n_eliminated=1)
    res = check_invariants(bad)
    assert res["passed"] is False
    assert res["checks"]["eliminating_zero"] is False
    broken = dict(good, n_remaining_pool=0)
    assert check_invariants(broken)["checks"]["identity_closed"] is False


def test_c2_primary_criterion_is_difference_in_differences():
    """尺子主判据必须是「两把尺子下 M0−D0 差值之差」，不是恒真的绝对差。"""
    def row(audit: float, euclid: float) -> dict:
        return {
            "n_arrived": 60, "n_completed": 58, "n_unreachable": 1,
            "n_dormant_end": 0, "n_remaining_pool": 1,
            "completion_rate_final": 1.0, "n_eliminated": 0,
            "n_hard_violations_total": 0,
            "path_m_audit": audit, "path_m_euclid3d": euclid,
            "f_audit": audit * 0.1, "on_time_rate_final": 0.8,
            "reward_yield": 0.9, "n_evals_total": 100,
            "wall_seconds": 1.0, "consistency_equal_all": True,
        }

    rows = {
        "M0": {
            42: row(1000.0, 900.0),
            43: row(1100.0, 1000.0),
            44: row(1200.0, 1100.0),
        },
        "D0": {
            42: row(1050.0, 940.0),
            43: row(1150.0, 1030.0),
            44: row(1250.0, 1120.0),
        },
    }
    report = build_claims_report(
        rows, seeds=[42, 43, 44], primary_methods=["M0", "D0"], profile="formal",
    )
    c2 = report["claims"]["C2"]
    assert c2["primary_criterion"] == "ruler_effect"
    # (M0−D0)@dem = −50 恒定；(M0−D0)@euclid = −40/−30/−20
    assert c2["ruler_effect"]["delta_dem_m_per_seed"] == [-50.0, -50.0, -50.0]
    assert c2["ruler_effect"]["delta_euclid3d_m_per_seed"] == [-40.0, -30.0, -20.0]
    assert c2["ruler_effect"]["per_seed"] == [-10.0, -20.0, -30.0]
    assert c2["ruler_effect"]["stats"]["mean"] == pytest.approx(-20.0)
    assert c2["ruler_effect"]["verdict"]["expected_direction"] == "any"
    # 定义性事实只报量级，不出三态结论
    assert all("verdict" not in v for v in c2["ruler_gap"].values())
    # 第 3 条「偏差因方法而异」已落地
    assert "M0_minus_D0_ratio" in c2["ruler_bias_by_method"]
    assert c2["ruler_bias_by_method"]["M0"]["ratio_per_seed"][0] == pytest.approx(
        1000 / 900, abs=1e-8
    )
    # C4 描述性档不得产出 verdict
    for other in report["claims"]["C4"]["pairs"].values():
        for key, entry in other.items():
            assert "verdict" not in entry, key
    # 主张句不得声称已证明求解端必须用 DEM
    assert "且必须同时用于求解与评价" not in CLAIMS[1].statement


def test_audit_failure_classification():
    reports = [
        {"status": "ok", "fitness": 1.0, "path_m": 10.0, "late_s": 0.0,
         "n_hard": 0, "n_skip": 0,
         "consistency": {"final_fitness": {"equal": True}},
         "failures": ["完成率 0.950000 < 1（分母 20 已排除不可达）",
                      "coverage_audit 未开（旧名 lexicographic_coverage）"]},
        {"status": "no_problem_captured"},
        {"status": "audit_error", "error": "ValueError: boom"},
    ]
    cols = summarize_audits(reports)
    assert cols["n_audit_ok"] == 1
    assert cols["n_audit_unavailable"] == 1
    assert cols["consistency_equal_all"] is True
    assert cols["audit_errors"] == ["ValueError: boom"]
    assert cols["audit_failure_counts"] == {
        "single_cycle_completion_scope": 1,
        "coverage_audit_off": 1,
    }


def test_invariants_dormant_is_subset_of_remaining_pool():
    """休眠任务仍在池中；恒等式不得把 dormant_end 重复相加（正式档 M0/44）。"""
    row = {
        "n_arrived": 60, "n_completed": 51, "n_unreachable": 4,
        "n_dormant_end": 5, "n_remaining_pool": 5,
        "completion_rate_final": 1.0, "n_eliminated": 0,
        "n_hard_violations_total": 0,
    }
    assert check_invariants(row)["passed"] is True
    assert check_invariants(dict(row, n_remaining_pool=4))["checks"][
        "identity_closed"
    ] is False


def test_consistency_flag_accepts_int_from_json():
    """metrics 落盘 consistency_equal_all 为 0|1；`is True` 会误报失败。"""
    row = {
        "n_arrived": 60, "n_completed": 58, "n_unreachable": 1,
        "n_dormant_end": 0, "n_remaining_pool": 1,
        "completion_rate_final": 1.0, "n_eliminated": 0,
        "n_hard_violations_total": 0,
        "path_m_audit": 1000.0, "path_m_euclid3d": 900.0,
        "f_audit": 100.0, "total_path_m": 1000.0,
        "on_time_rate_final": 0.8, "reward_yield": 0.9,
        "n_evals_total": 100, "wall_seconds": 1.0,
        "consistency_equal_all": 1,
        "audit_errors": [], "audit_failure_counts": {},
    }
    report = build_claims_report(
        {"M0": {42: row}}, seeds=[42], primary_methods=["M0"], profile="formal",
    )
    assert report["claims"]["C3"]["m0_all_equal"] is True
