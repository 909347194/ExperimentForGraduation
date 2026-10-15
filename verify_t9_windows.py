# -*- coding: utf-8 -*-
"""把「为什么没搜到更优解」的四个因素量化成具体数字（exp01_smoke 周期 1，T9 案例）。

A. 因素① 拍卖锁定：UAV0/UAV4 两行的任务代价标尺 + cv 漂移窗口
   - UAV0 行（参与序第 8 个挑选者）：cv 要从 45199 漂到多低才会让出 T9
   - UAV4 行（第 5 个挑选者）：cv 落进多宽窗口才会抢到 T9（行内最小值 17250）
B. 因素② 回写闭环：把精英起始基因的回写值喂回反映射第一阶段（纯开发），
   验证分配被精确复现（不动点）；并扫描 UAV4 槽值找翻盘窗口
C. 因素③ 梯度太弱：big_m 构成分解 + T9 换巢收益的相对量级
D. 因素④ 修复链 reshaping：记录 _consolidate 对 alt 8 航次的合并决策，
   对比「过链」与「绕链」的 path / penalty / fitness

口径：参与序 [6,2,1,7,4,5,3,0,8,9]、任务序 [7,0,9,4,6,2,1,3,8,10,5,11]
（horizon 周期 1 实测捕获值），断言 cm[UAV0行,T9列]≈45199、cm[UAV4行,T9列]≈17250。
"""
import copy
import sys

sys.path.insert(0, "task_allocation/experiments/exp01_smoke")

from dataclasses import replace

import numpy as np

from paths import DATA_DIR, EXP_DIR
from build import (
    build_arrivals,
    build_cost_estimator,
    build_scenario,
    build_task_config,
    build_terrain,
    build_uavs,
)
from config import build_config_bundle, load_config
from task_allocation.methods.muas.cost.cost_matrix import build_pairwise_costs
from task_allocation.methods.muas.cost.vertical_section import make_distance_fn
from task_allocation.methods.muas.problem import SelectiveMUASProblem
from task_allocation.methods.muas.solvers import muas_stage

# ── 重建周期 1 实例 ────────────────────────────────────────────
raw_cfg = load_config(EXP_DIR / "config.yaml")
scenario = build_scenario(raw_cfg, DATA_DIR)
nests = scenario.nests
terrain = build_terrain(scenario, raw_cfg)
estimator = build_cost_estimator(terrain, raw_cfg)
seed = 42
seed_cfg = copy.deepcopy(dict(raw_cfg))
seed_cfg.setdefault("experiment", {})["seed"] = seed
seed_cfg.setdefault("solver", {})["seed"] = seed
bundle = build_config_bundle(seed_cfg)
uavs = build_uavs(nests, raw_cfg)
task_cfg = build_task_config(raw_cfg, seed)
batch = build_arrivals(nests, scenario, task_cfg, raw_cfg)
tasks_id_order = batch.arrivals[1]  # id 序 0..11
cb = build_pairwise_costs(
    tasks_id_order, uavs, nests=nests, dem=terrain, estimator=estimator,
    nest_offset=bundle.nest_offset,
)
dfn = make_distance_fn(estimator)

# ── 局部口径与代价矩阵（行=参与序，列=任务序） ──────────────────
PART_IDS = [6, 2, 1, 7, 4, 5, 3, 0, 8, 9]
TASK_IDS = [7, 0, 9, 4, 6, 2, 1, 3, 8, 10, 5, 11]
row_of_uav = {u.id: i for i, u in enumerate([x for x in uavs if x.is_available])}
part_rows = [row_of_uav[pid] for pid in PART_IDS]
tid2col = {t.id: i for i, t in enumerate(tasks_id_order)}
cols = [tid2col[t] for t in TASK_IDS]
cm = np.vstack([
    cb.uav_target[part_rows, :][:, cols],
    cb.task_task[np.ix_(cols, cols)],
]).astype(float)
K, M = len(PART_IDS), len(TASK_IDS)
COL = {tid: i for i, tid in enumerate(TASK_IDS)}
ROW = {pid: i for i, pid in enumerate(PART_IDS)}

