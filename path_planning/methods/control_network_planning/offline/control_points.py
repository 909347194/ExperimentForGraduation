# -*- coding: utf-8 -*-
"""控制点布设（Lin2021 §2.2.1 Fig.5；README §4.3.5 离线阶段）。

职责：

    建筑外围与顶部布点，满足 d1（离建筑表面水平安全距离）、d2（离地最小高度）、
    d3（垂直方向层距，控制点密度参数）；
    静态永久禁飞区：控制点只布在安全缓冲之外（README §4.3.1）；
    建筑稀疏导致绕行严重时，补设虚拟控制点（Lin2021 §2.2.1 两个 issue 之一）。

输入→输出：CityEnvironment + OfflineParams → List[ControlPoint]。

不负责：连线（link_builder.py）。
"""
