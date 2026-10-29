# -*- coding: utf-8 -*-
"""从 profile_scaling.json 重新出图（英文标签，避免中文字体缺字）。"""
import json
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
out_dir = os.path.join(HERE, "results")
with open(os.path.join(out_dir, "profile_scaling.json"), encoding="utf-8") as f:
    data = json.load(f)
results = data["results"]

areas = [r["area_km2"] for r in results]
nat_n = [r["nat_nodes"] for r in results]
cap_n = [r["cap2000_nodes"] for r in results]
nat_e = [r["nat_edges_on"] for r in results]
cap_e = [r["cap2000_edges_on"] for r in results]
bt = [r["build_time_s"] for r in results]
qt = [r["query_time_per_od_s"] * 1000 for r in results]

fig, axs = plt.subplots(2, 2, figsize=(13, 10))
ax = axs[0, 0]
ax.plot(areas, nat_n, "o-", label="natural density (cap lifted)")
ax.plot(areas, cap_n, "s--", label="literal params (max_nodes=2000)")
ax.set_xlabel("Area (km^2)")
ax.set_ylabel("Nodes")
ax.set_title("Nodes vs Area")
ax.legend(); ax.grid(alpha=0.3); ax.set_yscale("log")

ax = axs[0, 1]
ax.plot(areas, nat_e, "o-", label="natural (ON, protection)")
ax.plot(areas, cap_e, "s--", label="literal (max_nodes=2000)")
ax.set_xlabel("Area (km^2)")
ax.set_ylabel("Edges")
ax.set_title("Edges vs Area")
ax.legend(); ax.grid(alpha=0.3); ax.set_yscale("log")

ax = axs[1, 0]
ax.plot(areas, bt, "o-", color="purple")
ax.set_xlabel("Area (km^2)")
ax.set_ylabel("Offline build time (s)")
ax.set_title("Build time vs Area")
ax.grid(alpha=0.3)

ax = axs[1, 1]
ax.plot(areas, qt, "o-", color="teal")
ax.set_xlabel("Area (km^2)")
ax.set_ylabel("Online query (ms/OD)")
ax.set_title("Query time vs Area")
ax.grid(alpha=0.3)

fig.suptitle("Visibility-graph scaling (fixed method params)")
fig.tight_layout()
path = os.path.join(out_dir, "figures", "profile_scaling.png")
os.makedirs(os.path.dirname(path), exist_ok=True)
fig.savefig(path, dpi=130, bbox_inches="tight")
print("saved", path)