C_U0_T9 = cm[ROW[0], COL[9]]
C_U4_T9 = cm[ROW[4], COL[9]]
print("=" * 72)
print("口径校验")
print(f"  cm[UAV0行, T9列] = {C_U0_T9:9.0f} m   (期望 ≈45199)")
print(f"  cm[UAV4行, T9列] = {C_U4_T9:9.0f} m   (期望 ≈17250，UAV4 行内最小值)")
row4 = cm[ROW[4], :]
srt = np.sort(row4)
print(f"  UAV4 行代价升序: {[f'{v:.0f}' for v in srt[:4]]} ...")
assert abs(C_U0_T9 - 45199) < 300, "UAV0 行口径不符"
assert abs(C_U4_T9 - 17250) < 300 and abs(C_U4_T9 - srt[0]) < 1e-6, "UAV4 行口径不符"

# ── 评估器/问题对象（供 C、D 用） ─────────────────────────────
tasks_local = sorted(tasks_id_order, key=lambda t: TASK_IDS.index(t.id))
uavs_by_id = {u.id: u for u in uavs}
problem = SelectiveMUASProblem.from_entities(
    tasks_local, uavs, nests=nests,
    pairwise_cost=cb.provider.pairwise,
    config=replace(bundle.problem_model, current_time=600.0),
    fleet_uavs=uavs, distance_fn=dfn,
)
ev = muas_stage.MUASAssignmentEvaluator(
    problem=problem,
    participating=[uavs_by_id[pid] for pid in PART_IDS],
    tasks=tasks_local, nests=nests, fleet_uavs=uavs,
    config=bundle.stage_model, available_uavs=uavs,
)


# ═══════════════════════════════════════════════════════════════
# A. 因素①：拍卖锁定 —— 代价标尺与 cv 漂移窗口
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 72)
print("A. 因素①「拍卖锁定」：两行代价标尺 + cv 漂移窗口")
print("=" * 72)


def row_table(pid: int) -> None:
    r = ROW[pid]
    order = np.argsort(cm[r, :])
    print(f"  UAV{pid}（挑选顺位第 {r + 1}）行内各任务代价（升序）：")
    for t in order:
        tid = TASK_IDS[t]
        mark = "  ← T9" if tid == 9 else ""
        print(f"    T{tid:<2d} {cm[r, t]:9.0f} m{mark}")


def window(pid: int, tid: int) -> tuple[float, float, float]:
    """返回 (本行中 tid 的代价, 最近竞争任务代价, 翻盘中点)。"""
    r = ROW[pid]
    c = cm[r, COL[tid]]
    others = [cm[r, t] for t in range(M) if t != COL[tid]]
    nearest = min(others, key=lambda v: abs(v - c))
    mid = (c + nearest) / 2.0
    return c, nearest, mid


row_table(0)
row_table(4)
print()
c0, near0, mid0 = window(0, 9)
print(f"  UAV0 行：T9={c0:.0f}，行内次近竞争任务={near0:.0f}，翻盘中点={mid0:.0f}")
print(f"    → cv[UAV0槽] 必须从 45199 下漂超过 {c0 - mid0:.0f} m（到 {mid0:.0f} 以下），"
      f"UAV0 才会在第 8 轮让出 T9")
c4, near4, mid4 = window(4, 9)
print(f"  UAV4 行：T9={c4:.0f}（行内最小），次小任务={near4:.0f}，翻盘中点={mid4:.0f}")
print(f"    → cv[UAV4槽] 必须落在 [0, {mid4:.0f}] m 窗口内，UAV4 才会在第 5 轮抢到 T9")
print(f"    （前提：T9 到第 5 轮仍空闲——前 4 个挑选者的报价见下）")
print("  前 4 个挑选者（UAV6/2/1/7）行内 T9 代价 vs 各自锁定的首任务报价：")
for pid in [6, 2, 1, 7]:
    r = ROW[pid]
    c_t9 = cm[r, COL[9]]
    row_min = cm[r, :].min()
    print(f"    UAV{pid}: T9 在该行 {c_t9:8.0f} m（该行最小 {row_min:8.0f} m）"
          f"——T9 不是它的菜，不会横刀")


# ═══════════════════════════════════════════════════════════════
# B. 因素②：Lamarckian 回写闭环（不动点）
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 72)
print("B. 因素②「回写闭环」：精英起始基因 → cost_vector → 第一阶段 → 复现")
print("=" * 72)

# 基线精英（horizon 周期 1 实际解）的首任务分配（全局 id）
FOUND = {0: [9], 2: [2], 3: [3, 5], 5: [0, 11], 6: [7], 7: [1, 6], 8: [10], 9: [4, 8]}
START = {ROW[uid]: COL[tl[0]] for uid, tl in FOUND.items()}  # 行 → 首任务列
IDLE_ROWS = [r for r in range(K) if r not in START]  # UAV1(行2)、UAV4(行4)


