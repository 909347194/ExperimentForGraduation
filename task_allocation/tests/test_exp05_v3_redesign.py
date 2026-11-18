# -*- coding: utf-8 -*-
"""exp05 E1 收窄设计的纯函数回归测试（不跑求解、不写产物）。

文件名沿用历史命名，内容守护 2026-11-13 定稿
``docs/exp05_e1_design_2026-11-13.md``：

- 单一主张 E1 / 4 方法（M0/B1/B2/D0）/ 单一 DEM 三维尺；
- 唯一推断主判据 = M0−B1 的每完成 DEM 航程（逐 seed 归一后配对）；
- 描述性档（B2 / D0 / 绝对量 / 代价）不得产出 verdict；
- 门槛恒等式 / eliminating / 独立重评一致性 / failures 分类；
- 精确 Student-t p 值 / 零方差不检验 / 三态判据；
- 唯一比较尺的逐腿有向几何重算（euclid 侧仅诊断留档）。
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

from claims import (  # noqa: E402
    CLAIMS,
    RED_LINES,
    build_claims_report,
    check_invariants,
)
from paired_stats import (  # noqa: E402
    CAVEATS,
    paired_stats,
    t_critical,
    two_sided_p,
    verdict,
)
from post_eval import recompute_two_rulers, summarize_audits  # noqa: E402

E1_DESIGN_VERSION = "e1_single_ruler_4arm_2026-11-13"
E1_METHODS = ("M0", "B1", "B2", "D0")


def _row(audit: float, completed: int, *, evals: float = 100.0) -> dict:
    """恒等式闭合的最小 summary 行（航程列同时给出自报尺与诊断尺）。"""
    return {
        "n_arrived": 60,
        "n_completed": completed,
        "n_unreachable": 1,
        "n_dormant_end": 0,
        "n_remaining_pool": 60 - completed - 1,
        "completion_rate_final": 1.0,
        "n_eliminated": 0,
        "n_hard_violations_total": 0,
        "path_m_audit": audit,               # 唯一比较尺
        "path_m_euclid3d": audit * 0.9,      # 诊断留档
        "ruler_gap_m": audit * 0.1,          # 诊断留档
        "f_audit": audit * 0.1,              # 混尺子，禁进主表
        "total_path_m": audit,               # 自报尺，禁进主表
        "on_time_rate_final": 0.9,
        "reward_yield": 0.8,
        "n_evals_total": evals,
        "wall_seconds": 1.0,
        "consistency_equal_all": 1,
        "audit_errors": [],
        "audit_failure_counts": {},
    }


def _e1_rows() -> dict:
    """4 方法 × 3 seed；M0 每完成航程恒 20，B1 依次 40 / 38.18 / 36.67。"""
    return {
        "M0": {42: _row(1000.0, 50), 43: _row(1100.0, 55), 44: _row(1200.0, 60)},
        "B1": {42: _row(2000.0, 50), 43: _row(2100.0, 55), 44: _row(2200.0, 60)},
        "B2": {42: _row(1200.0, 60), 43: _row(1320.0, 60), 44: _row(1440.0, 60)},
        "D0": {42: _row(1050.0, 50), 43: _row(1150.0, 55), 44: _row(1250.0, 60)},
    }


def _e1_report(rows: dict | None = None) -> dict:
    return build_claims_report(
        rows if rows is not None else _e1_rows(),
        seeds=[42, 43, 44],
        primary_methods=list(E1_METHODS),
        profile="formal",
        primary_pair=("M0", "B1"),
        descriptive_pairs=("B2", "D0"),
    )


# ---------------------------------------------------------------------------
# 精确 p / 三态结论（数值实现不变，E1 继续复用）
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 唯一比较尺：逐腿有向几何重算（euclid 仅诊断留档）
# ---------------------------------------------------------------------------


def test_unique_ruler_is_directional_and_counts_legs():
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
    assert r.path_m_audit == pytest.approx(320.0)   # 唯一比较尺：110 + 110 + 100
    assert r.path_m_euclid3d == pytest.approx(30.0)  # 仅诊断
    assert r.ruler_gap_m == pytest.approx(290.0)     # 仅诊断


def test_unique_ruler_missing_end_nest_is_counted():
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


# ---------------------------------------------------------------------------
# 门槛层硬断言
# ---------------------------------------------------------------------------


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
    """休眠任务仍在池中；恒等式不得把 dormant_end 重复相加。"""
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
    report = _e1_report({"M0": {42: _row(1000.0, 58)}})
    assert report["consistency"]["m0_all_equal"] is True
    assert report["consistency"]["anchor_method"] == "M0"


# ---------------------------------------------------------------------------
# E1：单一主张 / 单一推断主判据 / 描述性档无 verdict / 单尺落盘
# ---------------------------------------------------------------------------


def test_e1_is_the_only_claim_and_declares_single_ruler():
    assert len(CLAIMS) == 1
    assert CLAIMS[0].id == "E1"
    assert CLAIMS[0].pair == ("M0", "B1")
    assert CLAIMS[0].primary_metric == "path_m_audit_per_completion"

    report = _e1_report()
    assert set(report["claims"]) == {"E1"}          # C1–C5 / C4 已删除
    assert report["design_version"] == E1_DESIGN_VERSION
    assert report["design"] == "docs/exp05_e1_design_2026-11-13.md"
    assert report["primary_ruler"] == "dem_vertical_section_3d"
    assert report["methods"] == list(E1_METHODS)
    assert report["primary_pair"] == ["M0", "B1"]
    assert report["all_pairs_complete"] is True
    assert report["unpaired_seeds"] == []
    assert report["primary_gates_passed"] is True
    assert report["gate_failed_cells"] == []

    joined_lines = " ".join(RED_LINES)
    for banned in ("性能提升", "SOTA", "完成率恒 100%", "旧 7 臂", "单组件"):
        assert banned in joined_lines
    joined_caveats = " ".join(CAVEATS)
    assert "仅一条推断主判据" in joined_caveats
    assert "旧 7 臂" in joined_caveats


def test_e1_single_primary_criterion_is_normalized_per_seed():
    report = _e1_report()
    e1 = report["claims"]["E1"]
    assert e1["primary_criterion"] == "M0_minus_B1.path_m_audit_per_completion"

    pp = e1["primary_pair"]
    assert pp["pair"] == ["M0", "B1"]
    assert pp["expected"] == "negative"
    assert pp["descriptive_only"] is False
    assert "verdict" in pp                       # 唯一带三态结论的主判据
    assert pp["ruler"] == "dem_vertical_section_3d"
    # M0 每完成恒 20；B1 = 40 / 38.18 / 36.67 ⇒ 逐 seed 归一化差值
    assert pp["per_seed"] == pytest.approx(
        [-20.0, -18.181818, -16.666667], abs=1e-5
    )
    assert pp["paired_seeds"] == [42, 43, 44]
    assert pp["stats"]["mean"] == pytest.approx(-18.282828, abs=1e-5)
    assert pp["verdict"]["state"] == "supported"


def test_e1_descriptive_pairs_and_absolute_levels_have_no_verdict():
    report = _e1_report()
    e1 = report["claims"]["E1"]
    assert e1["descriptive_pairs"] == ["B2", "D0"]
    # pairs 含 B1（主判据对照，描述性呈现）+ B2 / D0，一律无 verdict
    assert set(e1["pairs"]) == {"B1", "B2", "D0"}
    for other, entry in e1["pairs"].items():
        for key, item in entry.items():
            assert "verdict" not in item, (other, key)
    for block in e1["reference"].values():
        for st in block["metrics"].values():
            assert "verdict" not in st
            assert "p_two_sided" not in st


def test_e1_table_spec_locks_single_ruler_and_excludes_mixed_columns():
    spec = _e1_report()["claims"]["E1"]["table_spec"]
    assert spec["role"] == "effectiveness_reference"
    assert spec["primary_ruler"] == "path_m_audit"
    assert spec["normalized_ruler"] == "path_m_audit_per_completion"
    assert spec["inference"] == "single_primary_criterion"
    excluded = set(spec["excluded_columns"])
    assert {"f_audit", "total_path_m", "path_per_completion_m",
            "path_m_euclid3d", "ruler_gap_m"} <= excluded


def test_e1_reference_only_four_methods_on_single_ruler():
    ref = _e1_report()["claims"]["E1"]["reference"]
    assert sorted(ref) == ["B1", "B2", "D0", "M0"]
    for block in ref.values():
        assert block["ruler"] == "dem_directed_3d"
        assert "path_m_euclid3d" not in block["metrics"]
        assert "f_audit" not in block["metrics"]
        for st in block["metrics"].values():
            assert set(st) == {"n", "mean", "std", "ci95"}
    assert ref["M0"]["metrics"]["path_m_audit"]["mean"] == pytest.approx(1100.0)
    assert ref["M0"]["n_missing_denominator"] == 0


def test_e1_normalized_pair_bad_denominator_is_not_silently_zeroed():
    rows = {
        "M0": {42: _row(1000.0, 0)},
        "B1": {42: _row(2000.0, 60)},
        "B2": {42: _row(1200.0, 60)},
        "D0": {42: _row(1050.0, 60)},
    }
    report = _e1_report(rows)
    pp = report["claims"]["E1"]["primary_pair"]
    assert pp["paired_seeds"] == []
    # 本用例只给 seed 42 的行，协议里的 3 个 seed 全部显式登记为未配对
    # （42 是分母 n_completed<=0，43/44 是缺行）；一律不静默补 0、不丢弃。
    assert pp["unpaired_seeds"] == [42, 43, 44]
    assert pp["stats"]["n_pairs"] == 0
    assert pp["per_seed"] == []
    assert report["all_pairs_complete"] is False
    assert report["unpaired_seeds"] == [42, 43, 44]
