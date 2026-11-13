# -*- coding: utf-8 -*-
"""从已落盘的 metrics/run_meta 重建 summary（不重跑求解）。

用途：给 ``_build_report`` / ``QUALITY_KEYS`` 新增聚合字段后，旧跑批只需
重建汇总，不必重算 84 次求解。**只读 run 产物**，不改任何 run 目录。

用法::

    python rebuild_summary.py --preset formal
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run as exp11_run  # noqa: E402
from metrics import write_summary_csv  # noqa: E402
from paths import EXP_DIR  # noqa: E402


def _purity_from_meta(stages: Mapping[tuple[int, int], Mapping[str, Any]]) -> dict[str, Any]:
    """与 run.py 同口径的纯度检查，但输入是 run_meta 里的 stage_effective 字典。"""
    allowed = {"end_nest_l1_mode", "end_nest_l3_mode", "cost_polish_accept_mode"}
    unexpected: dict[str, Any] = {}
    within: list[str] = []
    for (tier, seed), per_arm in stages.items():
        if set(per_arm) != set(exp11_run.ARMS):
            within.append(f"tier{tier}/seed{seed}: 臂不齐 {sorted(per_arm)}")
            continue
        base = per_arm["home"]
        for arm in exp11_run.ARMS[1:]:
            cur = per_arm[arm]
            for k in sorted(set(base) | set(cur)):
                if base.get(k) != cur.get(k):
                    unexpected.setdefault(k, {})[f"tier{tier}/seed{seed}/{arm}"] = cur.get(k)
    return {
        "differing_fields": sorted(unexpected),
        "n_differing_fields": len(unexpected),
        "only_expected_fields_differ": set(unexpected) <= allowed,
        "unexpected_fields": sorted(set(unexpected) - allowed),
        "shape_issues": within,
        "rebuilt_from_disk": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="exp11 summary 重建")
    ap.add_argument("--preset", default="formal")
    args = ap.parse_args(argv)
    root: Path = EXP_DIR / "results" / args.preset

    per_cell: dict[tuple[int, str], list[dict[str, Any]]] = {}
    stages: dict[tuple[int, int], dict[str, Mapping[str, Any]]] = {}
    seeds: list[int] = []
    tiers: list[int] = []
    per_cycle: int | None = None
    for mp in sorted(root.glob("T*/*/seed_*/metrics.json")):
        tier = int(mp.parts[-4].lstrip("T"))
        arm = mp.parts[-3]
        seed = int(mp.parts[-2].lstrip("seed_"))
        met = json.loads(mp.read_text(encoding="utf-8"))
        per_cell.setdefault((tier, arm), []).append(met)
        meta_p = mp.parent / "run_meta.json"
        if meta_p.exists():
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            stages.setdefault((tier, seed), {})[arm] = meta.get("stage_effective") or {}
            arr = meta.get("arrivals_per_cycle") or {}
            if per_cycle is None and arr:
                per_cycle = int(next(iter(arr.values())))
        for bucket, v in ((seeds, seed), (tiers, tier)):
            if v not in bucket:
                bucket.append(v)
    if not per_cell:
        print(f"[rebuild] 无 run 产物：{root}", flush=True)
        return 2
    seeds.sort()
    tiers.sort()

    purity = _purity_from_meta(stages)
    report = exp11_run._build_report(
        per_cell, seeds=seeds, tiers=tiers, profile=args.preset, purity=purity,
        per_cycle=per_cycle or exp11_run.PER_CYCLE,
    )
    (root / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    write_summary_csv(
        root / "summary.csv",
        {arm: [r for (t, a), rows in per_cell.items() if a == arm for r in rows]
         for arm in exp11_run.ARMS},
    )
    print(f"[rebuild] {len(per_cell)} 格 / {len(seeds)} seeds / {len(tiers)} tiers "
          f"→ {root / 'summary.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
