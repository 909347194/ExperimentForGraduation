# -*- coding: utf-8 -*-
"""exp01_smoke 入口：真实广州全城场 + 两侧真方法的闭环验证。

任务一次性投放（周期 1 全部到达），周期数不预设：跑到每个任务都拿到真实航迹为止，
`loop.max_cycles` 只作防死循环的安全阀。

只做编排：装配 → 环驱动 → 落盘 → 审计。算法与参数都不在这里；控制台呈现统一走
``support.console``，分「设定 / 过程 / 结果」三段，所有行即时刷新。
本实验只有一档：周期内事实回灌（写实测代价 + 不可飞边），至多重解一次；不设对照档，
也不提供跳审计的开关——审计就是本实验的验证结论。

跑法（仓库根、模块方式）::
    python -u -m orchestration.experiments.exp01_smoke.run
    python -u -m orchestration.experiments.exp01_smoke.run --detail   # 追加逐航次明细表
"""

from __future__ import annotations

import argparse
import time
from dataclasses import replace

from orchestration import LoopDriver
from orchestration.adapters.allocator_side import RollingHorizonAdapter
from orchestration.adapters.planner_side import PathPlannerAdapter
from orchestration.protocol import LoopTrace

from .support import console as cli
from .support import estimator as estimator_mod
from .support import field as field_mod
from .support import fleet as fleet_mod
from .support import report as report_mod
from .support import tasks as tasks_mod
from .support import upper as upper_mod
from .support.audit import audit_trace
from .support.config import default_run_dir, load_config, validate

