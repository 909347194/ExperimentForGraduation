"""聚合 seed 42/43/44 在 gen100 vs gen200 下的对照，重点看 on_time_rate 增益是否稳健。

读取目录约定：
  seed 42 : results_gen100 / results_gen200   （已跑）
  seed 43 : results_gen100_s43 / results_gen200_s43
  seed 44 : results_gen100_s44 / results_gen200_s44
各目录含 metrics.json，取其 summary。
"""
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
SEEDS = [42, 43, 44]
GENS = [100, 200]


def load(seed, gen):
    if seed == 42:
        d = HERE / f"results_gen{gen}"
    else:
        d = HERE / f"results_gen{gen}_s{seed}"
    m = json.load(open(d / "metrics.json", encoding="utf-8"))
    return m.get("summary", m)


KEYS = [
    ("on_time_rate_final", "按时率", ".4f"),
    ("completion_rate_final", "完成率", ".4f"),
    ("path_per_completion_m", "航程/完成(m)", ".2f"),
    ("total_path_m", "总航程(m)", ".1f"),
    ("unreachable_rate", "不可达率", ".4f"),
    ("s_coverage_rate", "门禁通过率", ".4f"),
    ("n_hard_violations_total", "硬违约数", "d"),
    ("wall_seconds", "墙钟(s)", ".1f"),
]


def main():
    rows = {}
    for s in SEEDS:
        for g in GENS:
            rows[(s, g)] = load(s, g)

    # 1) 逐种子逐指标表
    print("=" * 78)
    print("逐种子对照 (gen100 vs gen200, 同种子唯一变量 max_iter)")
    print("=" * 78)
    for s in SEEDS:
        print(f"\n----- seed {s} -----")
        print("%-16s %14s %14s %14s" % ("metric", "gen100", "gen200", "Δ(200-100)"))
        for key, label, fmt in KEYS:
            v100 = rows[(s, 100)].get(key)
            v200 = rows[(s, 200)].get(key)
            if isinstance(v100, (int, float)) and isinstance(v200, (int, float)):
                print("%-16s %14s %14s %14s" % (
                    label, format(v100, fmt), format(v200, fmt), format(v200 - v100, fmt)))
            else:
                print("%-16s %14s %14s" % (label, str(v100), str(v200)))

    # 2) 聚合：每档跨 seed 均值 ± 按时率增量
    print("\n" + "=" * 78)
    print("跨种子聚合 (mean ± std, n=3)")
    print("=" * 78)
    print("%-16s %14s %14s %14s %14s" % ("metric", "gen100μ", "gen200μ", "Δμ", "Δ(200-100)"))
    for key, label, fmt in KEYS:
        vals100 = [rows[(s, 100)].get(key) for s in SEEDS]
        vals200 = [rows[(s, 200)].get(key) for s in SEEDS]
        if all(isinstance(v, (int, float)) for v in vals100 + vals200):
            mu100 = sum(vals100) / len(vals100)
            mu200 = sum(vals200) / len(vals200)
            sd100 = (sum((v - mu100) ** 2 for v in vals100) / len(vals100)) ** 0.5
            sd200 = (sum((v - mu200) ** 2 for v in vals200) / len(vals200)) ** 0.5
            print("%-16s %14s %14s %14s %14s" % (
                label,
                f"{mu100:.4f}±{sd100:.4f}",
                f"{mu200:.4f}±{sd200:.4f}",
                f"{mu200-mu100:+.4f}",
                f"{mu200-mu100:+.4f}"))
        else:
            print("%-16s %14s %14s" % (label, str(vals100), str(vals200)))

    # 3) 结论判定
    ot100 = [rows[(s, 100)].get("on_time_rate_final") for s in SEEDS]
    ot200 = [rows[(s, 200)].get("on_time_rate_final") for s in SEEDS]
    mu_ot100 = sum(ot100) / len(ot100)
    mu_ot200 = sum(ot200) / len(ot200)
    gains = [b - a for a, b in zip(ot100, ot200)]
    print("\n" + "=" * 78)
    print("按时率增益稳健性判定")
    print("=" * 78)
    print(f"逐种子 Δ(200-100) 按时率: " + ", ".join(
        f"seed{s}={g:+.3f}" for s, g in zip(SEEDS, gains)))
    print(f"跨种子均值: gen100={mu_ot100:.4f} -> gen200={mu_ot200:.4f}  (Δμ={mu_ot200-mu_ot100:+.4f})")
    n_pos = sum(1 for g in gains if g > 0.01)
    print(f"正向增益(>1pp)种子数: {n_pos}/{len(SEEDS)}")
    if n_pos == len(SEEDS):
        print("结论: 200 代对按时率有 一致 正向增益 -> 支持保留 200 代")
    elif n_pos == 0:
        print("结论: 200 代对按时率 无 一致正向增益 -> 不支持凭此保留 200 代(建议回退100代)")
    else:
        print("结论: 200 代对按时率增益 不稳定(部分种子) -> 需更多种子/生产规模确认")


if __name__ == "__main__":
    main()
