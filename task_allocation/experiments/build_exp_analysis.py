# -*- coding: utf-8 -*-
"""跨实验分析报告生成器（exp01 / exp02 / exp05）。

只读产物、不跑实验：把三个实验的 metrics.json 汇总成一份 markdown 报告。

用法::

    python task_allocation/experiments/build_exp_analysis.py [--out PATH]

口径说明见各实验的 notes.md；本脚本只做读数与聚合，不做口径解释。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EXPS = ROOT / "task_allocation" / "experiments"


# --------------------------------------------------------------------------
# 通用工具
# --------------------------------------------------------------------------


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def ms(v: float) -> str:
    return f"{v / 1000.0:,.1f}"


def mean(xs: list[float]) -> float:
    return statistics.fmean(xs) if xs else float("nan")


def std(xs: list[float]) -> float:
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def ms_pm(xs: list[float], unit: str = "") -> str:
    """mean±std 字符串；单样本只给均值。"""
    if not xs:
        return "—"
    m, s = mean(xs), std(xs)
    u = unit
    if len(xs) > 1:
        return f"{m:.3f}±{s:.3f}{u}"
    return f"{m:.3f}{u}"


def km(x: float) -> str:
    return f"{x / 1000.0:,.1f}"


# --------------------------------------------------------------------------
# exp02：规模压缩扫描
# --------------------------------------------------------------------------


def analyze_exp02() -> dict[str, Any]:
    d = _load(EXPS / "exp02_selection_scale" / "results" / "metrics.json")
    rows = d["scales"]
    by_n: dict[int, dict[str, dict[str, Any]]] = {}
    for r in rows:
        by_n.setdefault(r["n_tasks"], {})[r["variant"]] = r
    return {"K": d["K"], "range_m": d["uav_max_range_m"], "by_n": by_n}


def render_exp02(a: dict[str, Any]) -> list[str]:
    L: list[str] = []
    by_n: dict[int, dict[str, dict[str, Any]]] = a["by_n"]
    L.append("## exp02 — 规模压缩与标定扫描\n")
    L.append(
        f"设定：机队 K={a['K']}，满电航程 {a['range_m']/1000:.0f} km，"
        "机巢 8×容量 4；候选/选择规模政策 5 个 variant + 一个「不压缩」全量 MUAS 上界。"
        "压缩比 = N / n_selected。\n"
    )
    L.append("| N | variant | 候选 n_cand | 选择 n_sel | 执行 | 压缩比 | F1 收益 | F2 航程 (km) | 端到端 (s) |")
    L.append("|---|---------|------:|------:|----:|------:|--------:|----------:|----------:|")
    for n in sorted(by_n):
        for v, r in by_n[n].items():
            sel = r.get("selection") or {}
            n_cand = sel.get("capability_n_cand") or sel.get("n_after_priority") or "—"
            mu = r["muas"]
            L.append(
                f"| {n} | {v} | {n_cand} | {r['n_selected']} | {mu['n_executed']} "
                f"| {r['compression_ratio']:.2f}× | {mu['f1_reward']:.1f} "
                f"| {mu['f2_cost']/1000:.1f} | {r['t_total_ms']/1000:.1f} |"
            )
    L.append("")

    # 相对全量上界的质量/耗时比
    L.append("### 相对「不压缩」上界的质量保持率与加速比\n")
    L.append("| N | variant | F1 / full | 耗时 / full | 加速比 | 每单位收益耗时 (s/收益) |")
    L.append("|---|---------|----------:|------------:|-------:|------------------------:|")
    for n in sorted(by_n):
        full = by_n[n].get("full_baseline")
        if not full:
            continue
        f1f, tf = full["muas"]["f1_reward"], full["t_total_ms"]
        for v, r in by_n[n].items():
            if v == "full_baseline":
                continue
            f1, t = r["muas"]["f1_reward"], r["t_total_ms"]
            L.append(
                f"| {n} | {v} | {f1/f1f*100:.1f}% | {t/tf*100:.1f}% "
                f"| {tf/t:.2f}× | {t/1000/max(f1,1e-9):.2f} |"
            )
    L.append("")
    return L


# --------------------------------------------------------------------------
# exp05：方法 vs 构造式基线
# --------------------------------------------------------------------------


def analyze_exp05() -> dict[str, Any]:
    base = EXPS / "exp05_baselines" / "results"
    per_arm: dict[str, list[dict[str, Any]]] = {}
    for f in sorted(base.glob("*/seed_*/metrics.json")):
        m = _load(f)
        per_arm.setdefault(m["arm"], []).append(m)
    return {"per_arm": per_arm}


def render_exp05(a: dict[str, Any]) -> list[str]:
    L: list[str] = []
    per_arm: dict[str, list[dict[str, Any]]] = a["per_arm"]
    order = ["M0", "D0", "B1", "B2", "B3", "B2e"]
    arms = [x for x in order if x in per_arm] + [x for x in per_arm if x not in order]
    labels = {
        "M0": "本文方法（DMDE + DEM 三维）",
        "D0": "同架构欧氏对照",
        "B1": "随机可行分配（下界）",
        "B2": "收益/代价比贪心插入",
        "B3": "Solomon VRPTW 插入",
        "B2e": "贪心 + 欧氏",
    }
    L.append("## exp05 — 方法 vs 构造式基线（多 seed）\n")
    n_seed = {len(v) for v in per_arm.values()}
    L.append(f"每臂 seed 数：{sorted(n_seed)}；任务到达序列同 seed 各臂共享（逐任务可比）。\n")
    L.append("| 臂 | 说明 | n | 完成率 | 有效完成率 | 奖励产出率 | 按时率 | 平均迟到 (s) | 航程 (km) | 求解 (s) |")
    L.append("|----|------|--:|-------:|-----------:|-----------:|-------:|-------------:|----------:|---------:|")
    for arm in arms:
        rs = per_arm[arm]
        g = lambda k: [float(r[k]) for r in rs]  # noqa: E731
        L.append(
            f"| {arm} | {labels.get(arm, arm)} | {len(rs)} "
            f"| {ms_pm(g('completion_rate'))} | {ms_pm(g('effective_completion_rate'))} "
            f"| {ms_pm(g('reward_yield'))} | {ms_pm(g('on_time_rate'))} "
            f"| {ms_pm(g('avg_lateness_s'))} | {ms_pm([x/1000 for x in g('total_path_m')])} "
            f"| {ms_pm([x/1000 for x in g('solve_time_ms')])} |"
        )
    L.append("")

    # 等完成率下的成本对照（第 2 问的核心判定口径）
    L.append("### 等完成率口径下的成本对照\n")
    L.append(
        "裸航程不可直接比：完成得越多，航程天然越长。下表把航程按「每完成一个任务」摊薄，"
        "并给出相对本文方法 M0 的差值（负值 = 更省）。\n"
    )
    L.append("| 臂 | 完成数 | 航程/完成 (km) | 相对 M0 | 按时率 | 奖励产出率 |")
    L.append("|----|-------:|---------------:|--------:|-------:|-----------:|")
    m0 = per_arm.get("M0")
    m0_uc = None
    if m0:
        m0_uc = mean([r["total_path_m"] / max(r["n_completed"], 1) for r in m0])
    for arm in arms:
        rs = per_arm[arm]
        uc = mean([r["total_path_m"] / max(r["n_completed"], 1) for r in rs])
        rel = f"{(uc/m0_uc-1)*100:+.1f}%" if m0_uc else "—"
        L.append(
            f"| {arm} | {mean([r['n_completed'] for r in rs]):.1f} | {uc/1000:.2f} | {rel} "
            f"| {ms_pm([float(r['on_time_rate']) for r in rs])} "
            f"| {ms_pm([float(r['reward_yield']) for r in rs])} |"
        )
    L.append("")
    return L


# --------------------------------------------------------------------------
# exp01：主实验（长程滚动）
# --------------------------------------------------------------------------


def analyze_exp01(dirs: list[str]) -> dict[str, Any]:
    """exp01：多种子运行把每个 seed 的 summary 写在 metrics.json['per_seed'] 里。

    单 seed 产物（旧目录/单跑）只有顶层 'summary'，也一并兼容。
    """
    per: dict[str, dict[str, Any]] = {}
    for name in dirs:
        p = EXPS / "exp01_smoke" / name / "metrics.json"
        if not p.exists():
            continue
        d = _load(p)
        ps = d.get("per_seed")
        if ps:
            per[name] = {f"seed {k}": v["summary"] for k, v in ps.items()}
        else:
            per[name] = {"summary": d["summary"]}
    return {"per_dir": per}


_EXP01_KEYS = [
    ("completion_rate", "完成率"),
    ("effective_completion_rate", "有效完成率"),
    ("reward_yield", "奖励产出率"),
    ("on_time_rate", "按时率"),
    ("correct_exec_rate", "正确执行率"),
    ("avg_lateness_s", "平均迟到 (s)"),
    ("max_lateness_s", "最大迟到 (s)"),
    ("n_completed", "完成数"),
    ("n_late", "迟到数"),
    ("n_infeasible_delivery", "废飞"),
    ("n_eliminated", "淘汰"),
    ("n_unreachable", "物理不可达"),
    ("n_remaining_pool", "期末池残留"),
]


def render_exp01(a: dict[str, Any]) -> list[str]:
    L: list[str] = []
    per: dict[str, dict[str, Any]] = a["per_dir"]
    L.append("## exp01 — 主实验（长程滚动闭环）\n")
    if not per:
        L.append("_尚无产物_。\n")
        return L

    keys = _EXP01_KEYS
    items = [(f"`{d}` / {lbl}", s) for d, m in per.items() for lbl, s in m.items()]

    L.append("| 运行 | " + " | ".join(lbl for _, lbl in keys) + " |")
    L.append("|---" * (len(keys) + 1) + "|")
    for name, s in items:
        cells = []
        for k, _ in keys:
            v = s.get(k)
            cells.append(f"{v:.3f}" if isinstance(v, float) else str(v))
        L.append(f"| {name} | " + " | ".join(cells) + " |")
    L.append("")

    L.append("| 运行 | 总航程 (km) | 总奖励 | 求解 (s) | 墙钟 (s) | 活跃周期 | 每活跃周期吞吐 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for name, s in items:
        L.append(
            f"| {name} | {s['total_cost']/1000:.1f} | {s['total_reward']:.1f} "
            f"| {s['solve_time_ms']/1000:.0f} | {s.get('wall_seconds', 0):.0f} "
            f"| {s.get('n_active_cycles', '—')} "
            f"| {s.get('throughput_per_active_cycle', float('nan')):.2f} |"
        )
    L.append("")

    # 跨 seed 聚合：按指标给 mean±std
    groups: dict[str, list[dict[str, Any]]] = {
        d: list(m.values()) for d, m in per.items()
    }
    for d, ss in groups.items():
        if len(ss) < 2:
            continue
        L.append(f"### 跨 seed 聚合（{len(ss)} 个 seed，mean±std）\n")
        L.append("| 指标 | mean±std | 逐 seed |")
        L.append("|---|--:|---|")
        for k, lbl in keys + [
            ("total_cost", "总航程 (m)"),
            ("total_reward", "总奖励"),
            ("solve_time_ms", "求解 (ms)"),
        ]:
            vals = [s.get(k) for s in ss if isinstance(s.get(k), (int, float))]
            if not vals:
                continue
            each = " / ".join(f"{v:.3f}" if isinstance(v, float) else str(v) for v in vals)
            L.append(f"| {lbl} | {ms_pm([float(v) for v in vals])} | {each} |")
        L.append("")
    return L


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 exp01/exp02/exp05 分析报告")
    ap.add_argument(
        "--exp01-dirs", nargs="*", default=["results"],
        help="exp01_smoke 下要汇总的结果目录名",
    )
    ap.add_argument(
        "--out", default=str(EXPS / "EXPERIMENTS_ANALYSIS.md"),
        help="输出 markdown 路径",
    )
    args = ap.parse_args()

    L: list[str] = ["# 实验分析报告（exp01 / exp02 / exp05）\n"]
    L.append("> 由 `task_allocation/experiments/build_exp_analysis.py` 自动汇总；"
             "所有数字直接读自各实验 `results/` 下的 metrics.json，未做手工修改。\n")

    try:
        L += render_exp02(analyze_exp02())
    except Exception as e:  # noqa: BLE001
        L.append(f"## exp02\n\n_读取失败：{e}_\n")

    try:
        L += render_exp05(analyze_exp05())
    except Exception as e:  # noqa: BLE001
        L.append(f"## exp05\n\n_读取失败：{e}_\n")

    try:
        L += render_exp01(analyze_exp01(args.exp01_dirs))
    except Exception as e:  # noqa: BLE001
        L.append(f"## exp01\n\n_读取失败：{e}_\n")

    out = Path(args.out)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"[analysis] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
