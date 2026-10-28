# -*- coding: utf-8 -*-
"""综合代价函数：飞行时间 - TPR 风险加权（README §4.3.3）。

职责：cost = λ_t · time(link) + λ_r · TPR(link)（权重口径以 config.RiskParams 为准），
统一暴露给 route_search 与 cost_matrix，保证"搜索目标"与"回填代价"同源。

不负责：各分量的计算（link_cost.py / risk_field.py）。
"""
