# -*- coding: utf-8 -*-
"""tables.py — 实验结果 LaTeX 表格生成器（exp01_smoke）。

与 ``plot/`` 同哲学：**只读 json（metrics.json / solution.json）、只制表**，
不含任何计算。产物为独立可编译的 LaTeX 文档（booktabs 三线表），
供论文实验章直接 \\input 或复制。

用法::

    python3 tables.py                       # 全部表 → results/tables/exp01_tables.tex
    python3 tables.py --out my_tables.tex   # 自定义输出路径

表清单（本文件底部 TABLE_ORDER 维护，新增表 = 加一个 build_tabX）::

    tab1_config     运行配置总览
    tab2_funnel     逐周期任务漏斗与命运
    tab3_objective  逐周期目标 / 可行性 / 求解开销
    tab4_tw         时间窗违约与重复出动明细
    tab5_outcome    结果总览与收益核算对账（含口径注）
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
RESULTS = EXP_DIR / "results"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _f(x: float, nd: int = 2) -> str:
    return f"{x:.{nd}f}" if isinstance(x, (int, float)) else str(x)


def _load() -> tuple[dict, dict]:
    m = json.load(open(RESULTS / "metrics.json", encoding="utf-8"))
    s = json.load(open(RESULTS / "solution.json", encoding="utf-8"))
    return m, s


def _rewards(s: dict) -> dict[int, float]:
    return {t["id"]: float(t.get("reward", 0.0)) for t in s["tasks"]}


# ---------------------------------------------------------------------------
# 各表
# ---------------------------------------------------------------------------

def build_tab1_config(m: dict, s: dict) -> str:
    prob = m.get("problem", {})
    rows = [
        ("随机种子", str(m.get("seeds", ["?"]))),
        ("滚动周期数", str(m.get("n_cycles"))),
        ("机巢数 / 单巢容量", f"{prob.get('n_nests')} / {prob.get('nest_capacity')}"),
        ("机队规模 $K_{\\mathrm{avail}}$", str(prob.get("n_uavs"))),
        ("无人机速度 / 满电航程", "15.0 m/s / 80.0 km"),
        ("任务总数（首批/后续到达）",
         f"{prob.get('n_tasks_initial')} + {prob.get('n_arrived', 0) - prob.get('n_tasks_initial', 0)}"),
        ("DMDE 种群 / 迭代代数", "50 / 100"),
        ("目标标量化", "字典序（big-M，收益优先）"),
        ("交付时间窗约束", "开启"),
        ("DEM 区域 / 重采样步长", "N29E091（约 97$\\times$111 km）/ 200 m"),
    ]
    body = "\n".join(f"{k} & {v} \\\\" for k, v in rows)
    return (
        "\\begin{table}[htbp]\\centering\n"
        "\\caption{exp01\\_smoke 运行配置总览（seed="
        + str(m.get("seeds", ["?"])[0]) + "）}\n"
        "\\label{tab:exp01-config}\n"
        "\\begin{tabular}{ll}\n\\toprule\n"
        "配置项 & 取值 \\\\\n\\midrule\n"
        + body + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n"
    )


def build_tab2_funnel(m: dict, s: dict) -> str:
    rows = []
    for c in m["cycles"]:
        sel = c.get("selection", {})
        pc = c.get("pool_counts", {})
        rows.append(
            f"{c['cycle']} & {pc.get('n_new', 0)} & {pc.get('n_remain', 0)} & "
            f"{pc.get('n_release', 0)} & {sel.get('n_after_prefilter', 0)} & "
            f"{sel.get('n_selected', 0)} & {c['muas'].get('n_executed', 0)} & "
            f"{c['muas'].get('n_deferred', 0)} & {len(c.get('completed_ids', []))} & "
            f"{len(c.get('infeasible_tags', {}))} \\\\"
        )
    body = "\n".join(rows)
    return (
        "\\begin{table}[htbp]\\centering\n"
        "\\caption{逐周期任务漏斗与任务命运（闸门拦截 = 交付被判违约、解绑回池的任务次）}\n"
        "\\label{tab:exp01-funnel}\n"
        "\\begin{tabular}{rcccccccccc}\n\\toprule\n"
        "周期 & 新到 & 滞留 & 释放 & 过预筛 & 选中 & 执行 & 延期 & 完成 & 拦截 \\\\\n"
        "\\midrule\n" + body + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n"
    )


def build_tab3_objective(m: dict, s: dict) -> str:
    rw = _rewards(s)
    rows = []
    for c in m["cycles"]:
        mu = c["muas"]
        comp = c.get("completed_ids", [])
        comp_r = sum(rw.get(t, 0.0) for t in comp)
        rows.append(
            f"{c['cycle']} & {_f(mu.get('f1_reward'))} & {_f(comp_r)} & "
            f"{mu.get('f2_cost', 0) / 1000:.1f} & {_f(mu.get('penalty'))} & "
            f"{len(mu.get('violations', []))} & "
            f"{'是' if mu.get('feasible') else '否'} & "
            f"{mu.get('solve_seconds', 0):.1f} \\\\"
        )
    body = "\n".join(rows)
    return (
        "\\begin{table}[htbp]\\centering\n"
        "\\caption{逐周期目标、约束可行性与求解开销（$F_1^{\\mathrm{plan}}$ 为规划口径收益，"
        "$F_1^{\\mathrm{comp}}$ 为闸门后实际完成收益；$C^{3D}$ 为三维路径总代价，单位 km）}\n"
        "\\label{tab:exp01-objective}\n"
        "\\begin{tabular}{rccccccc}\n\\toprule\n"
        "周期 & $F_1^{\\mathrm{plan}}$ & $F_1^{\\mathrm{comp}}$ & $C^{3D}$ (km) & "
        "惩罚 & 违约数 & 可行 & 求解 (s) \\\\\n"
        "\\midrule\n" + body + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n"
    )


def build_tab4_tw(m: dict, s: dict) -> str:
    tasks = {t["id"]: t for t in s["tasks"]}
    # 出动/拦截/完成历史
    flown: dict[int, list[int]] = {}
    blocked: dict[int, list[int]] = {}
    completed: set[int] = set()
    for c in m["cycles"]:
        for t in c.get("executed_ids", []):
            flown.setdefault(int(t), []).append(c["cycle"])
        for t in c.get("infeasible_tags", {}):
            blocked.setdefault(int(t), []).append(c["cycle"])
        completed.update(int(t) for t in c.get("completed_ids", []))
    interesting = sorted(set(flown) | set(blocked))
    rows = []
    for tid in interesting:
        tk = tasks.get(tid, {})
        verdict = "完成" if tid in completed else ("未交付（重复拦截）" if len(blocked.get(tid, [])) > 1 else "未交付")
        rows.append(
            f"T{tid} & {tk.get('latest', 0) / 60:.1f} & "
            f"{len(flown.get(tid, []))} & {len(blocked.get(tid, []))} & "
            f"{verdict} \\\\"
        )
    body = "\n".join(rows)
    return (
        "\\begin{table}[htbp]\\centering\n"
        "\\caption{时间窗违约与出动明细（按任务列出全部曾出动任务；"
        "重复出动 = 同一任务多周期再次派出）}\n"
        "\\label{tab:exp01-tw}\n"
        "\\begin{tabular}{rcccl}\n\\toprule\n"
        "任务 & $t_{\\mathrm{latest}}$ (min) & 出动次数 & 闸门拦截 & 结局 \\\\\n"
        "\\midrule\n" + body + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n"
    )


def build_tab5_outcome(m: dict, s: dict) -> str:
    rw = _rewards(s)
    sm = m.get("summary", {})
    all_exec = [int(t) for c in m["cycles"] for t in c.get("executed_ids", [])]
    all_comp = [int(t) for c in m["cycles"] for t in c.get("completed_ids", [])]
    uniq_comp = sorted(set(all_comp))
    comp_r = sum(rw.get(t, 0.0) for t in uniq_comp)
    plan_r = sum(c["muas"].get("f1_reward", 0.0) for c in m["cycles"])
    repeats = len(all_exec) - len(set(all_exec))
    # 终点机巢使用（solution tours）
    used_nests = set()
    for c in s.get("cycles", []):
        for t in c.get("tours", []):
            if t.get("task_ids") and t.get("end_nest_id") is not None:
                used_nests.add(t["end_nest_id"])
    rows = [
        ("到达任务总数 $|\\mathcal{T}|$", str(sm.get("n_arrived"))),
        ("完成任务数", f"{sm.get('n_completed')}（唯一 {len(uniq_comp)}）"),
        ("完成率", f"{sm.get('completion_rate', 0) * 100:.1f}\\%"),
        ("周期解可行率", f"{sm.get('cycle_feasible_rate', 0) * 100:.1f}\\%（0/3，全部含时间窗违约）"),
        ("规划口径收益 $\\sum F_1^{\\mathrm{plan}}$", _f(plan_r)),
        ("完成口径收益 $\\sum F_1^{\\mathrm{comp}}$", _f(comp_r)),
        ("核算膨胀率", f"{(plan_r / comp_r - 1) * 100:.1f}\\%（闸门拦截 + 重复出动重复计数）"),
        ("总三维路径代价", f"{sm.get('total_cost', 0) / 1000:.1f} km"),
        ("平均等待时间", f"{sm.get('avg_wait', 0) / 60:.1f} min"),
        ("总出动任务次 / 重复出动", f"{len(all_exec)} / {repeats}"),
        ("终点机巢使用", f"{len(used_nests)} / {m.get('problem', {}).get('n_nests')} 个"),
        ("总求解时间", f"{sm.get('solve_time_ms', 0) / 1000:.1f} s"),
    ]
    body = "\n".join(f"{k} & {v} \\\\" for k, v in rows)
    return (
        "\\begin{table}[htbp]\\centering\n"
        "\\caption{结果总览与收益核算对账}\n"
        "\\label{tab:exp01-outcome}\n"
        "\\begin{tabular}{ll}\n\\toprule\n"
        "指标 & 数值 \\\\\n\\midrule\n" + body + "\n\\bottomrule\n\\end{tabular}\n"
        "\n\\vspace{2pt}\\parbox{0.92\\linewidth}{\\footnotesize "
        "\\textbf{口径注}：$F_1^{\\mathrm{plan}}$ 为求解期目标值（含被完成闸门拦截的交付、"
        "以及重复出动任务的重复计数）；$F_1^{\\mathrm{comp}}$ 按最终完成的唯一任务计。"
        "论文报完成率与收益时应使用完成口径，规划口径仅用于说明优化过程。}"
        "\n\\end{table}\n"
    )


TABLE_ORDER = [
    build_tab1_config,
    build_tab2_funnel,
    build_tab3_objective,
    build_tab4_tw,
    build_tab5_outcome,
]


def render(out: Path) -> Path:
    m, s = _load()
    parts = [
        "% exp01_smoke 实验结果表（由 tables.py 从 metrics.json / solution.json 生成）",
        "% 编译：pdflatex tables.tex（依赖 booktabs）",
        "\\documentclass[a4paper,11pt]{article}",
        "\\usepackage[margin=2.2cm]{geometry}",
        "\\usepackage{booktabs}",
        "\\usepackage{ctex} % 中文；推荐 xelatex 编译（无 ctex 宏包时改英文文档类）",
        "\\begin{document}",
        "\\title{exp01\\_smoke 实验结果记录}",
        "\\maketitle",
    ]
    parts += [fn(m, s) for fn in TABLE_ORDER]
    parts.append("\\end{document}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n\n".join(parts), encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 exp01_smoke 结果 LaTeX 表格")
    ap.add_argument("--out", default=str(RESULTS / "tables" / "exp01_tables.tex"))
    args = ap.parse_args()
    path = render(Path(args.out))
    print(f"[tables] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
