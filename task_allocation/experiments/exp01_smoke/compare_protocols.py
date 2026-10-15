"""协议前后对照 + 新 2×2 汇总：旧硬闸门 vs 「迟到不是废飞」新协议 vs 「只有物理约束」物理口径。

只读各 results 目录的 metrics.json，不重跑实验。
用法：python compare_protocols.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (标签, 目录, 协议, seed, 代数)
RUNS = [
    ("s42@500 旧闸门", "results_pre_latefix", "旧", 42, 500),
    ("s42@100 旧闸门", "results_g100_pre", "旧", 42, 100),
    ("s43@500 旧闸门", "results_s43_pre", "旧", 43, 500),
    ("s43@100 旧闸门", "results_s43g100_pre", "旧", 43, 100),
    ("s42@500 新协议", "results", "新", 42, 500),
    ("s42@100 新协议", "results_g100", "新", 42, 100),
    ("s43@500 新协议", "results_s43", "新", 43, 500),
    ("s43@100 新协议", "results_s43g100", "新", 43, 100),
    ("s42@500 物理口径", "results_phys", "物理", 42, 500),
    ("s42@100 物理口径", "results_phys_g100", "物理", 42, 100),
]

KEYS = [
    ("n_arrived", "到达"),
    ("n_completed", "完成"),
    ("throughput_per_cycle", "吞吐"),
    ("correct_exec_rate", "正确率"),
    ("on_time_rate", "按时率"),
    ("n_late", "迟到"),
    ("avg_lateness_s", "均迟到s"),
    ("n_infeasible_delivery", "废飞"),
    ("n_eliminated", "淘汰"),
    ("n_unreachable", "不可达"),
    ("avg_wait", "等待s"),
    ("total_cost", "航程m"),
    ("total_reward", "收益"),
]


def _load(rdir: str) -> dict | None:
    p = HERE / rdir / "metrics.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _fmt(v, key) -> str:
    if v is None:
        return "  -  "
    if key in ("throughput_per_cycle",):
        return f"{v:.3f}"
    if key in ("correct_exec_rate", "on_time_rate"):
        return f"{v * 100:.1f}%"
    if key in ("avg_lateness_s", "avg_wait"):
        return f"{v:.0f}"
    if key in ("total_cost",):
        return f"{v / 1000:.0f}k"
    if key == "total_reward":
        return f"{v:.0f}"
    return str(v)


def main() -> None:
    print(f"{'运行':<16}" + "".join(f"{lab:>9}" for _, lab in KEYS))
    print("-" * (16 + 9 * len(KEYS)))
    for label, rdir, proto, seed, gen in RUNS:
        m = _load(rdir)
        if m is None:
            print(f"{label:<16}(未找到 {rdir})")
            continue
        s = m["summary"]
        row = "".join(f"{_fmt(s.get(k), k):>9}" for k, _ in KEYS)
        print(f"{label:<16}{row}")


if __name__ == "__main__":
    main()
