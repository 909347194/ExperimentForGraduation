# -*- coding: utf-8 -*-
"""规划参数：结构统一、**值不在此处**、派生量由参数推出。

职责：定义 ``PlanParams``——本模块全部算法参数的结构与校验。只描述"有哪些
      参数、什么取值范围、互相怎么约束"，**不写具体数值**；具体取值在
      ``experiments/<exp>/config.yaml``（一个实验一份，不继承不覆盖）。
边界：不含机体能力（逐机，见 ``common/vehicle_spec.py``），不含场景数据
      （见 ``common/scenario.py``），不含预算（见 ``common/budget.py``）。
相关：``common/vehicle_spec.py``、``methods/timeline/feasibility.py``、
      ``docs/time_mapping.md``（§2 速度口径、§4 弧长积分、§8 两个步长）。

为什么"结构统一、值独立"：论文里任何一个数字跑出来都必须能追溯到它自己那份
配置。层叠覆盖（default.yaml + 实验 yaml 合并）在这种要求下不可接受——事后没人
说得清某个参数最终取自哪一层。

命名纪律（贯穿全模块，禁止混用）：

- ``dt_min_s`` / ``dt_max_s``：NLP 决策步长区间（0.3~20 s 量级）
- ``detection_step_s(...)``：冲突检测的弦长（≈0.365 s），由 ``eps_a_m`` 与
  该机 ``a_max`` 反解得出，**逐机**，不是全局常数

两者数值接近（0.3 vs 0.365），早期一律叫 ``dt`` 是最大的命名陷阱。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "PlanParams",
]

#: 校验用相对容差
_REL_TOL = 1e-9


@dataclass(frozen=True)
class PlanParams:
    """本模块算法参数（结构与校验）。

    分八组，组内语义独立、组间有依赖的几条在 ``validate`` 里显式表达。

    Attributes（空间组 REST）:
        clearance_m:              轨迹与建筑/地面之间的最小净空（m）
        corridor_half_width_m:    走廊半宽（m），**必须严格大于 clearance_m**
        grid_cell_m:              场景粗栅格尺寸（m），用于快速剔除
        sdf_spacing_cells:        SDF 精栅格 (dx, dy, dz) 像元
        bbox_margin_m:            搜索域相对起终点的外扩（m）

    Attributes（RRT 组）:
        rrt_step_m / rrt_min_step_m:  RRT 步长上/下限（m）
        rrt_sdf_step_scale:           步长随 SDF 自适应的比例（0–1）
        rrt_max_iters:                单次采样预算
        rrt_goal_sample_rate:         GoalBias 概率
        rrt_shortcut_iters:           捷径化预算（软上限，见 rrt_path_search 模块说明）
        rrt_initializations:          多初值条数
        rrt_dedup_tol_m:              候选去重距离容差（m）
        clearance_sample_step_m:      段内净空采样间隔（m），不得大于 SDF 最细格距

    Attributes（时间组）:
        n_nodes:                  NLP 节点数
        dt_min_s / dt_max_s:      决策步长下/上界（s）
        max_iter:                 IPOPT 最大迭代
        solver_tol:               IPOPT 容差
        clearance_samples_per_segment: 净空校验在段内的内插采样点数
        arc_subintervals:         弧长 Gauss 积分的子区间数 M
        gauss_points:             每个子区间的 Gauss 点数

    Attributes（检测组）:
        eps_a_m:                  弦化位置误差预算 ε_a（m）
        time_eps_s:               时间维判定容差（s）
        space_eps_m:              空间维判定容差（m）
        ellipsoid_semiaxes_m:     保守膨胀椭球半轴 (a, b, c)
        cpa_dense_steps:          弧段退化为稠密采样时的步数

    Attributes（协调组）:
        local_region_margin_m:    局部重规划域相对冲突处的外扩（m）
        max_replan_rounds:        单轮最大重规划次数
        retreat_node_ladder:      求解失败时的节点数退让阶梯（严格递减）
        solve_budget_multiplier:  失败重试的求解预算倍数

    Attributes（策略组）:
        hover_weight:             悬停时长加权 κ_h（1.0 = 日历口径）
    """

    # --- 空间组 ---------------------------------------------------------
    clearance_m: float = 6.0
    corridor_half_width_m: float = 20.0
    grid_cell_m: float = 60.0
    sdf_spacing_cells: tuple[int, int, int] = (4, 4, 2)
    bbox_margin_m: float = 5.0

    # --- RRT 组 ----------------------------------------------------------
    rrt_step_m: float = 30.0
    rrt_min_step_m: float = 10.0
    rrt_sdf_step_scale: float = 0.5
    rrt_max_iters: int = 3000
    rrt_goal_sample_rate: float = 0.1
    rrt_shortcut_iters: int = 200
    rrt_initializations: int = 5
    rrt_dedup_tol_m: float = 8.0
    clearance_sample_step_m: float = 2.0

    # --- 时间组 ---------------------------------------------------------
    n_nodes: int = 60
    dt_min_s: float = 0.3
    dt_max_s: float = 20.0
    max_iter: int = 200
    solver_tol: float = 1e-4
    clearance_samples_per_segment: int = 3
    arc_subintervals: int = 8
    gauss_points: int = 2

    # --- 检测组 ---------------------------------------------------------
    eps_a_m: float = 0.05
    time_eps_s: float = 1e-3
    space_eps_m: float = 1e-3
    ellipsoid_semiaxes_m: tuple[float, float, float] = (30.0, 30.0, 15.0)
    cpa_dense_steps: int = 24

    # --- 协调组 ---------------------------------------------------------
    local_region_margin_m: float = 100.0
    max_replan_rounds: int = 3
    retreat_node_ladder: tuple[int, ...] = (40, 30)
    solve_budget_multiplier: float = 3.0
    #: 局部让路的竖直档（米）——最便宜的解：竖直层切换不用绕行
    detour_vertical_steps_m: tuple[float, ...] = (8.0, 16.0, 24.0)
    #: 局部让路的水平档（米）——次选：绕行会增加弧长
    detour_lateral_steps_m: tuple[float, ...] = (12.0, 24.0, 36.0)
    #: 真几何净空不达标时的抬阈值重解量（米）；照参考实现实测 SDF 乐观 2.2 m
    local_replan_clearance_fix_m: float = 2.0
    #: 局部重规划的几何引导管半径（米）：把节点钉在让路折线附近，否则 min ΣΔt
    #: 会把位移抹平（位置是自由变量，让路位移只在初值里存在）
    local_replan_guide_radius_m: float = 2.0

    # --- 策略组 ---------------------------------------------------------
    hover_weight: float = 1.0

    # ------------------------------------------------------------------
    # 派生量
    # ------------------------------------------------------------------

    def detection_step_s(self, a_max_mps2: float) -> float:
        """冲突检测弦长 Δt_det（**逐机**，由 a_max 反解）。

        由弦化误差界 ``‖p−s‖ <= Δt_det² · a_max / 8`` 反解：

            Δt_det = sqrt(8 · ε_a / a_max)

        ε_a=0.05 m、a_max=3 时约 0.365 s。**与 dt_max_s 不是同一个量**：
        dt 是优化决策步，可随解在 [dt_min, dt_max] 内变化；Δt_det 是把变速
        轨迹离散成匀速弦的固定步长。混用会让检测器直接吃 20 s 的段而漏检。
        """
        if a_max_mps2 <= 0.0:
            raise ValueError(f"a_max_mps2 必须为正：{a_max_mps2}")
        return math.sqrt(8.0 * self.eps_a_m / a_max_mps2)

    def chord_count(self, segment_duration_s: float, a_max_mps2: float) -> int:
        """把一段长 ``segment_duration_s`` 的轨迹切成几条弦（向上取整，至少 1）。"""
        step = self.detection_step_s(a_max_mps2)
        return max(1, int(math.ceil(segment_duration_s / step - 1e-9)))

    @property
    def ellipsoid_z_semiaxis_m(self) -> float:
        return float(self.ellipsoid_semiaxes_m[2])

    @property
    def decisions_per_node(self) -> int:
        """每节点决策变量个数：位置 3 + 速度 3 + 加速度 3 + 步长 1。"""
        return 10

    @property
    def n_decisions(self) -> int:
        """总决策变量个数（用于日志与性能对比）。"""
        return self.n_nodes * self.decisions_per_node

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """参数自洽校验。一次收集全部问题后抛一个异常。

        只校验**本层能判的**约束。依赖机体规格的几条（如 ``a_max <= 平飞可用
        加速度``）在 ``VehicleSpec.validate``；依赖场景的（如走廊能否容下建筑）
        在实际规划时判。
        """
        errs: list[str] = []

        def chk(cond: bool, msg: str) -> None:
            if not cond:
                errs.append(msg)

        # --- 正定性 -----------------------------------------------------
        chk(self.clearance_m > 0.0, f"clearance_m 必须为正：{self.clearance_m}")
        chk(self.corridor_half_width_m > 0.0,
            f"corridor_half_width_m 必须为正：{self.corridor_half_width_m}")
        chk(self.grid_cell_m > 0.0, f"grid_cell_m 必须为正：{self.grid_cell_m}")
        chk(self.bbox_margin_m >= 0.0, f"bbox_margin_m 不能为负：{self.bbox_margin_m}")
        chk(self.n_nodes >= 10, f"n_nodes 至少 10（给 RRT 与积分留余量）：{self.n_nodes}")
        chk(self.dt_min_s > 0.0, f"dt_min_s 必须为正：{self.dt_min_s}")
        chk(self.dt_max_s > 0.0, f"dt_max_s 必须为正：{self.dt_max_s}")
        chk(self.max_iter > 0, f"max_iter 必须为正：{self.max_iter}")
        chk(self.solver_tol > 0.0, f"solver_tol 必须为正：{self.solver_tol}")
        chk(self.clearance_samples_per_segment >= 1,
            f"clearance_samples_per_segment 至少 1：{self.clearance_samples_per_segment}")
        chk(self.arc_subintervals >= 1, f"arc_subintervals 至少 1：{self.arc_subintervals}")
        chk(self.gauss_points >= 2,
            f"gauss_points 至少 2（1 点退化为矩形公式，精度不足）：{self.gauss_points}")
        chk(self.eps_a_m > 0.0, f"eps_a_m 必须为正：{self.eps_a_m}")
        chk(self.time_eps_s > 0.0, f"time_eps_s 必须为正：{self.time_eps_s}")
        chk(self.space_eps_m > 0.0, f"space_eps_m 必须为正：{self.space_eps_m}")
        chk(self.cpa_dense_steps >= 4,
            f"cpa_dense_steps 至少 4：{self.cpa_dense_steps}")
        chk(self.local_region_margin_m >= 0.0,
            f"local_region_margin_m 不能为负：{self.local_region_margin_m}")
        chk(self.max_replan_rounds >= 1,
            f"max_replan_rounds 至少 1：{self.max_replan_rounds}")
        chk(self.solve_budget_multiplier >= 1.0,
            f"solve_budget_multiplier 至少 1：{self.solve_budget_multiplier}")
        chk(self.hover_weight > 0.0, f"hover_weight 必须为正：{self.hover_weight}")
        chk(all(float(v) > 0.0 for v in self.detour_vertical_steps_m),
            f"detour_vertical_steps_m 必须全为正：{self.detour_vertical_steps_m}")
        chk(all(float(v) > 0.0 for v in self.detour_lateral_steps_m),
            f"detour_lateral_steps_m 必须全为正：{self.detour_lateral_steps_m}")
        chk(self.local_replan_clearance_fix_m > 0.0,
            f"local_replan_clearance_fix_m 必须为正：{self.local_replan_clearance_fix_m}")
        chk(self.local_replan_guide_radius_m > 0.0,
            f"local_replan_guide_radius_m 必须为正：{self.local_replan_guide_radius_m}")

        # 正定性不满足时后续比较无意义，先短路
        if errs:
            raise ValueError(f"PlanParams 非法（{len(errs)} 项）：\n  " + "\n  ".join(errs))

        # --- ④ 走廊必须容得下净空 -------------------------------------
        # 半宽 <= 净空的走廊在几何上是空集：走廊内任一点到走廊边界都少于
        # clearance，解必然违反净空约束，表现为 OPT_FAILED 而非 CORRIDOR_TOO_NARROW。
        # 提前拦住，报出来的原因才准确。
        chk(
            self.clearance_m < self.corridor_half_width_m,
            f"clearance_m={self.clearance_m} 不小于 "
            f"corridor_half_width_m={self.corridor_half_width_m}：走廊容不下净空",
        )

        # --- ⑤ 椭球竖直半轴要能吸收弦化误差 ---------------------------
        # 椭球是保守膨胀后的判定体；竖直半轴若小到 2ε_a 以内，膨胀量会盖住
        # 竖直维的物理差异，等于把"不同高度层"判成同一层。
        chk(
            self.ellipsoid_z_semiaxis_m > 2.0 * self.eps_a_m,
            f"椭球竖直半轴 {self.ellipsoid_z_semiaxis_m} 不大于 2·eps_a="
            f"{2.0 * self.eps_a_m}：膨胀会吃掉竖直分辨力",
        )
        chk(
            all(ax > 0.0 for ax in self.ellipsoid_semiaxes_m),
            f"椭球半轴必须全为正：{self.ellipsoid_semiaxes_m}",
        )

        # --- ⑥ 决策步长区间非退化 -------------------------------------
        # dt_min >= dt_max 会让 NLP 的步长变量无可行区间，IPOPT 直接判不可行；
        # 报出来应是参数错误而不是 OPT_FAILED。
        chk(
            self.dt_min_s < self.dt_max_s,
            f"dt_min_s={self.dt_min_s} >= dt_max_s={self.dt_max_s}：步长区间退化",
        )

        # --- 退让阶梯严格递减 -----------------------------------------
        ladder = tuple(int(n) for n in self.retreat_node_ladder)
        chk(
            all(a > b for a, b in zip(ladder, ladder[1:])),
            f"retreat_node_ladder 必须严格递减：{ladder}",
        )
        chk(
            all(n >= 10 for n in ladder),
            f"retreat_node_ladder 各档至少 10：{ladder}",
        )
        chk(
            all(n <= self.n_nodes for n in ladder),
            f"retreat_node_ladder 不应超过 n_nodes={self.n_nodes}：{ladder}",
        )

        # --- SDF 栅格像元为正 -------------------------------------------
        chk(all(int(v) > 0 for v in self.sdf_spacing_cells),
            f"sdf_spacing_cells 必须全为正：{self.sdf_spacing_cells}")

        # --- RRT 组 -------------------------------------------------------
        chk(self.rrt_min_step_m > 0.0, f"rrt_min_step_m 必须为正：{self.rrt_min_step_m}")
        chk(self.rrt_step_m > self.rrt_min_step_m,
            f"rrt_step_m={self.rrt_step_m} 必须大于 rrt_min_step_m={self.rrt_min_step_m}")
        chk(0.0 < self.rrt_sdf_step_scale <= 1.0,
            f"rrt_sdf_step_scale 应在 (0,1]：{self.rrt_sdf_step_scale}")
        chk(self.rrt_max_iters > 0, f"rrt_max_iters 必须为正：{self.rrt_max_iters}")
        chk(0.0 < self.rrt_goal_sample_rate < 1.0,
            f"rrt_goal_sample_rate 应在 (0,1)：{self.rrt_goal_sample_rate}")
        chk(self.rrt_shortcut_iters >= 0,
            f"rrt_shortcut_iters 不能为负：{self.rrt_shortcut_iters}")
        chk(1 <= self.rrt_initializations <= 32,
            f"rrt_initializations 应在 [1,32]：{self.rrt_initializations}")
        chk(self.rrt_dedup_tol_m >= 0.0, f"rrt_dedup_tol_m 不能为负：{self.rrt_dedup_tol_m}")
        chk(self.clearance_sample_step_m > 0.0,
            f"clearance_sample_step_m 必须为正：{self.clearance_sample_step_m}")
        chk(self.clearance_sample_step_m <= float(min(int(v) for v in self.sdf_spacing_cells)),
            f"clearance_sample_step_m={self.clearance_sample_step_m} 大于 SDF 最细格距 "
            f"{min(int(v) for v in self.sdf_spacing_cells)}：段内采样会整格跳检")

        if errs:
            raise ValueError(f"PlanParams 校验不通过（{len(errs)} 项）：\n  " + "\n  ".join(errs))

    # ------------------------------------------------------------------
    # 诊断输出
    # ------------------------------------------------------------------

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for f in self.__dataclass_fields__.values():  # type: ignore[attr-defined]
            if f.name.startswith("_"):
                continue
            out[f.name] = getattr(self, f.name)
        out["n_decisions"] = self.n_decisions
        return out
