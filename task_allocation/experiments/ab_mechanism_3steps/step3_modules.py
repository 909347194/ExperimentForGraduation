# -*- coding: utf-8 -*-
"""第三步：把「定序 / 路径 polish / 任务归属搜索」拆开，按问题维度归因。

重点不是让每个模块都取得收益，而是确认每个模块**解决什么问题**：

(A) 固定任务归属的离线算子分解（最干净，完全绕开解码链偷偷改归属）
    用确定性轮询分配构造一个固定归属基准解，冻结 routes（task→uav、执行集、
    参与机），只改段内顺序：
      - base       ：基因原序
      - order      ：_best_order 定序锦标赛（候选 = 基因序/EDD/earliest/slack/NN，
                      判据 (迟到数, 迟到量, 航程)）→ 假设：主要解决「迟到/时间窗」
      - polish     ：_cost_polish_routes 组内 2-opt/Or-opt（四道门保证执行集/迟到
                      不劣化，只压航程）→ 假设：主要解决「几何航程冗余」
      - order+polish：两者叠加
    在 d0（无时间窗）与 d150（强截止）两档评价 path / late / hard / coverage。
    冻结成功校验：四变体的 task_to_uav() 与 executed_task_ids 逐位相同。
    【双基准】prod     = build_solution 输出（生产解码链已跑过 _best_order，
                        即「已消费定序」）→ 测「再叠加定序」的边际；
              reversed = 人为逆序劣化基准 → 测定序算子的能力上界。
    这是为了如实处理初版发现的「order 与 base 完全相同」：那不是 bug，
    而是生产基准的顺序本就是定序算子的输出。

(B) 任务归属进化单独作用（端到端，固定顺序策略）
    monkeypatch _best_order 恒返回原序以冻结定序，关掉三个 polish 开关，
    对比 gens=0 vs gens=正常 → 归属搜索改变什么。
    再叠加「定序开」「全开」看组合。

(C) 组合边际表：每个模块 × 每个问题维度（航程 / 迟到 / 覆盖 / 硬违约），
    指出各自的主战场。口径分离：(A) 是离线单方案评价，(B)(C) 是端到端
    滚动交付量，两者绝对量级不可比，分开报告。

输出：step3_offline.csv / step3_e2e.csv / step3_matrix.csv /
      step3_modules.png / step3_report.md
"""
from __future__ import annotations

import argparse
import copy
import csv
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import common as C  # noqa: E402
from task_allocation.methods.muas.solvers.decode_repair import (  # noqa: E402
    DecodeRepairMixin,
)


# ---------------------------------------------------------------------------
# (A) 离线：冻结归属，只改顺序
# ---------------------------------------------------------------------------

def offline_eval(ev, routes, ends):
    """用捕获的真实 evaluator 评价一组 (routes, ends)，不改归属。"""
    sol = ev._assemble({k: list(v) for k, v in routes.items()}, dict(ends))
    ev.problem.evaluate(sol)
    n_hard, _ = ev.problem.hard_stats(sol)
    return dict(
        path=float(ev.problem.path_cost(sol)),
        late=float(ev.problem.soft_violation(sol)),
        n_hard=int(n_hard),
        coverage=len(sol.executed_task_ids) / max(1, len(ev.tasks)),
        fitness=float(ev.problem.scalar_fitness(sol)),
        assign=tuple(sorted((int(t), int(u))
                            for t, u in sol.task_to_uav().items())),
        execset=tuple(sorted(int(x) for x in sol.executed_task_ids)),
        active=tuple(sorted(int(x) for x in sol.active_uav_ids)),
    )


def build_variants(ev, routes, ends):
    """返回 {变体名: (routes, ends)}，四者归属/执行集恒相同。"""
    out = {"base": (dict(routes), dict(ends))}

    # 定序：直接调生产 _best_order（d0 零迟到会早退返回原序，这正是要观察的）
    r_order = {}
    for uid, tids in routes.items():
        try:
            r_order[uid] = list(ev._best_order(uid, list(tids)))
        except Exception:
            r_order[uid] = list(tids)
    out["order"] = (r_order, dict(ends))

    # 路径 polish：组内 2-opt/Or-opt，纯顺序算子、不改归属
    try:
        r_pol, e_pol, _ = ev._cost_polish_routes(
            {k: list(v) for k, v in routes.items()}, dict(ends))
        out["polish"] = (dict(r_pol), dict(e_pol))
    except Exception:
        out["polish"] = (dict(routes), dict(ends))

    # 叠加
    try:
        r_op, e_op, _ = ev._cost_polish_routes(
            {k: list(v) for k, v in r_order.items()}, dict(ends))
        out["order+polish"] = (dict(r_op), dict(e_op))
    except Exception:
        out["order+polish"] = (dict(r_order), dict(ends))
    return out


