# -*- coding: utf-8 -*-
"""城市环境/场景数据解析（L0 中唯一允许做文件读取的模块）。

职责：把 data/raw 下的原始数据解析为内存环境对象 CityEnvironment：

    建筑（长方体化；非长方体取最小外包长方体）      —— Lin2021 §2.2.1
    DEM/地形（若与任务分配共用场景，口径须与
      task_allocation/common/scenario_data.py 对齐，含坐标系 NED/本地笛卡尔）
    人口密度栅格（昼/夜两套）                      —— README §4.3.2
    静态永久禁飞区、动态临时禁飞区初始集            —— README §4.3.1

输入→输出：data/raw/* → CityEnvironment（供 offline/pipeline 与 constraints/query 消费）。

不负责：几何相交判定（utils/geometry.py）、控制点布设（offline/control_points.py）。
"""
