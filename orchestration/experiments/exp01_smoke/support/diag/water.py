# -*- coding: utf-8 -*-
"""水域 nodata 诊断：只读，不改生产代码、不改任何结果。

对象：data/guangzhou/dem.tif 的 nodata（实测 255.0，非 -9999）。
目的：量化两件事，供是否修改填充策略决策——
  1. 有多少任务点落在水域像素上、当前 ground_z 被糊成多少、与候选 2 m 差多少；
  2. 若整块水域按 2 m 填，岸线相邻有效像元与 2 m 的高差分布（陡壁程度）。

跑法::
    python -m orchestration.experiments.exp01_smoke.support.diag.water
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from scipy import ndimage

from path_planning.methods.utils.dem_geotiff import GeoTIFFDEM

from ..config import EXP_DIR, load_config, resolve
from ..field import load_guangzhou

WATER_FILL_M = 2.0  # 用户选定的候选水面高程（全局最低有效值）


def _px(transform, lon: float, lat: float) -> tuple[int, int]:
    return int((transform.f - lat) / transform.yres), int((lon - transform.c) / transform.xres)


def diagnose() -> int:
    cfg = load_config()
    dem = GeoTIFFDEM(resolve(cfg["data"]["scenario_dir"]) / "dem.tif")
    a = np.asarray(dem.elevation, dtype=np.float64)
    nd = dem.nodata
    water = a == nd
    h, w = a.shape
    valid_vals = a[~water]

    print(f"DEM {h}×{w}，nodata={nd}")
    print(f"水域 {int(water.sum())} 像素，占 {water.mean() * 100:.2f}%")
    print(f"有效高程 {valid_vals.min():.1f}–{valid_vals.max():.1f} m，均值 {valid_vals.mean():.2f}")
    lab, n = ndimage.label(water)
    sizes = ndimage.sum(water, lab, range(1, n + 1))
    big = [(i + 1, int(sizes[i])) for i in range(n) if sizes[i] > 50]
    print(f"连通水域块（>50px）：{big}")
    print()

    # ---- 任务点落水清单 ----
    scene = load_guangzhou(cfg)
    fr = scene.frame
    tasks = json.loads((EXP_DIR / str(cfg["tasks"]["file"])).read_text(encoding="utf-8"))["tasks"]
    rows: list[dict[str, object]] = []
    for t in tasks:
        lat, lon, _ = fr.to_geodetic(t["x"], t["y"], 0.0)
        r, c = _px(dem.transform, float(lon), float(lat))
        in_water = 0 <= r < h and 0 <= c < w and bool(water[r, c])
        if not in_water:
            continue
        cur_ground = float(t["ground_z"])
        cur_z = float(t["z"])
        rows.append({
            "task_id": int(t["id"]), "cycle": int(t["cycle"]),
            "px_row": r, "px_col": c,
            "cur_ground_m": round(cur_ground, 2),
            "fill2m_ground_m": WATER_FILL_M,
            "ground_delta_m": round(cur_ground - WATER_FILL_M, 2),
            "cur_z_m": round(cur_z, 2),
            "fill2m_z_m": round(WATER_FILL_M + float(t["agl_m"]), 2),
            "cur_real_agl_m": round(cur_z - WATER_FILL_M, 2),
            "nominal_agl_m": round(float(t["agl_m"]), 2),
        })

    print(f"落水任务点：{len(rows)} / {len(tasks)}")
    print(f"{'id':>4} {'周期':>4} {'当前ground':>10} {'按2m后':>8} {'差':>7} "
          f"{'当前真实AGL':>11} {'名义AGL':>8}")
    for r in rows:
        print(f"{r['task_id']:>4} {r['cycle']:>4} {r['cur_ground_m']:>10} "
              f"{WATER_FILL_M:>8} {r['ground_delta_m']:>7} "
              f"{r['cur_real_agl_m']:>11} {r['nominal_agl_m']:>8}")

    # ---- 岸线陡壁：水域边界相邻有效像元高程 ----
    edge: list[float] = []
    ys, xs = np.where(water)
    for r, c in zip(ys, xs):
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and not water[rr, cc]:
                edge.append(float(a[rr, cc]))
    edge_arr = np.asarray(edge)
    print()
    print(f"水域边界相邻有效像元 {edge_arr.size} 个（含重复）：")
    if edge_arr.size:
        for q in (0, 10, 25, 50, 75, 90, 100):
            print(f"  P{q:>3} = {np.percentile(edge_arr, q):.2f} m")
        print(f"  按 2 m 填后与岸线高差：中位 {np.median(edge_arr) - WATER_FILL_M:.2f} m，"
              f"最大 {edge_arr.max() - WATER_FILL_M:.2f} m")

    # ---- 落盘（诊断不占正式账：不写 seed_<N>/，只进 results/diagnose/）----
    out_dir = EXP_DIR / "results" / "diagnose"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "water.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["task_id"])
        wr.writeheader()
        wr.writerows(rows)
    print(f"\n落盘 {csv_path}")
    return 0


def main() -> int:
    return diagnose()


if __name__ == "__main__":
    raise SystemExit(main())