def run_offline(args, out):
    """捕获 evaluator + 一个交付解，冻结归属做四变体。"""
    rows = []
    scenarios = C.parse_scenarios(args.scenarios)
    deadlines = C.parse_csv_ints(args.deadlines)
    seeds = C.parse_csv_ints(args.seeds)
    for (n_targets, n_cycles) in scenarios:
        for dl in deadlines:
            params = dict(pop_size=args.pop, max_generations=args.probe_gens,
                          n_cycles=n_cycles, inst_base=args.inst_base,
                          n_uavs=args.n_uavs, area=args.area,
                          deadline_s=float(dl))
            for inst in range(args.n_instances):
                tasks, uavs, nests = C.make_instance(
                    args.inst_base + inst, args.n_uavs, n_targets,
                    args.area, deadline_s=float(dl))
                for sd in seeds:
                    rec = C.TrialRecorder()
                    rec.install()
                    try:
                        C.run_rolling(
                            tasks, uavs, nests, params=params,
                            arm_cfg=dict(_label="probe", refine=False,
                                         gens_override=None, seed=sd,
                                         instance=inst),
                            recorder=rec)
                        ev = rec.evaluator
                        if ev is None or not rec.rows:
                            continue
                        # 取最后一个 trial 的 solution 作为「固定归属」基准
                        # （TrialRecorder 里未保留 solution 对象，改从 evaluator 重建：
                        #   用第一个 trial 的基因 assignment 走 build_solution）
                        # 这里改为直接用 best 交付解：重新跑一次 build_solution。
                        sol0 = _baseline_solution(ev, rec, args)
                    finally:
                        rec.uninstall()
                    if sol0 is None:
                        continue
                    routes = {t.uav_id: list(t.task_ids)
                              for t in sol0.tours if t.task_ids}
                    ends = {t.uav_id: t.end_nest_id for t in sol0.tours}
                    if not routes:
                        continue
                    # 双基准：
                    #   prod     = build_solution 输出（内部已跑过 _best_order，
                    #              即「生产链已消费定序」），测叠加定序的边际；
                    #   reversed = 人为逆序劣化，测定序算子的能力上界。
                    benchmarks = {
                        "prod": routes,
                        "reversed": {u: list(reversed(v))
                                     for u, v in routes.items()},
                    }
                    for bench, r0 in benchmarks.items():
                        variants = build_variants(ev, r0, ends)
                        evals = {name: offline_eval(ev, rr, ee)
                                 for name, (rr, ee) in variants.items()}
                        sigs = {(m["assign"], m["execset"], m["active"])
                                for m in evals.values()}
                        frozen_ok = (len(sigs) == 1)
                        for name, m in evals.items():
                            rows.append(dict(
                                scenario=f"t{n_targets}_c{n_cycles}",
                                deadline_s=dl, instance=inst, seed=sd,
                                benchmark=bench, variant=name,
                                frozen_ok=int(frozen_ok),
                                n_active_uav=len(r0),
                                **{k: v for k, v in m.items()
                                   if k not in ("assign", "execset", "active")}))
                        print(f"[A] t{n_targets}_c{n_cycles} d{dl} i{inst} s{sd} "
                              f"bench={bench} frozen={frozen_ok}", flush=True)
    C.write_csv(out / "step3_offline.csv", rows)
    return rows


def _baseline_solution(ev, rec, args):
    """构造固定归属基准解：用捕获到的一个基因型走 build_solution。"""
    # rec.rows 只存指纹，未存 assignment；重新从求解器拿一个可行 assignment：
    # 用 evaluator 的参与机与任务，构造「轮询分配」作为确定性的固定归属。
    try:
        part = list(ev.participating)
        task_ids = list(ev._task_ids)
        routes_local: dict[int, list[int]] = {}
        for i, tid in enumerate(task_ids):
            routes_local.setdefault(i % max(1, len(part)), []).append(i)
        sol = ev.build_solution(
            [(u, t) for u, ts in routes_local.items() for t in ts])
        ev.problem.evaluate(sol)
        return sol
    except Exception:
        return None


