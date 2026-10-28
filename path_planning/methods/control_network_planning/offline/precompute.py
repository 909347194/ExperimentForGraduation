# -*- coding: utf-8 -*-
"""离线预计算编排：链路飞行时间 + 链路 TPR 风险批量回填。

职责：遍历扩展网络全部虚拟链路，调用 cost/link_cost（时间）与 methods/tpr/risk_field
（风险）回填代价属性；白天/夜间两套风险场分别产出（README §4.3.2）。

不负责：算法公式（cost/ 与 tpr/）、落盘（network_io.py）。
"""
