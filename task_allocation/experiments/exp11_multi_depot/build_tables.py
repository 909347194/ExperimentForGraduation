# -*- coding: utf-8 -*-
"""exp11 结果 → 论文可直接粘贴的三线表（LaTeX booktabs）+ Markdown 预览。

用法::

    python build_tables.py --preset formal
    python build_tables.py --preset formal --tasks "2,4,6,10"   # 只取某些梯度

读 ``results/<preset>/summary.json``，产出同目录下：

    tables_main.tex     表 1  三臂 × 四梯度主结果（均值±标准差）
    tables_effects.tex  表 2  同 seed 配对差 + 精确 t 检验 p（六列）
    tables_accum.tex    表 3  跨周期累积分解（前缀差 + 边际增量）
    tables.md           上述三表的 Markdown 预览（对话/notes 直接用）

口径纪律（写死在表注里，禁删）：
  · 三臂同预算（population 24 / max_iter 60），绝对值不可与 exp01 正式档横比；
  · n=3 seed，df=2 ⇒ p 只作描述，不显著写「不显著」，禁写「无差异」；
  · 返航段两套口径：合计（受航次数影响）与每航次均值（主口径）；
  · home 臂不制造容量溢出，overflow 恒 0 ⇒ 差异只能记在航程/目标上。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import EXP_DIR  # noqa: E402

ARMS = ("home", "nearest", "mcf")
ARM_LABEL = {"home": "强制返原巢", "nearest": "就近贪心终点", "mcf": "本文 MCF"}
ARM_TEX = {"home": "强制返原巢", "nearest": "就近贪心", "mcf": "本文 MCF"}


def _fmt(v: Any, nd: int = 2) -> str:
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return "--"


def _pm(mean: Any, std: Any, nd: int = 2) -> str:
    return f"{_fmt(mean, nd)} $\\pm$ {_fmt(std, nd)}"


def _p_cell(eff: Mapping[str, Any]) -> str:
    p = eff.get("p_two_sided")
    if p is None:
        reason = eff.get("p_reason")
        if reason == "zero_variance_no_test":
            return "定值（零方差）"
        return f"不可算({reason})"
    return f"p={_fmt(p, 3)}"


def _signed(v: Any, nd: int = 2) -> str:
    try:
        return f"{float(v):+.{nd}f}"
    except (TypeError, ValueError):
        return "--"


def _ms(a: Mapping[str, Any], key: str) -> dict[str, float]:
    """安全取 mean/std（旧 summary 可能缺新增指标，不因此报错）。"""
    d = a.get(key) or {}
    return {"mean": float(d.get("mean", 0.0) or 0.0),
            "std": float(d.get("std", 0.0) or 0.0)}


def _eff(eff: Mapping[str, Any], key: str, pair: str) -> dict[str, Any]:
    """安全取配对差（缺字段时返回全 0 / p=None）。"""
    d = ((eff or {}).get(key) or {}).get(pair) or {}
    return {
        "mean": float(d.get("mean", 0.0) or 0.0),
        "p_two_sided": d.get("p_two_sided"),
        "p_reason": d.get("p_reason"),
    }


def _prefix_stats(summary: Mapping[str, Any]) -> tuple[int, int] | None:
    """前缀一致性审计的（不等格数，总格数）；读不到审计就返回 None。"""
    prof = str(summary.get("profile") or "formal")
    p = EXP_DIR / "results" / prof / "audit.json"
    if not p.exists():
        return None
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("prefix_check") or []
    except (OSError, ValueError):
        return None
    if not rows:
        return None
    return sum(1 for r in rows if not r.get("exact")), len(rows)


def _cf_nearest_rows(summary: Mapping[str, Any], tiers: Sequence[int]) -> list[tuple[int, float, float]]:
    """就近臂：逐梯度（同解Δ, 实得Δ=就近−MCF），供表 4 脚注引用。"""
    acc = summary.get("accumulation") or {}
    rows = []
    for tier in tiers:
        a = acc.get(str(tier), {})
        gap = float(a.get("same_routes_gap_per_sortie_nearest", 0.0) or 0.0)
        diff = float(_eff(summary["tiers_report"][str(tier)]["effects"],
                          "return_leg_per_sortie_m", "nearest_minus_mcf")["mean"])
        rows.append((tier, gap, diff))
    return rows


def _cf_nearest_tex(summary: Mapping[str, Any], tiers: Sequence[int]) -> str:
    rows = _cf_nearest_rows(summary, tiers)
    gaps = "/".join(f"{g:.0f}" for _, g, _ in rows)
    diffs = "/".join(_signed(d, 0) for _, _, d in rows)
    return f"{gaps}、{diffs}（按 $T$ 递增，m/航次）"


def _cf_nearest_md(summary: Mapping[str, Any], tiers: Sequence[int]) -> str:
    rows = _cf_nearest_rows(summary, tiers)
    gaps = "/".join(f"{g:.0f}" for _, g, _ in rows)
    diffs = "/".join(_signed(d, 0) for _, _, d in rows)
    return f"就近臂：同解Δ {gaps}、实得Δ {diffs}（m/航次，按 T 递增），实得近于 0 而直接价值不小，说明其规则几乎没吃到自由度收益。"


def _prefix_tex(summary: Mapping[str, Any]) -> str:
    st = _prefix_stats(summary)
    if st is None:
        return r"未生成前缀审计"
    ne, n = st
    return (rf"{n} 格中 {ne} 格不等（短梯度解$\neq$长梯度前缀，"
            rf"解码存在跨周期耦合）")


def _prefix_md(summary: Mapping[str, Any]) -> str:
    st = _prefix_stats(summary)
    if st is None:
        return "前缀一致性未审计"
    ne, n = st
    return f"前缀一致性审计 {n} 格中 {ne} 格不等（短梯度解≠长梯度前缀）"


def table_main(summary: Mapping[str, Any], tiers: Sequence[int]) -> str:
    lines = [
        r"\begin{table}[htbp]",
        r"  \centering",
        "  \\caption{三种终点机巢策略在四个滚动梯度上的主结果（"
        + str(len(summary.get("seeds") or [])) + " 个随机种子均值 $\\pm$ 标准差）}",
        r"  \label{tab:exp11-main}",
        r"  \footnotesize",
        r"  \setlength{\tabcolsep}{4pt}",
        r"  \begin{tabular}{llrrrrrrr}",
        r"    \toprule",
        r"    梯度 & 策略 & 任务数 & 总航程/km & 返航段/km & 返航段/航次/m"
        r" & 异巢占比 & 目标值$f$ & 按时率 \\",
        r"    \midrule",
    ]
    for tier in tiers:
        blk = summary["tiers_report"][str(tier)]
        for i, arm in enumerate(ARMS):
            a = blk["arms"][arm]
            g = f"$T={tier}$" if i == 0 else ""
            n = f"{blk['n_tasks_total']}" if i == 0 else ""
            row = (
                f"    {g} & {ARM_TEX[arm]} & {n}"
                f" & {_pm(_ms(a, 'total_path_m')['mean'] / 1000, _ms(a, 'total_path_m')['std'] / 1000)}"
                f" & {_pm(_ms(a, 'total_return_leg_m')['mean'] / 1000, _ms(a, 'total_return_leg_m')['std'] / 1000)}"
                f" & {_pm(_ms(a, 'return_leg_per_sortie_m')['mean'], _ms(a, 'return_leg_per_sortie_m')['std'], 0)}"
                f" & {_fmt(_ms(a, 'cross_nest_share')['mean'], 2)}"
                f" & {_pm(_ms(a, 'final_fitness_sum')['mean'], _ms(a, 'final_fitness_sum')['std'], 1)}"
                f" & {_fmt(_ms(a, 'on_time_rate_final')['mean'], 3)} \\\\"
            )
            lines.append(row)
        lines.append(r"    \midrule")
    lines[-1] = r"    \bottomrule"
    lines += [
        r"  \end{tabular}",
        r"  \par\vspace{2pt}",
        r"  {\footnotesize 注：三臂仅终点机巢策略不同（强制返原巢 / 就近贪心 / 本文 MCF），"
        r"其余配置逐位一致；各梯度共用同一到达流（每周期 10 个任务），但按自身"
        r"$n\_cycles=T$ 独立求解，短梯度解不等于长梯度解的前缀（审计 "
        + _prefix_tex(summary)
        + r"）。每格为 $n=7$ 个种子的均值$\pm$标准差。"
        r"返航段/km 为逐周期交付解 $\sum$(末任务$\rightarrow$终点巢) 之和，"
        r"受航次数影响；返航段/航次已除以航次数，为可比主口径。"
        r"三臂同预算（population 24, max\_iter 60），绝对值不可与 exp01 正式档横比。}",
        r"\end{table}",
    ]
    return "\n".join(lines)


def table_effects(summary: Mapping[str, Any], tiers: Sequence[int]) -> str:
    lines = [
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \caption{同种子配对差（相对本文 MCF；负值表示该策略更省）}",
        r"  \label{tab:exp11-effects}",
        r"  \footnotesize",
        r"  \setlength{\tabcolsep}{4pt}",
        r"  \begin{tabular}{llrrrr}",
        r"    \toprule",
        r"    梯度 & 对比 & $\Delta$总航程/m & $\Delta$返航段/航次/m"
        r" & $\Delta$目标值$f$ & 检验 \\",
        r"    \midrule",
    ]
    for tier in tiers:
        eff = summary["tiers_report"][str(tier)]["effects"]
        for i, (key, lab) in enumerate((("home_minus_mcf", "返原巢$-$MCF"),
                                       ("nearest_minus_mcf", "就近$-$MCF"))):
            g = f"$T={tier}$" if i == 0 else ""
            d_path = _eff(eff, "total_path_m", key)
            d_leg = _eff(eff, "return_leg_per_sortie_m", key)
            d_f = _eff(eff, "final_fitness_sum", key)
            lines.append(
                f"    {g} & {lab}"
                f" & {_signed(d_path['mean'], 1)}"
                f" & {_signed(d_leg['mean'], 1)}"
                f" & {_signed(d_f['mean'], 1)}"
                f" & {_p_cell(d_f)} \\\\"
            )
        lines.append(r"    \midrule")
    lines[-1] = r"    \bottomrule"
    lines += [
        r"  \end{tabular}",
        r"  \par\vspace{2pt}",
        r"  {\footnotesize 注：差值为同种子配对（$n=7$，$df=6$），$\Delta$总航程与"
        r"$\Delta$目标值单位分别为 m 与无量纲加权目标；检验列为配对 $t$ 检验的"
        r"双尾 $p$（精确不完全 $\beta$）。$p$ 不显著只表示证据不足，不等价于"
        r"「无差异」；$n=7$ 仍属小样本，结论须与效应量一并读。}",
        r"\end{table}",
    ]
    return "\n".join(lines)


def table_accum(summary: Mapping[str, Any], tiers: Sequence[int]) -> str:
    acc = summary.get("accumulation") or {}
    lines = [
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \caption{跨周期累积：强制返原巢相对本文 MCF 的额外航程/返航段随梯度变化}",
        r"  \label{tab:exp11-accum}",
        r"  \footnotesize",
        r"  \setlength{\tabcolsep}{4pt}",
        r"  \begin{tabular}{rrrrrrr}",
        r"    \toprule",
        r"    梯度 & 任务数 & 累计$\Delta$总航程/m & 占 MCF/\%"
        r" & 边际$\Delta$航程/m & 累计$\Delta$返航段/m & 边际$\Delta$返航段/m \\",
        r"    \midrule",
    ]
    prev_path = 0.0
    prev_leg = 0.0
    for tier in tiers:
        a = acc.get(str(tier), {})
        cum_path = float(a.get("extra_path_m_home_vs_mcf", 0.0) or 0.0)
        cum_leg = float(a.get("extra_return_leg_m_home_vs_mcf", 0.0) or 0.0)
        mcf_path = float(
            ((summary["tiers_report"][str(tier)]["arms"].get("mcf") or {})
             .get("total_path_m") or {}).get("mean", 0.0) or 0.0
        )
        ratio = (cum_path / mcf_path * 100.0) if mcf_path else 0.0
        lines.append(
            f"    $T={tier}$ & {a.get('n_tasks_total', tier * 10)}"
            f" & {_signed(cum_path, 1)} & {_signed(ratio, 2)}"
            f" & {_signed(cum_path - prev_path, 1)}"
            f" & {_signed(cum_leg, 1)} & {_signed(cum_leg - prev_leg, 1)} \\\\"
        )
        prev_path, prev_leg = cum_path, cum_leg
    lines.append(r"    \bottomrule")
    lines += [
        r"  \end{tabular}",
        r"  \par\vspace{2pt}",
        r"  {\footnotesize 注：累计列 = 该梯度相对 MCF 的总额外量（负值 = 返原巢更省，"
        r"与预期方向相反的梯度须如实保留）；边际列 = 相对上一梯度的增量，"
        r"用于判断收益是否随累积周期增长。各梯度为同一到达流下的独立跑批"
        r"（每次 $n\_cycles=T$），前缀一致性审计为 "
        + _prefix_tex(summary)
        + r"，故边际列读作「独立跑批之差」而非同轨迹增量。}",
        r"\end{table}",
    ]
    return "\n".join(lines)


def table_md(summary: Mapping[str, Any], tiers: Sequence[int]) -> str:
    out = ["## 表 1 三臂 × 四梯度主结果（均值±标准差）", "",
           "| T | 任务数 | 策略 | 总航程/km | 返航段/km | 返航段/航次 m | 异巢占比 | f | 按时率 |",
           "|---|---|---|---|---|---|---|---|---|"]
    for tier in tiers:
        blk = summary["tiers_report"][str(tier)]
        for arm in ARMS:
            a = blk["arms"][arm]
            out.append(
                f"| {tier} | {blk['n_tasks_total']} | {ARM_LABEL[arm]}"
                f" | {_fmt(_ms(a, 'total_path_m')['mean'] / 1000)}±{_fmt(_ms(a, 'total_path_m')['std'] / 1000)}"
                f" | {_fmt(_ms(a, 'total_return_leg_m')['mean'] / 1000)}±{_fmt(_ms(a, 'total_return_leg_m')['std'] / 1000)}"
                f" | {_fmt(_ms(a, 'return_leg_per_sortie_m')['mean'], 0)}"
                f" | {_fmt(_ms(a, 'cross_nest_share')['mean'], 2)}"
                f" | {_fmt(_ms(a, 'final_fitness_sum')['mean'], 1)}"
                f" | {_fmt(_ms(a, 'on_time_rate_final')['mean'], 3)} |"
            )
    out += ["", "## 表 2 同种子配对差（相对本文 MCF）", "",
            "| T | 对比 | Δ总航程/m | Δ返航段·航次⁻¹/m | Δf | 检验 |",
            "|---|---|---|---|---|---|"]
    for tier in tiers:
        eff = summary["tiers_report"][str(tier)]["effects"]
        for key, lab in (("home_minus_mcf", "返原巢−MCF"),
                         ("nearest_minus_mcf", "就近−MCF")):
            out.append(
                f"| {tier} | {lab}"
                f" | {_signed(_eff(eff, 'total_path_m', key)['mean'], 1)}"
                f" | {_signed(_eff(eff, 'return_leg_per_sortie_m', key)['mean'], 1)}"
                f" | {_signed(_eff(eff, 'final_fitness_sum', key)['mean'], 1)}"
                f" | {_p_cell(_eff(eff, 'final_fitness_sum', key))} |"
            )
    out += ["", "## 表 3 跨周期累积（返原巢 − MCF）", "",
            "| T | 任务数 | 累计Δ总航程/m | 占MCF航程/% | 边际Δ/m | 累计Δ返航段/m | 边际Δ/m |",
            "|---|---|---|---|---|---|---|"]
    acc = summary.get("accumulation") or {}
    prev_path = prev_leg = 0.0
    for tier in tiers:
        a = acc.get(str(tier), {})
        cum_path = float(a.get("extra_path_m_home_vs_mcf", 0.0) or 0.0)
        cum_leg = float(a.get("extra_return_leg_m_home_vs_mcf", 0.0) or 0.0)
        mcf_path = float(
            ((summary["tiers_report"][str(tier)]["arms"].get("mcf") or {})
             .get("total_path_m") or {}).get("mean", 0.0) or 0.0
        )
        out.append(
            f"| {tier} | {a.get('n_tasks_total', tier * 10)}"
            f" | {_signed(cum_path, 1)} | {_signed((cum_path / mcf_path * 100) if mcf_path else 0.0, 2)}"
            f" | {_signed(cum_path - prev_path, 1)}"
            f" | {_signed(cum_leg, 1)} | {_signed(cum_leg - prev_leg, 1)} |"
        )
        prev_path, prev_leg = cum_path, cum_leg
    out += ["", "## 表 4 同解换终点反事实（归因分账）", "",
            "| T | 任务数 | 同解Δ（直接价值）/m·航次⁻¹ | 实得Δ（返原巢−MCF）/m·航次⁻¹ | 保留率 |",
            "|---|---|---|---|---|"]
    acc_cf = summary.get("accumulation") or {}
    for tier in tiers:
        a = acc_cf.get(str(tier), {})
        blk = summary["tiers_report"][str(tier)]
        gap = float(a.get("same_routes_gap_per_sortie_mcf", 0.0) or 0.0)
        diff = float(_eff(blk["effects"], "return_leg_per_sortie_m",
                          "home_minus_mcf")["mean"])
        ret = (diff / gap) if gap > 0 else None
        out.append(
            f"| {tier} | {a.get('n_tasks_total', tier * 10)}"
            f" | {_signed(gap, 0)} | {_signed(diff, 0)}"
            f" | {f'{ret * 100:.1f}%' if ret is not None else '--'} |"
        )
    out += ["", "> 口径：三臂同预算（24×60）；每格 n=7 seed，p 只作描述；"
                "返航段以「每航次均值」为主口径（合计受航次数影响）；"
                + _prefix_md(summary) + "。",
            "> 同解Δ = 对 MCF 的同一组交付路径把终点全部改回「本航次起飞巢」后的返航段增量；"
            "它是费用反事实（不做容量可行性检查），返原巢臂该值恒 0、home 分配超容周期为 0，"
            "故可作终点自由度的可交付费用证据。"
            "保留率 = 实得Δ/同解Δ，明显小于 100% 说明返原巢臂通过改路径与排序吸收了大部分损失。",
            "> " + _cf_nearest_md(summary, tiers)]
    return "\n".join(out)


def table_counterfactual(summary: Mapping[str, Any], tiers: Sequence[int]) -> str:
    """表 4：同解换终点反事实 —— 终点自由度的直接价值 vs 臂间实际差。

    这一张是给审稿人看的「归因分账」：在**同一组交付路径**上把终点改回
    起飞巢，返航段会增加多少（直接价值）；而三臂端到端比较只剩多少
    （保留率）。两者之比说明 home 臂通过改路径吸收掉了多大比例的损失。
    """
    acc = summary.get("accumulation") or {}
    lines = [
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \caption{同解换终点反事实：终点自由度的直接价值与端到端保留率}",
        r"  \label{tab:exp11-counterfactual}",
        r"  \footnotesize",
        r"  \setlength{\tabcolsep}{4pt}",
        r"  \begin{tabular}{rrrrr}",
        r"    \toprule",
        r"    梯度 & 任务数 & 同解$\Delta$/m$\cdot$航次$^{-1}$"
        r" & 实得$\Delta$/m$\cdot$航次$^{-1}$ & 保留率 \\",
        r"    \midrule",
    ]
    for tier in tiers:
        a = acc.get(str(tier), {})
        blk = summary["tiers_report"][str(tier)]
        gap = float(a.get("same_routes_gap_per_sortie_mcf", 0.0) or 0.0)
        diff = float(
            _eff(blk["effects"], "return_leg_per_sortie_m", "home_minus_mcf")["mean"]
        )
        ret = (diff / gap) if gap > 0 else None
        ret_s = f"{ret * 100:.1f}\\%" if ret is not None else "--"
        lines.append(
            f"    $T={tier}$ & {a.get('n_tasks_total', tier * 10)}"
            f" & {_signed(gap, 0)} & {_signed(diff, 0)} & {ret_s} \\\\"
        )
    lines.append(r"    \bottomrule")
    lines += [
        r"  \end{tabular}",
        r"  \par\vspace{2pt}",
        r"  {\footnotesize 注：同解$\Delta$ = 对 MCF 的同一组交付路径把终点全部改回"
        r"「本航次起飞巢」后的返航段增量（m/航次），是\textbf{费用反事实}，"
        r"不做容量可行性检查；审计显示返原巢臂该值恒为 0、home 分配超容周期为 0，"
        r"故它可作终点自由度的可交付费用证据。实得$\Delta$ = 三臂端到端「返航段/航次」"
        r"的同种子配对差（返原巢$-$MCF）。保留率 = 实得$\Delta$/同解$\Delta$："
        r"明显小于 100\% 说明返原巢臂通过改变路径与排序吸收了大部分损失。"
        r"就近规则的同解$\Delta$ 依次为 "
        + "/".join(f"{g:.0f}" for _, g, _ in _cf_nearest_rows(summary, tiers))
        + r"，实得$\Delta$ 依次为 "
        + "/".join(_signed(d, 0) for _, _, d in _cf_nearest_rows(summary, tiers))
        + r"（m/航次，按 $T$ 递增）：实得$\Delta$ 近于 0 而直接价值不小，"
        r"说明收益来自「允许改派」本身，与「选最近巢」这一具体规则无关。}",
        r"\end{table}",
    ]
    return "\n".join(lines)


def preview_doc(t_main: str, t_eff: str, t_acc: str, t_cf: str, preset: str) -> str:
    """把四张三线表装进一个独立预览文档（xelatex 编译成 PDF，检查排版）。"""
    return "\n".join([
        r"\documentclass[11pt]{ctexart}",
        r"\usepackage{booktabs,array,geometry}",
        r"\geometry{a4paper,margin=2.2cm}",
        r"\begin{document}",
        r"\section*{exp11\_multi\_depot 表格预览（" + preset + r"）}",
        t_main, "",
        r"\clearpage",
        t_eff, "",
        r"\clearpage",
        t_acc, "",
        r"\clearpage",
        t_cf,
        r"\end{document}",
        "",
    ])


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="exp11 三线表生成")
    ap.add_argument("--preset", default="formal")
    ap.add_argument("--tasks", default=None, help="梯度列表，如 2,4,6,10")
    args = ap.parse_args(argv)

    summary_path = EXP_DIR / "results" / args.preset / "summary.json"
    if not summary_path.exists():
        print(f"[tables] 缺 summary.json：{summary_path}", flush=True)
        return 2
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    tiers = (
        [int(x) for x in args.tasks.split(",") if x.strip()]
        if args.tasks else [int(t) for t in summary.get("tiers", [])]
    )
    tiers = [t for t in tiers if str(t) in summary["tiers_report"]]
    out_dir = summary_path.parent
    main_tex = table_main(summary, tiers)
    eff_tex = table_effects(summary, tiers)
    acc_tex = table_accum(summary, tiers)
    cf_tex = table_counterfactual(summary, tiers)
    (out_dir / "tables_main.tex").write_text(main_tex, encoding="utf-8")
    (out_dir / "tables_effects.tex").write_text(eff_tex, encoding="utf-8")
    (out_dir / "tables_accum.tex").write_text(acc_tex, encoding="utf-8")
    (out_dir / "tables_counterfactual.tex").write_text(cf_tex, encoding="utf-8")
    (out_dir / "tables.md").write_text(table_md(summary, tiers), encoding="utf-8")
    (out_dir / "tables_preview.tex").write_text(
        preview_doc(main_tex, eff_tex, acc_tex, cf_tex, args.preset), encoding="utf-8"
    )
    print(f"[tables] 已生成：{out_dir}/tables_main.tex, tables_effects.tex, "
          f"tables_accum.tex, tables_counterfactual.tex, tables.md, "
          f"tables_preview.tex", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
