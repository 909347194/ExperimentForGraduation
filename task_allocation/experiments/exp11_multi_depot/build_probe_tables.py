"""方法级对照：构造式贪心 vs 本文 DMDE，7 seed 汇总出三线表。

读 ``results/probes/probe_greedy_formal.json``，输出可直接进稿的
``results/probes/tables_baseline.tex``（两个 ``table`` 环境：主表 + 配对差）
与 ``.md``；``.tex`` 可被 ``tables_baseline_preview.tex`` 直接 ``\\input``。

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
    "greedy(home)": r"构造式贪心（返原巢）",
    "dmde+home": r"DMDE + 返原巢",
    "dmde+nearest": r"DMDE + 就近终点",
    "dmde+mcf": r"DMDE + MCF 终点（本文）",
}
METRICS = ("path", "ontime", "viol", "sorties")
METRIC_LABEL = {
    "path": "总航程 / km",
    "ontime": "按时率",
    "viol": "违约+超容 / 次",
    "sorties": "航次",
}
TIERS = ("T2", "T4", "T6", "T10")


def _two_sided_p(t: float, df: int) -> float | None:
    """Student t 双尾 p（不完全 beta 精确实现，与 metrics 同式，不依赖 scipy）。"""
    if df <= 0 or not math.isfinite(t):
        return None

    def _betacf(a: float, b: float, x: float) -> float:
        MAXIT, EPS, FPMIN = 200, 3e-9, 1e-300
        qab, qap, qam = a + b, a + 1.0, a - 1.0
        c, d = 1.0, 1.0 - qab * x / qap
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
    return _two_sided_p(mean / (sd / math.sqrt(n)), n - 1)


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


def _star(p: float | None) -> str:
    if p is None:
        return ""
    if p < 0.001:
        return r"$^{***}$"
    if p < 0.01:
        return r"$^{**}$"
    if p < 0.05:
        return r"$^{*}$"
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp",
                    default="results/probes/probe_greedy_formal.json")
    ap.add_argument("--out-prefix", default="results/probes/tables_baseline")
    args = ap.parse_args()

    data = json.loads((HERE / args.inp).read_text(encoding="utf-8"))
    tiers = [t for t in TIERS if any(k.startswith(t + "_") for k in data)]
    seeds = {t: sorted(int(k.split("_seed")[1]) for k in data
                       if k.startswith(t + "_")) for t in tiers}
    n_seed = min(len(v) for v in seeds.values())

    def _cell(mean: float, sd: float, metric: str) -> str:
        if metric == "ontime":
            return f"{mean:.3f} $\\pm$ {sd:.3f}"
        return f"{mean:.1f} $\\pm$ {sd:.1f}"

    # ── 主表 ────────────────────────────────────────────────────────
    tex: list[str] = [r"\begin{table}[htbp]", r"  \centering",
                      r"  \caption{构造式贪心与本文方法在四个滚动梯度上的对比"
                      rf"（{n_seed} 个随机种子均值 $\pm$ 标准差）" r"}",
                      r"  \label{tab:exp11-baseline}",
                      r"  \footnotesize",
                      r"  \setlength{\tabcolsep}{3.5pt}",
                      r"  \begin{tabular}{llrrrr}", r"    \toprule",
                      "    方法 & 指标 & " + " & ".join(f"$T={t[1:]}$" for t in tiers)
                      + r" \\", r"    \midrule"]
    md: list[str] = ["| 方法 | 指标 | " + " | ".join(f"T={t[1:]}" for t in tiers) + " |",
                     "|---|---|" + "|".join(["---"] * len(tiers)) + "|"]

    for mi, method in enumerate(METHODS):
        if mi:
            tex.append(r"    \addlinespace[2pt]")
        tex.append(r"    \multicolumn{" + str(len(tiers) + 2) + r"}{l}{\textit{"
                   + LABEL[method] + r"}} \\")
        md.append(f"| **{LABEL[method]}** | | " + " | ".join([""] * len(tiers)) + " |")
        for metric in METRICS:
            cells_tex, cells_md = [], []
            for t in tiers:
                vals = [_value(data[f"{t}_seed{s}"], method, metric) for s in seeds[t]]
                mean = statistics.fmean(vals)
                sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
                cells_tex.append(_cell(mean, sd, metric))
                cells_md.append(f"{mean:.3f} ± {sd:.3f}" if metric == "ontime"
                                else f"{mean:.1f} ± {sd:.1f}")
            tex.append("     & " + METRIC_LABEL[metric] + " & " + " & ".join(cells_tex)
                       + r" \\")
            md.append(f"| | {METRIC_LABEL[metric]} | " + " | ".join(cells_md) + " |")
    tex += [r"    \bottomrule", r"  \end{tabular}", r"  \par\vspace{2pt}",
            r"  {\footnotesize 注：贪心为单次成型的构造式基线（无全局搜索），"
            r"其终点恒为起飞巢，故只能与「DMDE + 返原巢」对齐读求解器质量；"
            r"终点自由度在 DMDE 三臂间单独读。贪心的目标值被硬违约罚项主导"
            r"（$10^{7}$ 量级），故不列 $f$，只比航程 / 按时率 / 违约 / 航次。",
            r"  每格为独立跑批（各 $n\_cycles=T$），短梯度解不等于长梯度解的前缀。}",
            r"\end{table}"]

    # ── 配对差表 ────────────────────────────────────────────────────
    texb: list[str] = [r"\begin{table}[htbp]", r"  \centering",
                       r"  \caption{配对差（本文 DMDE+MCF $-$ 构造式贪心；负值表示本文更省）"
                       rf"（{n_seed} 个随机种子）" r"}",
                       r"  \label{tab:exp11-baseline-pair}",
                       r"  \footnotesize",
                       r"  \setlength{\tabcolsep}{4pt}",
                       r"  \begin{tabular}{l" + "r" * len(tiers) + "}", r"    \toprule",
                       "    指标 & " + " & ".join(f"$T={t[1:]}$" for t in tiers) + r" \\",
                       r"    \midrule"]
    mdb: list[str] = ["| 指标 | " + " | ".join(f"T={t[1:]}" for t in tiers) + " |",
                      "|---|" + "|".join(["---"] * len(tiers)) + "|"]
    notes = []
    for metric in METRICS:
        cells, cells_md = [], []
        for t in tiers:
            d = [_value(data[f"{t}_seed{s}"], "dmde+mcf", metric)
                 - _value(data[f"{t}_seed{s}"], "greedy(home)", metric)
                 for s in seeds[t]]
            mean, p = statistics.fmean(d), _paired_p(d)
            sign = "+" if mean > 0 else ""
            fmt = "{:+.3f}" if metric == "ontime" else "{:+.1f}"
            p_txt = "--" if p is None else (f"{p:.3f}" if p >= 0.001 else r"$<0.001$")
            cells.append(fmt.format(mean) + _star(p) + f" ($p={p_txt}$)")
            cells_md.append(f"{fmt.format(mean)} (p={p_txt})")
            notes.append((t, metric, mean, p))
        texb.append("    " + METRIC_LABEL[metric] + " & " + " & ".join(cells) + r" \\")
        mdb.append(f"| {METRIC_LABEL[metric]} | " + " | ".join(cells_md) + " |")
    texb += [r"    \bottomrule", r"  \end{tabular}", r"  \par\vspace{2pt}",
             r"  {\footnotesize 注：$^{*}$ $p<0.05$，$^{**}$ $p<0.01$，$^{***}$ $p<0.001$"
             r"（配对 t 检验，$df=6$）。$p$ 不显著只表示「不显著」，不表示「无差异」。}",
             r"\end{table}"]

    out_tex = HERE / (args.out_prefix + ".tex")
    out_md = HERE / (args.out_prefix + ".md")
    out_tex.write_text("\n".join(tex + [""] + texb) + "\n", encoding="utf-8")
    out_md.write_text("\n".join(md + [""] + mdb) + "\n", encoding="utf-8")
    print(f"[tables] 写入 {out_tex}\n[tables] 写入 {out_md}")
    for t, metric, mean, p in notes:
        print(f"  {t} {METRIC_LABEL[metric]:>12}: {mean:+.3f}  p={p}")


if __name__ == "__main__":
    main()