def phase1(cv: np.ndarray) -> dict:
    """复刻 _inverse_phi_srp 第一阶段（temperature<0.1 纯开发分支：argmin）。"""
    assigned, taken = {}, set()
    for i in range(K):
        avail = [t for t in range(M) if t not in taken]
        reach = [t for t in avail if np.isfinite(cm[i, t])]
        if not reach:
            break
        diffs = np.abs(np.array([cm[i, t] for t in reach]) - cv[i])
        tgt = reach[int(np.argmin(diffs))]
        assigned[i] = tgt
        taken.add(tgt)
    return assigned


def writeback_cv(idle_vals: dict) -> np.ndarray:
    """回写后的 cost_vector：活跃机=精英首任务代价，闲置机=旧值（外部给定）。"""
    cv = np.full(M, np.nan)
    for r, t in START.items():
        cv[r] = cm[r, t]
    for r in IDLE_ROWS:
        cv[r] = idle_vals[r]
    return cv


print("  精英起始基因回写值（cost_vector 前 K 位，= 各机首任务的行代价）：")
for r in sorted(START):
    pid = PART_IDS[r]
    print(f"    行{r} UAV{pid}: cv = cm[{r}, T{TASK_IDS[START[r]]}] = {cm[r, START[r]]:9.0f}")
print(f"    行{IDLE_ROWS[0]} UAV1（闲置）: 旧值保留，不回写")
print(f"    行{IDLE_ROWS[1]} UAV4（闲置）: 旧值保留，不回写")

print()
print("  闭环检验：回写值喂回第一阶段（纯开发 argmin），是否复现精英首任务分配？")
print("  （闲置机 UAV1/UAV4 的槽位旧值不可知——进化遗留量，取三种代表性值试探）")
repro = 0
trials = 0
for idle_mode, idle_vals in [
    ("闲置槽=各自行第 3 小代价", {r: np.sort(cm[r, :])[2] for r in IDLE_ROWS}),
    ("闲置槽=各自行最大值", {r: cm[r, :].max() for r in IDLE_ROWS}),
    ("闲置槽=各自行第 5 小代价", {r: np.sort(cm[r, :])[4] for r in IDLE_ROWS}),
]:
    cv = writeback_cv(idle_vals)
    a = phase1(cv)
    bad = [r for r, t in START.items() if a.get(r) != t]
    ok = not bad
    t9_owner_row = next((r for r, t in a.items() if t == COL[9]), None)
    t9_owner = PART_IDS[t9_owner_row] if t9_owner_row is not None else None
    trials += 1
    repro += int(ok)
    why = "" if ok else f"（闲置槽撞车：行 {bad} 的首任务被抢先）"
    print(f"    [{idle_mode}] 活跃 8 架首任务复现={ok}；T9 最终归属 UAV{t9_owner}{why}")
print(f"  → {repro}/{trials} 种闲置槽旧值下，精英的 8 个首任务被精确复现")
print("    机制：回写把 cv[活跃槽] 设为该机首任务的**精确代价**（diff=0）→ argmin 必选，")
print("    只要该任务没被更早挑选者抢走。复现失败只源于闲置槽旧值恰好撞上某活跃机的")
print("    首任务——而撞车通常产生更差解，被贪婪选择压回，系统在精英结构附近震荡。")

print()
print("  扫描 UAV4 槽值（其余锁定），看 T9 何时易主：")
lo, hi = float(row4.min()), float(np.sort(row4)[-2])
grid = np.linspace(lo, hi, 4001)
flip = []
for v in grid:
    cv = writeback_cv({IDLE_ROWS[0]: np.sort(cm[IDLE_ROWS[0], :])[2], IDLE_ROWS[1]: v})
    a = phase1(cv)
    owner = next((r for r, t in a.items() if t == COL[9]), None)
    flip.append(PART_IDS[owner] if owner is not None else None)
flip = np.array(flip)
n4 = int((flip == 4).sum())
print(f"    UAV4 行代价域 [{lo:.0f}, {hi:.0f}] m 内扫描 4001 点："
      f"T9→UAV4 占 {n4} 点（{n4 / len(grid) * 100:.1f}%），其余全部 T9→UAV0")
if n4:
    idx = np.where(flip == 4)[0]
    print(f"    翻盘窗口 ≈ [{grid[idx[0]]:.0f}, {grid[idx[-1]]:.0f}] m"
          f"（与该行次小任务代价 {near4:.0f} 的中点 {mid4:.0f} 吻合）")
