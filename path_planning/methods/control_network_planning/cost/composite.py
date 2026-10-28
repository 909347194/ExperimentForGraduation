# -*- coding: utf-8 -*-
"""综合代价函数：飞行时间 - TPR 风险加权（README §4.3.3）。

职责：cost = λ_t · time(link) + λ_r · TPR(link)（权重口径以 config.RiskParams 为准），
统一暴露给 route_search 与 cost_matrix，保证"搜索目标"与"回填代价"同源。

阶段口径（design_nofly_zones.md §0/§10）：当前阶段只做禁飞可行性，**λ_r = 0**，
目标退化为纯飞行时间；λ_r 槽位保留，TPR 为未来拓展。

不负责：各分量的计算（link_cost.py / risk_field.py）。
"""
