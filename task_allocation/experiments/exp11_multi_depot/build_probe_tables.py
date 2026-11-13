"""方法级对照：构造式贪心 vs 本文 DMDE，7 seed 汇总出三线表。

读 ``results/probes/probe_greedy_formal.json``，输出
``results/probes/tables_baseline.tex`` / ``.md``：

- 表 A：四方法 × 四档 × （总航程 / 按时率 / 违约 / 航次），均值 ± 标准差
- 表 B：配对差（本文 DMDE+MCF − 构造式贪心），带精确 t 双尾 p

用法（cwd = exp11 目录）：
    python build_probe_tables.py --in results/probes/probe_greedy_formal.json
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics

HERE = pathlib.Path(__file__).resolve().parent

METHODS = ("greedy(home)", "dmde+home", "dmde+nearest", "dmde+mcf")
LABEL = {
    "greedy(home)": "构造式贪心（返原巢）",
    "dmde+home": "DMDE + 返原巢",
    "dmde+nearest": "DMDE + 就近终点",
    "dmde+mcf": "DMDE + MCF 终点（本文）",
}
METRICS = ("path", "ontime", "viol", "sorties")
METRIC_LABEL = {
    "path": "总航程/km",
    "ontime": "按时率",
    "viol": "违约/超容",
    "sorties": "航次",
}
TIERS = ("T2", "T4", "T6", "T10")


def _two_sided_p(t: float, df: int) -> float | None:
    """Student t 双尾 p（不完全 beta 精确实现，与 metrics 同式）。"""
    if df <= 0 or not math.isfinite(t):
        return None

    def _betacf(a: float, b: float, x: float) -> float:
        MAXIT, EPS, FPMIN = 200, 3e-9, 1e-300
        qab, qap, qam = a + b, a + 1.0, a - 1.0
        c = 1.0
        d = 1.0 - qab * x / qap
        if abs(d) < FPMIN:
            d = FPMIN
        d = 1.0 / d
        h = d
        for m in range(1, MAXIT + 1):
            m2 = 2 * m
            aa = m * (b - m) * x / ((qam + m2) * (a + m2))
            d = 1.0 + aa * d
            if abs(d) < FPMIN:
                d = FPMIN
            c = 1.0 + aa / c
            if abs(c) < FPMIN:
                c = FPMIN
            d = 1.0 / d
            h *= d * c
            aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
            d = 1.0 + aa * d
            if abs(d) < FPMIN:
                d = FPMIN
            c = 1.0 + aa / c
            if abs(c) < FPMIN:
                c = FPMIN
            d = 1.0 / d
            de = d * c
            h *= de
            if abs(de - 1.0) < EPS:
                break
        return h

    def _betai(a: float, b: float, x: float) -> float:
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0
        bt = math.exp(
            math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
            + a * math.log(x) + b * math.log(1.0 - x)
        )
        if x < (a + 1.0) / (a + b + 2.0):
            return bt * _betacf(a, b, x) / a
        return 1.0 - bt * _betacf(b, a, 1.0 - x) / b

    return float(_betai(df / 2.0, 0.5, df / (df + t * t)))


def _paired_p(diffs: list[float]) -> float | None:
    n = len(diffs)
    if n < 2:
        return None
    mean = statistics.fmean(diffs)
    sd = statistics.stdev(diffs)
    if sd == 0.0:
        return 1.0 if mean == 0.0 else 0.0
    t = mean / (sd / math.sqrt(n))
    return _two_sided_p(t, n - 1)


def _value(group: dict, method: str, metric: str) -> float:
    m = group[method]
    if metric == "path":
        return m["total_path_m"] / 1000.0
    if metric == "ontime":
        return m["on_time_rate_final"]
    if metric == "viol":
        return float(m.get("n_hard_violations_total", 0)
                     + m.get("nest_overflow_total", 0))
    if metric == "sorties":
        return float(m["n_sorties"])
    raise KeyError(metric)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp",
                    default="results/probes/probe_greedy_formal.json")
    ap.add_argument("--out-prefix", default="results/probes/tables_baseline")
    args = ap.parse_args()

    data = json.loads((HERE / args.inp).read_text(encoding="utf-8"))
    tiers = [t for t in TIERS if any(k.startswith(t + "_") for k in data)]
    seeds_per_tier = {t: sorted(int(k.split("_seed")[1]) for k in data
                                if k.startswith(t + "_")) for t in tiers}

    # ── 表 A：均值 ± 标准差 ─────────────────────────────────────────
    lines_tex: list[str] = []
    lines_md: list[str] = []
    head_tex = " & ".join(tiers)
    lines_tex.append(r"\begin{tabular}{l" + "c" * len(tiers) + "}")
    lines_tex.append(r"\toprule")
    lines_tex.append("方法 / 指标 & " + head_tex + r" \\")
    lines_tex.append(r"\midrule")
    lines_md.append("| 方法 | 指标 | " + " | ".join(tiers) + " |")
    lines_md.append("|---|---|" + "|".join(["---"] * len(tiers)) + "|")

    fmt = {"path": lambda v: f"{v:.1f}", "ontime": lambda v: f"{v:.3f}",
           "viol": lambda v: f"{v:.1f}", "sorties": lambda v: f"{v:.1f}"}

    for mi, method in enumerate(METHODS):
        lines_tex.append(r"\midrule" if mi else "")
        lines_md.append(f"| **{LABEL[method]}** | | " + " | ".join("" for _ in tiers) + " |")
        for metric in METRICS:
            cells_tex, cells_md = [], []
            for t in tiers:
                vals = [_value(data[f"{t}_seed{s}"], method, metric)
                        for s in seeds_per_tier[t]]
                mean = statistics.fmean(vals)
                sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
                cells_tex.append(f"{fmt[metric](mean)}±{sd:.2f}" if metric != "ontime"
                                 else f"{mean:.3f}±{sd:.3f}")
                cells_md.append(f"{fmt[metric](mean)} ± {sd:.2f}")
            lines_tex.append(f"{METRIC_LABEL[metric]} & " + " & ".join(cells_tex) + r" \\")
            lines_md.append(f"| | {METRIC_LABEL[metric]} | " + " | ".join(cells_md) + " |")
    lines_tex.append(r"\bottomrule")
    lines_tex.append(r"\end{tabular}")

    clean_tex = [l for l in lines_tex if l]

    # ── 表 B：配对差（本文 − 贪心）──────────────────────────────────
    diffs_all: dict[str, dict[str, list[float]]] = {}
    texb = [r"\begin{tabular}{l" + "c" * len(tiers) + "}", r"\toprule",
            "指标（本文 − 贪心） & " + head_tex + r" \\", r"\midrule"]
    mdb = ["| 指标（本文 − 贪心） | " + " | ".join(tiers) + " |",
           "|---|" + "|".join(["---"] * len(tiers)) + "|"]
    for metric in METRICS:
        cells_tex, cells_md = [], []
        for t in tiers:
            d = [_value(data[f"{t}_seed{s}"], "dmde+mcf", metric)
                 - _value(data[f"{t}_seed{s}"], "greedy(home)", metric)
                 for s in seeds_per_tier[t]]
            diffs_all.setdefault(metric, {})[t] = d
            mean = statistics.fmean(d)
            p = _paired_p(d)
            star = "***" if p is not None and p < 0.001 else (
                "**" if p is not None and p < 0.01 else (
                    "*" if p is not None and p < 0.05 else ""))
            p_txt = "—" if p is None else f"{p:.3f}"
            sign = "+" if mean > 0 else ""
            cells_tex.append(f"{sign}{fmt[metric](mean)}{star} (p={p_txt})")
            cells_md.append(f"{sign}{fmt[metric](mean)} (p={p_txt})")
        texb.append(f"{METRIC_LABEL[metric]} & " + " & ".join(cells_tex) + r" \\")
        mdb.append(f"| {METRIC_LABEL[metric]} | " + " | ".join(cells_md) + " |")
    texb += [r"\bottomrule", r"\end{tabular}"]

    out_tex = HERE / (args.out_prefix + ".tex")
    out_md = HERE / (args.out_prefix + ".md")
    out_tex.write_text("\n".join(clean_tex + [""] + texb) + "\n", encoding="utf-8")
    out_md.write_text("\n".join(lines_md + [""] + mdb) + "\n", encoding="utf-8")
    print(f"[tables] 写入 {out_tex}\n[tables] 写入 {out_md}")
    for tier in tiers:
        for metric in METRICS:
            d = diffs_all[metric][tier]
            print(f"  {tier} {METRIC_LABEL[metric]:>10}: mean {statistics.fmean(d):+.3f} "
                  f"p={_paired_p(d)} n={len(d)}")


if __name__ == "__main__":
    main()
