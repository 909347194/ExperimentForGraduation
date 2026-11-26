# -*- coding: utf-8 -*-
"""单航段规划：走廊 → 场 → RRT 多初值 → 准入 → 逐候选 NLP → 复核 → 真几何闸。

职责：把一条航段（起点 → 目标点）解成带时间的 ``Trajectory``，或给出失败码 + 诊断。
边界：不做多段拼接、不判整条 tour 的预算（在 ``tour_planner.py``）。
相关：``admission.py``（预算下界的 0 秒拒）、``methods/timeline/trajectory_optimizer.py``、
      ``methods/timeline/feasibility.py``（独立复核）、``delivery_gate.py``（稠密净空）。

**为什么要独立成文件**：这是整条流水线最贵的一段（每次求解 0.1–15 s），改动频率
也最高；与"整趟编排"的逻辑（按停靠点串段、降级、预算判定）混在一处时，任何一次
调参都要在一份 350 行的文件里翻找。
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import numpy as np

from path_planning.common.failure_codes import FailureCode
from path_planning.common.plan_types import PlanRequest
from path_planning.common.trajectory import Trajectory
from path_planning.methods.planning.admission import admit_leg
from path_planning.methods.planning.delivery_gate import curve_clearance, dense_clearance
from path_planning.methods.planning.leg_segments import polyline_positions, sampled_positions
from path_planning.methods.spatial.corridor_window import CorridorWindow, corridor_window
from path_planning.methods.spatial.initial_paths import generate_initial_paths
from path_planning.methods.spatial.occupancy_sdf import SdfField
from path_planning.methods.spatial.rrt_path_search import RRT3D, RRTConfig
from path_planning.methods.timeline.feasibility import VerificationLimits, verify_trajectory
from path_planning.methods.timeline.nlp_config import NlpConfig
from path_planning.methods.timeline.trajectory_optimizer import optimize_trajectory

if TYPE_CHECKING:  # pragma: no cover
    from path_planning.methods.planning.tour_planner import TourPlanningContext

__all__ = ["plan_leg"]


def plan_leg(
    request: PlanRequest,
    ctx: "TourPlanningContext",
    start: np.ndarray,
    goal: np.ndarray,
    t0: float,
    max_time_s: float,
    max_len_m: float,
) -> tuple[Trajectory | None, FailureCode | None, dict[str, Any]]:
    """走廊 → 场 → RRT 多初值 → **准入** → 逐候选 NLP（含净空重解）→ 复核 → 真几何闸。

    准入放在候选初值之后：预算下界一旦判死就不进 NLP（那一路实测要几千次迭代），
    但几何已经过空间层，仍可交给 ``cruise_profile`` 做降级交付。
    """
    window = corridor_window(
        tuple(float(v) for v in start),
        tuple(float(v) for v in goal),
        ctx.corridor_rule,
        tuple(float(v) for v in ctx.field.grid.spacing),
        float(ctx.alt_range_m[0]),
        float(ctx.alt_range_m[1]),
    )
    window_field = SdfField(window.grid, ctx.field.window_sdf(window.grid))
    if float(window.half_width_m) <= float(ctx.params.clearance_m):
        # 半宽 <= 净空的走廊在几何上是空集：解必然违反净空，报出来的却是 OPT_FAILED。
        return None, FailureCode.CORRIDOR_TOO_NARROW, {
            "window_half_width_m": float(window.half_width_m),
            "clearance_m": float(ctx.params.clearance_m),
        }
    # 求解阈值的净空 = 交付净空 + SDF 竖直量化容差：SDF 量的是到体素中心的距离，
    # 屋顶夹在两格心之间时最多乐观一格（``sz``）。不加容差就是把乐观误差直接交付；
    # 交付与审计仍按 ``clearance_m`` 判（见 ``_limits``）。
    solve_clearance = float(ctx.params.clearance_m) + float(ctx.sdf_quant_tolerance_m)
    rrt = RRT3D(
        window_field, window, ctx.alt_range_m,
        RRTConfig.from_params(
            ctx.params, clearance_m=solve_clearance, seed=int(ctx.seed),
            endpoint_clearance_m=ctx.endpoint_clearance_m,
            endpoint_exempt_radius_m=float(ctx.endpoint_exempt_radius_m),
        ),
    )
    candidates = generate_initial_paths(
        rrt, start, goal,
        initializations=int(ctx.params.rrt_initializations),
        dedup_tol_m=float(ctx.params.rrt_dedup_tol_m),
    )
    diag: dict[str, Any] = {
        "t0": t0, "candidates": len(candidates),
        "alt_m": (float(ctx.alt_range_m[0]), float(ctx.alt_range_m[1])),
        "endpoints_m": {"start": [float(v) for v in start], "goal": [float(v) for v in goal]},
        "corridor": {
            "half_width_m": float(window.half_width_m),
            "clearance_m": float(ctx.params.clearance_m),
            "solve_clearance_m": solve_clearance,
            "grid_shape": [int(v) for v in window.grid.shape],
            "grid_origin": [round(float(v), 3) for v in window.grid.origin],
        },
        "rrt": [dict(s) for s in getattr(rrt, "last_stats_all", [])],
    }
    if not candidates:
        # 候选全灭有三种成因，处置完全不同：起点/终点本身不自由（点位或豁免圈问题）、
        # 走廊半宽小于净空（几何空集）、树长不到目标（采样预算或阈值问题）。
        seeds = [s for s in diag["rrt"]]
        diag["rrt_no_path"] = {
            "start_free": [s["start_free"] for s in seeds],
            "goal_free": [s["goal_free"] for s in seeds],
            "start_sdf_m": seeds[0]["start_sdf_m"] if seeds else None,
            "goal_sdf_m": seeds[0]["goal_sdf_m"] if seeds else None,
            "start_threshold_m": seeds[0]["start_threshold_m"] if seeds else None,
            "goal_threshold_m": seeds[0]["goal_threshold_m"] if seeds else None,
            "endpoint_threshold_m": seeds[0]["endpoint_threshold_m"] if seeds else None,
            "nodes_max": max((s["nodes"] for s in seeds), default=0),
            "closest_to_goal_min_m": min(
                (s["closest_to_goal_m"] for s in seeds if s["closest_to_goal_m"] is not None),
                default=None,
            ),
            "reject_clearance": sum(s["reject_clearance"] for s in seeds),
            "reject_window": sum(s["reject_window"] for s in seeds),
            "reject_steer": sum(s["reject_steer"] for s in seeds),
        }
        rrt_no_path = diag["rrt_no_path"]
        if not all(s["start_free"] for s in seeds) or not all(s["goal_free"] for s in seeds):
            reason = "端点不自由：起点或终点自身不满足净空（查点位/豁免圈）"
        elif rrt_no_path["nodes_max"] <= 1:
            reason = "无可用生长：窗内采样全被净空或窗界拒掉（查走廊窗与净空）"
        else:
            reason = "树长不到目标：采样预算或净空阈值过紧（查 max_iters / clearance）"
        diag["rrt_no_path"]["reason"] = reason
        return None, FailureCode.RRT_NO_PATH, diag
    # 降级交付要用一块"已被空间层判过无碰撞"的几何：取最便宜的初值候选。
    diag["fallback_path"] = np.asarray(candidates[0].path_enu, dtype=np.float64)

    code, admission = admit_leg(
        request.vehicle, start, goal, remaining_s=float(max_time_s), remaining_m=float(max_len_m)
    )
    diag["admission"] = admission
    cheap = code is not None
    if cheap and request.allow_estimate_fallback:
        # 调用方已声明"估算剖面可接受"：不下这一注，0 秒退，几何已在上面判过。
        diag["status"] = "admission_rejected"
        diag["tried"] = 0
        return None, code, diag

    fix = (
        float(ctx.params.local_replan_clearance_fix_m)
        if ctx.clearance_fix_m is None else float(ctx.clearance_fix_m)
    )
    if cheap:
        # 调用方没声明可接受估算交付：仍给一次机会拿真实航迹，但只解一手——
        # 单候选、不加净空、不爬节点阶梯、不放大迭代预算。失败码由交付后的预算判定给。
        diag["cheap_only"] = True
        candidates = candidates[:1]
        fix = 0.0
    tried = 0
    # 研究口径：即使这段最终不可行，也留一条"最接近可行"的轨迹（净空最大的那手）。
    # 它**不是交付物**，只写进诊断供上图与复核；失败码与交付语义都不变。
    best_clear = -1e9
    best_traj: Trajectory | None = None
    for cand_index, cand in enumerate(candidates):
        for add in (0.0,) if fix <= 0.0 else (0.0, fix):
            tried += 1
            cfg = _nlp_config(ctx, window, request.vehicle, max_time_s, add)
            if cheap:
                cfg = replace(cfg, retreat_node_ladder=(), solve_budget_multiplier=1.0)
            res = optimize_trajectory(
                cand.path_enu, window_field, cfg, uav_id=request.uav_id, t0=t0,
                sdf_cache=ctx.sdf_cache,
            )
            traj = res.trajectory
            # 每次尝试都留一行：7 次"迭代耗尽"曾经完全不入账，事后只能猜。
            attempt: dict[str, Any] = {
                "attempt": tried,
                "candidate": int(cand_index),
                "add_clearance_m": round(float(add), 3),
                "solver_status": getattr(res, "solver_status", None),
                "iterations": getattr(res, "iterations", None),
                "elapsed_s": getattr(res, "elapsed_s", None),
                "has_trajectory": traj is not None,
            }
            diag.setdefault("attempts", []).append(attempt)
            if traj is None:
                attempt["verdict"] = "solver_none"
                continue
            limits = _limits(cfg, traj, float(ctx.params.clearance_m), window)
            # 求解器自检：先用折线 2 m 密采样算净空，再决定"Solve_Succeeded"算不算数。
            # IPOPT 只保证 KKT 残差，不保证约束被满足；三线性 SDF 的梯度在格点边界
            # 不连续、会出死区，解可能停在"约束大幅违反的驻点"上却照样报成功。
            # 记账口径因此分两栏：raw_solver_status 保留求解器原话，solver_status 写
            # 复核后的真实性质（INFEASIBLE_POINT = 报了成功但连求解阈值都没到）。
            dense = dense_clearance(ctx, traj)
            attempt["dense_clearance_m"] = round(float(dense), 3)
            raw_status = getattr(res, "solver_status", None)
            if (raw_status == "Solve_Succeeded"
                    and float(dense) < float(cfg.clearance_m) - 1e-2):
                attempt["raw_solver_status"] = raw_status
                attempt["solver_status"] = "INFEASIBLE_POINT"
            verdict = verify_trajectory(
                traj, request.vehicle, window_field, limits, params=ctx.params
            )
            if verdict.warnings:
                diag.setdefault("warnings", []).extend(verdict.warnings)
            if not verdict.ok:
                first = verdict.first()
                attempt["verdict"] = "feasibility:" + "+".join(verdict.codes())
                attempt["worst_clearance_m"] = verdict.metrics.get("worst_clearance_m")
                core = verdict.metrics.get("worst_clearance_m")
                if core is not None and float(core) > best_clear:
                    best_clear, best_traj = float(core), traj
                diag.setdefault("rejected", []).append(
                    (res.solver_status, verdict.codes(),
                     verdict.metrics.get("worst_clearance_m"),
                     None if first is None else first.detail)
                )
                continue
            # 稠密净空与真几何都按**交付口径**判（不按涨阈值后的 cfg）：涨阈值只给求解留余量。
            # 容差 1e-2（10 mm）：三线性 SDF 在 0.2 m 步长上本身有 mm 级噪声，
            # 旧 1e-6 会把 5.995 当成 5.995 拒掉，而它离阈值只差一个栅格误差量级。
            limit = float(ctx.params.clearance_m)
            # 派生层参考：Hermite 曲线上的净空只记账（与折线的差即表示误差），不进判决
            attempt["curve_clearance_m"] = round(float(curve_clearance(ctx, traj)), 3)
            if dense < limit - 1e-2:
                attempt["verdict"] = f"dense {dense:.3f} < {limit:.3f}"
                diag.setdefault("rejected", []).append(f"dense {dense:.3f}")
                if float(dense) > best_clear:
                    best_clear, best_traj = float(dense), traj
                continue
            if ctx.prisms is not None:
                step = float(ctx.params.clearance_sample_step_m)
                # 真几何终闸按**曲线**取点：Hermite 才是按时间真正飞出的几何，
                # 它比节点折线多出的拱高正是"表示误差"，必须在这一道闸里吸收。
                clear = ctx.prisms.min_clearance(sampled_positions(traj, step), step)
                attempt["prism_clearance_m"] = round(float(clear), 3)
                if clear < limit - 1e-2:
                    attempt["verdict"] = f"prism {clear:.3f} < {limit:.3f}"
                    diag.setdefault("rejected", []).append(f"prism {clear:.3f}")
                    if float(clear) > best_clear:
                        best_clear, best_traj = float(clear), traj
                    continue
            attempt["verdict"] = "deliver"
            diag["tried"] = tried
            diag["length_m"] = cand.length_m
            diag["solver"] = res.solver_status
            return traj, None, diag

    diag["tried"] = tried
    diag["status"] = "all_candidates_failed"
    if best_traj is not None:
        # 失败段也留一条可复核的轨迹：折线点位 + 最紧点（图上画虚线、标最紧点的净空），
        # 明确标注"不可行、不是交付物"。研究要能看清"差在哪里、差多少"。
        pts = np.asarray(polyline_positions(best_traj, 4.0), dtype=float).reshape(-1, 3)
        sdf = np.asarray(window_field.at(pts), dtype=float).ravel()
        k = int(np.argmin(sdf))
        diag["best_effort"] = {
            "worst_clearance_m": round(float(sdf[k]), 3),
            "worst_point_m": [round(float(v), 2) for v in pts[k]],
            "length_m": round(float(best_traj.path_len_m), 1),
            "t0_s": round(float(best_traj.t0), 2),
            "feasible": False,
            "samples": [[round(float(v), 2) for v in p] for p in pts],
        }
    return None, FailureCode.OPT_FAILED, diag


def _nlp_config(
    ctx: "TourPlanningContext",
    window: CorridorWindow,
    vehicle: Any,
    max_time_s: float,
    extra_clearance: float,
) -> NlpConfig:
    """内层 NLP 配置：净空、半宽、走廊轴与枢轴必须与走廊窗口同源。

    求解净空 = ``clearance_m``（交付口径）+ SDF 量化容差 + 本次重解的抬阈值量。
    """
    return NlpConfig.from_params(
        ctx.params, vehicle,
        clearance_m=(float(ctx.params.clearance_m) + float(ctx.sdf_quant_tolerance_m)
                     + float(extra_clearance)),
        half_width_m=float(window.half_width_m),
        corridor_normal_xy=(float(window.normal[0]), float(window.normal[1])),
        corridor_pivot_xy=(float(window.start_xy[0]), float(window.start_xy[1])),
        max_time_s=float(max_time_s),
        alt_range_m=ctx.alt_range_m,
    )


def _limits(cfg: NlpConfig, traj: Trajectory, clearance_m: float,
            window: CorridorWindow) -> VerificationLimits:
    """段内复核的判据：净空按**交付口径**（``clearance_m``），其余与内层 NLP 同源。

    求解时可以把净空涨 ``clearance_fix_m`` 留余量（``cfg``里那份），但"能不能收"必须按
    交付阈值判——否则涨阈值那一路永远差 0.1–0.2 m 被判不收，重解等于白跑。

    走廊两段口径：``half_width_m``（名义半宽）照参考实现 pp1 只测量告警；
    ``corridor_lateral_m``（``window.lateral_m`` = 半宽 + margin）越界即
    ``CORRIDOR_OUT_OF_DOMAIN``——域外没建 SDF，净空不可核验。
    """
    band = (None if cfg.alt_min_m is None or cfg.alt_max_m is None
            else (float(cfg.alt_min_m), float(cfg.alt_max_m)))
    return VerificationLimits(
        clearance_m=float(clearance_m),
        half_width_m=None if cfg.half_width_m is None else float(cfg.half_width_m),
        corridor_lateral_m=float(window.lateral_m),
        corridor_normal_xy=cfg.corridor_normal_xy, corridor_pivot_xy=cfg.corridor_pivot_xy,
        endpoint_exempt_segments=int(cfg.endpoint_exempt_segments), sample_step_m=2.0,
        expected_start=traj.start_position, expected_end=traj.end_position, alt_range_m=band,
    )


