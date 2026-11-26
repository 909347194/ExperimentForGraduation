# -*- coding: utf-8 -*-
"""``LegCostRecord`` 的构造、编解码与失败分级透传。

职责：把"这条航段飞了多长、用了多久、为什么没飞成"压成一条**稳定**的记账单元，
      并以 JSON 原生字典形式进出 ``PlanResult.diagnostics``。
边界：只做形状归一与码表查表，不测长度、不估算时间、不改交付语义。
相关：``common/plan_types.py``（``LegCostRecord`` 定义）、``common/failure_codes.py``
      （``Severity`` 唯一来源）、``methods/planning/tour_planner.py``（唯一生产者）。

**为什么单开一个文件**：``LegCostRecord`` 是跨包契约（上层 ``task_allocation`` 与
编排层都按它回填代价），字段含义必须只有一处解释；把"哪个字段什么时候可以为
None"的判断散在 ``tour_planner`` 的多条返回路径上，迟早出现"失败段给了猜测长度"
这种不可复核的数。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .failure_codes import FAILURES, FailureCode, Severity
from .plan_types import LegCostRecord

__all__ = [
    "severity_of",
    "as_dict",
    "as_dicts",
    "from_dict",
    "from_dicts",
    "planned_record",
    "failed_record",
]


def severity_of(code: FailureCode | str | None) -> str | None:
    """失败码 → 严重性字符串；``None`` 原样返回。未登记的码直接报错。"""
    if code is None:
        return None
    key = code if isinstance(code, FailureCode) else _coerce_code(code)
    return FAILURES[key].severity.value


def as_dict(record: LegCostRecord) -> dict[str, Any]:
    """落盘形状（JSON 原生；``None`` 表示"没有实测值"，不写 0 冒充）。"""
    return {
        "from_id": int(record.from_id),
        "to_id": int(record.to_id),
        "length_m": None if record.length_m is None else float(record.length_m),
        "duration_s": None if record.duration_s is None else float(record.duration_s),
        "time_source": str(record.time_source),
        "failure_code": None if record.failure_code is None else str(record.failure_code),
        "severity": None if record.severity is None else str(record.severity),
    }


def as_dicts(records: Iterable[LegCostRecord]) -> list[dict[str, Any]]:
    return [as_dict(r) for r in records]


def from_dict(raw: Mapping[str, Any]) -> LegCostRecord:
    """读回一条记录；缺省字段按"未知"处理，不做任何推断。"""
    return LegCostRecord(
        from_id=int(raw["from_id"]),
        to_id=int(raw["to_id"]),
        length_m=_opt_float(raw.get("length_m")),
        duration_s=_opt_float(raw.get("duration_s")),
        time_source=str(raw.get("time_source", "planned")),
        failure_code=_opt_str(raw.get("failure_code")),
        severity=_opt_str(raw.get("severity")),
    )


def from_dicts(items: Iterable[Mapping[str, Any]] | None) -> tuple[LegCostRecord, ...]:
    if not items:
        return ()
    return tuple(from_dict(item) for item in items)


def planned_record(
    from_id: int,
    to_id: int,
    *,
    length_m: float,
    duration_s: float,
    time_source: str,
) -> LegCostRecord:
    """已解出航段（含定速降级剖面的 ``time_source=estimate``）。"""
    return LegCostRecord(
        from_id=int(from_id),
        to_id=int(to_id),
        length_m=float(length_m),
        duration_s=float(duration_s),
        time_source=str(time_source),
    )


def failed_record(
    from_id: int,
    to_id: int,
    code: FailureCode | str,
    *,
    length_m: float | None = None,
    duration_s: float | None = None,
) -> LegCostRecord:
    """失败航段：失败码与严重性必填，长度/时长只在**实测存在**时才给。

    这里刻意容忍 ``length_m`` 为 ``None``：多数失败发生在时间层/准入层，几何
    长度没解出来。用 0 或沿用估计值都会让上层把"没飞成"记成"飞了 0 米"，之后
    无法与真·零长度段区分。
    """
    return LegCostRecord(
        from_id=int(from_id),
        to_id=int(to_id),
        length_m=None if length_m is None else float(length_m),
        duration_s=None if duration_s is None else float(duration_s),
        time_source="failed",
        failure_code=str(code.value if isinstance(code, FailureCode) else code),
        severity=severity_of(code),
    )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _coerce_code(value: str) -> FailureCode:
    try:
        return FailureCode(value)
    except ValueError:
        raise KeyError(f"未知失败码 {value!r}：不在 FailureCode 枚举内") from None


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def severity_rank(severity: Severity | str | None) -> int:
    """分级排序（越低越严重）：``infeasible`` < ``internal`` < ``degraded``。"""
    if severity is None:
        return 9
    text = severity.value if isinstance(severity, Severity) else str(severity)
    return {"infeasible": 0, "internal": 1, "degraded": 2}.get(text, 9)
