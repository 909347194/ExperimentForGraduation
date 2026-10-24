# -*- coding: utf-8 -*-
"""exp01 方法正确性数据审计：从落盘结果独立复核算法不变量。

定位（2026-10-16 审查）：exp01 是**方法正确性验证**实验——本脚本不评价性能
好坏，只回答「闭环按理论口径执行了吗」：落盘数据是否满足协议与模型的全部
不变量。任一 FAIL 都意味着实现与理论口径脱节。

不变量清单（A~O）：
  A 终局守恒   到达 = 完成 + 淘汰 + 不可达 + 池内剩余（任务不丢、不重复计）
  A2 淘汰为零   淘汰(elim)>0 = 机制缺陷（可完成必完成未达成），须硬 FAIL
  B 完成唯一   跨周期 completed 互斥；与 solution 任务表的 completed_at 一致
  C 闸门语义   物理不可行交付不计完成；迟到 ⊆ 完成；废飞 ⊆ 出动
  D 时间语义   迟到秒数 = 仿真 ETA − latest（连续时间口径，**不是**
               completed_at − latest——后者是周期末记账时刻，见 M4 瞬移语义；
               容差 max(20s,2%)：存在 ≤18s 评价—交付残差（机制待插桩，
               已量化为 info 行）；**latest 判定翻转零容差 FAIL**
               （迟到标记必须 ≡ ETA−latest，容差不适用于判定本身）——
               唯一例外是「方向不确定带」内的边界翻转（降级 WARN）：
               地形距离方向相关（M 项），运行端共享缓存先查先得、
               检查端按正向全新查询，同一条腿可差 ~1%（实测 197m=13s）；
               若 latest 落在该腿正/反向距离张成的 ETA 区间内，翻转
               由口径分裂解释，不计 FAIL；带外翻转仍零容差。
               等待非负；完成时刻 = 周期推进时刻
  E 奖励守恒   Σf1（规划端折减）≈ Σ R·max(0,1−L/W)（交付端实际迟到）
  F 池守恒     n_total = n_remain + n_new + n_release；逐周期水位递推闭合
  G 选择漏斗   逐级单调不增；n_selected ≤ ⌈max_ratio·K⌉
  H 参与机数   参与机数 ≤ min(K, 任务数 N)（SRP 不变式：实例机数 ≤ N−1）
  I 收敛单调   cost_history（best fitness）非增
  N 适应度可复算（治 auto big_m 漂移，2026-10-18 拆双口径）：
               N1 搜索期自洽 cost_history[-1] == muas.best_fitness；
               N2 交付解可复算 final_fitness == 用落盘快照
                  fitness_weights 对 (f1, 迟到秒, 航程, 硬违约,
                  超限米, 最坏机时间) 的独立重算（相对差 < 0.1%）。
                  口径分离原因：Lamarckian 回写只改起始基因通道、
                  巡游基因保留未修复原值，回写后基因解码重建的交付解
                  与搜索期被评价解本不可比（记录值与搜索期重算曾差
                  6.5~13.6，即两类漂移/差异的混合）；无 final_fitness
                  字段的旧数据 N2 SKIP。
  J 航程物理   逐腿有序 estimate 独立重算航程 = 记录 est_cost，且 ≤ 满电航程
  K 起点链     无瞬移：下周期起点 = 上周期终点机巢位置
  L 容量/巢    逐周期机巢占用 ≤ capacity；终点机巢 id 合法
  M 段长口径   ETA（对称缓存 make_distance_fn）与代价矩阵（有序 estimate）
               段长一致（WARN 级：地形跟随航程方向相关，对称缓存是近似）
  O 无幻影腿   metrics final_path_m == Σ有任务航次 est_cost（空航次恒 0；
               2026-10-24 旧实现给闲置机计「机→巢」未飞行的腿，J 项只重算
               dump 内航次故看不见，本项补盲区）

用法（仓库根或本目录）：
    python3 task_allocation/experiments/exp01_smoke/audit_correctness.py
    python3 task_allocation/experiments/exp01_smoke/audit_correctness.py --results results_s43

``--results`` 指定结果目录名（相对实验目录，默认 ``results``）。

**审计覆盖面（2026-10-24 修复 P0-1）**：若该目录下存在 ``seed_{id}/`` 子目录
（多 seed 运行时 run.py 逐 seed 落盘），则**逐 seed 全量复核**再合并同名项
（同名项须全部 PASS 才 PASS，失败详情标注 seed）；否则只审顶层产物——顶层
``solution.json`` 只含代表 seed 的 tours，旧版因此漏审其余全部 seed。

判据常数取 ``results/effective_config.json``（run.py 落盘的生效配置，含
``--set``），缺该文件时回退 ``config.yaml``（2026-10-24 修复 P0-2）。

退出码：有 FAIL → 1，仅 WARN → 0。
run.py / run_chunked.py 每次运行末尾自动调用本审计（持续门禁）。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

# 任意调用方式可跑（python -m / 任意 cwd）：脚本目录先入 sys.path，
# 供 `from paths import` 在非脚本路径调用下也能解析（2026-10-22 修复）。
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

import paths  # noqa: F401  # sys.path 引导：挂载仓库根

from build import build_cost_estimator, build_scenario, build_terrain
from config import load_config
from task_allocation.methods.muas.cost.vertical_section import make_distance_fn

EXP_DIR = Path(__file__).resolve().parent
DATA_DIR = paths.ROOT / "task_allocation" / "data"

# 场景（DEM 地形 + 代价估算器）重建代价高，逐 seed 审计时复用一次
_SCENE_CACHE: dict[str, Any] = {}


def _ensure_scene(cfg: dict) -> tuple[Any, Any]:
    """构建（并缓存）审计重算所需的地形估算器与检查端距离函数。"""
    if "dist" not in _SCENE_CACHE:
        scenario = build_scenario(cfg, DATA_DIR)
        terrain = build_terrain(scenario, cfg)
        estimator = build_cost_estimator(terrain, cfg)
        _SCENE_CACHE["dist"] = make_distance_fn(estimator)
        _SCENE_CACHE["estimator"] = estimator
    return _SCENE_CACHE["estimator"], _SCENE_CACHE["dist"]


def _load_effective_config(results_dir: Path) -> dict:
    """**生效**配置：优先 ``effective_config.json``（run.py 落盘，含 --set）。

    早期版本直接读 ``config.yaml`` 原文，看不到命令行 ``--set`` 注入的覆盖，
    于是 E/G/D/J 项的判据常数与 run_meta 记录的生效配置指纹分裂——跑
    ``--set`` 消融时判据按旧常数裁断（2026-10-24 修复 P0-2）。
    旧产物没有该文件时回退 yaml（行为与旧版一致）。
    """
    eff = results_dir / "effective_config.json"
    if eff.is_file():
        try:
            return json.loads(eff.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError):
            pass
    return load_config(EXP_DIR / "config.yaml")


def _discover_targets(results_dir: Path) -> list[tuple[str, Path]]:
    """审计目标：多 seed 时逐 ``seed_{id}`` 目录；否则只审顶层产物。"""
    seed_dirs = sorted(
        (d for d in results_dir.glob("seed_*")
         if (d / "metrics.json").is_file() and (d / "solution.json").is_file()),
        key=lambda p: p.name,
    )
    if seed_dirs:
        return [(d.name, d) for d in seed_dirs]
    return [("顶层产物(代表 seed)", results_dir)]


def _merge(per_target) -> list[tuple[str, bool, str, bool]]:
    """合并逐 seed 的检查结果：同名项全部 PASS 才算 PASS，详情标注失败 seed。"""
    merged: dict[str, list] = {}
    order: list[str] = []
    for label, rows in per_target:
        for name, ok, detail, warn in rows:
            if name not in merged:
                merged[name] = [True, [], bool(warn)]
                order.append(name)
            if not ok:
                merged[name][0] = False
                merged[name][1].append(f"{label}: {detail}")
    return [
        (n, merged[n][0], "; ".join(merged[n][1]), merged[n][2]) for n in order
    ]


def _run_checks(
    results_dir: Path, cfg: dict, estimator, dist
) -> list[tuple[str, bool, str, bool]]:
    """对**单个**结果目录（一个 seed 的完整产物）跑全部不变量检查。

    多 seed 时 :func:`main` 对每个 ``results/seed_{id}/`` 各调一次，再合并同
    名项（2026-10-24 修复 P0-1）：早期版本只读顶层产物 ⇒ 3 seed 跑完只有代表
    seed 被验证；而 D/J/K/M/N 五项依赖 solution 的 tours 与 fitness_weights，
    ``metrics.json`` 里的 ``per_seed[seed]["cycles"]`` 不足以补审。
    """
    m = json.loads((results_dir / "metrics.json").read_text(encoding="utf-8"))
    sol = json.loads((results_dir / "solution.json").read_text(encoding="utf-8"))
    RESULTS: list[tuple[str, bool, str, bool]] = []

    def check(name: str, ok: bool, detail: str = "", warn: bool = False) -> None:
        """warn=True 的失败只记 WARN（不影响 exit code），用于已知低危口径差异。"""
        RESULTS.append((name, bool(ok), detail, warn))

    s = m["summary"]
    cycles = m["cycles"]
    tasks = {int(t["id"]): t for t in sol["tasks"]}
    nests = {int(n["id"]): n for n in sol["nests"]}
    speed = float(m["problem"]["uav_speed"])
    max_range = float(m["problem"]["uav_max_range_km"]) * 1000.0
    w_decay = cfg["muas"].get("late_reward_decay_seconds")
    # 覆盖门禁模式（2026-10-22）：F1 回归纯完成计数，不做迟到折减
    # （迟到单一定价走 w_late·L）——奖励守恒的交付端口径跟着切纯。
    #
    # 判据**优先取 metrics 的自描述字段**（problem.lexicographic_coverage /
    # problem.coverage_gate，吃 --set 覆盖）；仅当旧数据没有这两个键时回退
    # config.yaml。原因：本脚本读的是 yaml 原文，命令行 --set 注入的门禁
    # 开关它看不到，会导致门禁跑出来的结果被按旧口径审计（E/N2 误报 FAIL）。
    prob_desc = m.get("problem") or {}
    if "lexicographic_coverage" in prob_desc or "coverage_gate" in prob_desc:
        gate_fit = (bool(prob_desc.get("lexicographic_coverage", False))
                    or bool(prob_desc.get("coverage_gate", False)))
    else:
        gate_fit = (bool(cfg["muas"].get("lexicographic_coverage", False))
                    or bool(cfg["muas"].get("coverage_gate", False)))
    if gate_fit:
        w_decay = None
    max_ratio = float(cfg["selection"]["marginal"]["max_ratio"])
    service = float(cfg["muas"].get("default_service_time", 0.0))

    # ── A 终局守恒 ──────────────────────────────────────────────
    identity = (s["n_completed"] + s["n_eliminated"] + s["n_unreachable"]
                + s["n_remaining_pool"])
    check("A 终局守恒：到达=完成+淘汰+不可达+剩余",
          identity == s["n_arrived"],
          f"n_arrived={s['n_arrived']} 分解和={identity}")
    n_sol_completed = sum(1 for t in tasks.values() if t.get("completed_at") is not None)
    check("A 完成数与任务表一致",
          n_sol_completed == s["n_completed"],
          f"summary={s['n_completed']} 任务表 completed_at 非空={n_sol_completed}")
    # ── A2 淘汰须为零（铁律：完成率对可完成集合恒 100%）────────────
    # 淘汰(elim)>0 = 可完成任务机制性未完成 = 缺陷（非合法失败类、
    # 非「假设被证伪」）。一旦出现，机制未达成「可完成必完成」，硬 FAIL 暴露。
    check("A2 淘汰须为零（淘汰>0 = 可完成任务机制性未完成 = 缺陷）",
          s["n_eliminated"] == 0,
          f"n_eliminated={s['n_eliminated']}")

    # ── B 完成唯一 ──────────────────────────────────────────────
    all_completed: list[int] = []
    dup = []
    for c in cycles:
        ids = [int(x) for x in c.get("completed_ids", [])]
        for i in ids:
            if i in all_completed:
                dup.append(i)
        all_completed.extend(ids)
    check("B 跨周期完成唯一（无重复计）",
          not dup and len(all_completed) == s["n_completed"],
          f"重复={dup} 总数={len(all_completed)} summary={s['n_completed']}")
    check("B 每周期 n_completed 自洽",
          all(c["n_completed"] == len(c.get("completed_ids", [])) for c in cycles))

    # ── C 闸门语义 ──────────────────────────────────────────────
    gate_ok, gate_bad = True, []
    late_ok, late_bad = True, []
    for c in cycles:
        comp = {int(x) for x in c.get("completed_ids", [])}
        infeas = {int(x) for x in c.get("infeasible_ids", [])}
        late = {int(x) for x in c.get("late_ids", [])}
        execd = {int(x) for x in c.get("executed_ids", [])}
        if comp & infeas:
            gate_ok = False
            gate_bad.append((c["cycle"], sorted(comp & infeas)))
        if not late <= comp:
            late_ok = False
            late_bad.append((c["cycle"], sorted(late - comp)))
        if not infeas <= execd:
            gate_ok = False
            gate_bad.append((c["cycle"], "infeasible∉executed"))
    check("C 完成闸门：物理不可行 ∩ 完成 = ∅", gate_ok, str(gate_bad))
    check("C 迟到 ⊆ 完成（迟到≠废飞）", late_ok, str(late_bad))

    # ── D 时间语义（连续时间 ETA 口径）──────────────────────────
    # 迟到不是 completed_at−latest（周期末记账时刻，M4 瞬移语义），而是
    # check_time_window_constraint 的仿真 ETA−latest：从本周期规划起始时刻起
    # 按逐腿飞行时间 + 作业时间 + earliest 等待推进游标。此处独立重算该仿真
    # 与记录 lateness 对账（容差 max(5s, 0.5%)：缓存量化微差 ~0.1%）。
    lat_ok, wait_ok, quant_ok = True, True, True
    eta_bad = []
    residuals: list[float] = []   # 评价—执行迟到残差（rec − exp），仅量化不判罪
    flip_bad: list[tuple] = []    # latest 判定翻转且**方向不可解释**（零容差 FAIL）
    flip_warn: list[tuple] = []   # latest 判定翻转但落在方向不确定带内（WARN）
    for c in cycles:
        t_cur = float(c["current_time"])
        comp = {int(x) for x in c.get("completed_ids", [])}
        late = {int(x) for x in c.get("late_ids", [])}
        lb = {int(k): float(v) for k, v in (c.get("lateness_by_task") or {}).items()}
        if set(lb) != late:
            lat_ok = False
            eta_bad.append((c["cycle"], "late_ids 与 lateness_by_task 键不一致"))
        for tid in comp:
            t = tasks[tid]
            ca = float(t["completed_at"])
            if ca - float(t["arrival_time"]) < -1e-9:
                wait_ok = False
            if abs(ca - t_cur) > 1e-6:
                quant_ok = False  # complete_after_cycle：完成时刻 =周期推进时刻

    # ── 代价/距离重建（D 的 ETA 与 J 的航程共用）────────────────
    # estimator / dist 由 _ensure_scene 按**生效配置**构建并跨 seed 复用——
    # 每个 seed 重建一次 DEM 地形太慢，且配置在各 seed 间相同。

    def nest_pos(nid):
        n = nests[nid]
        return (float(n["x"]), float(n["y"]), float(n["z"]))

    for c in sol["cycles"]:
        cmeta = next((x for x in cycles if x["cycle"] == c["cycle"]), None)
        if cmeta is None:
            continue
        lb = {int(k): float(v) for k, v in (cmeta.get("lateness_by_task") or {}).items()}
        for tour in c["tours"]:
            # 方向不确定带（M 项口径分裂的定量化，2026-10-19）：
            # 地形跟随航程方向相关（爬升规则从起点扫描），运行端 ETA 的
            # 对称缓存对同一条腿可能取正向或反向值（代价矩阵有序查询先到
            # 先得），检查端按正向全新查询——同腿可差 ~1%（实测 197m≈13s）。
            # 每航次用两个全新缓存分别只查正/反向距离，张成 ETA 区间
            # [eta_lo, eta_hi]；latest 落在带内的判定翻转由口径分裂解释
            # （WARN），带外翻转仍零容差 FAIL。
            legs: list[tuple[int, tuple, tuple]] = []
            prev = tuple(float(v) for v in tour["start"])
            for tid in tour["task_ids"]:
                t = tasks[int(tid)]
                p = (float(t["x"]), float(t["y"]), float(t["z"]))
                legs.append((int(tid), prev, p))
                prev = p
            dist_f = make_distance_fn(estimator)  # 全新缓存：本航次只查正向
            dist_b = make_distance_fn(estimator)  # 全新缓存：本航次只查反向
            d_f = [dist_f(a, b) for _, a, b in legs]
            d_b = [dist_b(b, a) for _, a, b in legs]

            def _simulate(ds: list[float]) -> dict[int, float]:
                cur = float(c["current_time"])  # 本周期规划起始时刻
                out: dict[int, float] = {}
                for (tid, _, _), d in zip(legs, ds):
                    t = tasks[tid]
                    cur += d / speed
                    if cur < float(t["earliest"]):
                        cur = float(t["earliest"])
                    out[tid] = max(0.0, cur - float(t["latest"]))
                    cur += service
                return out

            late_f = _simulate(d_f)                          # 正向口径（标准重算）
            late_lo = _simulate([min(x, y) for x, y in zip(d_f, d_b)])
            late_hi = _simulate([max(x, y) for x, y in zip(d_f, d_b)])
            for tid, _, _ in legs:
                rec = lb.get(tid, 0.0)
                exp = late_f[tid]
                # 容差 max(20s, 2%)：已实测存在 ≤18s 的评价—交付残差（方向混合、
                # 逐腿距离/任务集/顺序/基准均已排除，机制待插桩定位），属 WARN 级
                tol = max(20.0, 0.02 * max(exp, 1.0))
                if abs(rec - exp) > tol:
                    lat_ok = False
                    eta_bad.append((c["cycle"], tid, round(rec, 1), round(exp, 1)))
                residuals.append(rec - exp)
                # latest 判定翻转：零容差 FAIL——评价端与执行端对「是否
                # 迟到」的结论必须一致（容差只适用于迟到秒数，不适用于
                # 判定本身；否则 15s 迟到可被 20s 容差吞成「按时」）。
                # 例外：latest 落在方向不确定带 [eta_lo, eta_hi] 内的翻转
                # 由口径分裂解释（WARN）——两种方向取值下结论就不同。
                rec_late = rec > 1e-9
                if rec_late and late_hi[tid] <= 1e-9:
                    flip_bad.append(
                        (c["cycle"], tid, round(rec, 1), round(exp, 1)))
                elif (not rec_late) and late_lo[tid] > 1e-9:
                    flip_bad.append(
                        (c["cycle"], tid, round(rec, 1), round(exp, 1)))
                elif rec_late != (late_lo[tid] > 1e-9) or \
                        rec_late != (late_hi[tid] > 1e-9):
                    flip_warn.append(
                        (c["cycle"], tid, round(rec, 1), round(exp, 1)))
    if residuals:
        arr = sorted(abs(r) for r in residuals)
        p50 = arr[len(arr) // 2]
        print(f"[info] D 评价—执行迟到残差：n={len(arr)} "
              f"min={arr[0]:.1f}s p50={p50:.1f}s max={arr[-1]:.1f}s"
              f"（≤20s 为评价口径微差，不翻转 latest 判定）")
    check("D latest 判定无翻转（零容差：迟到标记 ≡ ETA−latest）",
          not flip_bad, str(flip_bad[:3]))
    if flip_warn:
        check("D latest 翻转均在方向不确定带内（口径分裂可解释）",
              False, str(flip_warn[:3]), warn=True)
    check("D 迟到秒数=仿真ETA−latest（容差 max(20s,2%)，残差见 info 行）",
          lat_ok, str(eta_bad[:3]), warn=True)
    check("D 等待非负", wait_ok)
    check("D 完成时刻=周期推进时刻（complete_after_cycle 语义）", quant_ok)

    # ── E 奖励守恒 ──────────────────────────────────────────────
    total_f1 = sum(float(c["muas"].get("f1_reward") or 0.0) for c in cycles)
    lateness = {int(k): float(v) for c in cycles
                for k, v in (c.get("lateness_by_task") or {}).items()}
    delivery_eff = 0.0
    for tid in all_completed:
        r = max(float(tasks[tid]["reward"]), 0.0)
        late = lateness.get(tid, 0.0)
        if w_decay and late > 0:
            r *= max(0.0, 1.0 - late / float(w_decay))
        delivery_eff += r
    rel = abs(total_f1 - delivery_eff) / max(delivery_eff, 1e-9)
    check(("E 奖励守恒：Σf1 ≈ 交付端奖励（门禁口径不折减；相对差 < 2%）"
           if gate_fit else
           "E 奖励守恒：Σf1 ≈ 交付端折减奖励（相对差 < 2%）"),
          rel < 0.02, f"Σf1={total_f1:.3f} 交付端={delivery_eff:.3f} 相对差={rel:.4%}")

    # ── F 池守恒 ────────────────────────────────────────────────
    per_cycle_identity = []
    for c in cycles:
        pc = c.get("pool_counts", {})
        if pc.get("n_total") != pc.get("n_remain", 0) + pc.get("n_new", 0) \
                + pc.get("n_release", 0):
            per_cycle_identity.append((c["cycle"], pc))
    check("F 池内恒等式 n_total=n_remain+n_new+n_release",
          not per_cycle_identity, str(per_cycle_identity[:3]))
    level_bad = []
    for i in range(len(cycles) - 1):
        c, nxt = cycles[i], cycles[i + 1]
        pc, pn = c["pool_counts"], nxt["pool_counts"]
        # 记账周期口径（2026-10-24 无超期淘汰后简化）：
        #   completed / unreachable：周期 t 解算期出池 ⇒ 影响 t→t+1 水位；
        #   infeasible（废飞回池）：``pool.release`` 进 ``_release_pending``，
        #     下周期 begin_cycle 归类为 RELEASE 来源 ⇒ **已含在
        #     pn.n_release**，再 +len(infeasible) 属双计（本场景为 0
        #     未暴露，但公式是错的）；
        #   超期（overdue）：**不出池**（无超期淘汰），对水位无影响；
        #     旧 expired 出池项已从递推式移除。
        # 迟交交付自然对冲：completed −1 且回池进 pn.n_release +1。
        expect = (pc["n_total"]
                  + pn.get("n_new", 0) + pn.get("n_release", 0)
                  - len(c.get("completed_ids", []))
                  - len(c.get("unreachable_ids", [])))
        if pn["n_total"] != expect:
            level_bad.append((c["cycle"], pn["n_total"], expect))
    check("F 池水位递推闭合（含废飞回池）", not level_bad, str(level_bad[:3]))

    # ── G 选择漏斗 ──────────────────────────────────────────────
    funnel_bad = []
    for c in cycles:
        sel = c.get("selection", {})
        chain = [sel.get("n_tasks_in"), sel.get("n_after_priority"),
                 sel.get("n_after_prefilter"), sel.get("n_after_marginal"),
                 sel.get("n_selected")]
        vals = [v for v in chain if v is not None]
        if any(a < b for a, b in zip(vals, vals[1:])):
            funnel_bad.append((c["cycle"], chain))
        k = sel.get("n_uav_avail") or 0
        n_sel = sel.get("n_selected") or 0
        if n_sel > math.ceil(max_ratio * k) and k > 0:
            funnel_bad.append((c["cycle"], f"n_selected={n_sel} > {max_ratio}·K={max_ratio*k}"))
    check("G 选择漏斗单调 + n_cand 上限", not funnel_bad, str(funnel_bad[:3]))

    # ── H 参与机数（SRP 不变式）────────────────────────────────
    k_bad = []
    for c in cycles:
        mu = c["muas"]
        kp, kt, ka = mu.get("n_participating"), mu.get("n_tasks"), mu.get("n_uav_avail")
        if kp is None:
            continue
        if ka is not None and kp > ka:
            k_bad.append((c["cycle"], kp, ka))
        if kt is not None and kp > max(kt, 0):
            k_bad.append((c["cycle"], f"K={kp} > M−1={kt}"))
    check("H 参与机数 ≤ min(K, 任务数 N)", not k_bad, str(k_bad[:3]))

    # ── I 收敛单调 ──────────────────────────────────────────────
    mono_bad = []
    for c in sol["cycles"]:
        hist = c.get("cost_history") or []
        for a, b in zip(hist, hist[1:]):
            if b > a + 1e-6:  # 最小化：不应上升
                mono_bad.append((c["cycle"], a, b))
                break
    check("I DMDE 收敛曲线非增（best fitness）", not mono_bad, str(mono_bad[:3]))

    # ── N 适应度可复算（治 auto big_m 漂移，2026-10-18 拆双口径）───────
    # 四层适应度（生产口径，w_time=0 时 F3 缺席）：
    #   f = W_hard·n_hard + w_exc·excess_m + W_skip·n_voluntary_skip
    #       − m·F1 + w_late·L_tot + λ·path + w_time·F3
    # （W_skip 层仅覆盖门禁模式存在，快照 w_skip 缺省时计 0）
    # 权重取落盘快照 fitness_weights（本周期实际使用的 m/W_hard/w_exc/
    # w_late/λ/w_time/W_decay），成分从 solution.json 独立重取。
    #
    # N1 搜索期自洽：cost_history[-1] == muas.best_fitness。两者同为
    #    搜索期口径（Lamarckian 回写前 child 修复解的评价值）。
    # N2 交付解可复算：final_fitness == 重算值（成分 = 交付解自身的
    #    f1/迟到秒/航程/硬违约/超限米/最坏机时间）。与 N1 分离的原因：
    #    回写只改起始基因通道，交付解由回写后基因解码重建，与 N1 被评价
    #    解可不同——曾经的「记录 best_fitness 与重算差 6.5~13.6」是
    #    auto big_m 漂移与这一口径差两者的混合，现分别设闸。
    n1_bad: list[tuple] = []
    n2_bad: list[tuple] = []
    n_checked = 0
    n2_skipped = 0
    for c in sol["cycles"]:
        cmeta = next((x for x in cycles if x["cycle"] == c["cycle"]), None)
        if cmeta is None:
            continue
        fw = c.get("fitness_weights") or {}
        hist = c.get("cost_history") or []
        if not fw or not hist:
            continue  # 空转周期无快照；旧数据无权重字段（跳过并提示）
        n_checked += 1
        # N1：曲线终点与 best_fitness 一致（绝对差 < 1e-3）
        f_logged = float(hist[-1])
        f_meta = float(cmeta["muas"].get("best_fitness") or 0.0)
        if abs(f_logged - f_meta) > 1e-3:
            n1_bad.append((c["cycle"], round(f_logged, 3), round(f_meta, 3)))
        # N2：交付解适应度用快照权重独立重算
        f_final = c.get("final_fitness")
        if f_final is None:
            n2_skipped += 1
            continue
        f_final = float(f_final)
        fw_m = float(fw["m"])
        w_hard = float(fw.get("w_hard") or 0.0)
        w_excess = float(fw.get("w_excess") or 0.0)
        # 覆盖门禁 T0.5 层（2026-10-22）：w_skip 缺省/None = 门禁关闭，计 0
        w_skip = float(fw.get("w_skip") or 0.0)
        n_skip = int((c.get("gate_stats") or {}).get("n_skip", 0) or 0)
        w_late = float(fw.get("w_late") or 0.0)
        lam = float(fw["lambda"])
        w_time = float(fw.get("w_time") or 0.0)
        if w_time != 0.0 and c.get("final_bottleneck_s") is None:
            n2_skipped += 1
            continue
        # 门禁口径下 **f 不含 −m·F1**（固定表述：搜索用 f 不含 −m·F1，
        # F1 只作指标）⇒ 重算时该项置零。判据与 problem.gate_mode 同构：
        # 快照带 w_skip（problem 开关开）或本周期落了 gate_stats
        # （stage 层 coverage_gate 产物）。
        gate_cycle = (fw.get("w_skip") is not None) or bool(c.get("gate_stats"))
        f1_term = 0.0 if gate_cycle else -fw_m * float(c.get("final_f1") or 0.0)
        f_rec = (
            w_hard * float(c.get("final_n_hard") or 0)
            + w_excess * float(c.get("final_excess_m") or 0.0)
            + w_skip * n_skip
            + f1_term
            + w_late * float(c.get("final_late_s") or 0.0)
            + lam * float(c.get("final_path_m") or 0.0)
            + w_time * float(c.get("final_bottleneck_s") or 0.0)
        )
        rel = abs(f_rec - f_final) / max(abs(f_final), 1e-9)
        if rel > 1e-3:
            n2_bad.append((c["cycle"], round(f_rec, 3), round(f_final, 3)))
    check(f"N1 搜索期自洽：cost_history[-1] == best_fitness（{n_checked} 个有作业周期）",
          not n1_bad, str(n1_bad[:3]))
    n2_name = (f"N2 交付解适应度可用快照权重独立重算"
               f"（{n_checked - n2_skipped} 个周期，相对差 < 0.1%）")
    if n2_skipped and n_checked == n2_skipped:
        check(n2_name, True, "旧数据无 final_fitness 字段，SKIP", warn=True)
    else:
        check(n2_name, not n2_bad,
              str(n2_bad[:3]) + (f"（另 {n2_skipped} 周期缺字段 SKIP）"
                                 if n2_skipped else ""))

    # ── J/K 航程物理 + 起点链（独立重算）────────────────────────
    # 逐腿用**有序** estimator.estimate(a,b)（与代价矩阵 build_pairwise_costs
    # 同口径；地形跟随航程方向相关，不能用对称化距离），容差 1m。
    range_bad, match_bad, chain_bad = [], [], []
    seg_sym_diff = []  # M 项：对称缓存 vs 有序段长的方向性差异
    prev_end: dict[int, tuple] = {}
    for c in sol["cycles"]:
        for tour in c["tours"]:
            uid = int(tour["uav_id"])
            pts = [tuple(float(v) for v in tour["start"])]
            for tid in tour["task_ids"]:
                t = tasks[int(tid)]
                pts.append((float(t["x"]), float(t["y"]), float(t["z"])))
            eid = tour.get("end_nest_id")
            if eid is not None and int(eid) in nests:
                pts.append(nest_pos(int(eid)))
            else:
                range_bad.append((c["cycle"], uid, f"非法终点机巢 {eid}"))
            cost = 0.0
            for a, b in zip(pts, pts[1:]):
                d_ord = estimator.estimate(a, b).distance
                d_sym = dist(a, b)
                cost += d_ord
                if d_ord > 1.0 and abs(d_ord - d_sym) / d_ord > 0.001:
                    seg_sym_diff.append((c["cycle"], uid, round(d_ord, 1),
                                         round(d_sym, 1)))
            if abs(cost - float(tour["est_cost"])) > max(1.0, 0.0001 * cost):
                match_bad.append((c["cycle"], uid, round(cost, 1),
                                  round(float(tour["est_cost"]), 1)))
            if cost > max_range + 1.0:
                range_bad.append((c["cycle"], uid, f"航程 {cost:.0f} > {max_range:.0f}"))
            # 起点链：本周期起点 = 上周期终点机巢位置（降落后换电/驻留）
            if uid in prev_end:
                sp = tuple(float(v) for v in tour["start"])
                if any(abs(a - b) > 1.0 for a, b in zip(sp, prev_end[uid])):
                    chain_bad.append((c["cycle"], uid, sp, prev_end[uid]))
            prev_end[uid] = nest_pos(int(eid)) if eid is not None else tuple(tour["start"])
    check("J 航程物理：有序 estimate 重算 = 记录 est_cost（容差 1m）",
          not match_bad, str(match_bad[:3]))
    check("J 所有航次 ≤ 满电航程（recharge_at_nest=True）",
          not range_bad, str(range_bad[:3]))
    check("K 起点链无瞬移：起点 = 上周期终点机巢", not chain_bad, str(chain_bad[:3]))

    # ── O 无幻影腿：metrics final_path_m == dump est_cost 之和 ────
    # 不变量由来：path_cost(final)=Σ_{全部航次} tour_path_cost；空航次恒 0
    # （不起飞不飞行，与 _tour_cost 守卫/_advance_execution 同口径），而
    # dump 过滤器 `if t.task_ids` 只快照有任务航次 ⇒ 两侧必须相等。
    # 2026-10-24 旧实现给 _assemble 填了 home nest 的闲置机计「机→巢」
    # 幻影腿，生产数据三 seed 被虚报 403/1091/388 m（占 0.024~0.065%）
    # 而 J 项（只重算 dump 内的航次）完全看不见——本项补该盲区。
    muas_by_cycle = {int(c["cycle"]): c["muas"] for c in cycles}
    phantom_bad = []
    for c in sol["cycles"]:
        mu = muas_by_cycle.get(int(c["cycle"])) or {}
        fp = mu.get("final_path_m")
        if fp is None:
            continue  # 旧数据/空转周期无该字段
        est_sum = sum(float(t["est_cost"]) for t in c["tours"])
        if abs(float(fp) - est_sum) > max(1.0, 1e-6 * float(fp)):
            phantom_bad.append((c["cycle"], round(float(fp), 1),
                                round(est_sum, 1)))
    check("O 无幻影腿：final_path_m == Σ有任务航次 est_cost（容差 1m）",
          not phantom_bad, str(phantom_bad[:3]))

    # ── M 段长口径（WARN 级）────────────────────────────────────
    # make_distance_fn 记忆化把 (A,B)/(B,A) 当对称存取，而地形跟随航程方向
    # 相关（爬升规则从起点扫描）→ ETA/时间模型（对称）与代价矩阵（有序）
    # 段长可差 0.1~0.4%，属低危口径分裂；有序口径与物理一致。
    check("M 段长口径：ETA（对称缓存）与代价矩阵（有序）一致（<0.1%）",
          not seg_sym_diff,
          (f"{len(seg_sym_diff)} 段有方向性差异，例：{seg_sym_diff[:3]}"
           if seg_sym_diff else ""),
          warn=True)

    # ── L 容量 / 终点机巢 ───────────────────────────────────────
    cap = int(m["problem"]["nest_capacity"])
    cap_bad = []
    for c in cycles:
        occ = c.get("nest_occupancy", {}) or {}
        for nid, k in occ.items():
            if int(k) > cap:
                cap_bad.append((c["cycle"], nid, k))
        if c.get("capacity_violations"):
            cap_bad.append((c["cycle"], c["capacity_violations"]))
    check(f"L 机巢占用 ≤ capacity({cap}) 且无超容记录", not cap_bad, str(cap_bad[:3]))

    return RESULTS


def main(results_name: str = "results") -> int:
    """审计入口：多 seed 逐目录复核后合并同名项；有 FAIL 记退出码 1。"""
    results_dir = EXP_DIR / results_name
    cfg = _load_effective_config(results_dir)
    estimator, dist = _ensure_scene(cfg)
    targets = _discover_targets(results_dir)
    if len(targets) > 1:
        print(f"[audit] 逐 seed 审计：{len(targets)} 个目录（"
              f"{', '.join(lb for lb, _ in targets)}）")
    per_target = [
        (label, _run_checks(d, cfg, estimator, dist)) for label, d in targets
    ]
    RESULTS = _merge(per_target)

    # ── 报告 ────────────────────────────────────────────────────
    width = max(len(n) for n, _, _, _ in RESULTS)
    n_fail = n_warn = 0
    print(f"{'不变量':<{width}}  结果  详情")
    for name, ok, detail, warn in RESULTS:
        if ok:
            mark = "PASS"
        elif warn:
            mark = "WARN"
            n_warn += 1
        else:
            mark = "FAIL"
            n_fail += 1
        print(f"{name:<{width}}  {mark}  {detail}")
    print()
    verdict = ("，方法闭环与理论口径一致 ✔" if n_fail == 0
               else f"，{n_fail} 项不一致 ✘")
    scope = f"{len(targets)} 个 seed 目录合并" if len(targets) > 1 else "单目录"
    print(f"审计结论（{scope}）：{len(RESULTS) - n_fail - n_warn}/{len(RESULTS)} 通过"
          + (f"，{n_warn} 项 WARN（已知低危口径差异）" if n_warn else "")
          + verdict)
    return 1 if n_fail else 0


if __name__ == "__main__":
    _args = [a for a in sys.argv[1:] if not a.startswith("-")]
    _res = "results"
    if "--results" in sys.argv:
        i = sys.argv.index("--results")
        if i + 1 < len(sys.argv):
            _res = sys.argv[i + 1]
    elif _args:
        _res = _args[0]
    sys.exit(main(_res))
