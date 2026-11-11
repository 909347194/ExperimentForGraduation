# -*- coding: utf-8 -*-
"""exp05 C4 外部量级参照表：统一尺子 / 派生归一 / 出表门禁回归。

守护：主尺子必须是 ``path_m_audit``；``f_audit`` 混尺子禁进表；
``path_m_audit / n_completed`` 逐 seed 归一、分母非正不补 0；
非 formal / 未配对 / 旧结构报告一律拒绝出表；门槛反例必须写进表注。
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
    collect_rows,
    render_tex,
    validate_report,
)


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
    # C2 的 M0−D0 与 C4 的 M0−B1 也会登记配对完整性，故四个方法都要齐
    # （正式档为 7 方法 × 5 seed），否则 all_pairs_complete 为假、出表会被门禁拦下。
    rows = {
        "M0": {
            42: _row(1000.0, 50),
            43: _row(1100.0, 55),
            44: _row(1200.0, 60),
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
        "B1": {
            42: _row(2000.0, 50),
            43: _row(2100.0, 55),
            44: _row(2200.0, 60),
        },
    }
    return build_claims_report(
        rows, seeds=[42, 43, 44], primary_methods=["M0", "B2"],
        profile="formal",
    )


def test_c4_table_spec_locks_one_ruler():
    c4 = _report()["claims"]["C4"]
    spec = c4["table_spec"]
    assert spec["role"] == "external_magnitude_reference"
    assert spec["primary_ruler"] == "path_m_audit"
    assert spec["normalized_ruler"] == "path_m_audit_per_completion"
    assert "f_audit" in spec["excluded_columns"]
    assert spec["inference"] == "none"
    assert c4["pairs"]["B2"]["f_audit"]["not_for_table"] is True
    assert c4["pairs"]["B2"]["f_audit"]["ruler"] == "mixed"
    assert c4["pairs"]["B2"]["path_m_audit"]["ruler"] == "dem_directed_3d"
    # 描述性档仍不得产出 verdict
    for other in c4["pairs"].values():
        for item in other.values():
            assert "verdict" not in item


def test_normalized_pair_is_per_seed_and_reports_bad_denominator():
    c4 = _report()["claims"]["C4"]
    pair = c4["pairs"]["B2"]["path_m_audit_per_completion"]
    # 1000/50−1200/60=0；1100/55−1320/60=−2；1200/60−1440/60=−4
    assert pair["per_seed"] == [0.0, -2.0, -4.0]
    assert pair["stats"]["mean"] == pytest.approx(-2.0)
    assert pair["paired_seeds"] == [42, 43, 44]
    assert pair["descriptive_only"] is True

    rows = {
        "M0": {42: _row(1000.0, 0)},
        "B2": {42: _row(1200.0, 60)},
    }
    broken = build_claims_report(
        rows, seeds=[42], primary_methods=["M0", "B2"], profile="formal",
    )
    p = broken["claims"]["C4"]["pairs"]["B2"]["path_m_audit_per_completion"]
    assert p["paired_seeds"] == []
    assert p["unpaired_seeds"] == [42]          # 不静默补 0
    assert p["stats"]["n_pairs"] == 0


def test_reference_only_primary_methods_and_no_test_statistics():
    c4 = _report()["claims"]["C4"]
    ref = c4["reference"]
    assert sorted(ref) == ["B2", "M0"]
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
    assert any(r["metric"] == "path_m_audit_per_completion" for r in rows)
    tex = render_tex(
        report, rows, scenario="test", source_label="unit_test", extra_notes=notes,
    )
    assert "f_audit" not in tex
    assert "不作方法优劣结论" in tex
    assert r"\begin{tabular}{lrrrrr}" in tex
    assert "M0$-$B2" in tex


def test_gate_refuses_smoke_incomplete_and_legacy_reports():
    report = _report()
    with pytest.raises(SystemExit, match="冒烟"):
        validate_report({**report, "profile": "smoke"})
    with pytest.raises(SystemExit, match="未配对 seed"):
        validate_report({**report, "unpaired_seeds": [44]})
    with pytest.raises(SystemExit, match="重算 claims_report"):
        legacy = {**report, "claims": {
            **report["claims"],
            "C4": {**report["claims"]["C4"], "table_spec": {}},
        }}
        validate_report(legacy)
    with pytest.raises(SystemExit, match="排除列"):
        bad = {**report, "claims": {
            **report["claims"],
            "C4": {
                **report["claims"]["C4"],
                "table_spec": {
                    **report["claims"]["C4"]["table_spec"],
                    "excluded_columns": [],
                },
            },
        }}
        validate_report(bad)


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
