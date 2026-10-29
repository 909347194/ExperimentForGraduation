# exp02_vg：可见性图 / 稀疏路图规划器验证实验

> 验证用户提出的路径规划**替代**思路：建筑简化 + 轮廓采样节点 + 视线可达边 + 机动保护区；
> 放弃 TPR，仅用几何约束（建筑 + 禁飞区）；不使用 NetworkX（自实现邻接表 + 堆优化 Dijkstra/A*）。
> 方法实现见 `path_planning/methods/visibility_graph/`。
> **详细结论与数字见 [`results/VALIDATION_REPORT.md`](results/VALIDATION_REPORT.md)。**

## 1. 验证目标

1. 20,655 栋建筑能否压缩为「节点 < 2000、边仅连视线可达点对」的可行稀疏路图；
2. 机动保护区能否显著删边（文献量级 ~70%）且路径仍避障避禁飞、被阻断 OD 能正确绕行；
3. 同一套参数在大区 / 全图规模下节点 / 边 / 建图时间 / 在线查询时间曲线（**分区决策依据**）；
4. 动态禁飞区按**选项 A**（查询期时间窗检查）是否生效且图无需重建。

## 2. 运行

```bash
# 主验证（建图 + 保护区开/关对照 + 独立碰撞审计 + 动态时间窗演示 + 俯视图）
.venv/Scripts/python.exe path_planning/experiments/exp02_vg/run.py --plot

# 大区 / 全图扩展 profiling（节点/边/建图/查询曲线）—— 可能耗时数分钟到数十分钟
.venv/Scripts/python.exe path_planning/experiments/exp02_vg/profile_scaling.py --plot

# 机动保护区灵敏度扫描（固定图结构，扫 clearance）
.venv/Scripts/python.exe path_planning/experiments/exp02_vg/sweep_protection.py
```

> 不依赖 NetworkX；若用 uv 环境，等价地 `uv run python path_planning/experiments/exp02_vg/run.py --plot`。

## 3. 配置（`config.yaml`）

- `scenario`：裁剪窗口 `crop: [1766.6, 2575.7, 2366.6, 3175.7]`、`max_buildings: 900`（529 栋建筑 + 3 静态 / 3 动态禁飞，同时压建筑避让与禁飞避让）；
- `visibility_graph`：方法参数（`simplify_mode/simplify`、`d_build=5`、`node_step=40`、`node_buffer=5`、`altitude_layers=[20,50]`、`grid_step=100`、`max_nodes=2000`、`max_edge_len=200`、`edge_margin=2`、`max_degree=40`、`protection_clearance=8`、`attach_radius=80`、`include_dynamic_as_static=false`、`cruise_speed_mps=15`）；
- `nofly.source: file`、`od.n_pairs: 8`、`checks.audit_step_m: 2`。

## 4. 关键结果（clearance=8 m，529 栋建筑窗口）

| 指标 | 数值 | 对照 |
|---|---|---|
| 节点数 | **1397** | < 2000 ✓ |
| 机动保护区删边 | **65.2%** | 文献 ~70% ✓ |
| 可行 OD | ON 8/8，OFF 8/8 | 全部可达 ✓ |
| 独立零余量审计 | 8/8 全清 | 切实避障避禁飞 ✓ |
| 路径膨胀（文献口径 per-OD） | 均值 10.35% ± 9.35%（中位 7.55%） | 8/8 可行对保留（连通性保持 100%） |

**机动保护区灵敏度**（固定图结构扫 `protection_clearance`）：删边 57–66%，所有设置下 8/8 可行 + 审计全清（详见报告）。

**大区 profiling**（同一套参数、只放宽 `max_nodes`）：
全图 23.23 km² / 20,655 栋；在 6.76 km²（半宽 1300 m）实测 46,373 节点 / 947,661 边 / 建图 648 s / 查询 518 ms。
结论：**「图变大」本身不构成拒绝全局图的理由**（建图离线一次、内存~百 MB、查询亚秒）；真正需要分区仅当
① 部署内存受限，② 需频繁增量重建。本场景（建筑静态、动态走选项 A）**不部署在内存受限设备则单一全局静态图即可，分区是可选优化**。

**动态禁飞（选项 A）**：注入式演示中，同一 OD 在动态区 `idle` 时刻代价 238.98 m（≈直线）、`active` 时刻 301.76 m（绕行），均过零余量审计——证明动态门在查询期生效、图无需重建。

## 5. 输出（`results/`）

- `metrics.json`：主指标（含 `planning.path_length_inflation_on_vs_off` 文献口径定义、`dynamic_demo`、`dynamic_nofly_option`）；
- `VALIDATION_REPORT.md`：方法、两个关键 bug 修复、最终结果、灵敏度、profiling、选项 A 全记录；
- `figures/visibility_graph_topview.png`：俯视路图（节点 + LOS 边 + 一条规划路径）；
- `figures/profile_scaling.png` + `profile_scaling.json`：节点/边/时间曲线；
- `sweep_protection_clearance.json`：保护区灵敏度表；
- `metrics_tall_window_baseline.json`：早期 run2 备份（节点 1237、保护关对照）。

## 6. 复现注意

- 验证窗口是**手动裁剪**的小窗（非全图）；全图 profiling 为外推 + 部分实测（最大跑到 6.76 km²），非全图跑完。
- `include_dynamic_as_static=false` 为默认（选项 A）。若置 `true`，动态禁飞会被保守烤入建图（对应选项 B：安全但过度剪枝，丢失时间变化语义）。
- 数据文件中真实动态禁飞区位于 x≈3900, y≈900（exp01 窗口内），不在本实验裁剪窗内，故动态演示用合成区注入以隔离验证机制。
