# -*- coding: utf-8 -*-
"""领域核心类型（L0，纯数据结构，无算法、无 IO）。

职责：定义全模块共享的名词，与论文术语一一对应：

    route       控制网络上的连续虚拟链路序列（可行"走廊"）  —— Lin2021 Fig.1(a)
    path        路径平滑后的三维空间几何曲线                —— Lin2021 Fig.1(b)
    trajectory  带时间维的时空轨迹（位置/速度/加速度）       —— Lin2021 Fig.1(c)

拟定义类型：

    Vec3 / Cuboid          基础几何
    MotionState            运动状态 0..6（0=悬停；1..6=六方向最高速）  —— Lin2021 Fig.8(a)
    ControlPoint           控制点（位置、层号、来源：实点/虚拟点/补设点）
    Link / VirtualNode / VirtualLink   原始链路与扩展网络骨架
    ExpandedNetwork        扩展控制网络（离线产物的内存形态）
    Route / Path / Trajectory          三级结果对象
    ODRequest              在线输入（起点、终点、出发时刻、机型参数引用）
    PlanResult             在线输出（route/path/trajectory、总飞行时间、
                           总 TPR 风险、可行性标记、InfeasibilityReason）

不负责：参数默认值（config.py）、事件词汇表（events.py）、文件读写（scenario.py / network_io.py）。
映射：Lin2021 §2.1 三术语定义；README §4.3.5 离线-在线完整工作流程。
"""
