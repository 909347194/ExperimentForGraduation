"""诊断版：量化覆盖门禁的增量空间（纯后处理，不动求解器）。

科学问题：第二阶段「S 内可装必装」的覆盖门禁能挽回多少完成率？
若 S 内漏装（voluntary_skip 候选）本就 ≈0，完整版门禁的完成率增量 ≈0。

可算指标（现有 metrics 落盘粒度）：
  A. S 内漏装率 = Σ muas.n_deferred / Σ selection.n_selected（门禁增量的数量上界）
  B. UAV 参与率：n_participating < n_uav_avail 的周期数（存在「空闲机可开班」的周期）
  C. urgent（AntiStarvation 强制）任务的最终命运：完成/淘汰比例
  D. 淘汰任务全生命周期从未执行的占比；执行过又淘汰的（应为 0，恒等式校验）
  E. 每周期装机数 vs 可用 UAV 数分布

不可从现有落盘恢复（需 instrumentation 的精确分账）：
  - S 成员 id 列表（selection.forced_task_ids 被截断到 20，无 selected_ids）
  - per-UAV tour 结构（插入检验需要）
  - muas.n_deferred 的 id（只知道数量，不知道是哪些任务）
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PY = HERE.parent / ".venv" / "Scripts" / "python.exe"

# (标签, metrics.json 路径)
CELLS: list[tuple[str, Path]] = []
for d in sorted((HERE / "exp06_joint_regime" / "results").glob("K10_*")):
    if (d / "metrics.json").exists():
        CELLS.append((d.name, d / "metrics.json"))
p1 = HERE / "exp01_smoke" / "results" / "metrics.json"
if p1.exists():
    CELLS.append(("exp01_fixed_smoke", p1))


def diagnose(tag: str, path: Path) -> dict:
    m = json.loads(path.read_text(encoding="utf-8"))
    cycles = m.get("cycles") or []
    s = m.get("summary") or {}

    n_sel_total = 0
    n_deferred_total = 0          # S 内未装机（muas 层）
    n_tasks_total = 0
    part_lt_avail = 0             # 有 UAV 完全空闲的周期
    n_cycles_part = 0
    exec_per_cycle: list[int] = []
    avail_per_cycle: list[int] = []
    urgent_all: set[int] = set()
    urgent_slack: dict[int, float] = {}   # 该任务最后一次被强制时的 slack
    expired_all: set[int] = set()
    executed_all: set[int] = set()
    unreach_all: set[int] = set()

    for c in cycles:
        sel = c.get("selection") or {}
        muas = c.get("muas") or {}
        n_sel_total += int(sel.get("n_selected") or 0)
        n_deferred_total += int(muas.get("n_deferred") or 0)
        n_tasks_total += int(muas.get("n_tasks") or 0)
        avail = int(muas.get("n_uav_avail") or 0)
        part = muas.get("n_participating")
        if part is not None and avail:
            n_cycles_part += 1
            if int(part) < avail:
                part_lt_avail += 1
        ex = c.get("executed_ids") or []
        exec_per_cycle.append(len(ex))
        avail_per_cycle.append(avail)
        executed_all.update(int(x) for x in ex)
        expired_all.update(int(x) for x in (c.get("expired_ids") or []))
        unreach_all.update(int(x) for x in (c.get("unreachable_ids") or []))
        for ev in c.get("events") or []:
            if isinstance(ev, dict) and ev.get("type") == "urgent_task":
                tid = int(ev["task_id"])
                urgent_all.add(tid)
                urgent_slack[tid] = float(ev.get("slack") or 0.0)

    urgent_done = len(urgent_all & executed_all)
    urgent_expired = len(urgent_all & expired_all)
    urgent_open = len(urgent_all - executed_all - expired_all)
    expired_never_exec = len(expired_all - executed_all)
    executed_then_expired = len(expired_all & executed_all)

    # urgent 死亡任务的「最后自由时刻」：该任务最后一次被标记 urgent 时的 slack。
    # slack 大还死 = 有时间窗却没装上（无论没进 S 还是进了没装，都是系统失败）。
    dead_urgent = urgent_all & expired_all
    last_slacks = [urgent_slack[t] for t in dead_urgent if t in urgent_slack]
    last_slacks.sort()
    n_mark_events = sum(1 for c in cycles for ev in (c.get("events") or [])
                        if isinstance(ev, dict) and ev.get("type") == "urgent_task")

    # deferred（S 内漏装）发生周期的 UAV 参与率：part=K 且 deferred>0
    # ⇒ 无空闲机，漏装更接近 capacity_shortfall；part<K ⇒ voluntary_skip 嫌疑。
    def_cycles_full = 0
    def_cycles_idle = 0
    for c in cycles:
        muas = c.get("muas") or {}
        nd = int(muas.get("n_deferred") or 0)
        if nd <= 0:
            continue
        part = muas.get("n_participating")
        avail = int(muas.get("n_uav_avail") or 0)
        if part is not None and avail and int(part) >= avail:
            def_cycles_full += 1
        else:
            def_cycles_idle += 1

    return {
        "tag": tag,
        "n_cycles": len(cycles),
        "arrived": s.get("n_arrived"),
        "completed": s.get("n_completed"),
        "rate": s.get("completion_rate"),
        "elim": s.get("n_eliminated"),
        "unreach": s.get("n_unreachable"),
        "remain": s.get("n_remaining_pool"),
        "n_sel_total": n_sel_total,
        "n_deferred_total": n_deferred_total,
        "skip_rate": (n_deferred_total / n_sel_total) if n_sel_total else None,
        "part_lt_avail": part_lt_avail,
        "n_cycles_part": n_cycles_part,
        "exec_mean": (sum(exec_per_cycle) / len(exec_per_cycle)) if exec_per_cycle else 0,
        "avail_mean": (sum(avail_per_cycle) / len(avail_per_cycle)) if avail_per_cycle else 0,
        "urgent_n": len(urgent_all),
        "urgent_done": urgent_done,
        "urgent_expired": urgent_expired,
        "urgent_open": urgent_open,
        "expired_never_exec": expired_never_exec,
        "executed_then_expired": executed_then_expired,
        "dead_slack_med": (last_slacks[len(last_slacks)//2] if last_slacks else None),
        "dead_slack_max": (max(last_slacks) if last_slacks else None),
        "dead_slack_gt300": sum(1 for x in last_slacks if x > 300.0),
        "n_mark_events": n_mark_events,
        "def_cycles_full": def_cycles_full,
        "def_cycles_idle": def_cycles_idle,
    }


def main() -> None:
    if not CELLS:
        print("未找到任何可诊断格")
        return
    rows = [diagnose(t, p) for t, p in CELLS]

    L: list[str] = ["# 覆盖门禁增量空间诊断（2026-10-22，纯后处理）\n"]
    L.append("**问题：第二阶段「S 内可装必装」能挽回多少完成率？**")
    L.append("口径：S 内漏装率 = Σ muas.n_deferred / Σ selection.n_selected，")
    L.append("即门禁增量的数量上界（未区分自愿/能力不足——现有落盘无 S 成员 id）。\n")
    L.append("| 格 | 周期 | 到达 | 完成 | 完成率 | 淘汰 | 不可达 | 残留 | Σn_sel | S内漏装 | 漏装率 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        L.append(
            f"| {r['tag']} | {r['n_cycles']} | {r['arrived']} | {r['completed']} "
            f"| {r['rate']:.3f} | {r['elim']} | {r['unreach']} | {r['remain']} "
            f"| {r['n_sel_total']} | {r['n_deferred_total']} "
            f"| {r['skip_rate']*100:.1f}% |"
        )
    L.append("")
    L.append("## UAV 参与与 urgent 任务命运\n")
    L.append("| 格 | 参与<K周期 | 装机/周期 | 可用UAV | urgent数 | 完成 | 淘汰 | 未决 | 淘汰中从未执行 | 执行过又淘汰 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        L.append(
            f"| {r['tag']} | {r['part_lt_avail']}/{r['n_cycles_part']} "
            f"| {r['exec_mean']:.1f} | {r['avail_mean']:.1f} "
            f"| {r['urgent_n']} | {r['urgent_done']} | {r['urgent_expired']} | {r['urgent_open']} "
            f"| {r['expired_never_exec']} | {r['executed_then_expired']} |"
        )
    L.append("")
    L.append("## urgent 死亡任务与 deferred 周期形态\n")
    L.append("| 格 | urgent标记次数 | 死亡数 | 死时slack中位 | 死时slack最大 | slack>300s还死 | deferred周期(满载/有空闲) |")
    L.append("|---|---:|---:|---:|---:|---:|---|")
    for r in rows:
        med = f"{r['dead_slack_med']:.0f}s" if r["dead_slack_med"] is not None else "-"
        mx = f"{r['dead_slack_max']:.0f}s" if r["dead_slack_max"] is not None else "-"
        L.append(
            f"| {r['tag']} | {r['n_mark_events']} | {r['urgent_expired']} | {med} | {mx} "
            f"| {r['dead_slack_gt300']} | {r['def_cycles_full']}/{r['def_cycles_idle']} |"
        )
    L.append("")

    worst = max(rows, key=lambda r: r["skip_rate"] or 0)
    tot_sel = sum(r["n_sel_total"] for r in rows)
    tot_skip = sum(r["n_deferred_total"] for r in rows)
    L.append("## 判定\n")
    L.append(f"- 全局 S 内漏装率：{tot_skip}/{tot_sel} = **{tot_skip/tot_sel*100:.2f}%**"
             f"（最差格 {worst['tag']} 为 {(worst['skip_rate'] or 0)*100:.1f}%）")
    L.append(f"- urgent 强制任务全局：完成 {sum(r['urgent_done'] for r in rows)}、"
             f"淘汰 {sum(r['urgent_expired'] for r in rows)}、未决 {sum(r['urgent_open'] for r in rows)}")
    L.append(f"- 恒等式校验（执行过又淘汰应为 0）：Σ = {sum(r['executed_then_expired'] for r in rows)}")
    L.append("")
    L.append("> 注：S 内漏装是 voluntary_skip 的**上界**（其中可能含物理装不下的 capacity_shortfall，")
    L.append("> 现有落盘无 S 成员 id 与 tour 结构，无法二分）。精确分账需最小 instrumentation。")

    out = HERE / "SKIP_DIAGNOSTIC_2026-10-22.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"→ {out}")
    for r in rows:
        print(f"{r['tag']:<28} skip={r['n_deferred_total']:>3}/{r['n_sel_total']:<4} "
              f"({(r['skip_rate'] or 0)*100:5.1f}%)  part<K={r['part_lt_avail']}/{r['n_cycles_part']} "
              f"urgent {r['urgent_done']}/{r['urgent_expired']}/{r['urgent_open']} "
              f"exp_never_exec={r['expired_never_exec']}")


if __name__ == "__main__":
    main()