_STOP_TEXT = {
    "complete": "全部任务已到达",
    "no_progress": "无可派任务，提前收手",
    "safety_valve": "达到周期上限",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="exp01_smoke：真场编排闭环（单档：周期内事实回灌）")
    p.add_argument("--max-cycles", type=int, default=None,
                   help="覆盖 config 的 loop.max_cycles（运行上限，不是预设周期数）")
    p.add_argument("--detail", action="store_true",
                   help="结果段追加逐航次明细表（默认只打摘要，长表看 results/loop_trace.json）")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = load_config()
    if args.max_cycles is not None:
        cfg["loop"]["max_cycles"] = int(args.max_cycles)
    cfg = validate(cfg)                     # 覆盖 loop.max_cycles 后重校验
    out_dir = default_run_dir(cfg)

    # 任务与机队清单都是小文件，先读：设定段要在一场耗时几分钟的建场之前就打出来。
    tasks = tasks_mod.load_tasks(cfg)
    tags = tasks_mod.task_tags(tasks)
    rows = fleet_mod.load_uav_config()
    per_cycle = {int(c): sum(1 for t in tasks if int(t["cycle"]) == c)
                 for c in sorted({int(t["cycle"]) for t in tasks})}

    cli.banner("exp01_smoke 开始运行")
    cli.section(1, "实验设定")
    cli.kv("实验", f"{cfg['experiment']['name']}（seed {cfg['experiment']['seed']}）")
    cli.kv("场景", f"guangzhou：DEM 空洞按水面 {cfg['data']['water_level_m']} m 填充")
    cli.kv("机队", f"{sum(1 for r in rows if bool(r['enabled']))}/{len(rows)} 架机，"
           f"同巢错峰 {cfg['loop']['depart_stagger_s']:g} s")
    cli.kv("机巢", f"启用 {len(cfg['data']['active_nest_ids'])}/"
           f"{cfg['data']['nest_count']} 座")
    cli.kv("任务", f"{len(tasks)} 个（"
           + "、".join(f"周期{c} {n} 个" for c, n in per_cycle.items())
           + f"），指纹 {tasks_mod.sha256_of(tasks)[:12]}")
    sel = cfg["selection"]["capability"]
    sv = cfg["solver"]
    cli.kv("任务分配", f"滚动时域 + 单闸门选择（{sel['mode']}, n_compute={sel['n_compute']}），"
           f"pop{sv['pop_size']}×gen{sv['max_generations']}")
    plan_cfg = cfg["path"]["planning"]
    cli.kv("航迹规划", f"真场走廊 RRT + 联合优化，净空 {plan_cfg['clearance_m']:g} m，"
           f"椭球 {tuple(plan_cfg['ellipsoid_semiaxes_m'])} m")
    cli.kv("事实回灌", f"最多 {cfg['resolve']['max_in_cycle']} 次重解"
           "（触发：交付解出现几何不可飞边）")
    cli.kv("周期上限", f"{cfg['loop']['max_cycles']}（安全阀；全部到达即停）")
    cli.kv("输出目录", str(out_dir))

    # ---- 真场与两侧对象 -----------------------------------------------
    cli.section(2, "准备")
    t0 = time.perf_counter()
    scenario = field_mod.load_guangzhou(cfg)
    cli.stage(1, 5, "载入场景数据", f"{len(scenario.nests)} 座巢 / "
              f"{int(scenario.buildings.count)} 栋建筑" if scenario.buildings is not None
              else f"{len(scenario.nests)} 座巢")
    t0 = time.perf_counter()
    field, field_info = field_mod.load_or_build_field(cfg, scenario)
    contract_note = ""
    if field_info.get("max_od_length_m") is not None:
        contract_note = (
            f"；OD≤{field_info['max_od_length_m']:.0f} m / "
            f"pad={field_info['pad_m']:.0f} m（契约派生）"
        )
    cli.stage(2, 5, "全城 SDF 场",
              f"{'命中缓存' if field_info['source'] == 'cache' else '新建'}，"
              f"{cli.secs(field_info['elapsed_s'])}{contract_note}")
    t0 = time.perf_counter()
    prisms = field_mod.prisms_of(scenario, field, cfg)
    vehicles = fleet_mod.lower_vehicles(rows)
    planner, params, context = field_mod.build_planner(cfg, scenario, field, prisms, vehicles)
    cli.stage(3, 5, "装配航迹规划器", f"{len(vehicles)} 台机体，{cli.secs(time.perf_counter() - t0)}")
    t0 = time.perf_counter()
    estimator, estimator_info = estimator_mod.build_estimator(cfg, scenario)
    nests, all_nests = upper_mod.build_nests(cfg, scenario)
    uavs = fleet_mod.upper_uavs(rows, {n.id: n for n in nests})
    horizon = upper_mod.build_horizon(cfg, nests, uavs, estimator)
    cli.stage(4, 5, "装配任务分配器", f"{len(uavs)} 架机，{cli.secs(time.perf_counter() - t0)}")
    # 代理只加打印，不改任何返回值；环拿到的仍是同一批端口对象。
    arrivals = tasks_mod.arrivals(cfg, tasks, {n.id: n for n in nests})
    service_time = {int(t["id"]): float(t["service_time_s"]) for t in tasks}
    base_allocator = cli.AllocatorProgress(RollingHorizonAdapter(horizon), tags)
    # 实验侧错峰包在打印代理**外面**：adapter 直接读 horizon 的 pool/nests/
    # nest_offset 等内部属性，DepartStaggerAllocator 没有这些；它只转发端口
    # 方法，并在 step/prepare/resolve/commit 出口把同起点机号错开 0/Δ/2Δ…
    allocator = upper_mod.DepartStaggerAllocator(
        base_allocator, float(cfg["loop"]["depart_stagger_s"])
    )
    planner_port = cli.PlannerProgress(
        PathPlannerAdapter(planner, vehicles=vehicles,
                           service_time_of_task=service_time),
        tags,
    )
    driver = LoopDriver(
        allocator, planner_port,
        overrun_tol_s=float(cfg["loop"]["overrun_tol_s"]),
        prune_at_cycle_end=bool(cfg["loop"]["prune_at_cycle_end"]),
        max_in_cycle=int(cfg["resolve"]["max_in_cycle"]),
    )
    _require_resolve(driver)
    cli.stage(5, 5, "编排检查", "周期内事实回灌可用")
    cli.kv("提示", "同周期多机按机号错峰离巢；失败票承载的任务下一周期重投")

    # ---- 逐周期跑，航迹必须在下一周期 prune 前抓下来 --------------------
    # 周期数不预设：全部任务在周期 1 投放，跑到「每个任务都拿到真实航迹」为止。
    # loop.max_cycles 只是防死循环的安全阀；某周期既无航次也无新任务时提前收手。
    run_context = {
        **context,
        "nests": len(nests),
        # 出图只读结果：巢址必须进 run_meta，否则画图还得重建场景
        "nest_sites": [
            {"id": int(n.id), "x": float(n.x), "y": float(n.y), "z": float(n.z),
             "ground_z": float(n.meta["ground_z"]), "active": True}
            for n in nests
        ],
        "all_nest_sites": [
            {"id": int(i), "x": float(n.x), "y": float(n.y), "z": float(n.z),
             "ground_z": float(n.meta["ground_z"]),
             "active": bool(n.meta["active"])}
            for i, n in sorted(all_nests.items())
        ],
    }
    # 先立"这场是什么"，再逐周期追加账：长跑中途挂掉也不至于连配置都说不清。
    report_mod.write_meta(
        out_dir, cfg=cfg, tasks=tasks, field_info=field_info,
        estimator_info=estimator_info, context=run_context,
        tasks_sha256=tasks_mod.sha256_of(tasks),
    )

    max_cycles = int(cfg["loop"].get("max_cycles", 12))
    reopen_on_failure = bool(cfg["loop"].get("reopen_after_failure", True))
    plans, trajectories = [], {}
    landed: set[int] = set()
    stop_reason = "safety_valve"
    cli.section(3, "逐周期过程")
    for i in range(max_cycles):
        if i:
            cli.line("")
        t_cycle_start = time.perf_counter()
        planner_port.begin_cycle()
        plan = driver.step_one(arrivals[i] if i < len(arrivals) else None)
        # 回池：下层判死发生在"上层已把任务判完成"之后，必须显式重开，
        # 否则 rolled_back 只是账面上的数字，下一周期永远不重投。
        reopened: tuple[int, ...] = ()
        if reopen_on_failure and plan.rolled_back:
            reopened = allocator.reopen(plan.rolled_back)
            plan = replace(plan, reopened=reopened)
        plans.append(plan)
        trajectories[int(plan.cycle)] = dict(planner_port.last_trajectories)
        # 每周期落一次账：本周期之后挂掉，前几周期仍在盘上（不是白跑）。
        report_mod.write_trace(out_dir, plans, trajectories=trajectories)
        landed.update(int(tid) for t in plan.report.tickets.values() if t.ok
                      for tid in t.task_ids)
        flown = sum(len(t.task_ids) for t in plan.report.tickets.values() if t.ok)
        upd = plan.cost_update
        n_meas = len(upd.facts) if upd else 0
        n_inf = len(upd.infeasible) if upd else 0
        cli.line(f"周期 {plan.cycle}  时间窗 "
                 f"{plan.t_start:.0f}–{plan.t_end:.0f} s")
        cli.kv("分配结果", cli.assignments_text(plan.assignments, tags))
        cli.kv("周期结果", f"到达 {flown} 个，累计 {len(landed)}/{len(tasks)}，"
               f"回池 {len(reopened)} 个，重解 {plan.n_resolves} 次"
               f"（中间版失败 {plan.n_intermediate_failures} 个）")
        cli.kv("代价账", f"实测 {n_meas} 条 / 不可飞 {n_inf} 条，"
               f"版本 {plan.cost_version}，周期用时 {cli.secs(time.perf_counter() - t_cycle_start)}")
        if len(landed) >= len(tasks):
            stop_reason = "complete"
            break
        if not plan.report.tickets and i + 1 >= len(arrivals):
            stop_reason = "no_progress"
            break
    trace = LoopTrace(
        cycles=tuple(plans),
        executed_ids=tuple(t for p in plans for t in p.executed_ids),
        overrun_cycles=tuple(p.cycle for p in plans if p.overruns),
        failures={int(uid): str(t.failure)
                  for p in plans for uid, t in p.report.tickets.items()
                  if not t.ok},
    )

    problems = audit_trace(
        trace, cfg, tasks=tasks, context=context,
        scene_meta={"building_count": estimator_info["building_count"],
                    "nests": len(nests)},
        # 错峰集合由实验侧装饰器给出；它包在打印代理外面，可见 allowed_stagger_s。
        allowed_offsets=allocator.allowed_stagger_s(),
        fleet_ids=[int(u.id) for u in uavs],
    )
    metrics = report_mod.write_all(
        out_dir, cfg=cfg, trace=trace, tasks=tasks,
        field_info=field_info, estimator_info=estimator_info,
        context=run_context,
        trajectories=trajectories,
        problems=problems,
        uav_ids=[int(r["id"]) for r in rows],
        tasks_sha256=tasks_mod.sha256_of(tasks),
    )

    cli.section(4, "实验结果")
    cli.kv("运行周期", f"{metrics['cycles']} 个（{_STOP_TEXT[stop_reason]}）")
    cli.kv("任务到达", f"{metrics['flown_tasks']}/{metrics['task_total']}"
           f"（{metrics['completion_rate']:.1%}）")
    cli.kv("航次", f"{metrics['tickets_ok']}/{metrics['tickets_total']} 成功")
    cli.kv("排程", f"{metrics['assigned_tasks']}/{metrics['task_total']} 个；"
           f"从未排程 {cli.compact_ids(metrics['never_assigned_ids'], tags)}")
    cli.kv("闲置机", f"整场无成功航次 {[int(v) for v in metrics['idle_uavs']] or '无'}；"
           f"从未派出 {[int(v) for v in metrics['never_dispatched_uavs']] or '无'}")
    cli.kv("事实回灌", f"重解 {metrics['total_resolves']} 次，写入不可飞边 "
           f"{metrics['infeasible_edges_written']} 条 / 实测边 "
           f"{metrics['measured_edges_written']} 条，残留 "
           f"{metrics['unresolved_infeasible_edges']} 条")
    cli.kv("结果目录", str(out_dir))
    failed = cli.failed_tickets(trace, tags)
    if failed:
        cli.line("  失败航次")
        for text in failed:
            cli.line(text)
    if args.detail:
        cli.line("")
        cli.line("明细表（--detail）：")
        cli.line(report_mod.cycle_table(trace, tags))
        cli.line("")
        cli.line(report_mod.resolve_table(trace, tags))
        cli.line("")
        cli.line(report_mod.failure_table(trace, tags))

    cli.section(5, "审计")
    if problems:
        cli.kv("结论", "FAIL")
        for p in problems:
            cli.line(f"  - {p}")
        return 1
    cli.kv("结论", "PASS")
    cli.kv("口径", f"{metrics['cycles']} 周期 / "
           f"{metrics['tickets_ok']}/{metrics['tickets_total']} 航次成功 / "
           f"{metrics['flown_tasks']}/{metrics['task_total']} 任务有航迹")
    return 0


def _require_resolve(driver: LoopDriver) -> None:
    """本实验只有"事实回灌"这一档：做不了周期内重解就启动即报错。

    判据只在 ``LoopDriver.resolve_blocker()`` 一处；实验层不另立口径，
    也不许把"缺能力"跑成"看着像回灌了"。
    """
    blocker = driver.resolve_blocker()
    if blocker is not None:
        raise ValueError(f"周期内事实回灌不可用：{blocker}")


if __name__ == "__main__":
    raise SystemExit(main())
