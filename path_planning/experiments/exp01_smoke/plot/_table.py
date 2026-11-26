# -*- coding: utf-8 -*-
"""写表件：一张表一个 CSV + 一个 TeX（表头、caption、label、脚注同源）。

为什么单独一个模块：两个表脚本共用的只有"怎么落盘"，口径必须完全一致
（CSV 与 TeX 同一份行、同一套数字格式），所以写成函数而不是各抄一遍。
"""

from __future__ import annotations

import csv
from pathlib import Path


def write_rows(path: Path, rows: list[dict], *, caption: str, label: str, note: str) -> list:
    """写 ``<name>.csv`` 与 ``<name>.tex``，返回两个文件路径。"""
    if not rows:
        raise ValueError(f"{path.name}：没有可写的行（结果目录里缺数据？）")
    cols = list(rows[0])

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([_cell(r[c]) for c in cols])

    lines = [
        f"% {note}",
        "\\begin{table}[htbp]",
        "  \\centering",
        f"  \\caption{{{caption}}}",
        f"  \\label{{{label}}}",
        f"  \\begin{{tabular}}{{l{'r' * (len(cols) - 1)}}}",
        "    \\toprule",
        "    " + " & ".join(cols) + " \\\\",
        "    \\midrule",
    ]
    for r in rows:
        lines.append("    " + " & ".join(_cell(r[c]).replace("&", "\\&") for c in cols) + " \\\\")
    lines += ["    \\bottomrule", "  \\end{tabular}", "\\end{table}", ""]
    path.with_suffix(".tex").write_text("\n".join(lines), encoding="utf-8")
    return [path.with_suffix(".csv"), path.with_suffix(".tex")]


def _cell(v) -> str:
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)
