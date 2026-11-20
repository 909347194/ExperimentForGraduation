# -*- coding: utf-8 -*-
"""exp05 E1 整体有效性参照表生成器（单源：``claims_report.json``）。

把 E1 从「散在报告里的描述性数字」收口成一份可直接进论文的参照表：
唯一尺子 ``path_m_audit``（垂直切面 DEM 三维有向航程）＋逐 seed 归一的
每完成航程，评估次数与墙钟并列，**只给估计量与区间、不作方法优劣结论**。
E1 只证「整体有效性不是随便一个可行分配也能做到」，不排名、不宣称优越。

用法::

    python build_reference_table.py \
        --claims results_e1/claims_report.json \
        --out-dir results_e1 \
        --scenario lasa_chengguan \
        --source-label "exp05 E1 正式档"

门禁（不满足直接拒绝出表，绝不拿未闭合档/旧档冒充正式档）：

- ``design_version`` 必须等于 E1 收窄版本（**拒绝旧 7 臂 / v3 / v1v2 档**）；
- ``methods`` 必须恰为 4 个（M0/B1/B2/D0），主判据配对也须落在这 4 个方法内；
- ``profile`` 必须是 ``formal``；冒烟 / mid 档不出表；
- ``all_pairs_complete`` 为真且 ``unpaired_seeds`` 为空；
- ``E1.table_spec.role`` 为整体有效性参照、主尺为 ``path_m_audit``、
  ``f_audit`` 等混尺子列在排除清单；出表部分一律描述性、不含 verdict；
- ``primary_gates_passed`` 为假**不拒绝**，但必须在表注如实声明反例。

产出（写到 ``--out-dir``）：

- ``reference_table.csv`` 长表：绝对量 + 描述性配对差，含名义 p 留档列；
- ``reference_table.tex`` 一个浮动表：方法绝对量 + 描述性配对差 + 表注。

对应 ``task_allocation/docs/exp05_e1_design_2026-11-13.md`` §3 / §4 / §6。
本脚本不跑算法、不改写任何结果档。
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import paths  # noqa: F401  # sys.path 引导（与 exp05 其它模块一致）

__all__ = ["validate_report", "collect_rows", "render_tex", "main"]

#: 主表列：(键, 表头, CSV 单位, 小数位, 显示换算)
LEVEL_COLUMNS = (
    ("path_m_audit", r"DEM 航程 (km)", "m", 2, 1e-3),
    ("path_m_audit_per_completion", r"每完成航程 (m)", "m", 1, 1.0),
    ("on_time_rate_final", r"按时率", "—", 4, 1.0),
    ("n_evals_total", r"评估次数 / seed", "次", 0, 1.0),
    ("wall_seconds", r"墙钟 (s)", "s", 1, 1.0),
)

#: 只随 CSV 留档、不进论文表的列：完成是硬约束，不作展示列
CSV_ONLY_COLUMNS = (
    ("n_completed", r"完成数", "个", 0, 1.0),
)

#: 配对差列：(键, 表头, CSV 单位, 小数位, 显示换算)
PAIRED_COLUMNS = (
    ("path_m_audit", r"$\Delta$ DEM 航程 (km)", "m", 2, 1e-3),
    ("path_m_audit_per_completion", r"$\Delta$ 每完成航程 (m)", "m", 1, 1.0),
)

#: 表注固定段（红线口径，删改等于偷偷加强结论）
TABLE_NOTE = (
    "注：本表服务主张 E1——「整体有效性不是随便一个可行分配也能做到」，"
    "不排名、不宣称方法优越。航程列一律为交付解按垂直切面 DEM 三维有向航程"
    "独立几何重算所得（\\texttt{path\\_m\\_audit}），4 个方法共用这一把尺；"
    "每完成航程为该值除以完成数、逐 seed 归一后再跨 seed 汇总，完成数只随 CSV "
    "留档。表中航程单位由米换算为千米，只改单位、不改尺子。各方法自报 "
    "\\texttt{total\\_path\\_m}、\\texttt{path\\_per\\_completion\\_m}、"
    "逐周期重评 \\texttt{f\\_audit} 与三维直线重算 \\texttt{path\\_m\\_euclid3d} "
    "存在尺子或量纲差异，未列入本表。本表为整体有效性参照，不作方法优劣结论："
    "B1/B2 与 M0 的差异是整条求解范式（解码修复链、交付后精修、终点机巢分层），"
    "D0 是复合档（连 ETA/时间窗动力学一起换），均不是单因素效应；评估次数与"
    "墙钟同列以示求解代价。完成是可完成任务的硬约束，不作为成绩排序项，"
    "seed 反例须逐格归因。唯一推断主判据为 M0$-$B1 的每完成 DEM 航程配对差，"
    "其精确 $p$、效应量与三态结论在 \\texttt{claims\\_report.json} 单独给出；"
    "本表所有配对与绝对量只给估计量与区间、不出推断结论。单场景、单到达流、"
    "5 个 seed（df=4）、仅一条推断主判据故无多重比较，名义 $p$ 仅随 CSV 留档、"
    "不进本表；$p$ 不显著处一律表述为「本实验未检出差异」。旧 7 臂与 v1/v2 "
    "数字一律不进主文。$\\pm$ 为跨 seed 标准差，95\\% 置信区间随 CSV 一同落盘。"
)


#: E1 收窄版本标识 & 期望的 4 个方法（防止旧 7 臂 / v3 / v1v2 档混入主文）
E1_DESIGN_VERSION = "e1_single_ruler_4arm_2026-11-13"
E1_METHODS = frozenset({"M0", "B1", "B2", "D0"})


def _anchor(report: Mapping[str, Any]) -> str:
    """主判据的锚点方法（所有配对差恒为 ``anchor − other``）。"""
    pair = list(report.get("primary_pair") or [])
    if pair:
        return str(pair[0])
    e1 = (report.get("claims") or {}).get("E1") or {}
    return str(((e1.get("primary_pair") or {}).get("pair") or ["M0"])[0])


def validate_report(report: Mapping[str, Any]) -> list[str]:
    """门禁校验；返回必须写进表注的限制声明，不满足则抛 SystemExit。"""
    def _fail(msg: str) -> None:
        raise SystemExit(f"[exp05 参照表] 拒绝出表：{msg}")

    # 版本门禁：拒绝旧 7 臂 / v3 / v1v2 档（落实「旧数字一律不进主文」）
    if report.get("design_version") != E1_DESIGN_VERSION:
        _fail(f"design_version={report.get('design_version')!r} 不是 E1 收窄版 "
              f"（应为 {E1_DESIGN_VERSION!r}）；旧 7 臂 / v3 / v1v2 档不得出表")
    methods = set(report.get("methods") or [])
    if methods != set(E1_METHODS):
        _fail(f"方法集 {sorted(methods)} 不是 E1 的 4 方法 {sorted(E1_METHODS)}")
    pair = list(report.get("primary_pair") or [])
    if len(pair) != 2 or set(map(str, pair)) - set(E1_METHODS):
        _fail(f"primary_pair={pair!r} 异常，须为 E1 四方法中的两项 (A, B)")
    if report.get("primary_ruler") not in ("dem_vertical_section_3d", "path_m_audit"):
        _fail(f"唯一比较尺声明异常：{report.get('primary_ruler')!r}")
    if report.get("profile") != "formal":
        _fail(f"profile={report.get('profile')!r}，冒烟 / mid 档不可外推正式档")
    if report.get("all_pairs_complete") is not True:
        _fail("all_pairs_complete 非真，配对不完整")
    if report.get("unpaired_seeds"):
        _fail(f"存在未配对 seed：{report['unpaired_seeds']}")

    e1 = (report.get("claims") or {}).get("E1") or {}
    spec = e1.get("table_spec") or {}
    if spec.get("role") != "effectiveness_reference":
        _fail("缺少 E1.table_spec.role=effectiveness_reference；"
              "请先用当前 claims.py 重算 claims_report.json")
    if spec.get("primary_ruler") != "path_m_audit":
        _fail(f"主尺子被改为 {spec.get('primary_ruler')!r}，与单尺 DEM 口径冲突")
    if "f_audit" not in set(spec.get("excluded_columns") or ()):
        _fail("f_audit 未列入排除列，混尺子列可能混入参照表")
    if spec.get("inference") != "single_primary_criterion":
        _fail("E1 必须是单一推断主判据，描述性档不得产出结论")
    if "verdict" not in (e1.get("primary_pair") or {}):
        _fail("E1.primary_pair 缺少 verdict，主判据未落盘")
    # 描述性配对与绝对量一律不得含 verdict（防下游误读成方法优劣结论）
    for other, entry in (e1.get("pairs") or {}).items():
        for key, item in entry.items():
            if isinstance(item, dict) and "verdict" in item:
                _fail(f"描述性配对 {other}.{key} 含 verdict，越权产出结论")
    if not e1.get("reference"):
        _fail("E1.reference 为空，无法出绝对量表")

    notes: list[str] = []
    if report.get("primary_gates_passed") is not True:
        notes.append(
            "本档存在主表方法门槛未通过项（可完成任务遗留的反例），须逐格归因；"
            "本表不构成「完成率恒 100\\%」的证据。"
        )
    return notes


def _stats(entry: Mapping[str, Any]) -> dict[str, Any]:
    st = entry.get("stats") or {}
    ci = st.get("ci95") or [None, None]
    return {
        "mean": st.get("mean"),
        "std": st.get("std"),
        "ci_low": ci[0] if len(ci) > 0 else None,
        "ci_high": ci[1] if len(ci) > 1 else None,
        "n": st.get("n_pairs", (entry or {}).get("n")),
        "p": st.get("p_two_sided"),
    }


def collect_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """把 E1 的绝对量与描述性配对差整理成长表行（CSV 单源）。"""
    e1 = report["claims"]["E1"]
    anchor = _anchor(report)
    rows: list[dict[str, Any]] = []

    for method, block in sorted((e1.get("reference") or {}).items()):
        metrics = block.get("metrics") or {}
        for key, header, unit, digits, scale in LEVEL_COLUMNS + CSV_ONLY_COLUMNS:
            entry = metrics.get(key) or {}
            st = _stats({"stats": {**entry, "n_pairs": entry.get("n")}})
            only_csv = key in {c[0] for c in CSV_ONLY_COLUMNS}
            rows.append({
                "section": "levels",
                "method": method,
                "metric": key,
                "label": header,
                "unit": unit,
                "mean": st["mean"],
                "std": st["std"],
                "ci95_low": st["ci_low"],
                "ci95_high": st["ci_high"],
                "n": st["n"],
                "nominal_p_descriptive_only": "",
                "note": (
                    "完成是硬约束；仅作每完成航程的分母留档，不进论文表"
                    if only_csv else
                    ("统一 DEM 有向三维航程" if key.startswith("path_m_audit") else "")
                ),
            })

    for other, block in sorted((e1.get("pairs") or {}).items()):
        for key, header, unit, digits, scale in PAIRED_COLUMNS:
            entry = block.get(key)
            if not entry or entry.get("not_for_table"):
                continue
            st = _stats(entry)
            rows.append({
                "section": "paired",
                "method": f"{anchor}−{other}",
                "metric": key,
                "label": header,
                "unit": unit,
                "mean": st["mean"],
                "std": st["std"],
                "ci95_low": st["ci_low"],
                "ci95_high": st["ci_high"],
                "n": st["n"],
                "nominal_p_descriptive_only": (
                    "" if st["p"] is None else f"{st['p']:.6f}"
                ),
                "note": "描述性配对差：只给估计量与区间，不出推断结论",
            })
    return rows


def _fmt(value: Any, digits: int, scale: float = 1.0) -> str:
    if value is None:
        return "—"
    try:
        # 负号走数学符号，避免正文连字符被当成断字
        return f"{float(value) * scale:.{digits}f}".replace("-", "$-$")
    except (TypeError, ValueError):
        return "—"


def _ci(entry: dict[str, Any], digits: int, scale: float) -> str:
    lo, hi = entry.get("ci95_low"), entry.get("ci95_high")
    if lo is None or hi is None:
        return "—"
    return f"[{_fmt(lo, digits, scale)}, {_fmt(hi, digits, scale)}]"


def _tex_escape(text: str) -> str:
    for a, b in (("\\", r"\textbackslash{}"), ("_", r"\_"),
                 ("%", r"\%"), ("&", r"\&"), ("#", r"\#")):
        text = text.replace(a, b)
    return text


def _by_metric(rows: Sequence[Mapping[str, Any]], section: str) -> dict[str, dict[str, Any]]:
    return {
        (str(r["method"]), str(r["metric"])): dict(r)
        for r in rows if r["section"] == section
    }


def render_tex(
    report: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    scenario: str,
    source_label: str,
    extra_notes: Sequence[str],
) -> str:
    """渲染一个浮动表：方法绝对量 + 描述性配对差 + 表注。"""
    e1 = report["claims"]["E1"]
    ref = e1.get("reference") or {}
    methods = sorted(ref)
    pairs = sorted(e1.get("pairs") or {})
    anchor = _anchor(report)
    levels = _by_metric(rows, "levels")
    paired = _by_metric(rows, "paired")

    lines: list[str] = [
        "% 由 exp05_baselines/build_reference_table.py 生成；请勿手改数字。",
        r"\begin{table}[!htb]",
        r"    \centering",
        r"    \footnotesize",
        r"    \caption{完成约束下的整体有效性参照（描述性）}"
        r"\label{tab:exp05_reference}",
        r"    \setlength{\tabcolsep}{4pt}",
        r"    \begin{tabular}{l" + "r" * len(LEVEL_COLUMNS) + "}",
        r"        \toprule",
        "        方法 & " + " & ".join(c[1] for c in LEVEL_COLUMNS) + r" \\",
        r"        \midrule",
    ]
    for m in methods:
        cells = []
        for key, header, unit, digits, scale in LEVEL_COLUMNS:
            r = levels.get((m, key), {})
            mean = _fmt(r.get("mean"), digits, scale)
            std = _fmt(r.get("std"), digits, scale)
            cells.append(f"{mean} $\\pm$ {std}" if r.get("std") is not None else mean)
        lines.append("        " + _tex_escape(m) + " & " + " & ".join(cells) + r" \\")
    lines += [
        r"        \bottomrule",
        r"    \end{tabular}",
        r"",
        r"    \vspace{6pt}",
        r"    \begin{tabular}{l" + "c" * (2 * len(PAIRED_COLUMNS)) + "}",
        r"        \toprule",
        "        配对 & "
        + " & ".join(f"{c[1]} & 95\\% CI" for c in PAIRED_COLUMNS)
        + r" \\",
        r"        \midrule",
    ]
    for other in pairs:
        cells: list[str] = []
        for key, header, unit, digits, scale in PAIRED_COLUMNS:
            r = paired.get((f"{anchor}−{other}", key), {})
            cells.append(_fmt(r.get("mean"), digits, scale))
            cells.append(_ci(r, digits, scale))
        lines.append(
            "        " + _tex_escape(anchor) + "$-$" + _tex_escape(other) + " & "
            + " & ".join(cells) + r" \\"
        )
    lines += [
        r"        \bottomrule",
        r"    \end{tabular}",
        r"",
        r"    \begin{minipage}{\textwidth}",
        r"        \vspace{2pt}",
        r"        \footnotesize",
        "        " + TABLE_NOTE,
        "        " + "\n        ".join(extra_notes) if extra_notes else "",
        f"        数据来源：{_tex_escape(source_label)}；"
        f"场景：{_tex_escape(scenario)}；"
        f"seed：{', '.join(str(s) for s in report.get('seeds', []))}。",
        r"    \end{minipage}",
        r"\end{table}",
        "",
    ]
    return "\n".join(x for x in lines if x != "")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="exp05 E1 整体有效性参照表生成器")
    ap.add_argument(
        "--claims", type=Path,
        default=paths.EXP_DIR / "results_e1" / "claims_report.json",
        help="claims_report.json 路径（须由当前 claims.py 重算）",
    )
    ap.add_argument("--out-dir", type=Path, default=None, help="产出目录，缺省与 --claims 同目录")
    ap.add_argument("--scenario", default="lasa_chengguan", help="场景名（仅作表注来源标签）")
    ap.add_argument("--source-label", default="exp05 E1 正式档", help="来源档标签")
    args = ap.parse_args(argv)

    claims_path = Path(args.claims).resolve()
    if not claims_path.is_file():
        raise SystemExit(
            f"[exp05 参照表] 找不到 {claims_path}；"
            "正式档闭合后由 run.py 生成 claims_report.json，未就绪不出表"
        )
    report = json.loads(claims_path.read_text(encoding="utf-8"))
    notes = validate_report(report)
    rows = collect_rows(report)

    out_dir = Path(args.out_dir).resolve() if args.out_dir else claims_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "reference_table.csv"
    tex_path = out_dir / "reference_table.tex"

    fields = (
        "section", "method", "metric", "label", "unit", "mean", "std",
        "ci95_low", "ci95_high", "n", "nominal_p_descriptive_only", "note",
    )
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
    tex_path.write_text(
        render_tex(
            report, rows,
            scenario=str(args.scenario),
            source_label=str(args.source_label),
            extra_notes=notes,
        ),
        encoding="utf-8",
    )
    print(
        f"[exp05 参照表] {csv_path} / {tex_path}\n"
        f"  design_version={report['design_version']} "
        f"profile={report['profile']} seeds={len(report.get('seeds', []))} "
        f"方法={len(report['claims']['E1']['reference'])} "
        f"门槛通过={report.get('primary_gates_passed')} "
        f"表注附加声明={len(notes)} 条",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
