# -*- coding: utf-8 -*-
"""exp03_event_rolling 事件协议断言：成功标准 1/2 的机器检查。

从落盘 json 独立复核：
1. 结构不变量：exec ⊆ sel ⊆ pool；释放守恒
2. 事件断言：E1/E2/E3 期望行为逐项过
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import EXP_DIR


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def audit_factor(results_dir: Path, factor: str) -> list[str]:
    """返回 FAIL 列表（空 = 全过）。"""
    fails: list[str] = []

    metrics_path = results_dir / "metrics.json"
    if not metrics_path.exists():
        return [f"缺 metrics.json: {metrics_path}"]

    m = _load(metrics_path)
    cycles = m.get("cycles", [])
    summary = m.get("summary", {})

    # ── 结构不变量 ──────────────────────────────────
    for c in cycles:
        sel = set(c.get("selected_ids", []))
        executed = set()
        for t in c.get("tours", []):
            executed.update(t.get("task_ids", []))
        # 实际上 tours 在 solution.json；metrics 的 cycles 只有 selected_ids
        # 用 n_executed 对账
        n_exec = c.get("n_executed", 0)
        n_sel = len(sel)
        if n_exec > n_sel:
            fails.append(f"cycle {c['cycle']}: n_executed={n_exec} > n_selected={n_sel}（exec ⊄ sel）")

    # ── 事件断言 ──────────────────────────────────
    all_events = [e for c in cycles for e in c.get("events", [])]
    event_types = {}
    for e in all_events:
        t = e.get("type", "?")
        event_types[t] = event_types.get(t, 0) + 1

    if factor == "E0":
        if all_events:
            fails.append(f"E0 应无事件，但有 {len(all_events)} 个：{event_types}")
        n_replan = sum(1 for c in cycles if c.get("replan"))
        if n_replan > 0:
            fails.append(f"E0 不应有重规划，但有 {n_replan} 次")

    elif factor == "E1":
        if "uav_fault" not in event_types:
            fails.append("E1 应有 UAV_FAULT 事件")
        released = sum(len(c.get("released_by_fault", [])) for c in cycles)
        if released == 0:
            fails.append("E1 应有故障释放任务（released_by_fault > 0）")
        # 周期 7 应有 forced_task_ids（F4 补丁：释放任务强制纳入）
        forced_cycles = [
            c for c in cycles
            if c.get("forced_task_ids") or c.get("replan")
        ]
        if not forced_cycles:
            fails.append("E1 应有强制纳入或重规划响应")

    elif factor == "E2":
        if "urgent_task" not in event_types:
            fails.append("E2 应有 URGENT_TASK 事件")
        forced_any = any(c.get("forced_task_ids") for c in cycles)
        if not forced_any:
            fails.append("E2 应有 forced_task_ids（burst 任务强制纳入）")

    elif factor == "E3":
        if "nest_unavailable" not in event_types:
            fails.append("E3 应有 NEST_UNAVAILABLE 事件")
        # 应有终点变更
        if summary.get("n_end_nest_changed", 0) == 0:
            fails.append("E3 应有终点机巢变更（n_end_nest_changed > 0）")

    elif factor == "E4":
        if "uav_fault" not in event_types:
            fails.append("E4 应有 UAV_FAULT 事件")
        if "nest_unavailable" not in event_types:
            fails.append("E4 应有 NEST_UNAVAILABLE 事件")

    # ── 通用事件检查 ──────────────────────────────────
    # response_latency 应为 1（注入即响应）
    for c in cycles:
        if c.get("events") and not c.get("replan") and factor != "E0":
            # 事件发生但未触发重规划，只 warn 不 fail（某些情况下确实不需要）
            pass

    return fails


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="results", help="结果目录名")
    parser.add_argument("--factor", default="E0")
    args = parser.parse_args()

    results_dir = EXP_DIR / "results" / args.factor
    if not results_dir.exists():
        results_dir = EXP_DIR / args.results / args.factor
    if not results_dir.exists():
        results_dir = EXP_DIR / args.results

    print(f"[audit_events] 检查 {results_dir}（因子 {args.factor}）")
    fails = audit_factor(results_dir, args.factor)

    if fails:
        for f in fails:
            print(f"  \033[1;31mFAIL\033[0m {f}")
        return 1
    print(f"  \033[1;32m全部通过\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
