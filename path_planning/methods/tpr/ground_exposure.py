# -*- coding: utf-8 -*-
"""地面第三方暴露与后果建模。

职责：基于人口密度栅格（common/scenario 解析）计算航段下方暴露人口/财产 E(link)，
即 TPR 的第二、三因子 —— README §4.3.2。

输入→输出：(Link, 人口密度栅格) → 暴露量 E。

不负责：风险场时变切换（risk_field.py）。
"""
