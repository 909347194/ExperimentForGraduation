# -*- coding: utf-8 -*-
"""exp07_polish_accept 入口：交付后成本 polish 采纳判据的同 seed 配对对照。

两档（直白命名，不用「臂 / A/B 变体」黑话）：

* ``path``：按**航程分量**裁决（旧语义，`accept_cost_polish_view`，当前默认，
  与 ``method-phase1`` 逐位可比）；
* ``f``：按**完整目标 f** 裁决（`accept_f_view`：f 严格改进且约束向量
  ``n_hard / 执行集 / 迟到 / 航程超限`` 逐分量不退化）。

唯一差异用 ``dataclasses.replace(stage_model, cost_polish_accept_mode=...)``
锁定，并在 ``summary.json`` 里落盘两档配置对象 diff（必须只有该字段不同）。
每个 seed 先跑完两档再进下一个 seed，**串行、不并行任何实验**。

产物（``results/<profile>/``）::

    <mode>/seed_<s>/metrics.json     单次运行指标 + 采纳判据对账 + 铁律检查
    <mode>/seed_<s>/run_meta.json    生效配置、指纹、时间戳
    summary.json                     同 seed 配对差值 + 配对 t 检验 + 配置 diff
    summary.csv                      逐（档 × seed）主指标表

命令行::

    python run.py                                 # 冒烟档 2 档 × 3 seed
    python run.py --seeds 7,11,23 --dry-run       # 只打印生效配置，不求解
    python run.py --profile formal \
        --set tasks.n_tasks=20 --set solver.population=50 \
        --set solver.max_iter=200 --set rolling.n_cycles=60 \
        --set rolling.arrivals.counts=[5,5]       # 正式档（需单独发话）

⚠️ 冒烟档只验证实验设计与统计口径，**结论不可外推正式档**。
   跑批不渲图；图由 ``plot/compare.py`` 事后单独生成。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# 任意调用方式可跑：先挂脚本目录，再由 paths.py 挂仓库根
sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import EXP_DIR, ROOT  # noqa: E402
from build import (  # noqa: E402
    build_arrivals,
    build_building_layer,
    build_cost_estimator,
    build_scenario,
    build_task_config,
    build_terrain,
    build_uavs,
)
from config import build_config_bundle, load_config  # noqa: E402
from metrics import (  # noqa: E402
    build_pair_report,
    collect_run_metrics,
    write_json,
    write_summary_csv,
)

from task_allocation.methods.rolling.horizon import RollingHorizon  # noqa: E402
from task_allocation.methods.task_pool import TaskPool  # noqa: E402
from task_allocation.methods.uav_state import UAVFleet  # noqa: E402

__all__ = ["main"]

#: 两档，顺序固定（配对差值方向 = f − path）
MODES = ("path", "f")

#: 冒烟档参数（formal 档必须显式覆盖，防止把冒烟数当正式结论）。
#: 审查 B5：补上 rolling.arrivals.counts —— 漏了它就会拿冒烟到达流跑正式档。
SMOKE_SIGNATURE = {
    "tasks.n_tasks": 8,
    "solver.population": 12,
    "solver.max_iter": 20,
    "rolling.n_cycles": 8,
    "rolling.arrivals.counts": [2, 2],
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _coerce_set_value(v: str) -> Any:
    s = v.strip()
    low = s.lower()
    if low in ("null", "none"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if s[:1] in ("[", "{") and s[-1:] in ("]", "}"):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _apply_dotted_set(cfg: dict, items) -> None:
    """--set dotted.path=value 深合并（与 exp01/exp05 同语义，禁平铺死键）。"""
    for it in items or []:
        if "=" not in it:
            raise ValueError(f"--set 需要 dotted.path=value 形式：{it!r}")
        path, _, val = it.partition("=")
        parts = [p.strip() for p in path.strip().split(".")]
        d = cfg
        for p in parts[:-1]:
            d = d.setdefault(p, {})
            if not isinstance(d, dict):
                raise ValueError(f"--set 路径冲突（非 dict 节点）：{path}")
        d[parts[-1]] = _coerce_set_value(val)


def _parse_seed_list(tokens: list[str]) -> list[int]:
    seeds: list[int] = []
    for tok in tokens:
        for part in str(tok).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part[1:]:
                lo_s, hi_s = part.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
                if lo > hi:
                    lo, hi = hi, lo
                seeds.extend(range(lo, hi + 1))
            else:
                seeds.append(int(part))
    out: list[int] = []
    for s in seeds:
        if s not in out:
            out.append(s)
    if not out:
        raise ValueError("--seeds 解析结果为空")
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="交付后成本 polish 采纳判据配对对照")
    p.add_argument("--seeds", nargs="+", metavar="SEED",
                   help="覆盖 experiment.seeds：7 / 7,11,23 / 7-23")
    p.add_argument("--set", action="append", default=[], metavar="dotted.path=value",
                   help="深合并覆盖 config.yaml 任意键（必须写全嵌套路径）")
    p.add_argument("--profile", choices=("smoke", "mid", "formal"), default="smoke",
                   help="结果子目录名；formal 需显式覆盖冒烟参数才允许运行；"
                        "mid = 可控中间档（与 compare_l3b.py 的 mid 同参数），"
                        "仍非正式档、结论不可外推")
    p.add_argument("--dry-run", action="store_true",
                   help="只打印生效配置，不构建场景、不求解、不落盘")
    return p.parse_args(argv)


def _get(cfg: Mapping[str, Any], dotted: str) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _signature_match(actual: Any, expected: Any) -> bool:
    """规范化比较冒烟值：yaml 里写字符串 ``"8"`` 与整数 8 视为相同。

    审查 B5：原版 ``==`` 类型敏感，把 ``8`` 改成 ``"8"`` 就能骗过防呆，
    拿冒烟参数跑 formal 档。列表逐元素递归（``counts: [2, 2]``）。
    """
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (int, float)):
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return False
    if isinstance(expected, list):
        return (
            isinstance(actual, (list, tuple))
            and len(actual) == len(expected)
            and all(_signature_match(a, e) for a, e in zip(actual, expected))
        )
    return actual == expected


def _check_formal_profile(raw_cfg: Mapping[str, Any]) -> None:
    """formal 档必须显式覆盖冒烟参数，避免冒烟结论被当正式结论。"""
    stale = [
        f"{k}={v}" for k, v in SMOKE_SIGNATURE.items()
        if _signature_match(_get(raw_cfg, k), v)
    ]
    if stale:
        raise SystemExit(
            "--profile formal 拒绝运行：仍是冒烟参数 " + ", ".join(stale)
            + "；请显式 --set 覆盖（冒烟档结论不可外推正式档）。"
        )


def _fingerprint(raw_cfg: Mapping[str, Any], mode: str, seed: int) -> str:
    # 审查 B7：指纹必须含 seed，否则同 mode 各 seed 指纹相同、无法区分产物。
    blob = json.dumps(
        {"config": raw_cfg, "mode": mode, "seed": int(seed)}, sort_keys=True,
        ensure_ascii=False, default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 单次运行
# ---------------------------------------------------------------------------


def _emit_cycle_progress(record, step: int, n_total: int) -> None:
    m = record.muas or {}
    print(
        f"[cycle {step}/{n_total}] 执行={len(getattr(record, 'executed_ids', []) or [])} "
        f"顺延={len(getattr(record, 'deferred_ids', []) or [])} "
        f"不可达={len(getattr(record, 'unreachable_ids', []) or [])} "
        f"| 求解={m.get('solve_seconds', '?')}s "
        f"mode={m.get('cost_polish_accept_mode', '?')}",
        flush=True,
    )


def _stage_effective(stage: Any) -> dict[str, Any]:
    """把第二阶段关键生效值落盘，避免「平铺死键静默不生效」不被发现。

    审查 B7：``coverage_audit`` 属于 ``problem_model`` 而非 stage 对象，
    已从本表移除（改由 ``run_meta.problem_effective`` 记录），避免静默缺失
    被误读成「未开」。
    """
    keys = (
        "cost_polish_accept_mode", "cost_polish_eps", "cost_polish_eps_f",
        "cost_polish_enabled", "repair_replacement", "lamarckian",
        "projection_only", "compare_mode", "decode_mode",
        "coverage_gate", "coverage_first",
        "l3b_enabled", "l3b_source", "l3b_legacy_conflict",
        "effective_l3b_enabled", "effective_repair_replacement",
        "effective_cost_polish_relocate", "effective_polish_moves",
    )
    out: dict[str, Any] = {}
    for k in keys:
        if hasattr(stage, k):
            v = getattr(stage, k)
            out[k] = list(v) if isinstance(v, tuple) else v
    return out


def _run_once(
    raw_cfg: dict,
    seed: int,
    mode: str,
    *,
    scenario: Any,
    nests: list,
    terrain: Any,
    buildings: Any,
    estimator: Any,
    verbose: bool,
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    """跑一档 × 一 seed，返回 (metrics, run_meta, stage_cfg)。"""
    t0 = time.time()
    seed_cfg = copy.deepcopy(dict(raw_cfg))
    seed_cfg.setdefault("experiment", {})["seed"] = seed
    seed_cfg.setdefault("solver", {})["seed"] = seed
    bundle = build_config_bundle(seed_cfg)

    # 唯一对照变量：只翻采纳判据，其余字段逐位不动
    stage_cfg = replace(bundle.stage_model, cost_polish_accept_mode=mode)

    uavs = build_uavs(nests, raw_cfg)
    task_cfg = build_task_config(raw_cfg, seed)
    batch = build_arrivals(nests, scenario, task_cfg, raw_cfg)
    pool = TaskPool(current_time=0.0)
    fleet = UAVFleet(uavs, current_time=0.0)

    horizon = RollingHorizon(
        pool,
        fleet,
        nests,
        dem=terrain,
        estimator=estimator,
        horizon_config=bundle.horizon,
        selection_config=bundle.selection,
        problem_config=bundle.problem_model,
        stage_config=stage_cfg,
        solver_config=bundle.solver,
        event_config=bundle.event,
        nest_offset=bundle.nest_offset,
    )
    # 每档独立任务副本：TaskPool 会原位改写 status / meta
    records = horizon.run(
        n_cycles=bundle.n_cycles,
        arrivals=copy.deepcopy(batch.arrivals),
        progress_cb=_emit_cycle_progress if verbose else None,
    )
    wall = round(time.time() - t0, 3)

    metrics = collect_run_metrics(
        records, pool=pool, batch=batch, seed=seed, mode=mode,
        wall_seconds=wall,
    )
    run_meta = {
        "experiment": "exp07_polish_accept",
        "mode": mode,
        "seed": seed,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str((EXP_DIR / "config.yaml").relative_to(ROOT)),
        "repo_root": str(ROOT),
        "config_fingerprint": _fingerprint(raw_cfg, mode, seed),
        "stage_effective": _stage_effective(stage_cfg),
        "problem_effective": {
            "lambda_cost": getattr(bundle.problem_model, "lambda_cost", None),
            "w_violation": getattr(bundle.problem_model, "w_violation", None),
            "coverage_audit": getattr(bundle.problem_model, "coverage_audit", None),
        },
        "solver_effective": {
            "pop_size": getattr(bundle.solver, "pop_size", None),
            "max_generations": getattr(bundle.solver, "max_generations", None),
            "seed": getattr(bundle.solver, "seed", None),
        },
        "arrivals_per_cycle": batch.per_cycle_counts(),
        "stop_reason": getattr(records[-1], "stop_reason", None) if records else None,
    }
    return metrics, run_meta, stage_cfg


def _config_diff(stages: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
    """两档 stage 配置对象 diff；必须只有 cost_polish_accept_mode 不同。

    审查 B7：逐个 seed 都核验（旧版只取每个 mode 的首个样本，若后续 seed
    配置漂移不会被发现）。
    """
    if set(stages) != set(MODES) or any(not stages[m] for m in MODES):
        counts = {m: len(v) for m, v in stages.items()}
        return {"error": f"每档都须有 stage 样本，实际 {counts}"}
    a = asdict(stages["path"][0])
    b = asdict(stages["f"][0])
    keys = sorted(set(a) | set(b))
    diff = {
        k: {"path": a.get(k), "f": b.get(k)}
        for k in keys if a.get(k) != b.get(k)
    }
    within = {
        m: all(asdict(s) == asdict(stages[m][0]) for s in stages[m])
        for m in MODES
    }
    return {
        "differing_fields": diff,
        "only_accept_mode_differs": set(diff) == {"cost_polish_accept_mode"},
        "n_differing_fields": len(diff),
        "per_seed_samples": {m: len(stages[m]) for m in MODES},
        "within_mode_identical_across_seeds": within,
        "all_within_mode_identical": all(within.values()),
    }


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    raw_cfg = load_config(EXP_DIR / "config.yaml")
    _apply_dotted_set(raw_cfg, args.set)
    if args.seeds:
        raw_cfg.setdefault("experiment", {})["seeds"] = _parse_seed_list(args.seeds)
    if args.profile == "formal":
        _check_formal_profile(raw_cfg)

    seeds = [int(s) for s in (raw_cfg["experiment"].get("seeds") or [raw_cfg["experiment"].get("seed")])]
    print(
        f"[exp07] profile={args.profile} modes={MODES} seeds={seeds} "
        f"n_tasks={_get(raw_cfg, 'tasks.n_tasks')} "
        f"pop={_get(raw_cfg, 'solver.population')} "
        f"gen={_get(raw_cfg, 'solver.max_iter')} "
        f"n_cycles={_get(raw_cfg, 'rolling.n_cycles')}",
        flush=True,
    )
    if args.dry_run:
        print(json.dumps(raw_cfg, ensure_ascii=False, indent=2, default=str))
        return 0

    out_root = EXP_DIR / "results" / args.profile

    # 场景只建一次（与 seed / 档无关）
    scenario = build_scenario(raw_cfg)
    nests = scenario.nests
    terrain = build_terrain(scenario, raw_cfg)
    buildings = build_building_layer(raw_cfg, scenario)
    estimator = build_cost_estimator(terrain, raw_cfg, buildings=buildings)
    print(
        f"[exp07] 场景：{scenario.count} 机巢，"
        f"DEM={'已接入' if terrain is not None else '未接入'}，"
        f"建筑={buildings.count if buildings is not None else 0} 栋",
        flush=True,
    )

    per_mode: dict[str, list[dict[str, Any]]] = {m: [] for m in MODES}
    stage_of_mode: dict[str, list[Any]] = {m: [] for m in MODES}
    verbose = bool((raw_cfg.get("run") or {}).get("verbose", True))

    # 串行：同一 seed 先跑完两档，再进下一个 seed；任何时刻只有一个求解在跑。
    # 审查 B4：按 seed 交替两档的先后，抵消共用 estimator/场景的缓存预热偏差
    # （旧版 f 档永远第二跑，墙钟差值混了预热，不是纯判据效应）。
    for seed_idx, seed in enumerate(seeds):
        modes = MODES if seed_idx % 2 == 0 else tuple(reversed(MODES))
        for mode in modes:
            t0 = time.time()
            metrics, run_meta, stage = _run_once(
                raw_cfg, seed, mode,
                scenario=scenario, nests=nests, terrain=terrain,
                buildings=buildings, estimator=estimator, verbose=verbose,
            )
            per_mode[mode].append(metrics)
            # 审查 B7：保留每个 seed 的样本，逐 seed 核验配置纯度
            stage_of_mode[mode].append(stage)
            run_dir = out_root / mode / f"seed_{seed}"
            write_json(run_dir / "metrics.json", metrics)
            write_json(run_dir / "run_meta.json", run_meta)
            print(
                f"[exp07] mode={mode:<4} seed={seed}: "
                f"完成率={metrics['completion_rate_final']:.3f} "
                f"按时={metrics['on_time_rate_final']:.3f} "
                f"航程={metrics['total_path_m'] / 1000:.2f}km "
                f"f之和={metrics['final_fitness_sum']:.3f} "
                f"采纳={metrics['cost_polish_n_accepted']} "
                f"漂移={metrics['cost_polish_n_drift']} "
                f"f-only改进={metrics['cost_polish_n_f_only_gain']} "
                f"检查={'PASS' if metrics['invariants_passed'] else 'FAIL'} "
                f"墙钟={time.time() - t0:.1f}s",
                flush=True,
            )

    config_diff = _config_diff(stage_of_mode)
    report = build_pair_report(
        per_mode, seeds=seeds, profile=args.profile, config_diff=config_diff,
    )
    write_json(out_root / "summary.json", report)
    write_summary_csv(out_root / "summary.csv", per_mode)

    print(
        f"[exp07] 两档配置 diff 字段数={config_diff.get('n_differing_fields')} "
        f"仅采纳判据不同={config_diff.get('only_accept_mode_differs')} "
        f"同档跨 seed 一致={config_diff.get('all_within_mode_identical')} "
        f"各项检查全过={report['all_invariants_passed']} "
        f"配对 seed={report['paired_seeds']}",
        flush=True,
    )
    print(f"[exp07] verdict: {report['verdict']}", flush=True)

    # 审查 A3：硬失败必须让进程以非零码退出，不能只打印警告后照样返回 0
    # （否则 CI / 批处理会把「完成率不达标」当成一次成功跑批）。
    rc = 0
    if not config_diff.get("only_accept_mode_differs"):
        print("[exp07] ⚠️ 两档配置存在额外差异，配对结论不可用！", flush=True)
        rc = 1
    if config_diff.get("all_within_mode_identical") is False:
        print("[exp07] ⚠️ 同档跨 seed 配置漂移，配对结论不可用！", flush=True)
        rc = 1
    if not report["all_invariants_passed"]:
        print(f"[exp07] ⚠️ 铁律检查失败：{report['invariant_failures']}", flush=True)
        rc = 1
    if not report["paired_seeds"] or report["unpaired_seeds"]:
        print(f"[exp07] ⚠️ 配对不完整：{report['unpaired_seeds']}", flush=True)
        rc = 1

    print(f"[exp07] 产物目录：{out_root}", flush=True)
    print(
        "[exp07] 提醒：冒烟档只验证设计与统计口径；正式档结论须单独跑批，"
        "配对 t 检验不显著须如实声明。",
        flush=True,
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
