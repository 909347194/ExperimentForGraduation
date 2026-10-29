# -*- coding: utf-8 -*-
"""可见性图 / 稀疏路图规划器参数（path_planning 内自洽，不改动 common.config）。

设计目标（用户口径）：
    建筑简化 → 外扩安全距离后的简化多边形（AABB / 定向包围盒 / DP 简化），STRtree 加速查询
    节点     → 简化建筑外轮廓按较大步长采样 + 几个离散飞行高度层 + 机巢/任务点/路网采样点
    边       → 仅连「视线可达且不太远」的节点对，离线做一次碰撞检测；
               再引入机动保护区删掉高风险近距边
    规模     → 节点数先瞄准 < 2000，再按实际调
    代价     → 三维欧氏距离；堆优化 Dijkstra/A*（不引入 TPR，只考虑禁飞区 + 建筑障碍）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass
class VisibilityGraphParams:
    # ── 建筑简化 ──────────────────────────────────────────────────────────
    simplify_mode: str = "simplify"   # 'simplify' | 'convex_hull' | 'oriented_bbox' | 'axis_align'
    simplify_tol: float = 3.0         # Douglas-Peucker 容差（仅 simplify 模式，米）
    d_build: float = 5.0              # 建筑外扩安全距离（简化障碍已含此外扩；即安全净空下限）

    # ── 节点生成 ──────────────────────────────────────────────────────────
    node_step: float = 40.0           # 建筑外轮廓采样步长（米）
    node_buffer: float = 5.0          # 节点相对建筑原始轮廓的安全距离（采样环的外扩，叠在 d_build 上）
    altitude_layers: Sequence[float] = (20.0, 50.0)   # 离散飞行高度层（低空巡检常见：少数几层）
    grid_step: float = 100.0          # 自由空间粗网格步长（连通性骨干，保证开路可达）
    grid_layers: Sequence[float] = (20.0, 50.0)       # 粗网格铺设的高度层
    max_nodes_per_building: int = 8   # 单栋建筑最多采样节点（按层计，防止小建筑过度占预算）
    max_nodes: int = 2000             # 节点总数硬上限（< 2000 目标）

    # ── 边连接 ────────────────────────────────────────────────────────────
    max_edge_len: float = 200.0       # 最大连线距离（米）
    edge_margin: float = 2.0          # LOS 判定的额外余量（叠加在 d_build 外扩之上）
    max_degree: int = 40              # 每点最多保留 K 个最近候选（防密集场景候选爆炸 + 控边数）
    connect_layers: bool = True       # 是否在同一 (x,y) 的不同高度层间加竖向边（允许变高）
    protection_clearance: float = 8.0 # 机动保护区：相对「已外扩障碍」的最小净空；低于则删边
    attach_radius: float = 80.0       # OD 接入半径（米）

    # ── 禁飞区 ────────────────────────────────────────────────────────────
    # 选项 A（默认）：动态禁飞区不并入建图，仅在 A* 查询时按「时间窗」检查（图可建一次、长期复用）。
    # 选项 B：include_dynamic_as_static=True，把动态禁飞按「任意时段可能冲突」保守并入静态障碍（简单但过度剪枝）。
    include_dynamic_as_static: bool = False
    cruise_speed_mps: float = 15.0  # 路径长度→到达时刻的换算速度（用于动态禁飞时间窗判定）

    @classmethod
    def from_dict(cls, d: Optional[dict] = None) -> "VisibilityGraphParams":
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        out = {}
        for k in self.__dataclass_fields__:
            v = getattr(self, k)
            out[k] = list(v) if isinstance(v, tuple) else v
        return out