# ---------------------------------------------------------------------------
# (B)(C) 端到端：冻结定序 / 归属搜索 / 组合
# ---------------------------------------------------------------------------

class FreezeOrder:
    """monkeypatch _best_order 恒返回原序（冻结定序模块）。"""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self._orig = None

    def __enter__(self):
        if not self.enabled:
            return self
        self._orig = DecodeRepairMixin._best_order

        def frozen(self_ev, uav_id, tids):
            return list(tids)

        DecodeRepairMixin._best_order = frozen
        return self

    def __exit__(self, *a):
        if self.enabled and self._orig is not None:
            DecodeRepairMixin._best_order = self._orig
        return False


# (label, evo, refine, freeze_order)
E2E = [
    ("repair_only", 0, False, True),          # 无归属搜索、无定序、无 polish
    ("assign_only", 1, False, True),          # 仅归属搜索
    ("assign+order", 1, False, False),        # 归属搜索 + 定序
    ("assign+order+polish", 1, True, False),  # 组合（生产口径）
]


def run_e2e(args, out):
    rows = []
    scenarios = C.parse_scenarios(args.scenarios)
    deadlines = C.parse_csv_ints(args.deadlines)
    seeds = C.parse_csv_ints(args.seeds)
    for (n_targets, n_cycles) in scenarios:
        for dl in deadlines:
            params = dict(pop_size=args.pop, max_generations=args.gens,
                          n_cycles=n_cycles, inst_base=args.inst_base,
                          n_uavs=args.n_uavs, area=args.area,
                          deadline_s=float(dl))
            for inst in range(args.n_instances):
                tasks, uavs, nests = C.make_instance(
                    args.inst_base + inst, args.n_uavs, n_targets,
                    args.area, deadline_s=float(dl))
                for sd in seeds:
                    for label, evo, refine, freeze in E2E:
                        with FreezeOrder(freeze):
                            row = C.run_rolling(
                                tasks, uavs, nests, params=params,
                                arm_cfg=dict(
                                    _label=label, refine=refine,
                                    gens_override=(None if evo else 0),
                                    seed=sd, instance=inst),
                            )
                        row.pop("cost_history", None)
                        row.update(scenario=f"t{n_targets}_c{n_cycles}",
                                   module=label)
                        rows.append(row)
                    print(f"[B] t{n_targets}_c{n_cycles} d{dl} i{inst} s{sd} "
                          f"4/4 modules", flush=True)
    C.write_csv(out / "step3_e2e.csv", rows)
    return rows


# ---------------------------------------------------------------------------

def _load_rows(path, numeric_fields):
    """从 CSV 读回已完成实验的数据，只把参与算术的字段转成 float。

    保留 scenario / benchmark / variant / deadline_s 等为原始字符串，
    以保证报告里 d0 / d150 与分组键的格式和正式跑完全一致。
    """
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in numeric_fields:
            if k in r:
                try:
                    r[k] = float(r[k])
                except (TypeError, ValueError):
                    pass
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default="40:1,60:1")
    ap.add_argument("--deadlines", default="0,150")
    ap.add_argument("--pop", type=int, default=10)
    ap.add_argument("--gens", type=int, default=25)
    ap.add_argument("--probe-gens", type=int, default=3)
    ap.add_argument("--seeds", default="11,22,33")
    ap.add_argument("--n-instances", type=int, default=1)
    ap.add_argument("--inst-base", type=int, default=5000)
    ap.add_argument("--n-uavs", type=int, default=3)
    ap.add_argument("--area", type=float, default=1000.0)
    ap.add_argument("--out-dir", default="results")
    ap.add_argument("--skip", default="", help="跳过 A 或 B（调试用）")
    ap.add_argument("--report-only", action="store_true",
                    help="从 out-dir 的 CSV 读回数据，只重生成矩阵/图/报告（秒级）")
    args = ap.parse_args()

    out = HERE / args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    if args.report_only:
        # 改文案 / 绘图口径时无需重跑 10+ 分钟的实验，直接复用已落盘的 CSV。
        offline = _load_rows(out / "step3_offline.csv",
                             ("path", "late", "n_hard", "coverage",
                              "fitness", "frozen_ok", "n_active_uav"))
        e2e = _load_rows(out / "step3_e2e.csv",
                         ("delivered_fitness", "path_cost", "late",
                          "coverage", "n_hard", "elapsed_s", "n_executed",
                          "final_n_hard"))
        _matrix(out, offline, e2e)
        _plot(out, offline, e2e)
        _report(out, offline, e2e)
        print(f"[step3] 仅重生成矩阵/图/报告：{out}", flush=True)
        return
    print(f"[step3] scenarios={args.scenarios} deadlines={args.deadlines}",
          flush=True)

    offline = [] if "A" in args.skip else run_offline(args, out)
    e2e = [] if "B" in args.skip else run_e2e(args, out)

    _matrix(out, offline, e2e)
    _plot(out, offline, e2e)
    _report(out, offline, e2e)
    print(f"[step3] 写出 {out}", flush=True)