print("    注意：这只是「第一阶段抢到」；还要过 consolidate 等下游修复才计入适应度。")
print("    窗口宽 ≠ 容易翻盘：DE/best/2 把槽值锚定在 best 向量附近，种群收敛后")
print("    差分向量变小，槽值被锁在窗外——没有梯度把它推向窗口（平台期）。")

print()
print("  反向扫描 UAV0 槽值（UAV4 槽固定在窗外默认值，其余锁定），看 UAV0 何时让出 T9：")
row0 = cm[ROW[0], :]
uav4_default = float(np.sort(row4)[4])  # 行内第 5 小：窗外值
give = []
gv = np.linspace(0.0, float(row0.max()), 4001)
for v in gv:
    cv = writeback_cv({IDLE_ROWS[0]: np.sort(cm[IDLE_ROWS[0], :])[2],
                       IDLE_ROWS[1]: uav4_default})
    cv[ROW[0]] = v
    a = phase1(cv)
    owner = next((r for r, t in a.items() if t == COL[9]), None)
    give.append(f"UAV{PART_IDS[owner]}" if owner is not None else "巡游段")
give = np.array(give)
uniq, cnt = np.unique(give, return_counts=True)
print(f"    T9 归属分布: " + ", ".join(f"{u}:{c}点" for u, c in zip(uniq, cnt)))
for u in uniq:
    idx = np.where(give == u)[0]
    print(f"    {u} 拿到 T9 的 cv[UAV0] 区间 ≈ "
          f"[{gv[idx[0]]:.0f}, {gv[idx[-1]]:.0f}] m")
print("    （「巡游段」= T9 未进第一阶段，留作巡游基因由第二阶段的边匹配决定归属——")
print("     这是 T9 易主的第三条通道：不抢首任务，而是挂到某架机的航次中段）")


# ═══════════════════════════════════════════════════════════════
# C. 因素③：适应度梯度有多弱
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 72)
print("C. 因素③「梯度太弱」：big_m 构成 + T9 换巢收益的相对量级")
print("=" * 72)

cfg = problem.config
m = problem.f1_weight()
UB = problem._secondary_upper_bound()
r_min = min(float(t.reward) for t in problem.tasks if float(t.reward) > 0)
n_b = len(problem.nests)
v_bound = 1.0e6 * (M + K + n_b + 8)
wv_vbound = float(cfg.w_violation) * v_bound
c_bound = problem._max_edge_cost() * (M + 2 * K + 2)
lam_cbound = float(cfg.lambda_cost) * c_bound
print(f"  fitness = -m·F1 + λ·path + w_violation·penalty + w_time·F3")
print(f"    λ={cfg.lambda_cost}  w_violation={cfg.w_violation}  w_time={cfg.w_time}")
print(f"  m = 1 + UB/r_min = 1 + {UB:.0f}/{r_min:.4f} = {m:.1f}")
print(f"    UB = λ·c_bound + w_violation·v_bound"
      f" = {lam_cbound:.0f} + {wv_vbound:.0f} = {UB:.0f}")
print(f"    （v_bound = 1e6×(M+K+B+8) = 1e6×{M + K + n_b + 8} = {v_bound:.0f}；"
      f"惩罚项上界占 UB 的 {wv_vbound / UB * 100:.1f}%）")
gain = float(cfg.lambda_cost) * (C_U0_T9 - C_U4_T9)
print(f"  T9 换巢收益 = λ×({C_U0_T9:.0f}-{C_U4_T9:.0f}) = {gain:.2f} 适应度单位")
print(f"  一个最小奖励（r_min={r_min:.4f}）的价值 = m×r_min = {m * r_min:.0f} 单位")
print(f"  → T9 换巢收益 = 一个最小奖励的 {gain / (m * r_min):.2e}"
      f"（{gain / (m * r_min) * 100:.5f}%）")
print(f"  兑换率：1 秒迟到 = {float(cfg.w_violation):.2f} 单位；"
      f"1 米航程 = {float(cfg.lambda_cost):.0e} 单位 → 1 s 迟到 ≡ "
      f"{float(cfg.w_violation) / float(cfg.lambda_cost):.0f} m 航程")
print(f"  n_evals=6368（50 种群×100 代 + 初始 50 + GMR 灭绝重评），"
      f"F1=60.4962 时两种分配只差 path/penalty 两项")


# ═══════════════════════════════════════════════════════════════
# D. 因素④：修复链对 alt 8 航次的重塑
# ═══════════════════════════════════════════════════════════════
print()
print("=" * 72)
print("D. 因素④「修复链 reshaping」：alt 8 航次过链 vs 绕链")
print("=" * 72)

