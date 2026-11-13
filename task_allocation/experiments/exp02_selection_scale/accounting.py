# -*- coding: utf-8 -*-
"""exp02 单周期任务去向分账与口径门禁（实验自持，不进公共层）。

本模块只做两件事，且都不碰求解：

1. :func:`accounting_from` —— 把「到达 N / 选中 / 交付」摊成主线恒等式
   ``arrived = done + unreachable + remain`` 的可审计形式。单周期静态快照里
   **没有几何不可达判定**，故 ``unreachable`` 记为 ``None``（不可观测）而非
   伪装成 0；此时闭合式退化为 ``arrived = done + remain``，并显式标注。
2. :data:`BANNED_CLAIMS` —— 越界的「质量优劣」结论词，供回归测试扫源码，
   防止本实验重新滑回「方法好坏对照台」。

口径边界（务必与 notes.md 一致）：

* 本实验是**单周期静态快照**，没有后续周期。未选中任务既不叫「暂缓」也不
  叫「顺延」，一律记 ``remain``（未完成）。因此本实验**不宣称完成约束被
  满足**，也不产出完成率/方法优劣结论；「完成是约束」由 exp01 滚动闭环检验。
* 分账的目的仅是**可审计**：任何一格都不允许出现「任务凭空消失」。
"""
from __future__ import annotations

from typing import Any

#: 越界结论词：出现在本实验对外文本里即视为职责漂移。
#: 只列强结论短语，不禁单字「质量」（「质量门禁」等合法用法不受影响）。
BANNED_CLAIMS: tuple[str, ...] = (
    "质量保持率",
    "不掉质量",
    "右下为优",
    "加速比",
    "更优",
    "最优",
    "平衡点",
    "损失多少质量",
    "质量上界",
)


def assert_no_quality_claims(text: str, where: str = "") -> None:
    """文本里出现越界结论词即抛错（供出图/出表前调用）。"""
    hits = [w for w in BANNED_CLAIMS if w in text]
    if hits:
        tag = f" [{where}]" if where else ""
        raise ValueError(f"exp02 职责越界{tag}：出现结论词 {hits}；"
                         f"本实验只做规模可扩展性描述，不做优劣判定。")


def accounting_from(
    n_arrived: int,
    n_selected: int,
    n_done: int,
    *,
    selection_active: bool,
) -> dict[str, Any]:
    """单周期任务去向分账；不满足闭合式直接抛错（门禁不是装饰）。

    Args:
        n_arrived: 本周期到达任务数 N（= 选择层输入 ``n_tasks_in``）。
        n_selected: 进入二阶段 MUAS 的任务数（全量对照 = N）。
        n_done: 二阶段真正交付的任务数（``executed_task_ids`` 长度）。
        selection_active: 本格是否真的触发了选择层截断（N > 选择上界）。
    """
    n_arrived, n_selected, n_done = int(n_arrived), int(n_selected), int(n_done)
    if min(n_arrived, n_selected, n_done) < 0:
        raise ValueError("分账计数不得为负")
    if not (0 <= n_done <= n_selected <= n_arrived):
        raise ValueError(
            f"分账序关系不成立：done={n_done} <= selected={n_selected} "
            f"<= arrived={n_arrived}"
        )

    n_not_selected = n_arrived - n_selected
    n_deferred_in_selected = n_selected - n_done
    n_remain = n_arrived - n_done

    # 主线恒等式 arrived = done + unreachable + remain；本实验无几何不可达
    # 判定，故 unreachable 不可观测（None），闭合式退化为 arrived = done + remain。
    identity_ok = (n_arrived == n_done + n_remain)
    if not identity_ok:  # pragma: no cover - 构造上恒成立，保留为防御
        raise ValueError("arrived = done + remain 未闭合")

    return {
        "n_arrived": n_arrived,
        "n_selected": n_selected,
        "n_not_selected": n_not_selected,
        "n_done": n_done,
        "n_deferred_in_selected": n_deferred_in_selected,
        "n_remain": n_remain,
        "n_unreachable": None,
        "unreachable_not_observed": True,
        "identity": "arrived = done + remain（unreachable 本实验不可观测）",
        "identity_ok": bool(identity_ok),
        "selection_active": bool(selection_active),
        "scope": "static_single_cycle_snapshot",
        "note": (
            "单周期快照无后续周期：未选中与选中未交付一律记 remain（未完成）；"
            "本实验不评价完成约束，完成约束由 exp01 滚动闭环检验。"
        ),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """全格分账汇总，写进 metrics 顶层供复现核对。"""
    accs = [r["accounting"] for r in rows if r.get("accounting")]
    return {
        "n_cells": len(rows),
        "n_accounted": len(accs),
        "identity_ok_all": bool(accs) and all(a["identity_ok"] for a in accs),
        "n_cells_selection_active": sum(1 for a in accs if a["selection_active"]),
        "n_cells_selection_inactive": sum(1 for a in accs if not a["selection_active"]),
    }
