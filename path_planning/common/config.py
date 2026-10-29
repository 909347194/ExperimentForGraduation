# -*- coding: utf-8 -*-
"""参数与配置模型（L0）。

职责：把论文中的全部参数收拢为带默认值的配置对象，实验 config.yaml 的 schema 与之一一对应。

    UAVParams      m、F_min、F_max、v_max、v_up、v_down
                   （Lin2021 §3.1 参考值：m=1kg、F∈[9,16]N、v_max=15、v_up=6、v_down=2）
    OfflineParams  d1 水平安全距离、d2 离地高度、d3 垂直层距、d* 最大连线距离、
                   d_s_stat / d_s_dyn 禁飞安全膨胀（分设）、裁剪窗口
    OnlineParams   平滑偏差 d 及其收缩策略、复核迭代上限、分层 L1/L2/L2.5 开关
    RiskParams     综合代价权重 λ_r（本阶段恒 0，槽位保留）

不负责：从文件解析（scenario.py 负责把文件解析为这些对象）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

G = 9.8  # 重力加速度 m/s²


@dataclass
class UAVParams:
    """无人机动力学与限速参数（Lin2021 §3.1）。"""

    mass: float = 1.0  # kg
    f_max: float = 16.0  # N
    f_min: float = 9.0  # N
    v_max: float = 15.0  # m/s
    v_up: float = 6.0  # m/s
    v_down: float = 2.0  # m/s
    g: float = G

    @classmethod
    def from_dict(cls, d: dict | None) -> "UAVParams":
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


@dataclass
class OfflineParams:
    """离线建网参数。d1/d2/d3/d* 见 Lin2021 §2.2.1 Fig.5。"""

    d1: float = 5.0  # 控制点距建筑（投影）表面的水平安全距离 m
    d2: float = 10.0  # 控制点离地最小高度 m
    d3: float = 10.0  # 垂直方向相邻控制点间距（密度）m
    d_star: float = 150.0  # 最大连线距离 m
    max_degree: int = 24  # 每点最多保留 K 个最近候选（防密集场景候选爆炸）
    d_s_stat: float = 5.0  # 静态禁飞安全膨胀 m（法定间隔，较大）
    d_s_dyn: float = 2.0  # 动态禁飞安全膨胀 m（临时管制余量）
    top_clearance: float = 5.0  # 顶部控制点高出屋顶 m
    max_levels: int = 12  # 单栋建筑最多布几层（防爆炸）
    corner_min_dist: float = 6.0  # 同一层相邻角点过近时抽稀的最小间距 m
    # 自由空间补点层（Lin2021 §2.2.1 "建筑稀疏/需补设虚拟控制点" 的另一半）：
    # 低矮城区只贴建筑布点会导致"禁飞区只能绕行、不能飞越"，故按网格补高空层
    free_grid_step: float = 0.0  # m，0=不补
    free_levels: tuple = (45.0, 80.0)  # 补点高度 m

    @classmethod
    def from_dict(cls, d: dict | None) -> "OfflineParams":
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


@dataclass
class OnlineParams:
    """在线参数：平滑、复核与分层策略（design §5）。"""

    smooth_deviation: float = 4.0  # Dubins 平滑偏差 d（m），Eq.6
    shrink_factor: float = 0.6  # 相邻转向不可行时偏差收缩系数（Fig.14）
    max_shrink_iters: int = 6
    check_step_m: float = 2.0  # 平滑后复核的采样步长 m
    enable_l1: bool = True
    enable_l2: bool = True
    max_l2_iters: int = 3  # L2 删冲突边重跑上限
    enable_l25: bool = True  # L2.5 保守全剪（仍是改网络 + 普通 Dijkstra）
    t_horizon: float = 3600.0  # L2.5 / 保守判定的任务时段长度 s

    @classmethod
    def from_dict(cls, d: dict | None) -> "OnlineParams":
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


@dataclass
class RiskParams:
    """TPR 相关（本阶段 λ_r = 0：论文保留章节、代码不实现）。"""

    lambda_r: float = 0.0
    enabled: bool = False

    @classmethod
    def from_dict(cls, d: dict | None) -> "RiskParams":
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


@dataclass
class Config:
    uav: UAVParams = field(default_factory=UAVParams)
    offline: OfflineParams = field(default_factory=OfflineParams)
    online: OnlineParams = field(default_factory=OnlineParams)
    risk: RiskParams = field(default_factory=RiskParams)
    scenario: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)  # 原始配置（实验自定义段：nofly/od/checks…）

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        return cls(
            uav=UAVParams.from_dict(d.get("uav")),
            offline=OfflineParams.from_dict(d.get("offline")),
            online=OnlineParams.from_dict(d.get("online")),
            risk=RiskParams.from_dict(d.get("risk")),
            scenario=d.get("scenario", {}) or {},
            raw=d,
        )

    def section(self, name: str) -> dict:
        """取实验自定义配置段（顶层键），缺省返回 {}。"""
        return self.raw.get(name, {}) or {}

    def to_dict(self) -> dict:
        return {
            "uav": asdict(self.uav),
            "offline": asdict(self.offline),
            "online": asdict(self.online),
            "risk": asdict(self.risk),
            "scenario": self.scenario,
        }


def load_config(path: str) -> Config:
    """从实验 config.yaml 载入（唯一入口，避免各处自行解析）。"""
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Config.from_dict(raw)