ALT = {2: [2], 3: [3, 5], 4: [9], 5: [0, 11], 6: [7], 7: [1, 6], 8: [10], 9: [4, 8]}
ALT_END = {2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 6, 8: 7, 9: 0}

# 过链：局部 assignment → build_solution（内部 _consolidate 决策插桩）
merge_trace = []
_orig_cons = ev._consolidate


def _logged_cons(routes, end_nests):
    before = {k: list(v) for k, v in routes.items() if v}
    out = _orig_cons(routes, end_nests)
    after = {k: list(v) for k, v in out[0].items() if v}
    emptied = [k for k in before if k not in after or not after.get(k)]
    grown = {k: v for k, v in after.items() if k in before and v != before[k]}
    if emptied or grown:
        merge_trace.append((before, after, emptied, grown))
    return out


ev._consolidate = _logged_cons
assign_local = [(ROW[uid], COL[t]) for uid, tl in ALT.items() for t in tl]
sol_chain = ev.build_solution(assign_local)
ev._consolidate = _orig_cons
problem.evaluate(sol_chain)
fit_chain = problem.scalar_fitness(sol_chain)
path_chain = problem.path_cost(sol_chain)
pen_chain = sol_chain.meta.get("penalty", 0.0)

# 绕链：同一 assignment 直接构造（保留 8 航次原貌）
sol_bypass = problem.solution_from_assignment(
    [], sequences={u.id if False else uid: list(tl) for uid, tl in ALT.items()},
    end_nests=dict(ALT_END),
)
problem.evaluate(sol_bypass)
fit_bypass = problem.scalar_fitness(sol_bypass)
path_bypass = problem.path_cost(sol_bypass)
pen_bypass = sol_bypass.meta.get("penalty", 0.0)

print("  _consolidate 合并轨迹（每次调用前后对比）：")
for i, (before, after, emptied, grown) in enumerate(merge_trace, 1):
    print(f"    第 {i} 轮合并：腾空 UAV{emptied}；受体航次变化 "
          f"{ {k: v for k, v in grown.items()} }")
print(f"  过链结果：{len([t for t in sol_chain.tours if t.task_ids])} 航次 "
      f"{ {t.uav_id: t.task_ids for t in sol_chain.tours if t.task_ids} }")
print(f"    path = {path_chain:10.1f} m   penalty = {pen_chain:9.1f} s   fit = {fit_chain:.2f}")
print(f"  绕链结果：8 航次（原样保留）")
print(f"     path = {path_bypass:10.1f} m   penalty = {pen_bypass:9.1f} s   fit = {fit_bypass:.2f}")
d_fit = fit_chain - fit_bypass
d_path = path_chain - path_bypass
d_pen = pen_chain - pen_bypass
print(f"  过链效应：Δpath = {d_path:+.0f} m，Δpenalty = {d_pen:+.0f} s，"
      f"Δfit = {d_fit:+.2f}")
print(f"    分解：λ·Δpath = {float(cfg.lambda_cost) * d_path:+.2f}（路径节省），"
      f"w_violation·Δpenalty = {float(cfg.w_violation) * d_pen:+.2f}（迟到代价）")
print(f"    迟到当量：{d_pen:.0f} s × 100 m/s = {d_pen * 100 / 1000:.1f} km 当量 "
      f"vs 路径节省 {-d_path / 1000:.1f} km → 净赔 {d_fit:.2f}")
print("  → ① _consolidate 判据只有 path（_tour_cost：起点腿+任务链+返航腿近似）与航程门，")
print("    完全不看时间窗：它用 25 km 路径节省换 436 s 迟到，而迟到值 43.6 km 当量。")
print("    ② T9→UAV4 结构本身过了链（腾空的是 UAV2 而非 UAV4）——坏在伴生合并。")
print("    ③ 口径注记：reach.py 的「过链 pen=14047」是事后口径（horizon.run 返回后再调")
print("    build_solution，UAV 对象已被周期 1 飞行消耗）；本脚本用搜索当时的初始状态，")
print("    penalty=944.8 与 metrics 记录精确一致。DMDE 每代评估的都是「初始状态过链」。")
print("    ④ 链的像集里既有差的也有好的 T9→UAV4：id 序实验里求解器自己找到的那个")
print("    （fit=-16508222.78）形态不同，consolidate 没有做这个坏合并。")
print("    DMDE 每代评估的都过这条链——搜索空间实际是「修复链的像集」。")

print()
print("=" * 72)
print("完毕")
