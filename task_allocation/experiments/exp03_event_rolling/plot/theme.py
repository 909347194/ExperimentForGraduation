# -*- coding: utf-8 -*-
"""exp03_event_rolling 共享绘图样式。"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 中文显示
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

#: 因子配色
FACTOR_COLORS = {
    "E0": "#2196F3",
    "E1": "#FF5722",
    "E2": "#4CAF50",
    "E3": "#FF9800",
    "E4": "#9C27B0",
}

#: 因子标签
FACTOR_LABELS = {
    "E0": "E0 无事件",
    "E1": "E1 UAV故障",
    "E2": "E2 突发高优",
    "E3": "E3 机巢闭塞",
    "E4": "E4 组合",
}
