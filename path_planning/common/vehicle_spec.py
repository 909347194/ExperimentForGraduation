# -*- coding: utf-8 -*-
"""单机机体规格：物理能力、派生量、以及**逐机**可行性校验。

职责：描述一架无人机的物理能力（质量、推力上下限、速度上下限、加速度上限、
      续航与航程），以 ``@property`` 派生全部二次量（推力加速度、垂直加速度
      上下界、平飞水平加速度上限、最小转弯半径、规划速度上限），并提供
      ``validate()`` 做逐机物理自洽校验。
边界：不含规划策略（走廊半宽、检测步长、退让阶梯等归 ``common/params.py``），
      不含气动/能耗模型（能耗属本轮排除项）。
相关：``common/params.py``（``PlanParams.validate`` 会调用本类校验）、
      ``common/types.py``（``Trajectory.uav_id`` 对应的规格由 ``Scenario`` 持有）。

**异构是硬约束**：本项目机队是异构的，所有派生量必须挂在**每架机**的对象上。
若把 ``a_z_max`` 之类挂到全局配置，轻载机会沿用重载机的值，约束一边过松一边
过紧，且这种错误在 defaults 上永远查不出来（详见 ``validate`` 的说明）。

单位一律写在字段名后缀里，不做隐式换算。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "GRAVITY_MPS2",
    "PLAN_SPEED_RATIO",
    "VehicleSpec",
]

#: 重力加速度。全模块唯一出处，禁止在别处写字面量 9.81。
GRAVITY_MPS2 = 9.81

#: 规划速度上限相对物理速度上限的比例。
#:
#: 取 0.9 留出 10% 内部空间，是**数值需要**不是物理需要：若取 1.0，平飞时
#: 合速度约束 ``sqrt(vx²+vy²+vz²) <= v_max`` 被顶死在 ``v_h = v_max``，
#: 可行域在水平方向退化成一个点，IPOPT 在离散化误差一叠加就不可行，
#: 表现为"几何明明可飞却 OPT_FAILED"。取 0.9 让可行域有内部。
#:
#: 副作用方向是安全的：交付轨迹的速度真正最高只有 0.9·v_max，故按它算出的
#: 飞行时长偏长（busy_until 偏晚），不会让上层以为机回来得太早。
PLAN_SPEED_RATIO = 0.9

#: 校验用的相对容差（浮点比较）。1e-9 足够吸收二进制表示误差而不掩盖真错。
_REL_TOL = 1e-9


@dataclass(frozen=True)
class VehicleSpec:
    """一架无人机的机体规格（不可变值对象）。

    Attributes:
        model:       型号名（仅溯源/日志用）
        mass_kg:     起飞质量（kg，> 0）
        v_max_mps:   合速度上限（m/s，> 0）——三条速度约束里的统一上限
        v_up_mps:    最大爬升率（m/s，>= 0）
        v_dn_mps:    最大下降率（m/s，>= 0）
        a_max_mps2:  加速度模长上限（m/s²，> 0）
        f_max_n:     最大推力（N，> 0）
        f_min_n:     最小推力（N，> 0）——必须 <= mg <= f_max，否则无法悬停
        max_range_m: 满电航程（m，> 0）——**距离口径**
        max_time_s:  最大飞行时间（s，> 0）——**能量口径**，与航程相互独立
    """

    model: str
    mass_kg: float
    v_max_mps: float
    v_up_mps: float
    v_dn_mps: float
    a_max_mps2: float
    f_max_n: float
    f_min_n: float
    max_range_m: float
    max_time_s: float

    # ------------------------------------------------------------------
    # 推力派生量（质量归一）
    # ------------------------------------------------------------------

    @property
    def thrust_max_per_mass(self) -> float:
        """最大比推力（N/kg）。"""
        return self.f_max_n / self.mass_kg

    @property
    def thrust_min_per_mass(self) -> float:
        """最小比推力（N/kg）。"""
        return self.f_min_n / self.mass_kg

    @property
    def gravity_force_n(self) -> float:
        """悬停所需推力（N）= mg。"""
        return self.mass_kg * GRAVITY_MPS2

    @property
    def a_z_max_mps2(self) -> float:
        """最大向上加速度 = ``f_max/m - g``（N/kg 与 m/s² 同量纲）。"""
        return self.thrust_max_per_mass - GRAVITY_MPS2

    @property
    def a_z_min_mps2(self) -> float:
        """最小（最负）竖直加速度 = ``f_min/m - g``。

        正常机体为负值（可以主动下压）；若为正说明最小推力已大于重力，
        该机**无法下降也无法悬停**，``validate`` 会拦住。
        """
        return self.thrust_min_per_mass - GRAVITY_MPS2

    @property
    def a_horiz_max_level_mps2(self) -> float:
        """平飞时的水平加速度上限。

        平飞需竖直分量恰好等于 ``mg``，可用水平分量为
        ``sqrt(f_max² - (mg)²)/m``，即 ``sqrt((f_max/m)² - g²)``。
        这是 ``a_max_mps2`` 的上界，不是替代品。
        """
        t = self.thrust_max_per_mass
        v = t * t - GRAVITY_MPS2 * GRAVITY_MPS2
        return math.sqrt(v) if v > 0.0 else 0.0

    # ------------------------------------------------------------------
    # 速度派生量
    # ------------------------------------------------------------------

    @property
    def v_max_plan_mps(self) -> float:
        """规划用速度上限 = ``PLAN_SPEED_RATIO · v_max``（见模块常量说明）。"""
        return PLAN_SPEED_RATIO * self.v_max_mps

    @property
    def v_h_max_at_climb_mps(self) -> float:
        """满爬升时的水平速度允许上限 = ``sqrt(v_max² - v_up²)``。

        用于诊断：该值若小于 ``v_max_plan_mps``，说明合速度约束在最紧处
        比规划上限更紧，(C3) 会真正绑定。
        """
        v = self.v_max_mps * self.v_max_mps - self.v_up_mps * self.v_up_mps
        return math.sqrt(v) if v > 0.0 else 0.0

    def v_h_limit_mps(self, v_z_mps: float) -> float:
        """给定竖直速度时水平速度的允许上限。

        即三条约束的显式解 ``min{v_max_plan, sqrt(v_max² - vz²)}``。
        仅供校验与诊断使用——**优化器里不要把 min 写成 CasADi 表达式**，
        它在折点处不可微，会让 IPOPT 在可行域边界反复失败；应分别作为
        两条约束交给求解器。
        """
        if abs(v_z_mps) > self.v_max_mps:
            return 0.0
        v = self.v_max_mps * self.v_max_mps - v_z_mps * v_z_mps
        return min(self.v_max_plan_mps, math.sqrt(max(v, 0.0)))

    @property
    def default_cruise_mps(self) -> float:
        """降级剖面用的巡航速度缺省值 = ``v_max_plan``。

        仅用于拿不到优化轨迹时的估算（``time_source=estimate``），
        不参与正式规划——正式规划的速度由优化器在可行域内自由决定。
        """
        return self.v_max_plan_mps

    # ------------------------------------------------------------------
    # 几何派生量
    # ------------------------------------------------------------------

    def r_min_m(self, v_mps: float | None = None) -> float:
        """最小转弯半径 ``R_min(v) = v² / a_max``。

        缺省按 ``v_max_plan`` 取值。**注意 R_min 是速度的函数而非常数**：
        a_max=3 时 15 m/s 对应 75 m、13.5 m/s 对应 60.75 m、10 m/s 对应 33 m。
        走廊按 ``R_min(v_max)`` 设计（最坏情况），轨迹按 ``R_min(v_max_plan)``
        生成，两者不可混用；混用会得到"设计得太紧"或"飞得比设计更弯"。
        """
        v = self.v_max_plan_mps if v_mps is None else float(v_mps)
        return (v * v) / self.a_max_mps2

    # ------------------------------------------------------------------
    # 校验
    # ------------------------------------------------------------------

    def can_hover(self) -> bool:
        """最小推力是否 <= 重力 <= 最大推力。"""
        return self.f_min_n <= self.gravity_force_n <= self.f_max_n

    def validate(self) -> None:
        """逐机物理自洽校验。**必须对机队每一架都跑，不能只跑 defaults。**

        一次收集全部问题后抛一个异常，避免"修一条报一条"的往复。

        为什么强调逐机：``f_min <= m·g <= f_max`` 这条在 defaults
        （m=1.0、f_min=9、mg=9.81）上恰好成立，而轻载机 m=0.8 时 mg=7.85，
        若 f_min 取 8.0 则 8.0 > 7.85 —— 最小推力已大于重力，**根本无法悬停**
        （推起来就持续上飘，连定高都做不到）。这种错误在 defaults 上永远
        查不出来，只在异构逐机展开时才暴露。
        """
        errs: list[str] = []

        def chk(cond: bool, msg: str) -> None:
            if not cond:
                errs.append(msg)

        # --- 正定性 -----------------------------------------------------
        chk(self.mass_kg > 0.0, f"mass_kg 必须为正：{self.mass_kg}")
        chk(self.v_max_mps > 0.0, f"v_max_mps 必须为正：{self.v_max_mps}")
        chk(self.v_up_mps >= 0.0, f"v_up_mps 不能为负：{self.v_up_mps}")
        chk(self.v_dn_mps >= 0.0, f"v_dn_mps 不能为负：{self.v_dn_mps}")
        chk(self.a_max_mps2 > 0.0, f"a_max_mps2 必须为正：{self.a_max_mps2}")
        chk(self.f_max_n > 0.0, f"f_max_n 必须为正：{self.f_max_n}")
        chk(self.f_min_n > 0.0, f"f_min_n 必须为正：{self.f_min_n}")
        chk(self.max_range_m > 0.0, f"max_range_m 必须为正：{self.max_range_m}")
        chk(self.max_time_s > 0.0, f"max_time_s 必须为正：{self.max_time_s}")

        # 以下校验依赖上面的正定性，先短路
        if errs:
            raise ValueError(f"机体规格 {self.model!r} 非法：\n  " + "\n  ".join(errs))

        # --- ① 悬停可行性：f_min <= mg <= f_max ---------------------------
        mg = self.gravity_force_n
        chk(
            self.f_min_n <= mg * (1.0 + _REL_TOL),
            f"f_min_n={self.f_min_n:.3f} N > mg={mg:.3f} N：最小推力大于重力，"
            f"无法悬停/下降（该机 f_min/m={self.thrust_min_per_mass:.3f} > g）",
        )
        chk(
            mg <= self.f_max_n * (1.0 + _REL_TOL),
            f"mg={mg:.3f} N > f_max_n={self.f_max_n:.3f} N：推力不足以悬停",
        )

        # --- ② 加速度上限与推力上限相容 -----------------------------------
        # a_max 是规划的加速度模长上限，平飞时可用水平加速度受推力上界约束：
        # a_max <= sqrt((f_max/m)² - g²)。超了说明规划的加速度在平飞时根本
        # 给不出来，NLP 会给出物理上不可执行的解。
        chk(
            self.a_max_mps2 <= self.a_horiz_max_level_mps2 * (1.0 + _REL_TOL),
            f"a_max_mps2={self.a_max_mps2:.3f} > 平飞可用水平加速度上限 "
            f"{self.a_horiz_max_level_mps2:.3f}（由 f_max/m 与 g 决定）",
        )

        # --- ③ 垂直速度不得超过合速度上限 --------------------------------
        chk(
            self.v_up_mps <= self.v_max_mps * (1.0 + _REL_TOL),
            f"v_up_mps={self.v_up_mps:.3f} > v_max_mps={self.v_max_mps:.3f}",
        )
        chk(
            self.v_dn_mps <= self.v_max_mps * (1.0 + _REL_TOL),
            f"v_dn_mps={self.v_dn_mps:.3f} > v_max_mps={self.v_max_mps:.3f}",
        )

        # --- ④ 规划速度上限不低于爬升率 -----------------------------------
        # 设计约定：水平"让路"速度不应低于爬升率，否则绕行代价相对爬升
        # 明显偏高，协调层会过度偏好垂直层切换。
        chk(
            self.v_max_plan_mps >= self.v_up_mps * (1.0 - _REL_TOL),
            f"v_max_plan={self.v_max_plan_mps:.3f} < v_up={self.v_up_mps:.3f}："
            f"规划水平速度上限低于爬升率",
        )

        # --- ⑤ 防空约束：续航不得宽于航程派生值 ----------------------------
        # 续航（能量）与航程（距离）相互独立，这是拍板口径；但若 max_time 给
        # 得比 max_range/v_max 还大，则"续航"永远不会先绑定，等于一条永不生效
        # 的空约束——统计上会让人误判"续航充裕"。故设此上界。
        derived = self.max_range_m / self.v_max_mps
        chk(
            self.max_time_s <= derived * (1.0 + _REL_TOL),
            f"max_time_s={self.max_time_s:.1f} > max_range_m/v_max="
            f"{derived:.1f}：续航比航程松，将成为永不绑定的空约束",
        )

        if errs:
            raise ValueError(
                f"机体规格 {self.model!r} 校验不通过（{len(errs)} 项）：\n  "
                + "\n  ".join(errs)
            )

    # ------------------------------------------------------------------
    # 诊断输出
    # ------------------------------------------------------------------

    def as_dict(self) -> dict[str, float | str]:
        """导出全部输入值与派生值，供日志/diagnostics 落盘。"""
        return {
            "model": self.model,
            "mass_kg": self.mass_kg,
            "v_max_mps": self.v_max_mps,
            "v_up_mps": self.v_up_mps,
            "v_dn_mps": self.v_dn_mps,
            "a_max_mps2": self.a_max_mps2,
            "f_max_n": self.f_max_n,
            "f_min_n": self.f_min_n,
            "max_range_m": self.max_range_m,
            "max_time_s": self.max_time_s,
            # 派生量
            "gravity_force_n": self.gravity_force_n,
            "a_z_max_mps2": self.a_z_max_mps2,
            "a_z_min_mps2": self.a_z_min_mps2,
            "a_horiz_max_level_mps2": self.a_horiz_max_level_mps2,
            "v_max_plan_mps": self.v_max_plan_mps,
            "v_h_max_at_climb_mps": self.v_h_max_at_climb_mps,
            "r_min_m": self.r_min_m(),
            "can_hover": self.can_hover(),
        }
