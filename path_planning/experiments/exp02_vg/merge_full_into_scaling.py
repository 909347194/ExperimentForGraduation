# -*- coding: utf-8 -*-
"""把 profile_full_region.json 的全图实测点并入 profile_scaling.json（保留已有曲线）。"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_RES = os.path.join(_HERE, "results")

with open(os.path.join(_RES, "profile_scaling.json"), encoding="utf-8") as f:
    scaling = json.load(f)
with open(os.path.join(_RES, "profile_full_region.json"), encoding="utf-8") as f:
    full = json.load(f)

rec = full["full_region"]

# 去重：若已含同半宽，替换；否则追加
sizes = scaling.get("sizes_scanned", [])
existing = {r["half_width_m"]: i for i, r in enumerate(scaling["results"])}
if rec["half_width_m"] in existing:
    scaling["results"][existing[rec["half_width_m"]]] = rec
else:
    scaling["results"].append(rec)
    sizes = sorted(set(sizes + [rec["half_width_m"]]))
scaling["sizes_scanned"] = sizes

# 全图信息（覆盖为本次实测口径）
scaling["full_map"] = full["full_map"]
scaling["params_method"] = full["params_method"]

# 分区建议（以全图为上限）
target = 2000
tiles = max(1, int(__import__("math").ceil(rec["nat_nodes"] / target)))
scaling["partition_suggestion"] = {
    "max_half_width_m": rec["half_width_m"],
    "max_area_km2": rec["area_km2"],
    "max_nat_nodes": rec["nat_nodes"],
    "max_build_time_s": rec["build_time_s"],
    "suggested_tile_nodes_target": target,
    "suggested_n_tiles": tiles,
    "note": "若全图规模超出可承受上限（内存/重复重建），则按 ~target 节点/片划分网格瓦片；"
            "实测 104k 节点/2.44M 边建图≈21min 为一次性成本，分区非必须。",
}

# 记录口径说明
scaling["notes"] = scaling.get("notes", [])
scaling["notes"].append(
    "全图点(2396m)用 include_dynamic_as_static=false（选项A真实部署口径）；"
    "原有 h=300..1300 曲线点为早期 include_dynamic_as_static=true 跑出，"
    "二者仅差动态禁飞是否并入静态（3 个动态多边形，影响极小）。"
)

with open(os.path.join(_RES, "profile_scaling.json"), "w", encoding="utf-8") as f:
    json.dump(scaling, f, indent=2, ensure_ascii=False)

print("merged. sizes_scanned =", scaling["sizes_scanned"])
print("full point:", json.dumps(rec, ensure_ascii=False))
print("partition_suggestion:", json.dumps(scaling["partition_suggestion"], ensure_ascii=False))
