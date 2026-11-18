# -*- coding: utf-8 -*-
"""exp05 E1 整体有效性参照表：单尺 / 派生归一 / 出表门禁回归。

守护：主尺子必须是 ``path_m_audit``；``f_audit`` 混尺子禁进表；
``path_m_audit / n_completed`` 逐 seed 归一、分母非正不补 0；
``design_version`` 与 4 方法集合门禁拒绝旧 7 臂 / v3 / v1v2 档；
非 formal / 未配对 / 描述性档含 verdict 一律拒绝出表；
门槛反例必须写进表注，且出表部分一律描述性、不作方法优劣结论。
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

from claims import build_claims_report  # noqa: E402
from build_reference_table import (  # noqa: E402
    E1_DESIGN_VERSION,
    E1_METHODS,
    collect_rows,
    render_tex,
    validate_report,
)

E1_METHOD_LIST = ["M0", "B1", "B2", "D0"]


def _row(audit: float, completed: int, *, evals: float = 100.0) -> dict:
    """恒等式闭合的最小 summary 行。"""
    return {
        "n_arrived": 60,
        "n_completed": completed,
        "n_unreachable": 1,
        "n_dormant_end": 0,
        "n_remaining_pool": 60 - completed - 1,
        "completion_rate_final": 1.0,
        "n_eliminated": 0,
        "n_hard_violations_total": 0,
        "path_m_audit": audit,
        "path_m_euclid3d": audit * 0.9,
        "f_audit": audit * 0.1,
        "total_path_m": audit,
        "on_time_rate_final": 0.9,
        "reward_yield": 0.8,
        "n_evals_total": evals,
        "wall_seconds": 1.0,
        "consistency_equal_all": 1,
        "audit_errors": [],
        "audit_failure_counts": {},
    }


def _report() -> dict:
    # 4 方法都要齐（配对的 anchor 是 M0；B1 是主判据对照，B2/D0 是描述性梯度），
    # 否则 all_pairs_complete 为假、出表会被门禁拦下。
    rows = {
        "M0": {
            42: _row(1000.0, 50),
            43: _row(1100.0, 55),
            44: _row(1200.0, 60),
        },
        "B1": {
            42: _row(2000.0, 50),
            43: _row(2100.0, 55),
            44: _row(2200.0, 60),
        },
        "B2": {
            42: _row(1200.0, 60),
            43: _row(1320.0, 60),
            44: _row(1440.0, 60),
        },
        "D0": {
            42: _row(1050.0, 50),
            43: _row(1150.0, 55),
            44: _row(1250.0, 60),
        },
    }
    return build_claims_report(
        rows, seeds=[42, 43, 44], primary_methods=E1_METHOD_LIST,
        profile="formal",
        primary_pair=("M0", "B1"),
        descriptive_pairs=("B2", "D0"),
    )


def _with_e1(report: dict, **changes) -> dict:
    e1 = {**report["claims"]["E1"], **changes}
    return {**report, "claims": {**report["claims"], "E1": e1}}


def test_e1_table_spec_locks_one_ruler():
    e1 = _report()["claims"]["E1"]
    spec = e1["table_spec"]
    assert spec["role"] == "effectiveness_reference"
    assert spec["primary_ruler"] == "path_m_audit"
    assert spec["normalized_ruler"] == "path_m_audit_per_completion"
    assert "f_audit" in spec["excluded_columns"]
    assert spec["inference"] == "single_primary_criterion"
    assert "verdict" in e1["primary_pair"]
    # 描述性档仍不得产出 verdict
    for other in e1["pairs"].values():
        for item in other.values():
            assert "verdict" not in item


def test_normalized_pair_is_per_seed_and_reports_bad_denominator():
    e1 = _report()["claims"]["E1"]
    pair = e1["pairs"]["B2"]["path_m_audit_per_completion"]
    # 1000/50−1200/60=0；1100/55−1320/60=−2；1200/60−1440/60=−4
    assert pair["per_seed"] == [0.0, -2.0, -4.0]
    assert pair["stats"]["mean"] == pytest.approx(-2.0)
    assert pair["paired_seeds"] == [42, 43, 44]
    assert pair["descriptive_only"] is True

    rows = {
        "M0": {42: _row(1000.0, 0)},
        "B1": {42: _row(2000.0, 60)},
        "B2": {42: _row(1200.0, 60)},
        "D0": {42: _row(1050.0, 60)},
    }
    broken = build_claims_report(
        rows, seeds=[42], primary_methods=E1_METHOD_LIST, profile="formal",
    )
    p = broken["claims"]["E1"]["pairs"]["B2"]["path_m_audit_per_completion"]
    assert p["paired_seeds"] == []
    assert p["unpaired_seeds"] == [42]          # 不静默补 0
    assert p["stats"]["n_pairs"] == 0


def test_reference_only_four_methods_and_no_test_statistics():
    e1 = _report()["claims"]["E1"]
    ref = e1["reference"]
    assert sorted(ref) == ["B1", "B2", "D0", "M0"]
    for block in ref.values():
        assert block["ruler"] == "dem_directed_3d"
        for st in block["metrics"].values():
            assert set(st) == {"n", "mean", "std", "ci95"}
            assert "p_two_sided" not in st
    assert ref["M0"]["metrics"]["path_m_audit"]["mean"] == pytest.approx(1100.0)
    assert ref["M0"]["n_missing_denominator"] == 0


def test_generate_reference_table_excludes_mixed_ruler():
    report = _report()
    notes = validate_report(report)
    assert notes == []
    rows = collect_rows(report)
    assert all("f_audit" not in r["metric"] for r in rows)
    assert all("path_m_euclid3d" not in r["metric"] for r in rows)
    assert any(r["metric"] == "path_m_audit_per_completion" for r in rows)
    # 配对行一律描述性，名义 p 只随 CSV 留档
    assert all(
        r["note"] == "描述性配对差：只给估计量与区间，不出推断结论"
        for r in rows if r["section"] == "paired"
    )
    tex = render_tex(
        report, rows, scenario="test", source_label="unit_test", extra_notes=notes,
    )
    assert "f_audit" not in tex
    assert "不作方法优劣结论" in tex
    assert "唯一推断主判据" in tex
    assert r"\begin{tabular}{lrrrrr}" in tex
    assert "M0$-$B2" in tex
    assert "旧 7 臂" in tex


def test_anchor_drives_paired_row_labels():
    """配对差标签由 primary_pair[0] 驱动，不写死 M0。"""
    report = {**_report(), "primary_pair": ["B2", "B1"]}
    validate_report(report)
    paired = [r["method"] for r in collect_rows(report) if r["section"] == "paired"]
    assert set(paired) == {"B2−B1", "B2−B2", "B2−D0"}
    tex = render_tex(
        report, collect_rows(report), scenario="test",
        source_label="unit_test", extra_notes=[],
    )
    assert "B2$-$B1" in tex


def test_gate_refuses_legacy_smoke_incomplete_and_overreaching_reports():
    report = _report()
    with pytest.raises(SystemExit, match="旧 7 臂"):
        validate_report({**report, "design_version": "v3_formal_2026-11-09"})
    with pytest.raises(SystemExit, match="4 方法"):
        validate_report({**report, "methods": ["M0", "B1", "B2"]})
    with pytest.raises(SystemExit, match="primary_pair"):
        validate_report({**report, "primary_pair": ["M0", "Q0"]})
    with pytest.raises(SystemExit, match="唯一比较尺"):
        validate_report({**report, "primary_ruler": "euclidean_3d"})
    with pytest.raises(SystemExit, match="冒烟"):
        validate_report({**report, "profile": "smoke"})
    with pytest.raises(SystemExit, match="配对不完整"):
        validate_report({**report, "all_pairs_complete": False})
    with pytest.raises(SystemExit, match="未配对 seed"):
        validate_report({**report, "unpaired_seeds": [44]})
    with pytest.raises(SystemExit, match="重算 claims_report"):
        validate_report(_with_e1(report, table_spec={}))
    with pytest.raises(SystemExit, match="排除列"):
        validate_report(_with_e1(report, table_spec={
            **report["claims"]["E1"]["table_spec"], "excluded_columns": [],
        }))
    with pytest.raises(SystemExit, match="verdict"):
        validate_report(_with_e1(report, primary_pair={}))
    # 描述性配对里混进 verdict ⇒ 越权产出结论，拒绝出表
    pairs = {
        k: dict(v) for k, v in report["claims"]["E1"]["pairs"].items()
    }
    pairs["B2"] = dict(pairs["B2"])
    pairs["B2"]["path_m_audit"] = {
        **pairs["B2"]["path_m_audit"], "verdict": {"state": "supported"},
    }
    with pytest.raises(SystemExit, match="越权产出结论"):
        validate_report(_with_e1(report, pairs=pairs))


def test_failed_gate_is_declared_not_hidden():
    report = {**_report(), "primary_gates_passed": False}
    notes = validate_report(report)
    assert len(notes) == 1
    assert "反例" in notes[0]
    tex = render_tex(
        report, collect_rows(report), scenario="test",
        source_label="unit_test", extra_notes=notes,
    )
    assert "完成率恒 100" in tex


def test_constants_match_design_document():
    assert E1_DESIGN_VERSION == "e1_single_ruler_4arm_2026-11-13"
    assert set(E1_METHODS) == {"M0", "B1", "B2", "D0"}
