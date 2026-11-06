# -*- coding: utf-8 -*-
"""decision_ledger.py — 统一决策台账（第二阶段 P0 契约层，纯观测）。

来源：`task_allocation/docs/stage2_final_scheme_2026-11-06.md` §6 要素 6
「完整搜索轨迹与消融：DecisionLedger 默认开（纯观测）+ ConstraintSpec
唯一判据 + 三级/2×2 消融」与 §8 P0「台账 reason 字段覆盖率 100%」。

设计纪律（P0 零行为变化）：

- **纯观测**：只追加事件、只累加计数；不参与任何控制流、不改判定、
  不消耗 RNG ⇒ 无条件 bit-exact（验收探针逐位对照）；
- **reason 必填**：每次 (算子, 动作) 必须带 ``reason="hard"|"opt"``——
  hard = 该动作以恢复可行性为目的（修复/投影/构造补全），opt = 该动作的
  接受判据是目标改进（成本严格下降等）。分类按**接受判据**不按函数名
  （plan §5 风险与纪律）；混合算子按其接受判据整体归类并在
  ``gate_reason`` 注明（``_consolidate`` 成本合并 → opt，P1 拆分后细化）；
- **有界内存**：事件流进 ``deque(maxlen=...)``，计数器恒全量；
  生产默认开、事件上限可配，超限只丢最旧事件不丢计数。

最小字段（定稿 §6）::

    (代, 层, 算子, constraint_id, action, object, from, to, reason=hard|opt,
     Δf, Δviolation, accepted, gate_reason,
     selection_object, inherited_object, ledger_id)

``selection_object`` / ``inherited_object`` 服务于 §5 一致性契约：
选择看见的对象（S_repair）与实际进入下一代的基因（g_raw / g_repaired）。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

__all__ = [
    "DecisionEvent",
    "DecisionLedger",
    "REASON_HARD",
    "REASON_OPT",
]

REASON_HARD = "hard"
REASON_OPT = "opt"
_VALID_REASONS = (REASON_HARD, REASON_OPT)

DEFAULT_MAX_EVENTS = 200_000


@dataclass(frozen=True)
class DecisionEvent:
    """一次决策记录（逐体/逐算子/逐移动）。"""

    ledger_id: int
    layer: str                      # decode | polish | select | search | gate
    operator: str                   # 算子名（build_solution 各算子 / dmde_select …）
    constraint_id: str              # ConstraintSpec id 或组合（"range+nest_capacity"）
    action: str                     # merge | pop_task | reflow | assign_end_nests | …
    object: str                     # 作用对象（uav:0 / task:7 / route:0 / solution …）
    from_state: str                 # 动作前状态摘要
    to_state: str                   # 动作后状态摘要
    reason: str                     # hard | opt（必填）
    delta_f: float | None = None
    delta_violation: float | None = None
    accepted: bool = True
    gate_reason: str | None = None
    selection_object: str | None = None
    inherited_object: str | None = None
    gen: int | None = None


@dataclass
class DecisionLedger:
    """决策台账：有界事件流 + 全量计数 + 外部计数源并账。"""

    enabled: bool = True
    max_events: int = DEFAULT_MAX_EVENTS
    _events: deque = field(init=False, repr=False)
    _next_id: int = field(default=0, init=False, repr=False)
    # 全量计数（不受事件上限影响）
    n_total: int = field(default=0, init=False)
    n_reason_missing: int = field(default=0, init=False)
    by_reason: dict[str, int] = field(default_factory=dict, init=False)
    by_operator: dict[str, int] = field(default_factory=dict, init=False)
    by_constraint: dict[str, int] = field(default_factory=dict, init=False)
    n_accepted: int = field(default=0, init=False)
    n_rejected: int = field(default=0, init=False)
    # 外部计数源（order_winner_counts / writeback_stats 并入台账）
    counter_sources: dict[str, Callable[[], Mapping[str, int]]] = field(
        default_factory=dict, init=False
    )
    # 计数族（直接由本台账累加的辅助计数，如 dmde:lamarck_fallback）
    family_counters: dict[str, int] = field(default_factory=dict, init=False)
    # 当前代际号（dmde_solver 每代 set_gen；record 的 gen=None 时取此值）
    current_gen: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._events: deque = deque(
            maxlen=max(1, int(self.max_events)) if self.enabled else 1
        )

    def set_gen(self, gen: int | None) -> None:
        """设置当前代际号（纯观测元数据）。"""
        self.current_gen = None if gen is None else int(gen)

    # ---- 记录 ----

    def record(
        self,
        *,
        layer: str,
        operator: str,
        constraint_id: str,
        action: str,
        object: str,
        from_state: str = "",
        to_state: str = "",
        reason: str,
        delta_f: float | None = None,
        delta_violation: float | None = None,
        accepted: bool = True,
        gate_reason: str | None = None,
        selection_object: str | None = None,
        inherited_object: str | None = None,
        gen: int | None = None,
    ) -> DecisionEvent | None:
        """追加一条决策记录。``reason`` 必须是 hard|opt（缺失即计数并拒绝入账）。"""
        if not self.enabled:
            return None
        if reason not in _VALID_REASONS:
            # 台账纪律：reason 必填且只能是 hard|opt。违规**不入账**但计数，
            # 让「覆盖率 < 100%」在 snapshot 里可见（不静默）。
            self.n_reason_missing += 1
            return None
        ev = DecisionEvent(
            ledger_id=self._next_id,
            layer=layer,
            operator=operator,
            constraint_id=constraint_id,
            action=action,
            object=object,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            delta_f=delta_f,
            delta_violation=delta_violation,
            accepted=bool(accepted),
            gate_reason=gate_reason,
            selection_object=selection_object,
            inherited_object=inherited_object,
            gen=(self.current_gen if gen is None else gen),
        )
        self._next_id += 1
        self._events.append(ev)
        self.n_total += 1
        self.by_reason[reason] = self.by_reason.get(reason, 0) + 1
        self.by_operator[operator] = self.by_operator.get(operator, 0) + 1
        self.by_constraint[constraint_id] = self.by_constraint.get(constraint_id, 0) + 1
        if accepted:
            self.n_accepted += 1
        else:
            self.n_rejected += 1
        return ev

    def tick(self, family: str, n: int = 1) -> None:
        """累加一族辅助计数（如 ``dmde:lamarck_fallback``）。"""
        if not self.enabled:
            return
        self.family_counters[family] = self.family_counters.get(family, 0) + int(n)

    # ---- 读取 ----

    @property
    def events(self) -> list[DecisionEvent]:
        return list(self._events)

    def events_by(self, operator: str) -> list[DecisionEvent]:
        return [e for e in self._events if e.operator == operator]

    def snapshot(self) -> dict[str, Any]:
        """台账摘要（落 ``solution.meta["decision_ledger"]`` / 诊断消费）。

        并入外部计数源（``_ORDER_WINNER_COUNTS`` / ``_WRITEBACK_STATS``），
        使「修复地板 / 进化 / 精修」归因所需的计数在一个出口可得。
        """
        merged: dict[str, dict[str, int]] = {}
        for name, src in self.counter_sources.items():
            try:
                merged[name] = {str(k): int(v) for k, v in dict(src()).items()}
            except Exception:
                merged[name] = {"__error__": 1}
        n = max(1, self.n_total)
        return {
            "enabled": bool(self.enabled),
            "n_total": int(self.n_total),
            "n_events_kept": len(self._events),
            "by_reason": dict(self.by_reason),
            "by_operator": dict(self.by_operator),
            "by_constraint": dict(self.by_constraint),
            "n_accepted": int(self.n_accepted),
            "n_rejected": int(self.n_rejected),
            # reason 覆盖率 = 入账事件中带合法 reason 的比例（恒 1.0 除非
            # record 被传入非法 reason——此时 n_reason_missing > 0 可见）。
            "reason_coverage": float(self.n_total / n),
            "n_reason_missing": int(self.n_reason_missing),
            "family_counters": dict(self.family_counters),
            "counters": merged,
        }


# ---------------------------------------------------------------------------
# 全局默认台账（求解器/评价器共享；tests 可 reset）
# ---------------------------------------------------------------------------
# 口径：一次 solve = 一个 MUASAssignmentEvaluator 实例，实例自带
# ``self.ledger``（见 evaluator.__init__）；本全局实例服务于不挂评价器的
# 记录点（如 dmde_solver 的选择事件在无 evaluator.ledger 时的兜底）。

_GLOBAL_LEDGER = DecisionLedger(enabled=True)


def get_ledger() -> DecisionLedger:
    return _GLOBAL_LEDGER


def reset_ledger(**kwargs: Any) -> DecisionLedger:
    """重建全局台账（测试隔离用）。"""
    global _GLOBAL_LEDGER
    _GLOBAL_LEDGER = DecisionLedger(**kwargs)
    return _GLOBAL_LEDGER
