# -*- coding: utf-8 -*-
"""tables.py — 实验结果 LaTeX 表格生成器（exp01_smoke）。

与 ``plot/`` 同哲学：**只读 json（metrics.json / solution.json）、只制表**，
不含任何计算。产物为独立可编译的 LaTeX 文档（booktabs 三线表），
供论文实验章直接 \\input 或复制。

用法::

    python3 tables.py                       # 全部表 → results/tables/exp01_tables.tex
    python3 tables.py --out my_tables.tex   # 自定义输出路径

本文件由 ``run.py`` 在每次运行末尾**自动调用**（``run.make_tables`` 控制，
与 ``make_plots`` 对齐），保证 metrics.json / solution.json / 图 / 结果表
永远同源同步；也可单独手动执行（读盘上现有 json 重制表）。

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


def _load(results_dir: Path | None = None) -> tuple[dict, dict]:
    base = Path(results_dir) if results_dir is not None else RESULTS
    m = json.load(open(base / "metrics.json", encoding="utf-8"))
    s = json.load(open(base / "solution.json", encoding="utf-8"))
    return m, s


def _rewards(s: dict) -> dict[int, float]:
    return {t["id"]: float(t.get("reward", 0.0)) for t in s["tasks"]}


def _decay_W() -> str:
    """迟到奖励折减容忍 W（s）：从 config.yaml 实读，表注释不写死。"""
    try:
        import yaml  # type: ignore

        cfg = yaml.safe_load(
            (EXP_DIR / "config.yaml").read_text(encoding="utf-8")
        ) or {}
        w = (
            cfg.get("muas", {}) or {}
        ).get("late_reward_decay_seconds")
        if w:
            return f"{float(w):g}"
    except Exception:
        pass
    return "1800"


# ---------------------------------------------------------------------------
# 各表
# ---------------------------------------------------------------------------

def _tex_escape(s: str) -> str:
    """转义 LaTeX 特殊字符（下划线/井号/百分号/与号），防止文件名破坏编译。"""
    for ch, esc in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"),
                    ("$", r"\$"), ("#", r"\#"), ("_", r"\_"),
                    ("{", r"\{"), ("}", r"\}"), ("~", r"\textasciitilde{}"),
                    ("^", r"\textasciicircum{}")):
        s = s.replace(ch, esc)
    return s


def _dem_region(dem_file: str) -> str:
    """DEM 区域名从文件名提取（ASTGTMV003_N29E091_dem.tif → N29E091）。

    区域码格式：``[NS]`` + 2 位纬度 + ``[EW]`` + 3 位经度（共 7 字符）。
    历史上按 6 字符匹配导致永不命中，整个文件名（含下划线）漏进 LaTeX
    编译失败（"Missing $ inserted"），PDF 停留在旧版。
    """
    for token in dem_file.replace(".", "_").split("_"):
        if len(token) == 7 and token[0] in "NS" and token[3] in "EW" \
                and token[1:3].isdigit() and token[4:7].isdigit():
            return token
    return dem_file


def build_tab1_config(m: dict, s: dict) -> str:
    prob = m.get("problem", {})
    region = _dem_region(str(prob.get("dem_file", "未接入")))
    rows = [
        ("随机种子", str(m.get("seeds", ["?"]))),
        ("滚动周期数", str(m.get("n_cycles"))),
        ("机巢数 / 单巢容量", f"{prob.get('n_nests')} / {prob.get('nest_capacity')}"),
        ("机队规模 $K_{\\mathrm{avail}}$", str(prob.get("n_uavs"))),
        ("无人机速度 / 满电航程",
         f"{prob.get('uav_speed', '?')} m/s / {prob.get('uav_max_range_km', '?')} km"),
        ("任务总数（首批/后续到达）",
         f"{prob.get('n_tasks_initial')} + {prob.get('n_arrived', 0) - prob.get('n_tasks_initial', 0)}"),
        ("DMDE 种群 / 迭代代数",
         f"{prob.get('dmde_population', '?')} / {prob.get('dmde_max_iter', '?')}"),
        ("目标标量化", str(prob.get("scalarization", "?"))),
        ("交付时间窗约束", "开启" if prob.get("time_window") else "关闭"),
        ("DEM 区域 / 重采样步长", f"{_tex_escape(region)} / {prob.get('dem_step_m', '?')} m"),
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
            f"{c.get('n_late', 0)} & "
            f"{len(c.get('infeasible_tags', {}))} \\\\"
        )
    body = "\n".join(rows)
    return (
        "\\begin{table}[htbp]\\centering\n"
        "\\caption{逐周期任务漏斗与任务命运（迟到 = 完成但晚于 latest，计入完成；"
        "拦截 = 交付被判物理不可行、解绑回池的任务次）}\n"
        "\\label{tab:exp01-funnel}\n"
        "\\begin{tabular}{rccccccccccc}\n\\toprule\n"
        "周期 & 新到 & 滞留 & 释放 & 过预筛 & 选中 & 执行 & 延期 & 完成 & 迟到 & 拦截 \\\\\n"
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
    w_str = _decay_W()
    return (
        "\\begin{table}[htbp]\\centering\n"
        "\\caption{逐周期目标、约束可行性与求解开销（$F_1^{\\mathrm{plan}}$ 为规划口径收益"
        "（迟到任务按 $W=" + w_str + "$~s 线性折减），$F_1^{\\mathrm{comp}}$ 为实际完成任务的"
        "全额收益；$C^{3D}$ 为三维路径总代价，单位 km）}\n"
        "\\label{tab:exp01-objective}\n"
        "\\begin{tabular}{rccccccc}\n\\toprule\n"
        "周期 & $F_1^{\\mathrm{plan}}$ & $F_1^{\\mathrm{comp}}$ & $C^{3D}$ (km) & "
        "惩罚 & 违约数 & 可行 & 求解 (s) \\\\\n"
        "\\midrule\n" + body + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n"
    )


def build_tab4_tw(m: dict, s: dict) -> str:
    tasks = {t["id"]: t for t in s["tasks"]}
    # 出动/物理拦截/完成历史 + 每任务最大迟到
    flown: dict[int, list[int]] = {}
    blocked: dict[int, list[int]] = {}
    completed: set[int] = set()
    late_max: dict[int, float] = {}
    for c in m["cycles"]:
        for t in c.get("executed_ids", []):
            flown.setdefault(int(t), []).append(c["cycle"])
        for t in c.get("infeasible_tags", {}):
            blocked.setdefault(int(t), []).append(c["cycle"])
        completed.update(int(t) for t in c.get("completed_ids", []))
        for tid, late in (c.get("lateness_by_task") or {}).items():
            tid = int(tid)
            late_max[tid] = max(late_max.get(tid, 0.0), float(late))
    interesting = sorted(set(flown) | set(blocked))
    rows = []
    for tid in interesting:
        tk = tasks.get(tid, {})
        if tid in completed:
            verdict = f"完成（晚 {late_max[tid] / 60:.1f} min）" if tid in late_max else "完成"
        else:
            verdict = "未交付（重复拦截）" if len(blocked.get(tid, [])) > 1 else "未交付"
        rows.append(
            f"T{tid} & {tk.get('latest', 0) / 60:.1f} & "
            f"{len(flown.get(tid, []))} & {len(blocked.get(tid, []))} & "
            f"{verdict} \\\\"
        )
    body = "\n".join(rows)
    return (
        "\\begin{table}[htbp]\\centering\n"
        "\\caption{时间窗违约与出动明细（按任务列出全部曾出动任务；"
        "重复出动 = 同一任务多周期再次派出；拦截 = 物理不可行弹回）}\n"
        "\\label{tab:exp01-tw}\n"
        "\\begin{tabular}{rcccl}\n\\toprule\n"
        "任务 & $t_{\\mathrm{latest}}$ (min) & 出动次数 & 物理拦截 & 结局 \\\\\n"
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
    # 周期可行率括注按数据算（曾硬编码「0/3」，与 1/3 的实际值矛盾 → 表说谎）
    n_cyc = len(m["cycles"])
    n_feas = sum(1 for c in m["cycles"] if c["muas"].get("feasible"))
    n_late_cyc = sum(1 for c in m["cycles"] if c.get("n_late"))
    # 终点机巢使用（solution tours）
    used_nests = set()
    for c in s.get("cycles", []):
        for t in c.get("tours", []):
            if t.get("task_ids") and t.get("end_nest_id") is not None:
                used_nests.add(t["end_nest_id"])
    rows = [
        ("到达任务总数 $|\\mathcal{T}|$", str(sm.get("n_arrived"))),
        ("完成任务数（含迟到交付）", f"{sm.get('n_completed')}（唯一 {len(uniq_comp)}）"),
        ("完成率", f"{sm.get('completion_rate', 0) * 100:.1f}\\%"),
        ("正确执行吞吐（任务/周期）", _f(sm.get("throughput_per_cycle", 0.0), 3)),
        ("平均正确执行率（按时交付/出动）",
         f"{sm.get('correct_exec_rate', 0) * 100:.1f}\\%"),
        ("按时率（按时出动/总出动）",
         f"{sm.get('on_time_rate', 0) * 100:.1f}\\%"),
        ("迟到交付次数（计入完成）", str(sm.get("n_late", 0))),
        ("平均 / 最大迟到",
         f"{sm.get('avg_lateness_s', 0) / 60:.1f} / "
         f"{sm.get('max_lateness_s', 0) / 60:.1f} min"),
        ("废飞次数（物理不可行弹回）", str(sm.get("n_infeasible_delivery", 0))),
        ("不可达任务数（往返航程超限，过于偏远）",
         str(sm.get("n_unreachable", 0))),
        ("超期淘汰任务数", str(sm.get("n_eliminated", 0))),
        ("周期解可行率",
         f"{sm.get('cycle_feasible_rate', 0) * 100:.1f}\\%"
         f"（{n_feas}/{n_cyc}；其中 {n_late_cyc} 个周期含迟到交付）"),
        ("规划口径收益 $\\sum F_1^{\\mathrm{plan}}$", _f(plan_r)),
        ("完成口径收益 $\\sum F_1^{\\mathrm{comp}}$", _f(comp_r)),
        ("核算膨胀率", f"{(plan_r / comp_r - 1) * 100:.1f}\\%（废飞拦截 + 重复出动重复计数）"),
        ("总三维路径代价", f"{sm.get('total_cost', 0) / 1000:.1f} km"),
        ("平均等待时间", f"{sm.get('avg_wait', 0) / 60:.1f} min"),
        ("总出动任务次 / 重复出动", f"{len(all_exec)} / {repeats}"),
        ("终点机巢使用", f"{len(used_nests)} / {m.get('problem', {}).get('n_nests')} 个"),
        ("总求解时间", f"{sm.get('solve_time_ms', 0) / 1000:.1f} s"),
    ]
    body = "\n".join(f"{k} & {v} \\\\" for k, v in rows)
    w_str = _decay_W()
    return (
        "\\begin{table}[htbp]\\centering\n"
        "\\caption{结果总览与收益核算对账}\n"
        "\\label{tab:exp01-outcome}\n"
        "\\begin{tabular}{ll}\n\\toprule\n"
        "指标 & 数值 \\\\\n\\midrule\n" + body + "\n\\bottomrule\n\\end{tabular}\n"
        "\n\\vspace{2pt}\\parbox{0.92\\linewidth}{\\footnotesize "
        "\\textbf{口径注}：长程协议下主指标为**正确执行吞吐 / 正确执行率**"
        "（完成率只是窗口长度产物，不作质量证据）。迟到不是废飞"
        "（2026-10-15 协议）：晚到任务计入完成与吞吐，有效奖励按迟到时长"
        "线性折减（$W=" + w_str + "$~s），按时率/平均迟到单独报告；废飞只剩**物理"
        "不可行**的弹回交付（航程/续航/终点机巢违约），总航程下降若靠它"
        "换来则是净亏损。$F_1^{\\mathrm{plan}}$ 为求解期目标值（含迟到"
        "折减与被拦交付及重复出动计数），$F_1^{\\mathrm{comp}}$ 按最终"
        "完成的唯一任务计全额奖励，论文报收益应使用完成口径，规划口径"
        "仅用于说明优化过程。}"
        "\n\\end{table}\n"
    )


TABLE_ORDER = [
    build_tab1_config,
    build_tab2_funnel,
    build_tab3_objective,
    build_tab4_tw,
    build_tab5_outcome,
]


def render(out: Path, results_dir: Path | None = None) -> Path:
    """制表并写入 ``out``。

    Args:
        out: 输出 .tex 路径。
        results_dir: 读取 metrics.json / solution.json 的目录；缺省为本实验
            的 ``results/``。``run.py`` 传入实际输出目录，保证表与刚写盘的
            json 同源。
    """
    m, s = _load(results_dir)
    parts = [
        "% exp01_smoke 实验结果表（由 tables.py 从 metrics.json / solution.json 生成）",
        "% 编译：latexmk -xelatex exp01_tables.tex（ctex 需 xelatex；run.py 自动编译）",
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
