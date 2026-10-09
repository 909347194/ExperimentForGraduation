"""exp01_smoke 入口：目录与配置联调，不实现完整算法。

本次已接入真实场景数据：从 ``data/nest_location_data.csv`` 读机巢经纬度，
按 ``problem.n_nests`` / ``problem.nest_capacity`` 构建机巢，并用 DEM 采样高程。
算法在 methods/ 落地后再替换求解部分。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

EXP_DIR = Path(__file__).resolve().parent
ROOT = EXP_DIR.parents[2]  # 仓库根目录
DATA_DIR = ROOT / "task_allocation" / "data"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_config(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("需要 PyYAML：请在 pyproject.toml 中加入 pyyaml 依赖后安装")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_scenario(cfg: dict):
    """按 config 的 data/problem 段构建机巢场景；source=synthetic 时返回 None。"""
    data_cfg = cfg.get("data", {}) or {}
    if data_cfg.get("source", "synthetic") != "file":
        return None

    from task_allocation.common.scenario import build_nest_scenario

    problem_cfg = cfg["problem"]
    csv_path = DATA_DIR / data_cfg["path"]
    dem_name = data_cfg.get("dem")
    dem_path = (DATA_DIR / dem_name) if dem_name else None

    return build_nest_scenario(
        csv_path,
        count=problem_cfg.get("n_nests"),
        capacity=problem_cfg.get("nest_capacity", 10),
        column=data_cfg.get("coordinate_column", "Coordinate"),
        dem_path=dem_path,
    )


def main() -> int:
    cfg_path = EXP_DIR / "config.yaml"
    cfg = load_config(cfg_path)

    out_dir = EXP_DIR / cfg["experiment"]["output_dir"]
    plot_dir = EXP_DIR / cfg["experiment"]["plot_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    plot_dir.mkdir(parents=True, exist_ok=True)

    scenario = build_scenario(cfg)

    # smoke：仅验证配置可读、目录可写、场景可构建；算法在 methods/ 落地后再替换此处
    metrics = {
        "status": "smoke_ok",
        "n_tasks": cfg["problem"]["n_tasks"],
        "n_uavs": cfg["problem"]["n_uavs"],
        "n_nests": cfg["problem"]["n_nests"],
        "nest_capacity": cfg["problem"].get("nest_capacity"),
        "method": cfg["method"],
        "note": "placeholder metrics; methods not implemented yet",
    }
    if scenario is not None:
        frame = scenario.frame
        metrics["local_frame"] = {"lat0": frame.lat0, "lon0": frame.lon0}
        metrics["nests"] = [
            {
                "id": n.id,
                "x": round(n.x, 3),
                "y": round(n.y, 3),
                "z": round(n.z, 3),
                "capacity": n.capacity,
                "lat": n.meta["lat"],
                "lon": n.meta["lon"],
            }
            for n in scenario.nests
        ]
    run_meta = {
        "experiment": cfg["experiment"]["name"],
        "seed": cfg["experiment"]["seed"],
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config_path": str(cfg_path.relative_to(ROOT)),
        "repo_root": str(ROOT),
    }

    if cfg["run"].get("save_results", True):
        (out_dir / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (out_dir / "run_meta.json").write_text(
            json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if cfg["run"].get("verbose", True):
        print(f"[exp01_smoke] config={cfg_path}")
        if scenario is not None:
            print(
                f"[exp01_smoke] scenario: {scenario.count} nests, "
                f"capacity={metrics['nest_capacity']}, "
                f"frame=({scenario.frame.lat0:.6f}, {scenario.frame.lon0:.6f})"
            )
            for n in scenario.nests:
                print(
                    f"[exp01_smoke]   nest {n.id}: x={n.x:9.1f} y={n.y:9.1f} "
                    f"z={n.z:6.1f}  ({n.meta['lat']:.6f}, {n.meta['lon']:.6f})"
                )
        print(f"[exp01_smoke] wrote {out_dir / 'metrics.json'}")
        print(f"[exp01_smoke] status={metrics['status']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
