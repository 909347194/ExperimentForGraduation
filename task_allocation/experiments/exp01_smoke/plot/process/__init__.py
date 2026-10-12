# -*- coding: utf-8 -*-
"""exp01_smoke 图组：**process 层——求解过程观测**。

回答「滚动闭环到底怎么跑的」：逐周期、按顺序把一轮分配发生的事讲清楚。
与算法强相关，是方法调试与诊断的主要观察窗口。

六张图按一个周期内发生的先后顺序排列::

    task_selection   动态任务选择：漏斗收敛 / 保留率 / 预选咬合 / 池守恒
    dmde_search      DMDE 搜索机制：动态交叉率 CR / 探索—开发 / 收敛 / 开销
    allocation       单周期分配结构：UAV—任务甘特 / 空间分配 / 逐周期 DEM 演化
    task_dynamics    任务命运：存量 / 流量 / 状态矩阵 / 等待与滞留
    fleet_state      机队与机巢：三口径出动 / 占用热力 / 选巢归因
    constraint_state 约束与时限：预筛归因 / 约束状态 / 罚项量纲 / 时间窗

 registry.FIGURES 是事实来源；本层各模块导出 ``plot_<id>`` 统一签名函数。
"""
