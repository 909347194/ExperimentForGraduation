# -*- coding: utf-8 -*-
"""paths.py — exp04 目录定位与 sys.path 引导。

职责：只定位本实验目录与仓库根，把两者插入 sys.path。
边界：不加载配置、不构建场景（归 config.py / run.py）。
"""

from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
ROOT = EXP_DIR.parents[2]          # .../task_allocation/experiments/exp04 -> repo root
DATA_DIR = ROOT / "data"
RESULTS_DIR = EXP_DIR / "results"

for _p in (str(ROOT), str(EXP_DIR)):
    if _p not in __import__("sys").path:
        __import__("sys").path.insert(0, _p)
