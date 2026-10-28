# -*- coding: utf-8 -*-
"""参数与配置模型（L0）。

职责：把论文中的全部参数收拢为带默认值的配置对象，实验 config.yaml 的 schema 与之一一对应。

拟分组：

    UAVParams      m、F_min、F_max、v_max、v_up、v_down
                   （Lin2021 §3.1 参考值：m=1kg、F∈[9,16]N、v_max=15、v_up=6、v_down=2）
    OfflineParams  d1 水平安全距离、d2 离地高度、d3 垂直层距、d* 最大连线距离、
                   静态禁飞安全缓冲、虚拟控制点补设开关          —— Lin2021 §2.2.1 Fig.5
    OnlineParams   平滑偏差 d 及其收缩策略、二次校验迭代上限、轨迹离散步长
    RiskParams     综合代价权重 λ、TPR 安全阈值、昼/夜风险场选择   —— README §4.3.2-4.3.3

不负责：从文件解析（scenario.py 负责把文件解析为这些对象）、参数校验逻辑的调用点
（各 pipeline 入口应先校验）。
"""
