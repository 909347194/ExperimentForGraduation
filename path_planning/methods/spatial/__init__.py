# -*- coding: utf-8 -*-
"""空间层：走廊构造、SDF 场、RRT 初始路径、几何优化、初始候选集合。

职责：把"障碍在哪"变成一个可查询的场，并把"从哪飞到哪"变成若干条**纯几何**
      候选路径（不带时间）。
边界：不做时间映射、不做冲突检测、不做局部重规划——那些在 ``timeline`` /
      ``conflict`` / ``coordination``。
相关：``world_field.py``（全域 SDF，规划里唯一的障碍真值）、
      ``occupancy_sdf.py``（栅格化与 EDT）、``corridor_window.py``（斜栅格走廊）、
      ``common/grid_spec.py``（栅格坐标契约）。

对外只应通过本包拿 SDF：``build_world_field(scenario, ...)`` 一次建场，
其余模块一律用它取净空。若某个模块自己又算一遍距离，两处口径迟早分叉，
而"到底是 6 m 还是 6.2 m 算安全"这种分歧不会以异常形式暴露。

本包文件分工：

* ``field_contract.py``   契约异常
* ``field_artifact.py``   场指纹（可复现性）
* ``occupancy_sdf.py``    建筑棱柱栅格化 + 两遍 EDT
* ``world_field.py``      全域场对象与三线性取样
* ``world_field_builder.py`` 建场（含禁飞区、竖向包络）
* ``corridor_window.py``  走廊半宽规则与斜栅格窗口
* ``rrt_path_search.py``  RRT 采样（窗内、SDF 自适应步长、多初值）
* ``initial_paths.py``    候选评估 / 去重 / 排序
* ``sdf_casadi.py``       SDF → CasADi 三线性插值转接头（NLP 净空读数）
"""

from path_planning.methods.spatial.corridor_window import (
    CorridorRule,
    CorridorWindow,
    corridor_window,
    required_pad_m,
    window_area_m2,
    z_envelope_from_ground,
)
from path_planning.methods.spatial.initial_paths import (
    PathCandidate,
    evaluate_candidate,
    generate_initial_paths,
)
from path_planning.methods.spatial.rrt_path_search import RRT3D, RRTConfig, RRTResult
from path_planning.methods.spatial.sdf_casadi import SdfOnGrid, casadi_available

__all__ = [
    "CorridorRule",
    "CorridorWindow",
    "corridor_window",
    "required_pad_m",
    "window_area_m2",
    "z_envelope_from_ground",
    "PathCandidate",
    "evaluate_candidate",
    "generate_initial_paths",
    "RRT3D",
    "RRTConfig",
    "RRTResult",
    "SdfOnGrid",
    "casadi_available",
]