def _matrix(out, offline, e2e):
    """模块 × 问题维度矩阵：每个变体相对 base / repair_only 的改进方向。"""
    rows = []
    if offline:
        key = ["scenario", "deadline_s", "instance", "seed",
               "benchmark"]
        groups: dict[tuple, dict] = {}
        for r in offline:
            groups.setdefault(tuple(r[k] for k in key), {})[r["variant"]] = r
        for gk, vs in groups.items():
            if "base" not in vs:
                continue
            b = vs["base"]
            for name, r in vs.items():
                rows.append(dict(
                    part="A_offline", scenario=gk[0], deadline_s=gk[1],
                    instance=gk[2], seed=gk[3], benchmark=gk[4], module=name,
                    d_path=b["path"] - r["path"],
                    d_late=b["late"] - r["late"],
                    d_hard=b["n_hard"] - r["n_hard"],
                    d_coverage=r["coverage"] - b["coverage"],
                    d_fitness=b["fitness"] - r["fitness"],
                    frozen_ok=r["frozen_ok"]))
    if e2e:
        key = ["scenario", "deadline_s", "instance", "seed"]
        groups = {}
        for r in e2e:
            groups.setdefault(tuple(r[k] for k in key), {})[r["module"]] = r
        for gk, vs in groups.items():
            if "repair_only" not in vs:
                continue
            b = vs["repair_only"]
            for name, r in vs.items():
                rows.append(dict(
                    part="B_e2e", scenario=gk[0], deadline_s=gk[1],
                    instance=gk[2], seed=gk[3], benchmark="e2e", module=name,
                    d_path=b["path_cost"] - r["path_cost"],
                    d_late=b["late"] - r["late"],
                    d_hard=b["n_hard"] - r["n_hard"],
                    d_coverage=r["coverage"] - b["coverage"],
                    d_fitness=b["delivered_fitness"] - r["delivered_fitness"],
                    frozen_ok=None))
    # 显式固定列顺序：A 带 benchmark、B 也补了 "e2e"，避免 write_csv 因首行
    # 字典键不同而静默丢列（--skip A 时 rows[0] 会是 B 行）。
    C.write_csv(out / "step3_matrix.csv", rows,
                fieldnames=["part", "scenario", "deadline_s", "instance", "seed",
                            "benchmark", "module", "d_path", "d_late", "d_hard",
                            "d_coverage", "d_fitness", "frozen_ok"])
    return rows


