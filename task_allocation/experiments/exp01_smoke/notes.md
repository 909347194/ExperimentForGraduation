# exp01_smoke

**status:** active

## 目的

- 验证实验目录约定、`config.yaml` 读取与 `results/` 写入是否通畅。
- **不**作为算法性能结论；methods 落地前仅为占位。

## 成功标准

1. `python run.py`（在本目录或指定路径）能读配置并写出 `results/metrics.json`、`results/run_meta.json`。
2. 目录结构符合 `experiments/README.md`。

## 已知问题

- 依赖 `PyYAML`；若环境未装，需先在 `pyproject.toml` 增加依赖并安装。
- `methods/` 仍为注释占位，本实验不调用真实求解器。

## 下一步

- 实现 `TaskPool` / `UAVState` 最小数据结构后，将 smoke 改为一次最小合成实例的端到端调用。
