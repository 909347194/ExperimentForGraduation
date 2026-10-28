# -*- coding: utf-8 -*-
"""exp01_smoke 目录布局与导入引导。

仓库根不在 ``sys.path`` 里（实验目录靠脚本目录机制被找到），因此这里做一次
显式挂载：其它模块只需 ``from paths import ROOT``，不必各自 ``sys.path.insert``，
避免顺序依赖被悄悄破坏。

约定（见 ``experiments/README.md``）::

    EXP_DIR/      本实验：config.yaml / run.py / 各拆分模块
    EXP_DIR/results/   数值产物（metrics.json / run_meta.json / solution.json）
    EXP_DIR/plot/      可视化模块（代码）
    EXP_DIR/plot/figures/  图产物（png / pdf）
    DATA_DIR/     场景数据注册表根（task_allocation/data/；场景目录取
                  data/<scenario>/，解析统一走 common/scenario_data.py）
"""

from __future__ import annotations

import sys
from pathlib import Path

#: 本实验目录
EXP_DIR = Path(__file__).resolve().parent
#: 仓库根目录
ROOT = EXP_DIR.parents[2]
#: 场景数据注册表根（task_allocation/data/；场景目录取 data/<scenario>/，
#: 文件解析用 task_allocation.common.scenario_data.resolve_file）
DATA_DIR = ROOT / "task_allocation" / "data"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

__all__ = ["EXP_DIR", "ROOT", "DATA_DIR"]
