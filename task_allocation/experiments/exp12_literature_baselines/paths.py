# -*- coding: utf-8 -*-
"""exp05_baselines 目录布局与导入引导（与 exp01 同构，见 experiments/README.md）。

仓库根不在 ``sys.path`` 里（实验目录靠脚本目录机制被找到），因此这里做一次
显式挂载：其它模块只需 ``from paths import ROOT``。

约定::

    EXP_DIR/                 本实验：config.yaml / run.py / 各拆分模块
    EXP_DIR/results_e1/      E1 唯一数值产物（<ID>/seed_<s>/{metrics,run_meta,solution,audit}.json）
    EXP_DIR/results_e1/claims_report.json   E1 主判据 + 门槛 + 一致性 + 红线（单源）
    EXP_DIR/results_e1/summary_primary.csv  4 方法逐 seed 明细 + 均值行
    （旧 v3 / v1v2 档一律冻结为历史存档、不进主文；顶层 results_v3/
      半截档已于 2026-11-13 清理，完整档见
      ../_archive/exp05_baselines__results_v3/；v1v2 见
      ../_archive/exp05_baselines__results_v1v2_2026-10-25/）
    DATA_DIR/                场景数据注册表根（data/；
                             场景目录取 data/<scenario>/，解析走 scenario_data）
"""

from __future__ import annotations

import sys
from pathlib import Path

#: 本实验目录
EXP_DIR = Path(__file__).resolve().parent
#: 仓库根目录
ROOT = EXP_DIR.parents[2]
#: 场景数据注册表根（data/）；场景文件用
#: task_allocation.common.scenario_data.resolve_file 解析
DATA_DIR = ROOT / "data"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

__all__ = ["EXP_DIR", "ROOT", "DATA_DIR"]
