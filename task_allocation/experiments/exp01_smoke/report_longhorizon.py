"""长程协议结果速读：吞吐 / 按时率 / 迟到 / 废飞(物理不可行) / 淘汰 / 等待。

只读 results/metrics.json，不重跑实验。
用法：python report_longhorizon.py [results_dir]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "results"


def main() -> None:
    rdir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    m = json.loads((rdir / "metrics.json").read_text(encoding="utf-8"))
    s = m["summary"]

    exp = m.get("experiment")
    exp_name = exp.get("name") if isinstance(exp, dict) else exp
    print(f"实验            : {exp_name}")
    print(f"周期数          : {m.get('n_cycles')}")
    print(f"累计到达任务    : {s.get('n_arrived')}")
    print(f"累计完成任务    : {s.get('n_completed')}（含迟到交付）")
    print(f"完成率(窗口)    : {s.get('completion_rate')}")
    print(f"周期可行率      : {s.get('cycle_feasible_rate')}")
    print("-" * 56)
    print("── 长程吞吐口径（真实目标） ──")
    print(f"吞吐(任务/周期) : {s.get('throughput_per_cycle')}")
    print(f"平均正确执行率  : {s.get('correct_exec_rate')}（按时出动/总出动）")
    print(f"按时率          : {s.get('on_time_rate')}")
    print(f"迟到交付        : {s.get('n_late')}"
          f"（平均 {s.get('avg_lateness_s', 0) / 60:.1f} min，"
          f"最大 {s.get('max_lateness_s', 0) / 60:.1f} min）")
    print(f"废飞(物理不可行): {s.get('n_infeasible_delivery')}")
    print(f"不可达(过于偏远): {s.get('n_unreachable', 0)}"
          "（用户原则下唯一合法的未完成原因）")
    print(f"超期淘汰        : {s.get('n_eliminated')}")
    print(f"平均等待(s)     : {s.get('avg_wait')}")
    print(f"总航程(m)       : {s.get('total_cost')}")
    print(f"总收益(折减后)  : {s.get('total_reward')}")
    print(f"期末滞留池      : {s.get('n_remaining_pool')}")
    print(f"求解总耗时(s)   : {s.get('solve_time_ms', 0) / 1000:.1f}")
    print("-" * 56)

    print("逐周期：cycle  到达  完成  按时  迟到  废飞  不可达  淘汰  滞留  按时率")
    for c in m.get("cycles", []):
        pool = c.get("pool_counts", {})
        n_exec = c.get("n_executed", 0)
        n_inf = c.get("n_infeasible", 0)
        n_late = c.get("n_late", 0)
        n_done = c.get("n_completed", 0)
        n_unr = len(c.get("unreachable_ids", []))
        rate = c.get("correct_exec_rate")
        rate_s = f"{rate:.3f}" if isinstance(rate, (int, float)) else "  -  "
        print(
            f"        {c.get('cycle'):>3}  "
            f"{pool.get('n_new', 0)!s:>4}  "
            f"{n_done!s:>4}  {n_exec - n_inf - n_late!s:>4}  {n_late!s:>4}  "
            f"{n_inf!s:>4}  {n_unr!s:>4}  "
            f"{len(c.get('expired_ids', []))!s:>4}  "
            f"{pool.get('n_remain', 0)!s:>4}  {rate_s}"
        )


if __name__ == "__main__":
    main()
