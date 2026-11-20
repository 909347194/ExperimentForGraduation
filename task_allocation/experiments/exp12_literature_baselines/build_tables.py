# -*- coding: utf-8 -*-
"""build_tables.py — exp12 文献基线并排表生成器（单源：results_lit/ 落盘结果）。

从 ``results_lit/<ARM>/seed_*/metrics.json`` 读数，生成描述性并排表：

    results_lit/tables/literature_baselines.csv    全字段（mean±std）
    results_lit/tables/literature_baselines.tex    论文三线表（booktabs）
    results_lit/tables/literature_baselines.md     阅读版

口径（与 config.yaml / notes.md 一致）：

- **描述性**：只报 mean±std 与量级，不产 p 值、不产 verdict；
- 唯一比较尺 = ``path_m_audit``（DEM 有向三维航程几何重算）；
- 每完成航程 = ``path_m_audit / n_completed``（分母 0 记 NaN，不补 0）；
- 缺格（某 arm×seed 未跑）直接报错，不静默跳过——禁手写数字、禁凑表。
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
OUT_ROOT = EXP_DIR / "results_lit"
TABLE_DIR = OUT_ROOT / "tables"

# 并排表顺序与通道标注（族 / 通道）
ARMS: list[tuple[str, str, str]] = [
    # (id, 族, 通道)
    ("M0", "DMDE（本文）", "解码链+polish"),
    ("GA", "GA（文献）", "engine swap"),
    ("PMXDE", "PMX-DE（文献）", "engine swap"),
    ("SORTDE", "Sort-DE（文献）", "engine swap"),
    ("VRPTW", "VRPTW 插入（文献）", "构造式预构建"),
    ("GREEDY", "贪心插入（文献）", "构造式预构建"),
]

# 表列：(标题, metrics 键, 单位换算, 小数位)
COLUMNS: list[tuple[str, str, float, int]] = [
    ("完成率", "completion_rate_final", 1.0, 3),
    ("按时率(交付集)", "on_time_rate_final", 1.0, 3),
    ("交付数", "n_completed", 1.0, 1),
    ("总航程(km)", "path_m_audit", 1e-3, 2),
    ("每完成航程(km)", "__per_completion__", 1e-3, 2),
    ("硬违约", "n_hard_violations_total", 1.0, 1),
    ("淘汰", "n_eliminated", 1.0, 1),
    ("评价次数", "n_evals_total", 1.0, 0),
    ("墙钟(s)", "wall_seconds", 1.0, 1),
]

SEEDS_EXPECTED = [42, 43, 44]


def _load_cells() -> dict[str, list[dict]]:
    """arm → 逐 seed summary 列表（缺格即报错）。"""
    cells: dict[str, list[dict]] = {}
    for arm, _fam, _ch in ARMS:
        rows = []
        for seed in SEEDS_EXPECTED:
            p = OUT_ROOT / arm / f"seed_{seed}" / "metrics.json"
            if not p.is_file():
                raise SystemExit(f"缺格：{p} 不存在——先跑完 run.py 再出表（禁凑表）")
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        cells[arm] = rows
    return cells


def _values(rows: list[dict], key: str) -> list[float]:
    out = []
    for r in rows:
        if key == "__per_completion__":
            n = float(r.get("n_completed") or 0)
            v = (float(r["path_m_audit"]) / n) if n > 0 else float("nan")
        else:
            v = float(r.get(key, float("nan")))
        out.append(v)
    return out


def _mean_std(vals: list[float]) -> tuple[float, float]:
    ok = [v for v in vals if not math.isnan(v)]
    if not ok:
        return float("nan"), float("nan")
    mean = statistics.fmean(ok)
    std = statistics.stdev(ok) if len(ok) >= 2 else 0.0
    return mean, std


def main() -> int:
    cells = _load_cells()
    TABLE_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 数值汇总 ----
    table: list[dict[str, str]] = []
    for arm, fam, channel in ARMS:
        rows = cells[arm]
        row: dict[str, str] = {"arm": arm, "family": fam, "channel": channel}
        for title, key, scale, nd in COLUMNS:
            mean, std = _mean_std(_values(rows, key))
            mean *= scale
            std *= scale
            row[title] = f"{mean:.{nd}f}±{std:.{nd}f}"
        table.append(row)

    col_titles = [t for t, _k, _s, _d in COLUMNS]

    # ---- CSV ----
    csv_path = TABLE_DIR / "literature_baselines.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["方法", "族", "通道"] + [f"{t}(mean±std)" for t in col_titles])
        for row in table:
            w.writerow(
                [row["arm"], row["family"], row["channel"]]
                + [row[t] for t in col_titles]
            )

    # ---- LaTeX 三线表 ----
    tex_path = TABLE_DIR / "literature_baselines.tex"
    lines = [
        "% exp12_literature_baselines —— 文献基线并排表（描述性，3 seed mean±std）",
        "% 唯一比较尺：path_m_audit（DEM 有向三维航程几何重算）；禁写优于/性能提升。",
        "\\begin{table}[htbp]",
        "  \\centering",
        "  \\caption{文献代表基线与本文方法的并排对照（guangzhou 场景，"
        "seed 42/43/44，mean$\\pm$std，描述性）}",
        "  \\label{tab:literature_baselines}",
        "  \\begin{tabular}{lll" + "c" * len(col_titles) + "}",
        "    \\toprule",
        "    方法 & 族 & 通道 & "
        + " & ".join(t.replace("(", "\\ (").replace(")", "\\)") for t in col_titles)
        + " \\\\",
        "    \\midrule",
    ]
    for row in table:
        lines.append(
            "    "
            + row["arm"]
            + " & "
            + row["family"]
            + " & "
            + row["channel"]
            + " & "
            + " & ".join(row[t] for t in col_titles)
            + " \\\\"
        )
    lines += [
        "    \\bottomrule",
        "  \\end{tabular}",
        "\\end{table}",
    ]
    tex_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- Markdown 阅读版 ----
    md_path = TABLE_DIR / "literature_baselines.md"
    md = [
        "# 文献基线并排表（描述性，3 seed mean±std）",
        "",
        f"- 场景：guangzhou（3 巢×2 泊位、6 机 10km@15m/s）；seed {SEEDS_EXPECTED}",
        "- 唯一比较尺：`path_m_audit`（DEM 有向三维航程几何重算）",
        "- 红线：描述性量级对照，**不写优于/性能提升/SOTA**；不与 exp05 E1 推断混用",
        "",
        "| 方法 | 族 | 通道 | " + " | ".join(col_titles) + " |",
        "|---|---|---|" + "---|" * len(col_titles),
    ]
    for row in table:
        md.append(
            "| "
            + " | ".join(
                [row["arm"], row["family"], row["channel"]]
                + [row[t] for t in col_titles]
            )
            + " |"
        )
    md.append("")
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"[exp12] 并排表已生成：{csv_path.name} / {tex_path.name} / {md_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
