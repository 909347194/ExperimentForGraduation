# -*- coding: utf-8 -*-
"""链路飞行时间代价（动力学预计算，Lin2021 §2.2.2）。

职责：按 4 种虚拟链路状态组合给出最小飞行时间（Lin2021 Eq.(1)-(4)）：

    sA=0, sB=0    停→停：加-匀-减或纯加减三角形（Eq.1，按 D 与 v_u²(1/a_AB+1/a_BA)/2 比较分段）
    sA=0, sB≠0   停→通：Eq.(2)
    sA≠0, sB=0   通→停：Eq.(3)
    sA≠0, sB≠0   通→通：D / v_u（Eq.4）

其中加减速 a_AB/a_BA 由升力限幅 [F_min, F_max] 与重力合成推导（Lin2021 Fig.10），
限速 v_u 由 v_max/v_up/v_down 与航段俯仰决定（Lin2021 §2.2.2）。

不负责：TPR 项（methods/tpr/）、加权合成（composite.py）。
"""