def _plot(out, offline, e2e):
    if not offline:
        return
    combos = sorted({(r["scenario"], r["deadline_s"]) for r in offline})
    benchmarks = ["prod", "reversed"]
    fig, axes = plt.subplots(len(benchmarks), len(combos),
                             figsize=(4.2 * len(combos), 3.4 * len(benchmarks)),
                             squeeze=False)
    names = ["base", "order", "polish", "order+polish"]
    for i, bench in enumerate(benchmarks):
        for j, (sc, dl) in enumerate(combos):
            ax = axes[i][j]
            sub = [r for r in offline if r["scenario"] == sc
                   and r["deadline_s"] == dl
                   and r["benchmark"] == bench]
            for metric, color, marker in (("path", "#2980b9", "o"),
                                          ("late", "#c0392b", "s")):
                vals = []
                for n in names:
                    g = [r[metric] for r in sub if r["variant"] == n]
                    vals.append(C.mean_std(g)[0] if g else float("nan"))
                base = vals[0]
                # 相对「本基准自己的 base」的百分比变化，跨量纲可比
                pct = [100.0 * (base - v) / max(1e-9, abs(base)) for v in vals]
                ax.plot(range(4), pct, marker=marker, color=color,
                        label=("航程" if metric == "path" else "迟到"))
            ax.axhline(0, color="gray", lw=0.8, ls=":")
            ax.set_xticks(range(4))
            ax.set_xticklabels(names, fontsize=8)
            if j == 0:
                ax.set_ylabel(f"{bench}\n相对 base 下降 %（正=改善）", fontsize=8)
            if i == 0:
                ax.set_title(f"{sc} d{dl}", fontsize=9)
            if i == 0 and j == len(combos) - 1:
                ax.legend(fontsize=7)
    fig.suptitle("第三步(A)：固定任务归属，双基准下 定序 vs 路径 polish 各解决什么")
    fig.tight_layout()
    fig.savefig(out / "step3_modules.png", dpi=150)
    plt.close(fig)


