# -*- coding: utf-8 -*-
"""离线阶段编排门面（README §4.3.5「离线预处理阶段」）。

职责：只做编排调度，不堆细节（与 task_allocation/methods/muas/solvers/muas_stage.py
的门面风格一致）：

    CityEnvironment → control_points → link_builder → virtual_network
                    → precompute → network_io(data/processed)

输出：带参数指纹的扩展控制网络产物。
"""
