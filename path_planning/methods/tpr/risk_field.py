# -*- coding: utf-8 -*-
"""时变 TPR 风险场与链路风险预计算。

职责：TPR(link) = p_crash(link) × 暴露(link) × 后果，支持白天/夜间两套风险场
（README §4.3.2 时变风险场）；提供：

    批量预计算接口 —— 供 offline/precompute 回填全部虚拟链路
    单链路时变查询  —— 供 cost/composite 在线加权

不负责：加权综合代价（cost/composite.py）。
"""