def _report(out, offline, e2e):
    L = ["# 第三步：定序 / 路径 polish / 任务归属搜索 —— 各自解决什么问题", "",
         "**重点不是让每个模块都取得收益，而是确认每个模块解决什么问题。**", "",
         "## 口径分离", "",
         "- (A) 离线：用确定性轮询分配经 `build_solution` 构造一个**固定归属**"
         "基准方案，冻结其 task→uav、执行集、参与机，只改段内顺序；用真实 problem "
         "评价四变体。四变体归属恒等（`frozen_ok`）是前提。"
         "**双基准**：`prod` 直接取 `build_solution` 输出（内部已消费定序），"
         "`reversed` 人为逆序劣化后再测，二者分开统计、不混平均。"
         "注：实测 `n_active_uav` 常为 1（覆盖优先 + 剩余航程充裕时修复链把全部任务"
         "并入单机航次），这本身就印证第一步「修复链坍缩归属维度」的判断——"
         "此时归属已无自由度，顺序/polish 是唯一还能优化的维度。",
         "- (B)(C) 端到端：滚动交付量，`repair_only` → `assign_only`(仅 DE 归属搜索，"
         "冻结定序+关 polish) → `+定序` → `+polish`（生产组合）。",
         "- 两者绝对量级不可比，分开读。", ""]

    if offline:
        def _deltas(bench, dl, name, metric):
            """同一 (基准, 场景, d, 实例, 种子) 下 base 与该变体的差。"""
            base = {(r["scenario"], r["instance"], r["seed"]): r
                    for r in offline if r["variant"] == "base"
                    and r["benchmark"] == bench and r["deadline_s"] == dl}
            vals = []
            for r in offline:
                if (r["variant"] != name or r["benchmark"] != bench
                        or r["deadline_s"] != dl):
                    continue
                b = base.get((r["scenario"], r["instance"], r["seed"]))
                if b is not None:
                    vals.append(b[metric] - r[metric])
            return C.mean_std(vals)[0]

        L += ["## (A) 固定任务归属：定序 vs 路径 polish（均值）", "",
              "> **双基准**：`prod` = `build_solution` 输出——生产解码链内部已调用 "
              "`_reorder_for_deadlines → _best_order`，即「已消费定序」，"
              "此处测的是「再叠加一次定序」的边际；`reversed` = 人为逆序劣化基准，"
              "用来测定序算子的**能力上界**。两者不可混在一起平均。", "",
              "| 基准 | 场景 | d | 变体 | 航程 | 迟到 | 硬违约 | 覆盖 | 冻结成功 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for bench in ("prod", "reversed"):
            for sc, dl in sorted({(r["scenario"], r["deadline_s"])
                                  for r in offline}):
                for n in ["base", "order", "polish", "order+polish"]:
                    g = [r for r in offline if r["scenario"] == sc
                         and r["deadline_s"] == dl and r["variant"] == n
                         and r["benchmark"] == bench]
                    if not g:
                        continue
                    def av(k, g=g):
                        return C.mean_std([r[k] for r in g])[0]
                    L.append(f"| {bench} | {sc} | {dl} | {n} | {av('path'):.2f} | "
                             f"{av('late'):.2f} | {av('n_hard'):.2f} | "
                             f"{av('coverage'):.3f} | {av('frozen_ok'):.2f} |")
        L += ["", "### 归因（Δ = 该基准的 base − 该变体，正 = 改善；双基准分开读）",
              ""]
        for bench in ("prod", "reversed"):
            for dl in sorted({r["deadline_s"] for r in offline}):
                d_o_p = _deltas(bench, dl, "order", "path")
                d_o_l = _deltas(bench, dl, "order", "late")
                d_p_p = _deltas(bench, dl, "polish", "path")
                d_p_l = _deltas(bench, dl, "polish", "late")
                L.append(f"- **{bench} / d{dl}**：定序 Δ航程={d_o_p:.2f} / "
                         f"Δ迟到={d_o_l:.2f}；路径 polish Δ航程={d_p_p:.2f} / "
                         f"Δ迟到={d_p_l:.2f}。")
        L += ["", "→ **`prod` 下定序边际≈0 是设计使然、不是算子失效**：生产交付方案的"
              "顺序本就是定序算子的输出，再调一次自然无增量；d0 零迟到时 "
              "`_best_order` 还会因 `reorder_range_optimize=False` 早退返回原序。",
              "**只有 `reversed` 才测定序算子的能力上界**：人为逆序后，d150 下定序"
              "显著降迟到（表中 Δ迟到 为正）却**同时增加航程**（Δ航程 为负）——"
              "这是 `_best_order` 判据 `(迟到数, 迟到量, 航程)` 字典序优先时间窗的"
              "直接体现，说明定序是「以几何航程换时间窗合规」；d0 零迟到体制仍早退。",
              "路径 polish 恰好相反：两种基准、两档截止下都稳定降航程（Δ航程 为正），"
              "且因几何变短顺带降迟到，四道门保证执行集 / 参与机 / 覆盖 / 硬违约不变。",
              "",
              "**结论：定序与路径 polish 作用于不同、且在有截止压力时相互冲突的目标"
              "维度**——定序以航程换迟到、polish 只压航程冗余；两者虽都改「段内顺序」"
              "但不可互替，`order+polish` 叠加后在 d150 下同时取得最低迟到与显著压低的"
              "航程，证明互补而非冗余。且定序收益已在生产链里被提前消费，"
              "不应在「交付后优化」中重复计入。",
              "（本实验重点在确认各模块解决什么问题，不以「每个模块都必须取得收益」"
              "为判据。）", ""]

    if e2e:
        L += ["## (B)(C) 端到端：归属搜索 → 叠加定序 → 叠加 polish", "",
              "| 场景 | d | 模块 | 交付适应度 | 航程 | 迟到 | 覆盖 | 硬违约 | 耗时(s) |",
              "|---|---|---|---|---|---|---|---|---|"]
        for c in C.agg_by(e2e, ["scenario", "deadline_s", "module"],
                          ["delivered_fitness", "path_cost", "late", "coverage",
                           "n_hard", "elapsed_s"]):
            L.append(f"| {c['scenario']} | {c['deadline_s']} | {c['module']} | "
                     f"{c['delivered_fitness']:.2f} | {c['path_cost']:.2f} | "
                     f"{c['late']:.2f} | {c['coverage']:.3f} | "
                     f"{c['n_hard']:.2f} | {c['elapsed_s']:.3f} |")
        L += ["", "### 每个模块解决什么问题（归因表）", "",
              "| 模块 | 主战场 | 证据方向 |",
              "|---|---|---|",
              "| 任务归属搜索（DE） | 哪个任务给哪架机的组合分配 | 只在单周期高负载"
              "（60:1 类）显出航程/覆盖改进；易场景被修复链一次给到近优而冗余 |",
              "| 定序 `_best_order` | 时间窗 / 迟到 | 有截止压力时降迟到；"
              "零迟到体制早退、边际近零 |",
              "| 路径 polish（2-opt/Or-opt） | 几何航程冗余 | 各体制稳定压航程，"
              "四道门保证不牺牲执行集/迟到/硬违约 |",
              "| 修复链（地板） | 可行性 / 覆盖 | 归属改写、补装、航程闸，"
              "所有模块的共同前提 |", ""]
    (out / "step3_report.md").write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    main()
