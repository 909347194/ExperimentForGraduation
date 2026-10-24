"""exp06_joint_regime：联合域（K × Poisson 到达压力 × λ_cost）矩阵编排 + 观测聚合。

不复制滚动外壳：以 ``--set`` 深合并驱动 ``exp01_smoke/run.py``（同一
build/horizon/metrics 代码路径，口径逐位继承），本层只做三件事：

1. **矩阵编排**：逐格子进程调 exp01 run.py（``--no-plots --no-audit``），
   覆盖 ``problem.n_uavs`` / ``rolling.arrivals.mode|lambda`` /
   ``rolling.n_cycles`` / ``muas.lambda_cost`` / ``experiment.output_dir``；
2. **联合域观测聚合**：cap(t) 时序、选择数/cap(t) 比值、任务池积压 |T_t| 时序
   （全部来自 exp01 per-cycle metrics 的 ``cycles[].selection`` /
   ``cycles[].pool_counts``，无新增口径）；
3. **预注册判定**：按 config.yaml 的 criteria 核对用户假设 1 在积压域是否成立。

用法：
    python run.py                        # 按 config.yaml 全矩阵
    python run.py --arrivals 10          # 只跑积压格
    python run.py --lcs 1e-1             # 只跑某 λ_cost 臂
    python run.py --analyze-only         # 不跑，只聚合已有 results/
    python run.py --smoke                #  plumbing 冒烟（3 代 × 3 周期）

产物：results/<tag>/metrics.json（exp01 原样落盘）+ results/summary.csv
      + results/joint_observables.json + JOINT_REGIME_REPORT.md
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from statistics import mean, pstdev

HERE = Path(__file__).resolve().parent
EXP01_DIR = HERE.parent / "exp01_smoke"
EXP01_RUN = EXP01_DIR / "run.py"
RESULTS = HERE / "results"

SEP = "=" * 72


def _load_cfg() -> dict:
    import yaml

    return yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))


def _tag(K: int, lam: float, lc: float, seed: int) -> str:
    return f"K{int(K)}_arr{lam:g}_lc{lc:g}_s{int(seed)}"


def run_cell(K: int, lam: float, lc: float, seed: int, n_cycles: int,
             smoke: bool) -> Path:
    """单格：子进程驱动 exp01 滚动外壳，产物落 exp06/results/<tag>/。"""
    tag = _tag(K, lam, lc, seed)
    out_rel = f"../exp06_joint_regime/results/{tag}"  # 相对 exp01 的 EXP_DIR
    cmd = [
        sys.executable, "-u", str(EXP01_RUN),
        "--seeds", str(seed), "--no-plots", "--no-audit",
        "--set", f"problem.n_uavs={int(K)}",
        "--set", "rolling.arrivals.mode=poisson",
        "--set", f"rolling.arrivals.lambda={lam:g}",
        "--set", f"rolling.n_cycles={n_cycles}",
        "--set", f"muas.lambda_cost={lc:g}",
        "--set", f"experiment.output_dir={out_rel}",
    ]
    if smoke:
        cmd += ["--set", "solver.max_generations=3", "--set", "rolling.n_cycles=3"]
    print(f"{SEP}\n[exp06] 格 {tag}（smoke={smoke}）\n  $ {' '.join(cmd[3:])}\n{SEP}")
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        print(f"[exp06][warn] 格 {tag} 退出码 {r.returncode}")
    return RESULTS / tag


def read_cell(tag_dir: Path, seed: int) -> dict | None:
    """读单格 metrics.json（兼容单 seed 扁平结构与多种子 per_seed 结构）。"""
    p = tag_dir / "metrics.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    if "per_seed" in d and str(seed) in d["per_seed"]:
        m = d["per_seed"][str(seed)]
    else:
        m = d
    # exp01 summary 用 total_cost，统一映射成 total_path_m
    s = m.get("summary", m)
    if "total_path_m" not in s and "total_cost" in s:
        s = dict(s)
        s["total_path_m"] = s["total_cost"]
        m = dict(m)
        m["summary"] = s
    return m


def cell_observables(m: dict) -> dict:
    """联合域观测量：cap(t) / 选择数-cap(t) 比值 / 积压 |T_t| 时序。"""
    cycles = m.get("cycles") or []
    cap, ratio, backlog, n_sel = [], [], [], []
    for c in cycles:
        sel = c.get("selection") or {}
        pc = c.get("pool_counts") or {}
        ct = sel.get("capability_cap")
        ns = sel.get("capability_n_sel")
        if ct:
            cap.append(int(ct))
            n_sel.append(int(ns) if ns is not None else None)
            ratio.append(round(ns / ct, 4) if ns is not None else None)
        backlog.append(int(pc.get("n_total", 0)))
    obs: dict = {
        "n_cycles": len(cycles),
        "cap_t": cap,
        "n_sel_over_cap_t": [r for r in ratio if r is not None],
        "backlog_T_t": backlog,
        "sel_total": sum(int(x) for x in n_sel if x is not None),
        "forced_max": max((int((c.get("selection") or {}).get("n_forced_in") or 0)
                           for c in cycles), default=0),
    }
    if cap:
        obs["cap_t_first"] = cap[0]
        obs["cap_t_last"] = cap[-1]
        obs["cap_t_mean"] = round(mean(cap), 2)
        obs["cap_t_decay_pct"] = round((cap[0] - cap[-1]) / cap[0] * 100, 1) if cap[0] else None
    if backlog:
        obs["backlog_first"] = backlog[0]
        obs["backlog_last"] = backlog[-1]
        obs["backlog_max"] = max(backlog)
        obs["backlog_growth_per_cycle"] = round(
            (backlog[-1] - backlog[0]) / max(len(backlog) - 1, 1), 3)
    valid_ratio = [r for r in obs["n_sel_over_cap_t"]]
    if valid_ratio:
        obs["n_sel_over_cap_t_mean"] = round(mean(valid_ratio), 4)
        obs["n_sel_over_cap_t_last"] = valid_ratio[-1]
    return obs


def aggregate(cfg: dict) -> None:
    """聚合全部格 → summary.csv + joint_observables.json + JOINT_REGIME_REPORT.md。"""
    seeds = list(cfg["experiment"].get("seeds") or [42])
    rows: list[dict] = []
    for K in cfg["matrix"]["K"]:
        for lam in cfg["matrix"]["arrival_lambda"]:
            for lc in cfg["matrix"]["lambda_cost"]:
                for seed in seeds:
                    tag = _tag(K, lam, lc, seed)
                    m = read_cell(RESULTS / tag, seed)
                    if m is None:
                        print(f"[exp06][warn] 缺格：{tag}")
                        continue
                    s = m.get("summary", m)
                    rows.append({
                        "tag": tag, "K": K, "arrival_lambda": lam,
                        "lambda_cost": lc, "seed": seed,
                        "n_arrived": s.get("n_arrived"),
                        "n_completed": s.get("n_completed"),
                        # 2026-10-24：exp01 删除 naive completion_rate / 池化
                        # on_time_rate（A1/A2）⇒ 完成率取 final 口径、按时率
                        # 取交付集条件率，与论文主表同源
                        "completion_rate_final": s.get("completion_rate_final"),
                        "effective_completion_rate": s.get("effective_completion_rate"),
                        "reward_yield": s.get("reward_yield"),
                        "on_time_rate_final": s.get("on_time_rate_final"),
                        "n_late": s.get("n_late"),
                        "avg_lateness_s": s.get("avg_lateness_s"),
                        "n_infeasible_delivery": s.get("n_infeasible_delivery"),
                        "n_eliminated": s.get("n_eliminated"),
                        "n_unreachable": s.get("n_unreachable"),
                        "n_remaining_pool": s.get("n_remaining_pool"),
                        "total_path_m": s.get("total_path_m"),
                        "throughput_per_cycle": s.get("throughput_per_cycle"),
                        "solve_time_ms": s.get("solve_time_ms"),
                        "n_cycles": m.get("n_cycles"),
                        **{f"obs_{k}": v for k, v in cell_observables(m).items()
                           if not isinstance(v, list)},
                    })
    if not rows:
        print("[exp06] 无可聚合格")
        return
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (RESULTS / "joint_observables.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[exp06] 汇总 {len(rows)} 格 → results/summary.csv, joint_observables.json")

    # ── 预注册判定 ──
    L: list[str] = ["# exp06 联合域报告\n"]
    crit = cfg.get("criteria", {}).get("assumption1", {})
    comp_min = float(crit.get("completion_rate_min", 1.0))
    elim_max = int(crit.get("n_eliminated_max", 0))
    L.append("## 1. 主表\n")
    L.append("| 格 | K | 到达λ | λ_cost | 到达 | 完成 | 完成率 | 淘汰 | 不可达 | 废飞 | "
             "按时率 | 航程km | 积压末值 | cap(t)首→末 | n_sel/cap(t)末 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        L.append(
            f"| {r['tag']} | {r['K']} | {r['arrival_lambda']:g} | {r['lambda_cost']:g} "
            f"| {r['n_arrived']} | {r['n_completed']} | {r['completion_rate_final']:.3f} "
            f"| {r['n_eliminated']} | {r['n_unreachable']} | {r['n_infeasible_delivery']} "
            f"| {r['on_time_rate_final']:.3f} | {r['total_path_m']/1000:.0f} "
            f"| {r.get('obs_backlog_last')} | {r.get('obs_cap_t_first')}→{r.get('obs_cap_t_last')} "
            f"| {r.get('obs_n_sel_over_cap_t_last')} |"
        )
    L.append("")
    L.append("## 2. 假设 1 逐格判定（可完成的一定被完成）\n")
    L.append(f"主判据：淘汰 ≤ {elim_max}（排队中超期=可完成而未完成；不可达>0 合法，物理口径；"
             "周期末池内残留属运行边界，不计违反）。\n")
    L.append("| 格 | 完成率 | 淘汰 | 不可达 | 残留 | 完成/配额 | 判定 |")
    L.append("|---|---:|---:|---:|---:|---:|---|")
    for r in rows:
        ok = (r["n_eliminated"] or 0) <= elim_max
        quota = r.get("obs_sel_total") or 0
        fill = f"{r['n_completed']}/{quota}" if quota else "-"
        L.append(f"| {r['tag']} | {r['completion_rate_final']:.3f} | {r['n_eliminated']} "
                 f"| {r['n_unreachable']} | {r['n_remaining_pool']} "
                 f"| {fill} | {'成立' if ok else '**被证伪**'} |")
    L.append("")
    L.append("> 归因注记：若「完成/配额」接近跑满（≥90%）而完成率仍 <1，未完成根因为"
             "**产能饿死**（到达率 > 选择名额 ⌈max_ratio·K⌉），非适应度用成本换覆盖"
             "（λ·path 仍仅 m·F1 的 ~1e-3）。\n")
    # 积压格按 λ_cost 配对比较（用户 2026-10-22 判定规则的精化版）
    hi = cfg.get("criteria", {}).get("h1_backlog", {}).get("arrival_lambda")
    L.append("## 3. λ_cost 配对比较（同到达率下 1e-1 vs 1e-4）\n")
    L.append("判据（预注册 paired_lambda_rule）：λ=1e-1 完成率不低于 λ=1e-4 且淘汰不增 "
             "⇒ 积压域安全，可作生产默认候选；否则回退「完成优先 + 较高 λ」。\n")
    L.append("| 到达λ | 完成率 1e-4→1e-1 | Δ完成率 | 淘汰 1e-4→1e-1 | 航程km 1e-4→1e-1 | Δ航程 | 判定 |")
    L.append("|---:|---:|---:|---:|---:|---:|---|")
    for lam in cfg["matrix"]["arrival_lambda"]:
        g_lo = next((r for r in rows if r["arrival_lambda"] == lam
                     and abs(r["lambda_cost"] - 1e-4) < 1e-12), None)
        g_hi = next((r for r in rows if r["arrival_lambda"] == lam
                     and abs(r["lambda_cost"] - 1e-1) < 1e-12), None)
        if g_lo is None or g_hi is None:
            L.append(f"| {lam:g} | 缺格 | - | - | - | - | 待补跑 |")
            continue
        d_comp = (g_hi["completion_rate_final"] or 0) - (g_lo["completion_rate_final"] or 0)
        d_elim = (g_hi["n_eliminated"] or 0) - (g_lo["n_eliminated"] or 0)
        d_path = ((g_hi["total_path_m"] or 0) - (g_lo["total_path_m"] or 0)) / 1000.0
        safe = d_comp >= -1e-9 and d_elim <= 0
        L.append(
            f"| {lam:g} | {g_lo['completion_rate_final']:.3f}→{g_hi['completion_rate_final']:.3f} "
            f"| {d_comp:+.3f} | {g_lo['n_eliminated']}→{g_hi['n_eliminated']} "
            f"| {g_lo['total_path_m']/1000:.0f}→{g_hi['total_path_m']/1000:.0f} "
            f"| {d_path:+.0f} | {'**安全**' if safe else '**不安全，需回退**'} |")
    L.append("")
    if hi is not None:
        L.append(f"## 4. 积压格（arrival_lambda={hi:g}）形态\n")
        for lc in cfg["matrix"]["lambda_cost"]:
            grp = [r for r in rows if r["arrival_lambda"] == hi and r["lambda_cost"] == lc]
            if not grp:
                continue
            for r in grp:
                L.append(f"- λ_cost={lc:g}：完成率 {r['completion_rate_final']:.3f}、"
                         f"淘汰 {r['n_eliminated']}、不可达 {r['n_unreachable']}、"
                         f"残留 {r['n_remaining_pool']}、按时率 {r['on_time_rate_final']:.3f}、"
                         f"平均迟到 {r['avg_lateness_s']:.0f}s、"
                         f"积压末值 {r.get('obs_backlog_last')}、"
                         f"solve {r['solve_time_ms']/1000/60:.0f}min")
        L.append("")
    (HERE / "JOINT_REGIME_REPORT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[exp06] 报告 → JOINT_REGIME_REPORT.md")


def main() -> None:
    ap = argparse.ArgumentParser(description="exp06_joint_regime 矩阵编排")
    ap.add_argument("--Ks", default=None, help="逗号分隔覆盖 matrix.K")
    ap.add_argument("--arrivals", default=None, help="逗号分隔覆盖 matrix.arrival_lambda")
    ap.add_argument("--lcs", default=None, help="逗号分隔覆盖 matrix.lambda_cost")
    ap.add_argument("--seeds", default=None, help="逗号分隔覆盖 experiment.seeds")
    ap.add_argument("--smoke", action="store_true", help="plumbing 冒烟（3 代 × 3 周期）")
    ap.add_argument("--analyze-only", action="store_true", help="不跑，只聚合")
    args = ap.parse_args()

    cfg = _load_cfg()
    if args.Ks:
        cfg["matrix"]["K"] = [int(x) for x in args.Ks.split(",")]
    if args.arrivals:
        cfg["matrix"]["arrival_lambda"] = [float(x) for x in args.arrivals.split(",")]
    if args.lcs:
        cfg["matrix"]["lambda_cost"] = [float(x) for x in args.lcs.split(",")]
    if args.seeds:
        cfg["experiment"]["seeds"] = [int(x) for x in args.seeds.split(",")]

    n_cycles = int(cfg.get("overrides", {}).get("rolling", {}).get("n_cycles", 24))
    if not args.analyze_only:
        for K in cfg["matrix"]["K"]:
            for lam in cfg["matrix"]["arrival_lambda"]:
                for lc in cfg["matrix"]["lambda_cost"]:
                    for seed in cfg["experiment"]["seeds"]:
                        run_cell(K, lam, lc, seed, n_cycles, args.smoke)
        print(f"\n[exp06] 全部格完成 {args and ''}")
    aggregate(cfg)


if __name__ == "__main__":
    main()
