# -*- coding: utf-8 -*-
"""时空联合优化（NLP）：几何折线 → 带时间的可行 Hermite 轨迹。

职责：在走廊窗内解"最短飞行时间"轨迹（CasADi + IPOPT），输出 ``Trajectory``；
      失败时按三级退让重试，仍失败则返回 ``trajectory=None``（不抛异常）。
边界：不建 SDF（``sdf_casadi.py`` 转接）、不生成几何初值（``initial_guess.py``）、
      不做独立可行性复核（``feasibility.py``）、不做多机协调。
相关：``docs/time_mapping.md`` §2/§3（口径 B、逐节点 Δt）、
      ``common/plan_types.py``（Hermite 交付）、改造方案 §5.3/§5.4。

模型口径（与参考实现的三点关键差别）：

1. **Δt 逐节点**：决策 ``p_i,v_i,a_i∈R³ + Δt_i``，共 ``10N=600``（N=60）。
   动力学用前 N−1 个 Δt；``Δt[N-1]`` 镜像 ``Δt[N-2]``（避免死变量，保持 10N 布局）。
   目标 ``min Σ_{i<N-1} Δt_i``。参考实现是单一标量 Δt，本项目不是。
2. **速度三条联立（口径 B）**：``‖v_xy‖≤v_max_plan``、``-v_dn≤v_z≤v_up``、
   ``‖v‖≤v_max`` 分别作为约束交给求解器——**不写 min**（折点不可微，IPOPT 会失败）。
3. **端点净空豁免**：机巢处实测 SDF 仅 +1.6/−0.6，首末爬升段若按全程净空判恒不可行。
   豁免首末 ``endpoint_exempt_segments`` 段及其节点；``feasibility.py`` 必须用同一豁免。

三级退让（照 pp1 已验证路径，只换初值/离散/预算，不换模型）：
①走廊冷启动退化 → 无走廊解热启动；②节点数按 ``retreat_node_ladder`` 退让；
③撞迭代上限时预算 ×``solve_budget_multiplier``。退让写进 ``message``，不静默降级。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace

try:  # 可选依赖：没装时几何层照常用
    import casadi as ca
except ImportError:  # pragma: no cover
    ca = None

import numpy as np

from path_planning.common.plan_types import HermiteSegment, Trajectory
from path_planning.common.vehicle_spec import VehicleSpec
from path_planning.methods.spatial.occupancy_sdf import SdfField
from path_planning.methods.spatial.sdf_casadi import SdfOnGrid
from path_planning.methods.spatial.world_field import WorldField
from path_planning.methods.timeline.initial_guess import (
    initial_guess_from_limits,
    resample_path,
)
from path_planning.methods.timeline.nlp_config import NlpConfig
from path_planning.methods.timeline.splines import trajectory_arc_length, trajectory_nodes

__all__ = ["NlpConfig", "NlpResult", "optimize_trajectory"]

_MAX_ITER_STATUS = "Maximum_Iterations_Exceeded"


@dataclass
class NlpResult:
    """求解结果。``trajectory is None`` 时其余字段说明为什么没有（不抛异常）。"""

    trajectory: Trajectory | None
    solver_status: str
    iterations: int | None
    elapsed_s: float
    message: str = ""


def optimize_trajectory(
    path_enu: np.ndarray,
    field: SdfField | WorldField,
    cfg: NlpConfig,
    *,
    uav_id: int = 0,
    t0: float = 0.0,
    warm: Trajectory | None = None,
) -> NlpResult:
    """在走廊内解最短时间轨迹；三级退让后仍失败返回 ``trajectory=None``。

    ``field`` 两种都收：航段规划给窗口 ``SdfField``（``(nx,ny,nz)``），局部重规划
    直接给全域 ``WorldField``（**z-major** ``(nz,nx,ny)``）。存储布局由
    ``SdfOnGrid`` 归一化——这里不再要求调用方先切窗口。

    "几何可飞、时间剖面没解出来 ≠ 航段不可行"：前者报 ``OPT_FAILED`` 可降级，
    后者才报不可行。本函数只负责产出/不产出，失败码由编排层定。
    """
    if ca is None:
        return NlpResult(None, "casadi_missing", None, 0.0, "未安装 casadi")
    sdf = SdfOnGrid(field)
    n0 = int(cfg.n_nodes)
    elapsed = 0.0
    base = resample_path(path_enu, n0)

    def solve(path, n, max_iter, cfg_use, warm_use):
        nonlocal elapsed
        r = _solve_once(path, sdf, cfg_use, n, max_iter, warm_use, uav_id, t0)
        elapsed += r.elapsed_s
        r.elapsed_s = elapsed
        return r

    # 第一手：冷启动
    r = solve(base, n0, cfg.max_iter, cfg, warm)
    # ① 走廊冷启动退化 → 无走廊解热启动
    if r.trajectory is None and cfg.half_width_m is not None:
        plain = replace(cfg, half_width_m=None, corridor_normal_xy=None, corridor_pivot_xy=None)
        seed = solve(base, n0, cfg.max_iter, plain, None)
        if seed.trajectory is not None:
            warm_r = solve(base, n0, cfg.max_iter, cfg, seed.trajectory)
            if warm_r.trajectory is not None:
                warm_r.message = "走廊约束冷启动不收敛，用无走廊解热启动才收敛"
                return warm_r
    if r.trajectory is not None:
        return r

    # ② 节点数退让（只能比 n0 小）
    hit_iter_limit = r.solver_status == _MAX_ITER_STATUS
    last = r
    for n in cfg.retreat_node_ladder:
        if not (2 <= n < n0):
            continue
        r = solve(resample_path(path_enu, n), n, cfg.max_iter, cfg, warm)
        if r.trajectory is not None:
            r.message = f"节点数从 {n0} 退到 {n} 才收敛（粗离散换收敛，转弯粒度变粗）"
            return r
        hit_iter_limit = hit_iter_limit or r.solver_status == _MAX_ITER_STATUS
        last = r
    if not hit_iter_limit:
        return last

    # ③ 迭代预算放大（只在撞迭代上限时）
    big = int(round(cfg.max_iter * cfg.solve_budget_multiplier))
    r = solve(base, n0, big, cfg, warm)
    if r.trajectory is not None:
        r.message = f"迭代预算从 {cfg.max_iter} 放大到 {big} 才收敛（慢机/病态尺度）"
    return r


def _solve_once(
    path: np.ndarray,
    sdf: SdfOnGrid,
    cfg: NlpConfig,
    n: int,
    max_iter: int,
    warm: Trajectory | None,
    uav_id: int,
    t0: float,
) -> NlpResult:
    """固定节点数 n 与迭代上限解一次（path 已重采样到 n 点）。"""
    t_start = time.perf_counter()
    opti = ca.Opti()
    x = opti.variable(3, n)
    v = opti.variable(3, n)
    a = opti.variable(3, n)
    dt = opti.variable(n)

    # --- 时间步 ---
    opti.subject_to(opti.bounded(cfg.dt_min_s, dt, cfg.dt_max_s))
    opti.subject_to(dt[n - 1] == dt[n - 2])  # 末位镜像，避免死变量
    if cfg.max_time_s is not None:
        opti.subject_to(ca.sum1(dt[: n - 1]) <= cfg.max_time_s)

    # --- 速度三条联立（口径 B，分别约束，不写 min）---
    vh2 = v[0, :] ** 2 + v[1, :] ** 2
    opti.subject_to(vh2 <= cfg.v_max_plan_mps ** 2)
    opti.subject_to(opti.bounded(-cfg.v_dn_mps, v[2, :], cfg.v_up_mps))
    opti.subject_to(ca.sum1(v ** 2) <= cfg.v_max_mps ** 2)

    # --- 加速度 + 推力（托住重力之后再谈加速度）---
    opti.subject_to(ca.sum1(a ** 2) <= cfg.a_max_mps2 ** 2)
    g = cfg.gravity_mps2
    thrust2 = cfg.thrust_max_per_mass ** 2
    opti.subject_to(ca.sum1(a ** 2) + 2.0 * g * a[2, :] + g * g <= thrust2)
    opti.subject_to(a[2, :] >= cfg.thrust_min_per_mass - g)

    # --- 走廊横向（交付硬约束，ENU 线性式，不绑栅格索引）---
    if cfg.half_width_m is not None and cfg.corridor_normal_xy and cfg.corridor_pivot_xy:
        cn_x, cn_y = (float(c) for c in cfg.corridor_normal_xy)
        cp_x, cp_y = (float(c) for c in cfg.corridor_pivot_xy)
        lateral = cn_x * (x[0, :] - cp_x) + cn_y * (x[1, :] - cp_y)
        opti.subject_to(opti.bounded(-cfg.half_width_m, lateral, cfg.half_width_m))

    # --- 绝对 z 飞行带（NLP 节点是自由变量，不钉住会爬高越过障碍）---
    # 只钉节点不够：交付的是 Hermite 段（端点 z 与 z 速度），节点处 v_z≠0 时段中点会
    # 拱出节点框外——实测节点全在 103.30 以内，曲线却到 110.42（dt≈5 s、v_z≈6 m/s 时
    # 中点偏移 = dt·(v_z0−v_z1)/8）。故按交付曲线本身的 z 采样一起约束。
    if cfg.alt_min_m is not None and cfg.alt_max_m is not None:
        lo = float(cfg.alt_min_m)
        hi = float(cfg.alt_max_m)
        opti.subject_to(opti.bounded(lo, x[2, :], hi))
        for seg_i in range(n - 1):
            for s in _Z_BAND_SAMPLES:
                z = _hermite_axis(
                    x[2, seg_i], v[2, seg_i], x[2, seg_i + 1], v[2, seg_i + 1], dt[seg_i], s
                )
                opti.subject_to(opti.bounded(lo, z, hi))

    # --- 梯形动力学（逐节点 Δt）---
    for i in range(n - 1):
        opti.subject_to(x[:, i + 1] == x[:, i] + 0.5 * (v[:, i] + v[:, i + 1]) * dt[i])
        opti.subject_to(v[:, i + 1] == v[:, i] + 0.5 * (a[:, i] + a[:, i + 1]) * dt[i])
    opti.subject_to(x[:, 0] == ca.DM(path[0]))
    opti.subject_to(x[:, n - 1] == ca.DM(path[-1]))
    opti.subject_to(v[:, 0] == 0)
    opti.subject_to(v[:, n - 1] == 0)

    # --- 几何引导管（可选）---
    # 位置是自由变量、目标只有 min ΣΔt，没有这一项时求解器会把节点全拉回直线段：
    # 局部重规划给出的让路位移只是在"初值"里存在，一解就消失（表现为"改了等于没改"）。
    # 给定引导管后，节点被钉在参考折线 ``guide_radius_m`` 内，让路位移才真正落进轨迹。
    if cfg.guide_radius_m is not None:
        r = float(cfg.guide_radius_m)
        if r <= 0.0:
            raise ValueError(f"guide_radius_m 必须为正：{r}")
        r2 = r * r
        for i in range(1, n - 1):
            opti.subject_to(ca.sumsqr(x[:, i] - ca.DM(path[i])) <= r2)

    # --- 窗口硬边界 + 净空（节点 + 段内插；首末爬升段豁免）---
    ii, jj, kk = sdf.index_of(x)
    nx, ny, nz = sdf.shape
    opti.subject_to(opti.bounded(0.0, ii, nx - 1.0))
    opti.subject_to(opti.bounded(0.0, jj, ny - 1.0))
    opti.subject_to(opti.bounded(0.0, kk, nz - 1.0))
    k = cfg.endpoint_exempt_segments
    for i in range(n):
        if i < k or i >= n - k:
            continue  # 端点豁免
        opti.subject_to(sdf.at(x[:, i]) >= cfg.clearance_m)
    for seg_i in range(n - 1):
        if seg_i < k or seg_i >= n - 1 - k:
            continue  # 首末爬升段豁免
        for j in range(1, cfg.segment_samples + 1):
            alpha = float(j) / float(cfg.segment_samples + 1)
            xm = (1.0 - alpha) * x[:, seg_i] + alpha * x[:, seg_i + 1]
            opti.subject_to(sdf.at(xm) >= cfg.clearance_m)

    opti.minimize(ca.sum1(dt[: n - 1]))

    # --- 初值 ---
    if warm is not None and len(warm.segments) + 1 == n and cfg.guide_radius_m is None:
        ig_x, ig_v, ig_dt = trajectory_nodes(warm)
        ig_a = np.zeros((n, 3))
    else:
        # 引导管求解（局部让路）**不用热启动**：旧航迹与候选相差一个让路位移
        # （16–36 m），位置初值若照旧航迹，首步就违反半径 2 m 的管约束；而把位置换成
        # 候选折线、速度/Δt 仍用旧航迹，又会与梯形动力学矛盾（x_{i+1}=x_i+½(v_i+v_{i+1})Δt）。
        # 两种错配实测都表现为 120/8 下 Maximum_Iterations_Exceeded 或
        # Infeasible_Problem_Detected（同一候选在 40/3 冷启动下能收敛）。故一律冷启动。
        ig_x = path
        ig_v, ig_a, ig_dt = initial_guess_from_limits(
            path,
            v_cruise_mps=cfg.v_max_plan_mps,
            v_max_mps=cfg.v_max_mps,
            v_up_mps=cfg.v_up_mps,
            v_dn_mps=cfg.v_dn_mps,
            a_max_mps2=cfg.a_max_mps2,
            dt_min_s=cfg.dt_min_s,
            dt_max_s=cfg.dt_max_s,
        )
    opti.set_initial(x, ig_x.T)
    opti.set_initial(v, ig_v.T)
    opti.set_initial(a, ig_a.T)
    opti.set_initial(dt, ig_dt)
    opti.solver(
        "ipopt",
        {"print_time": False,
         "ipopt": {"max_iter": max_iter, "print_level": 0, "sb": "yes", "tol": cfg.solver_tol}},
    )

    try:
        opti.solve()
    except Exception as exc:  # noqa: BLE001 — 求解器失败一律降级，不抛
        try:
            stats = opti.stats()
            status = str(stats.get("return_status", "solve_failed"))
            iters = int(stats.get("iter_count", 0) or 0)
        except Exception:  # noqa: BLE001
            status, iters = "solve_failed", 0
        return NlpResult(None, status, iters, time.perf_counter() - t_start,
                         f"{type(exc).__name__}: {str(exc).splitlines()[0]}")

    stats = opti.stats()
    traj = _build_trajectory(opti, x, v, dt, n, uav_id, t0, stats)
    return NlpResult(
        traj,
        str(stats.get("return_status", "Solve_Succeeded")),
        int(stats.get("iter_count", 0) or 0),
        time.perf_counter() - t_start,
    )


def _build_trajectory(opti, x, v, dt, n, uav_id, t0, stats) -> Trajectory:
    """把节点解装配成 Hermite 段序列（弧长由 Gauss 重积分算出）。"""
    pv = np.asarray(opti.value(x), dtype=float).T
    vv = np.asarray(opti.value(v), dtype=float).T
    dtv = np.asarray(opti.value(dt), dtype=float).ravel()
    times = t0 + np.concatenate([[0.0], np.cumsum(dtv[: n - 1])])
    segs = tuple(
        HermiteSegment(
            float(times[i]), float(times[i + 1]),
            tuple(pv[i]), tuple(pv[i + 1]), tuple(vv[i]), tuple(vv[i + 1]),
        )
        for i in range(n - 1)
    )
    return Trajectory(
        uav_id=uav_id,
        segments=segs,
        path_len_m=trajectory_arc_length(segs),
        meta={
            "solver_status": str(stats.get("return_status", "Solve_Succeeded")),
            "iterations": int(stats.get("iter_count", 0) or 0),
            "n_nodes": n,
        },
    )


#: z 飞行带在**交付曲线**上的采样位置（段内归一化参数）。中点 0.5 正是
#: "两端 z 相等、两个 z 速度反向"时 Hermite 拱顶的位置；0.2/0.4/0.6/0.8 兜住
#: 非对称情形（节点框内但曲线外凸）。
_Z_BAND_SAMPLES: tuple[float, ...] = (0.2, 0.4, 0.6, 0.8)


def _hermite_axis(p0, v0, p1, v1, dt, s: float):
    """单轴 Hermite 在段内参数 ``s`` 处的取值（与 ``HermiteSegment`` 同一基函数）。"""
    s2 = s * s
    s3 = s2 * s
    h00 = 2.0 * s3 - 3.0 * s2 + 1.0
    h10 = s3 - 2.0 * s2 + s
    h01 = -2.0 * s3 + 3.0 * s2
    h11 = s3 - s2
    return h00 * p0 + h10 * dt * v0 + h01 * p1 + h11 * dt * v1

