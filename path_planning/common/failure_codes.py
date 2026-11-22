# -*- coding: utf-8 -*-
"""失败码：全部对外失败码、严重性分级与异常载体。

职责：集中定义 ``path_planning`` 可能上报的所有失败码，给出每个码的
      **严重性**（决定上层是"改任务"还是"接受降级产物"）、中文说明与
      建议补救动作；提供 ``PlanFailure`` 异常供内部抛出。
边界：只管码表与分级，不实现补救动作（补救由上层按 ``severity`` 查表决策）。
相关：``common/types.py``（``PlanResult.failure``）、``api.py``（对外唯一出口）。

分级口径（关键，决定上层行为）：

- ``INFEASIBLE``：这条航次**真的飞不了**。上层必须改任务（减点、换机、换巢）。
- ``DEGRADED``：能飞但质量降级。上层可接受，但必须知道时间/净空是估算值。
- ``INTERNAL``：本模块自身 bug 或数值不可信。**不得**当成"任务不可行"上报。

最后一条最容易错：``KINEMATIC_INCONSISTENT`` / ``CONFLICT_UNRESOLVED`` 若被
当成 INFEASIBLE 上报，会让上层误以为任务本身做不到，从而删掉一条本可飞的
任务——实际是代码算错了。两者必须能区分。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "Severity",
    "FailureCode",
    "FailureInfo",
    "FAILURES",
    "PlanFailure",
    "describe",
]


# ---------------------------------------------------------------------------
# 严重性
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    """失败码的严重性分级。"""

    #: 航次不可行，上层须改任务
    INFEASIBLE = "infeasible"
    #: 可交付但质量降级（时间/净空为估算）
    DEGRADED = "degraded"
    #: 本模块内部问题，不得当成任务不可行上报
    INTERNAL = "internal"


# ---------------------------------------------------------------------------
# 失败码
# ---------------------------------------------------------------------------


class FailureCode(str, Enum):
    """失败码枚举。值即对外字符串，禁止改名（上层按字符串匹配）。"""

    # --- 空间层 ---------------------------------------------------------
    CORRIDOR_TOO_NARROW = "CORRIDOR_TOO_NARROW"
    RRT_NO_PATH = "RRT_NO_PATH"
    CLEARANCE_VIOLATION = "CLEARANCE_VIOLATION"

    # --- 时间层 ---------------------------------------------------------
    OPT_FAILED = "OPT_FAILED"
    TIME_NOT_OPTIMAL = "TIME_NOT_OPTIMAL"
    TIME_EXCEEDS_BUDGET = "TIME_EXCEEDS_BUDGET"
    KINEMATIC_INCONSISTENT = "KINEMATIC_INCONSISTENT"

    # --- 冲突与协调层 ---------------------------------------------------
    NO_LOCAL_SOLUTION = "NO_LOCAL_SOLUTION"
    CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"

    # --- 预算 ----------------------------------------------------------
    RANGE_EXCEEDED = "RANGE_EXCEEDED"
    ENDURANCE_EXCEEDED = "ENDURANCE_EXCEEDED"

    # --- 上层接口 -------------------------------------------------------
    CYCLE_MISALIGN = "CYCLE_MISALIGN"
    CAPABILITY_BELOW_REQUEST = "CAPABILITY_BELOW_REQUEST"


@dataclass(frozen=True)
class FailureInfo:
    """单个失败码的元信息。"""

    code: FailureCode
    severity: Severity
    summary: str
    remedy: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "summary": self.summary,
            "remedy": self.remedy,
        }


#: 码表。新增码必须同时登记 severity——没有默认值，强制想清楚分级。
FAILURES: dict[FailureCode, FailureInfo] = {
    FailureCode.CORRIDOR_TOO_NARROW: FailureInfo(
        code=FailureCode.CORRIDOR_TOO_NARROW,
        severity=Severity.INFEASIBLE,
        summary="走廊在给定半宽下不可构造（建筑过密或通道缺口）",
        remedy="放宽走廊半宽、降低飞行高度层，或改终点机巢",
    ),
    FailureCode.RRT_NO_PATH: FailureInfo(
        code=FailureCode.RRT_NO_PATH,
        severity=Severity.INFEASIBLE,
        summary="RRT 在采样预算内未找到无碰撞初始路径",
        remedy="增大采样预算/放宽净空；仍失败则判几何不可达",
    ),
    FailureCode.CLEARANCE_VIOLATION: FailureInfo(
        code=FailureCode.CLEARANCE_VIOLATION,
        severity=Severity.INFEASIBLE,
        summary="轨迹净空低于 clearance（含采样点内插校验）",
        remedy="抬高飞行高度层或重规划该段",
    ),
    FailureCode.OPT_FAILED: FailureInfo(
        code=FailureCode.OPT_FAILED,
        severity=Severity.INFEASIBLE,
        summary="NLP 求解失败（IPOPT 返回非成功状态）",
        remedy="按退让阶梯重试（无走廊热启动 / 减节点 / 放预算）",
    ),
    FailureCode.TIME_NOT_OPTIMAL: FailureInfo(
        code=FailureCode.TIME_NOT_OPTIMAL,
        severity=Severity.DEGRADED,
        summary="求解收敛但未到局部最优（容差/迭代上限触发）",
        remedy="可交付，标注时间非最优；必要时加迭代预算重解",
    ),
    FailureCode.TIME_EXCEEDS_BUDGET: FailureInfo(
        code=FailureCode.TIME_EXCEEDS_BUDGET,
        severity=Severity.DEGRADED,
        summary="优化时间超过求解预算（wall-clock 或迭代数）",
        remedy="降级为估算时间交付，并标 time_source=estimate",
    ),
    FailureCode.KINEMATIC_INCONSISTENT: FailureInfo(
        code=FailureCode.KINEMATIC_INCONSISTENT,
        severity=Severity.INTERNAL,
        summary="运动学自检不通过（速度超限、弧长-时间不相容、段内速度过零）",
        remedy="修代码：查梯形积分一致性 / 弧长积分 / 速度约束",
    ),
    FailureCode.NO_LOCAL_SOLUTION: FailureInfo(
        code=FailureCode.NO_LOCAL_SOLUTION,
        severity=Severity.DEGRADED,
        summary="局部重规划在局部域内无解（通道被既有航迹占满）",
        remedy="按备选优先升级：垂直层切换 → 缩小局部域重试 → 放弃该轮",
    ),
    FailureCode.CONFLICT_UNRESOLVED: FailureInfo(
        code=FailureCode.CONFLICT_UNRESOLVED,
        severity=Severity.INTERNAL,
        summary="重规划轮次耗尽仍有冲突（终止条件触发）",
        remedy="查检测器与重规划是否自洽；必要时由上层跨周期顺延",
    ),
    FailureCode.RANGE_EXCEEDED: FailureInfo(
        code=FailureCode.RANGE_EXCEEDED,
        severity=Severity.INFEASIBLE,
        summary="航程超出剩余航程（距离口径）",
        remedy="换机 / 中途换电 / 去掉远端任务点",
    ),
    FailureCode.ENDURANCE_EXCEEDED: FailureInfo(
        code=FailureCode.ENDURANCE_EXCEEDED,
        severity=Severity.INFEASIBLE,
        summary="航次总时长超出最大飞行时间（能量口径）",
        remedy="减任务 / 换电；注意本轮 κ_h=1 使判据偏乐观",
    ),
    FailureCode.CYCLE_MISALIGN: FailureInfo(
        code=FailureCode.CYCLE_MISALIGN,
        severity=Severity.DEGRADED,
        summary="航次结束时刻跨过周期边界（busy_until 落在下一周期）",
        remedy="非致命告警：上层按 busy_until 过滤即可，勿当违约",
    ),
    FailureCode.CAPABILITY_BELOW_REQUEST: FailureInfo(
        code=FailureCode.CAPABILITY_BELOW_REQUEST,
        severity=Severity.DEGRADED,
        summary="上层期望速度/能力高于本模块机体上限",
        remedy="接口层校验；不得静默按上限飞，须上报并由上层改排程 ETA",
    ),
}


# ---------------------------------------------------------------------------
# 异常载体
# ---------------------------------------------------------------------------


class PlanFailure(Exception):
    """内部抛出的带码异常。

    内部各层用 ``raise PlanFailure(code, detail)`` 表达失败，由 ``api.py``
    统一捕成 ``PlanResult``；上层**不应**看到这个异常，只看 ``PlanResult``。
    """

    def __init__(self, code: FailureCode, detail: str = "",
                 **context: Any) -> None:
        self.code = code
        self.detail = detail
        self.context = dict(context)
        info = FAILURES[code]
        msg = f"[{code.value}] {info.summary}"
        if detail:
            msg += f" | {detail}"
        super().__init__(msg)

    @property
    def severity(self) -> Severity:
        return FAILURES[self.code].severity

    def as_dict(self) -> dict[str, Any]:
        out = FAILURES[self.code].as_dict()
        out["detail"] = self.detail
        if self.context:
            out["context"] = dict(self.context)
        return out


def describe(code: FailureCode) -> FailureInfo:
    """取码表项；未登记的码直接报错（防止新增码忘记分级）。"""
    try:
        return FAILURES[code]
    except KeyError:  # pragma: no cover - 枚举与码表一旦脱钩即构错
        raise KeyError(f"失败码 {code} 未在 FAILURES 登记严重性") from None
