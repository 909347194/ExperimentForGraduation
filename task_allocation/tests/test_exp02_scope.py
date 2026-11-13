# -*- coding: utf-8 -*-
"""exp02 职责收窄回归：分账恒等式、结论禁词、单因素对照、复现链。

守护「单周期任务规模可扩展性台」的边界：

* 任务去向分账必须闭合，``unreachable`` 必须是 ``None``（不可观测）而非 0；
* 收窄前旧档（无 scope / 无 accounting）一律拒绝出表；
* 全目录不得出现优劣类结论词（门禁定义文件本身除外）；
* 主表不出现 F1 列、F1 只作描述性字段；
* 参照臂与选择臂共用同一惰性建表口径（单因素）；
* ``--dry-run`` 能验 ``--set`` 生效且不落盘。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXP02 = REPO / "task_allocation" / "experiments" / "exp02_selection_scale"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(EXP02) not in sys.path:
    sys.path.insert(0, str(EXP02))

from accounting import (  # noqa: E402
    BANNED_CLAIMS,
    accounting_from,
    assert_no_quality_claims,
    summarize,
)
from build_tables import build, validate_metrics  # noqa: E402


# ── 1. 分账恒等式 ────────────────────────────────────────────────

def test_accounting_closes_and_keeps_unreachable_unobserved():
    a = accounting_from(100, 48, 44, selection_active=True)
    assert a["n_arrived"] == a["n_done"] + a["n_remain"] == 100
    assert a["n_remain"] == 56
    assert a["n_not_selected"] == 52
    assert a["n_deferred_in_selected"] == 4
    assert a["n_unreachable"] is None
    assert a["unreachable_not_observed"] is True
    assert a["identity_ok"] is True
    assert a["scope"] == "static_single_cycle_snapshot"


def test_accounting_reference_arm_has_no_not_selected():
    """参照臂：N=100 只交付 64 个，剩下 36 个必须记在 remain，不能凭空消失。"""
    a = accounting_from(100, 100, 64, selection_active=False)
    assert a["n_not_selected"] == 0
    assert a["n_deferred_in_selected"] == 36
    assert a["n_remain"] == 36
    assert a["n_done"] + a["n_remain"] == 100
    assert a["selection_active"] is False


def test_accounting_rejects_impossible_order():
    for args in [(10, 12, 8), (10, 8, 12), (-1, 0, 0), (10, 8, -1)]:
        with pytest.raises(ValueError):
            accounting_from(*args, selection_active=True)


def test_summarize_flags_inactive_cells():
    rows = [
        {"accounting": accounting_from(10, 10, 10, selection_active=False)},
        {"accounting": accounting_from(50, 12, 12, selection_active=True)},
    ]
    s = summarize(rows)
    assert s["n_cells"] == 2 and s["n_accounted"] == 2
    assert s["identity_ok_all"] is True
    assert s["n_cells_selection_active"] == 1
    assert s["n_cells_selection_inactive"] == 1


def test_summarize_empty_is_not_all_ok():
    assert summarize([])["identity_ok_all"] is False


# ── 2. 结论禁词 ──────────────────────────────────────────────────

@pytest.mark.parametrize("word", BANNED_CLAIMS)
def test_banned_claims_are_rejected(word):
    with pytest.raises(ValueError, match="职责越界"):
        assert_no_quality_claims(f"本图显示{word}。", where="unit")


def test_descriptive_text_passes():
    assert_no_quality_claims(
        "单周期规模—耗时曲线；只描述量级，不作机制归因，不报告耗时倍数。",
        where="unit",
    )


def test_no_banned_claims_anywhere_in_exp02_sources():
    """门禁定义文件之外，全目录不允许出现优劣类结论词（防口径回潮）。"""
    offenders: list[str] = []
    for path in sorted(EXP02.rglob("*")):
        if path.suffix not in {".py", ".md", ".yaml", ".tex", ".csv"}:
            continue
        if "results" in path.parts or path.name == "accounting.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for word in BANNED_CLAIMS:
            if word in text:
                offenders.append(f"{path.relative_to(EXP02)}: {word}")
    assert not offenders, f"出现越界结论词：{offenders}"


# ── 3. 出表门禁 ──────────────────────────────────────────────────

def _metrics(n: int = 50, *, done: int | None = None) -> dict:
    d = n if done is None else done
    row = {
        "n_tasks": n,
        "variant": "quota_a2.5",
        "n_selected": n,
        "compression_ratio": 1.0,
        "t_hint_ms": 1.0,
        "t_selection_ms": 2.0,
        "t_build_ms": 3.0,
        "t_muas_ms": 4.0,
        "t_total_ms": 10.0,
        "lazy_edges": 100,
        "selection": {"capability_n_cand": n},
        "muas": {"f1_reward_descriptive": 123.0, "n_executed": d},
        "accounting": accounting_from(n, n, d, selection_active=False),
    }
    return {
        "status": "ok",
        "experiment": "exp02_selection_scale",
        "scope": {"kind": "single_cycle_scalability_observation"},
        "K": 6,
        "uav_max_range_m": 10000.0,
        "scales": [row],
    }


def test_validate_rejects_prescoped_archive():
    """收窄前旧档：无 scope、无 accounting，必须拒绝出表。"""
    old = {"status": "ok", "K": 6, "scales": [{"n_tasks": 50, "variant": "x"}]}
    with pytest.raises(ValueError, match="归档"):
        validate_metrics(old)


def test_validate_rejects_missing_accounting():
    m = _metrics()
    m["scales"][0].pop("accounting")
    with pytest.raises(ValueError, match="分账"):
        validate_metrics(m)


def test_validate_rejects_unclosed_identity():
    m = _metrics()
    m["scales"][0]["accounting"]["identity_ok"] = False
    with pytest.raises(ValueError, match="恒等式"):
        validate_metrics(m)


def test_build_tables_main_table_has_no_f1_and_has_accounting(tmp_path):
    out = tmp_path / "tables"
    paths = build(_metrics(), out)
    assert len(paths) == 4 and all(p.exists() for p in paths)
    tex = (out / "exp02_main_results.tex").read_text(encoding="utf-8")
    assert "未完成" in tex
    assert "arrived = done + remain" in tex
    header = next(ln for ln in tex.splitlines() if "$N$ &" in ln)
    assert "F1" not in header        # F1 不作为主表列
    assert "收益比值" not in header
    assert r"\begin{tabular}{llrrrrrr}" in tex
    csv_text = (out / "exp02_main_results.csv").read_text(encoding="utf-8-sig")
    assert "F1_descriptive_only" in csv_text   # 只作描述性字段保留
    assert "n_remain" in csv_text
    assert "identity_ok" in csv_text


# ── 4. 单因素对照（结构守卫） ────────────────────────────────────

def test_both_arms_use_the_same_lazy_build_path():
    """参照臂不得回退到 eager 全量建表，否则两臂差了两个自变量。"""
    src = (EXP02 / "run.py").read_text(encoding="utf-8")
    calls = re.findall(r"cb_\w+\s*=\s*build_pairwise_costs\((.*?)\)\n",
                       src, flags=re.S)
    assert len(calls) == 2, f"应恰有两次建表调用，实际 {len(calls)}"
    assert all("pairwise_source=" in c for c in calls), (
        "两臂都必须通过 pairwise_source 走惰性活视图"
    )
    assert "pairwise_source=lazy_full" in src
    assert "lazy_full.n_computed" in src


def test_reference_arm_deferred_is_not_hardcoded_zero():
    src = (EXP02 / "run.py").read_text(encoding="utf-8")
    assert '"n_deferred": n_tasks - n_done_full' in src


# ── 5. 复现链：--dry-run ─────────────────────────────────────────

def test_dry_run_shows_effective_config_without_writing(tmp_path):
    out_name = f"_exp02_dryrun_{tmp_path.name}"
    proc = subprocess.run(
        [sys.executable, str(EXP02 / "run.py"), "--dry-run",
         "--set", "solver.max_generations=77",
         "--set", f"experiment.output_dir={out_name}"],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    eff = json.loads(proc.stdout)
    assert eff["resolved"]["solver"]["max_generations"] == 77
    assert eff["resolved"]["selection_marginal_max_ratio"] == 2.0
    assert eff["resolved"]["scenario"] == "guangzhou"
    assert eff["resolved"]["K"] == 6
    assert eff["resolved"]["scales"] == [10, 50, 100]
    assert eff["cli_set"][0] == "solver.max_generations=77"
    # dry-run 不得创建输出目录、不得落盘
    assert not (EXP02 / out_name).exists()
    assert not (EXP02 / out_name / "effective_config.json").exists()


def test_config_exposes_selection_cap_as_live_key():
    """max_ratio 不再是「写了也静默不生效」的死键。"""
    import yaml

    cfg = yaml.safe_load((EXP02 / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["selection"]["marginal"]["max_ratio"] == 2.0
    src = (EXP02 / "run.py").read_text(encoding="utf-8")
    assert 'marg_d.get("max_ratio", 2.0)' in src


# ── 6. 结果保存与补图链路（2026-11-13 审查加固） ─────────────────

def test_three_artifacts_are_written_atomically_after_solving():
    """三份 json 同一时点原子落盘，带配置指纹 / 行数 / 运行时长。"""
    src = (EXP02 / "run.py").read_text(encoding="utf-8")
    assert "def _write_json_atomic" in src
    assert src.count("_write_json_atomic(out_dir /") == 3
    for name in ("effective_config.json", "metrics.json", "run_meta.json"):
        assert f'_write_json_atomic(out_dir / "{name}"' in src
    assert "effective_config_sha256" in src
    assert '"n_rows": len(results)' in src
    assert '"wall_clock_s":' in src
    # 配置快照不得在求解前落盘（旧实现会留「有配置、无数值」的孤儿档）
    assert src.index("t_start = time.perf_counter()") < src.index(
        '_write_json_atomic(out_dir / "effective_config.json"'
    )


def test_setup_params_renders_from_any_results_dir(tmp_path):
    """S01 不得因 --results 换目录而静默丢图（预演实测 5/6）。

    用干净子进程而非包导入：多个实验都有同名 ``plot`` 包，整套 pytest 里
    ``sys.modules['plot']`` 会被别的实验先占，包导入方式不可靠。
    """
    d = tmp_path / "alt_results"
    d.mkdir()
    (d / "metrics.json").write_text(
        json.dumps(_metrics(), ensure_ascii=False), encoding="utf-8"
    )
    proc = subprocess.run(
        [sys.executable, str(EXP02 / "render_from_json.py"), "png",
         "--results", str(d), "--figure", "setup_params"],
        cwd=str(REPO), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (d / "figures" / "S01_setup_params.png").exists()


def _run_render(results_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(EXP02 / "render_from_json.py"), "png",
         "--results", str(results_dir)],
        cwd=str(REPO), capture_output=True, text=True, timeout=120,
    )


def test_render_reports_missing_metrics_cleanly(tmp_path):
    proc = _run_render(tmp_path / "empty_results")
    assert proc.returncode == 1
    assert "缺少" in proc.stdout


def test_render_rejects_prescoped_archive(tmp_path):
    """补图与出表共用门禁，旧档不得绕过收窄口径直接出图。"""
    d = tmp_path / "old_results"
    d.mkdir()
    (d / "metrics.json").write_text(
        json.dumps({"status": "ok", "K": 6,
                    "scales": [{"n_tasks": 50, "variant": "x"}]}),
        encoding="utf-8",
    )
    proc = _run_render(d)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "门禁拒绝" in proc.stdout


def test_render_gate_shares_validator_with_build_tables():
    src = (EXP02 / "render_from_json.py").read_text(encoding="utf-8")
    assert "from build_tables import validate_metrics" in src
    assert "validate_metrics(metrics)" in src


def test_table_note_explains_selection_active(tmp_path):
    """N 小于选择封顶时该列为 F，但 t_sel_ms 仍非零；表注须说明避免误读。"""
    tex = (build(_metrics(), tmp_path / "tables")[1]).read_text(encoding="utf-8")
    assert "selection_active" in tex
    assert "截断" in tex
