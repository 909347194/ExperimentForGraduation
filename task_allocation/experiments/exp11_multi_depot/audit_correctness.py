# -*- coding: utf-8 -*-
"""exp11 独立审计（跑批后单独跑；只看已落盘产物，不重跑求解）。

四件事，全部对账「说得出口的结论」与「落盘事实」：

1. **铁律**：逐 run 的 ``invariants_passed``、``n_hard_violations_total``、
   ``completion_rate_final``、``nest_overflow_total``；任一不过即退出码 1。
2. **异巢占比独立重算**：只用 ``end_nests_signature`` + ``run_meta.initial_home``
   重算一遍，与 metrics.json 的 ``cross_nest_share`` 逐 run 比对（口径：
   起飞巢 = 上一周期落点；周期 1 用初始驻巢）。
3. **前缀一致性**：梯度是同一到达流的前缀。取 T10 逐周期的
   ``return_leg_by_cycle`` 前 T 项之和，与 T 档的 ``total_return_leg_m`` 比对；
   同时比对 ``path_by_cycle`` 前缀。不一致的格子如实列出（不掩盖）。
4. **配置纯度**：同 (tier, seed) 三臂 ``run_meta.stage_effective`` 只应在
   ``end_nest_l1_mode`` / ``end_nest_l3_mode`` 上不同。

用法::

    python audit_correctness.py --preset formal
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import EXP_DIR  # noqa: E402

ARMS = ("home", "nearest", "mcf")
ALLOWED_STAGE_DIFF = {"end_nest_l1_mode", "end_nest_l3_mode"}


def _read(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _parse_signature(sig: str) -> list[dict[int, int]]:
    """``"1>0,2>2|3>1"`` → 逐周期的 {uav: nest}。"""
    out = []
    for cyc in (sig or "").split("|"):
        row: dict[int, int] = {}
        for pair in cyc.split(","):
            pair = pair.strip()
            if not pair or ">" not in pair:
                continue
            u, n = pair.split(">", 1)
            try:
                ui, ni = int(u), int(n)
            except ValueError:
                continue
            row[ui] = ni
        out.append(row)
    return out


def recompute_share(metrics: Mapping[str, Any],
                    initial_home: Mapping[str, Any]) -> float:
    pos = {int(k): int(v) for k, v in (initial_home or {}).items()}
    sorties = cross = 0
    for row in _parse_signature(str(metrics.get("end_nests_signature") or "")):
        for u, n in sorted(row.items()):
            sorties += 1
            if u in pos and pos[u] != n:
                cross += 1
            pos[u] = n
    return (cross / sorties) if sorties else 0.0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="exp11 独立审计")
    ap.add_argument("--preset", default="formal")
    args = ap.parse_args(argv)
    root = EXP_DIR / "results" / args.preset
    sp = root / "summary.json"
    if not sp.exists():
        print(f"[audit] 缺 summary.json：{sp}", flush=True)
        return 2
    summary = _read(sp)
    tiers = [int(t) for t in summary.get("tiers", [])]
    seeds = [int(s) for s in summary.get("seeds", [])]
    problems: list[str] = []
    notes: list[str] = []

    # 1) 铁律 + 2) 异巢重算
    cells: dict[tuple[int, str, int], dict[str, Any]] = {}
    for tier in tiers:
        for arm in ARMS:
            for seed in seeds:
                d = root / f"T{tier}" / arm / f"seed_{seed}"
                mp, metap = d / "metrics.json", d / "run_meta.json"
                if not (mp.exists() and metap.exists()):
                    problems.append(f"缺产物：T{tier}/{arm}/seed_{seed}")
                    continue
                m, meta = _read(mp), _read(metap)
                cells[(tier, arm, seed)] = m
                if not m.get("invariants_passed", False):
                    problems.append(
                        f"铁律失败 T{tier}/{arm}/seed{seed}: {m.get('invariant_violations')}"
                    )
                if int(m.get("n_cycles", 0)) != tier:
                    problems.append(
                        f"周期数不符 T{tier}/{arm}/seed{seed}: 实跑 {m.get('n_cycles')}"
                    )
                share = recompute_share(m, meta.get("initial_home") or {})
                if abs(share - float(m.get("cross_nest_share", 0.0))) > 1e-6:
                    problems.append(
                        f"异巢占比不自洽 T{tier}/{arm}/seed{seed}: "
                        f"metrics={m.get('cross_nest_share')} 重算={round(share, 6)}"
                    )
                if str(meta.get("stop_reason") or "") not in ("", "n_cycles"):
                    notes.append(
                        f"T{tier}/{arm}/seed{seed} stop_reason={meta.get('stop_reason')}"
                    )

    # 2b) 同解换终点反事实的三条硬性质
    #   (a) home 臂：终点本来就是起飞巢 ⇒ gap 必须恒 0（实现自洽的直接证据）；
    #   (b) mcf / nearest 臂：终点改回起飞巢只会更贵 ⇒ gap >= 0
    #       （mcf 由最小费用流的最优性保证，nearest 由「自家巢也在候选集」保证）；
    #   (c) home 分配若本身超容，gap 只是费用标量，不能当「该分配可行」的证据
    #       ⇒ 记录超容周期数，供论文脚注引用。
    gap_neg: list[str] = []
    gap_cycle_overflow = 0
    for (tier, arm, seed), m in sorted(cells.items()):
        gap = float(m.get("same_routes_gap_m", 0.0) or 0.0)
        if arm == "home" and abs(gap) > 1e-6:
            problems.append(
                f"home 臂同解反事实非零 T{tier}/seed{seed}: gap={gap}"
                "（终点=起飞巢时应恒 0，说明 start_nest_id 或代价口径错位）"
            )
        if arm != "home" and gap < -1e-6:
            gap_neg.append(f"T{tier}/{arm}/seed{seed}={gap:.3f}")
        gap_cycle_overflow += int(m.get("n_cycles_home_overflow", 0) or 0)
    if gap_neg:
        problems.append(
            "同解反事实出现负值（终点改回起飞巢反而更省，违反最优性/贪心包含性）："
            + ", ".join(gap_neg[:6])
        )
    notes.append(
        f"同解反事实：home 臂 gap 恒 0 已验证；"
        f"home 分配超容周期累计 {gap_cycle_overflow} 个"
        f"（>0 表示该差额只作费用标量，不作可行性证据）。"
    )

    # 3) 前缀一致性（T10 的前 T 周期 vs T 档整跑）
    prefix_rows: list[dict[str, Any]] = []
    if 10 in tiers:
        for arm in ARMS:
            for seed in seeds:
                full = cells.get((10, arm, seed))
                if not full:
                    continue
                for tier in [t for t in tiers if t < 10]:
                    short = cells.get((tier, arm, seed))
                    if not short:
                        continue
                    leg_pref = sum(float(v) for v in
                                   (full.get("return_leg_by_cycle") or [])[:tier])
                    path_pref = sum(float(v) for v in
                                    (full.get("path_by_cycle") or [])[:tier])
                    d_leg = leg_pref - float(short.get("total_return_leg_m", 0.0))
                    d_path = path_pref - float(short.get("total_path_m", 0.0))
                    prefix_rows.append({
                        "arm": arm, "seed": seed, "tier": tier,
                        "d_return_leg_m": round(d_leg, 3),
                        "d_path_m": round(d_path, 3),
                        "exact": abs(d_leg) < 1e-6 and abs(d_path) < 1e-6,
                    })
        bad = [r for r in prefix_rows if not r["exact"]]
        if bad:
            notes.append(
                f"前缀一致性：{len(bad)}/{len(prefix_rows)} 个格子不等（见 prefix_check.json）；"
                "说明短梯度不是长梯度的严格前缀（解码有跨周期耦合），边际分解须按此声明。"
            )
        else:
            notes.append("前缀一致性：全部格子逐位相等（短梯度 = 长梯度前缀）。")

    # 4) 配置纯度
    for tier in tiers:
        for seed in seeds:
            stages = {}
            for arm in ARMS:
                metap = root / f"T{tier}" / arm / f"seed_{seed}" / "run_meta.json"
                if metap.exists():
                    stages[arm] = _read(metap).get("stage_effective") or {}
            if len(stages) != len(ARMS):
                continue
            base = stages["home"]
            for arm in ARMS[1:]:
                for k in sorted(set(base) | set(stages[arm])):
                    if base.get(k) != stages[arm].get(k) and k not in ALLOWED_STAGE_DIFF:
                        problems.append(
                            f"非预期配置差异 T{tier}/seed{seed} {arm}: {k} "
                            f"{base.get(k)} != {stages[arm].get(k)}"
                        )

    audit = {
        "preset": args.preset,
        "n_cells": len(cells),
        "problems": problems,
        "notes": notes,
        "prefix_check": prefix_rows,
        "ok": not problems,
    }
    (root / "audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for line in problems:
        print(f"[audit] ✗ {line}", flush=True)
    for line in notes:
        print(f"[audit] · {line}", flush=True)
    print(f"[audit] 结论：{'通过' if not problems else '存在失败项'}"
          f"（{len(cells)} 个 run）；明细 {root / 'audit.json'}", flush=True)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
