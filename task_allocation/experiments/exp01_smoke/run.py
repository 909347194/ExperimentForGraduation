"""exp01_smoke 入口：目录与配置联调，不实现完整算法。"""

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


def load_config(path: Path) -> dict:
    if yaml is None:
        raise RuntimeError("需要 PyYAML：请在 pyproject.toml 中加入 pyyaml 依赖后安装")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    cfg_path = EXP_DIR / "config.yaml"
    cfg = load_config(cfg_path)

    out_dir = EXP_DIR / cfg["experiment"]["output_dir"]
    plot_dir = EXP_DIR / cfg["experiment"]["plot_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    plot_dir.mkdir(parents=True, exist_ok=True)

    # smoke：仅验证配置可读、目录可写；算法在 methods/ 落地后再替换此处
    metrics = {
        "status": "smoke_ok",
        "n_tasks": cfg["problem"]["n_tasks"],
        "n_uavs": cfg["problem"]["n_uavs"],
        "n_nests": cfg["problem"]["n_nests"],
        "method": cfg["method"],
        "note": "placeholder metrics; methods not implemented yet",
    }
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
        print(f"[exp01_smoke] wrote {out_dir / 'metrics.json'}")
        print(f"[exp01_smoke] status={metrics['status']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
