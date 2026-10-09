# task_allocation 实施路线图（TODO）

依据 `Chapter3_README.md` 的技术路线，结合当前代码实际完成度拆解。
状态标记：`[x]` 已完成 / `[~]` 部分完成 / `[ ]` 待做 / `[!]` 阻塞

---

## 0. 现状盘点

### 0.1 论文技术路线 → 代码落点

| 技术路线环节（§8）                      | 代码位置                                 | 状态 | 备注                                                                                          |
| --------------------------------------- | ---------------------------------------- | ---- | --------------------------------------------------------------------------------------------- |
| 动态任务到达 → 任务池更新               | `methods/task_pool.py`                   | [x]  | `T_t = remain ∪ new ∪ release` 归类、周期推进、释放/过期                                      |
| 当前可用 UAV 确定                       | `methods/uav_state.py`                   | [x]  | `U_t^avail` / `K_avail(t)`、状态机、UAV↔任务绑定                                              |
| 任务收益/紧迫度/等待/协同/代价          | `methods/selection/priority.py`          | [x]  | 五项加权`P_i(t)`，权重归一化                                                                  |
| 基础优先级预筛选（Top-αK）              | `selection/priority.top_alpha_preselect` | [x]  | α∈[2,3]                                                                                       |
| 边际收益增量选择                        | `methods/selection/marginal_gain.py`     | [x]  | `insert_delta_cost` 已实现 3D 插入式 `ΔC_i ≈ C_ai + C_ib − C_ab`                              |
| 快速可行性检查                          | `methods/selection/feasibility.py`       | [x]  | prefilter / postcheck                                                                         |
| 选择性多机巢 MUAS 模型（§4）            | `methods/muas/problem.py`                | [~]  | 决策变量 y/x/π/z、双目标 F1/F2 均已建模；**缺机巢容量与三维可达约束**                         |
| 三维地理代价`C_ij^3D`（§5(5)）          | `methods/muas/cost/*`                    | [x]  | DEM + 垂直切面 + 代价矩阵，已与真实 DEM 打通                                                  |
| 统一编码 / 映射 / 逆映射（§5(1)(2)(3)） | `methods/muas/representation/*`          | [!]  | encoder / mapper / inverse_mapper 有，但**`repair_rules/` 三个文件缺失 → 整条链 import 失败** |
| 进化算子                                | `methods/muas/operators/*`               | [x]  | crossover / mutation / extinction / scale_factor                                              |
| 约束处理（§5(4)）                       | `methods/muas/constraints/`              | [ ]  | **空包**，约束检查散落在 `problem.check_feasible`                                             |
| 改进 DMDE 求解器（§5）                  | `methods/muas/solvers/dmde_solver.py`    | [!]  | 258 行已写，但**当前无法导入**                                                                |
| 动态滚动与闭环（§6）                    | `methods/rolling/`                       | [ ]  | **`horizon.py` / `event_trigger.py` 均为 0 行**                                               |
| 对照方法                                | `baselines/*`                            | [ ]  | greedy / random_assign 仅规范注释占位                                                         |
| 场景与数据                              | `common/scenario.py`、`data/`            | [x]  | 机巢 CSV + DEM + 坐标转换工具链                                                               |
| 实验                                    | `experiments/exp01_smoke`                | [~]  | 仅目录与配置联调                                                                              |

### 0.2 关键阻塞（必须先解）(是否需要改进？)

```
methods/muas/representation/repair_rules/
├── __init__.py
├── nearest_match.py      存在
├── unique_filter.py      存在
└── invalid_mutator.py    存在
```

`inverse_mapper.py` 第 16-18 行 import 这三个模块，实测：

```
ModuleNotFoundError: No module named
  'task_allocation.methods.muas.representation.repair_rules.nearest_match'
```

连带 `dmde_solver` 无法导入 → **第二阶段（论文 §4-§5）整条链跑不起来**。

---

## 1. 阶段 0：打通第二阶段（P0）

**目标**：MUAS 能求解并产出 `AllocationSolution`。

| #   | 任务                                | 说明                                                                                                            |
| --- | ----------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| 0.1 | 补`repair_rules/invalid_mutator.py` | 需导出`repair_invalid(cm, mask, invalid_indices, model_type, n_uavs) -> iterable[(uav_id, tgt_id, cost)]`       |
| 0.2 | 补`repair_rules/unique_filter.py`   | 需导出`mask_balanced(mask, row, col)`、`mask_overloaded(mask, row, col)`                                        |
| 0.3 | 补`repair_rules/nearest_match.py`   | 需导出`nearest_match_adaptive(cv, cm, mask, temperature=..., rng=...)`，top_k 随温度衰减                        |
| 0.4 | 求解器最小闭环                      | `DMDESolver.solve()` 对 10 任务 / 3 机 / 2 巢产出 `SolverResult`（`extra["solution"]` 挂 `AllocationSolution`） |
| 0.5 | 端到端冒烟                          | `TaskPool.begin_cycle → selection.run → muas → apply_solution → complete/release` 全链跑通                      |

**验收标准**

- `python -c "import task_allocation.methods.muas.solvers.dmde_solver"` 通过
- 小规模算例产出的解满足 `validate_unique_tasks()`，且 `check_feasible` 无 violation
- 0.5 的闭环跑 3 个滚动周期不抛异常

**风险**：`repair_rules` 的实现质量直接决定 DMDE 的解质量与收敛性，是 §5 的核心。建议先按 `inverse_mapper` 的调用签名写最小可用版本，再对照论文 §5(4) 的约束处理补强。

---

## 2. 阶段 1：补齐 MUAS 约束（P1）

对应 §4.3 列出的 7 条约束，当前 `check_feasible` 覆盖情况：

| 约束（§4.3）              | 现状                                        | 待做                     |
| ------------------------- | ------------------------------------------- | ------------------------ |
| 每个任务至多一架 UAV      | [x]`validate_unique_tasks`                  | —                        |
| 每架 UAV 可不执行任务     | [x]`allow_idle_uav`                         | —                        |
| UAV 航程约束              | [x]`pc > u.remaining_range`                 | —                        |
| 任务时间窗约束            | [x]`earliest` / `latest` 游标推进           | —                        |
| 终点机巢可达性            | [x]`end_nest_id is None` / `nest.available` | —                        |
| **机巢容量或可用性**      | [~] 只查`nest.available`                    | **2.1 增加容量计数约束** |
| **三维地理环境 / 可达性** | [ ] 仅有代价近似                            | **2.2 增加可达性判定**   |

| #   | 任务                                                                                                      |
| --- | --------------------------------------------------------------------------------------------------------- |
| 2.1 | 机巢容量约束：按`end_nest_id` 统计各巢降落数 ≤ `Nest.capacity`，超出进 penalty                            |
| 2.2 | 三维可达性：`vertical_section` 已声明"不做完整三维可飞性"，需新增可达性判定（最小离地余量 / 禁飞区）      |
| 2.3 | `methods/muas/constraints/` 落位：把约束判定 + 惩罚从 `problem.check_feasible` 拆出，形成可单测的约束集合 |
| 2.4 | 约束违反的惩罚归一化：`f2 = 路径代价 + w_penalty × penalty`，各项惩罚需同量纲                             |

**验收标准**：`check_feasible` 覆盖上述 7 条，每条有独立 violation 标记与对应惩罚；构造违反样例可单测。

---

## 3. 阶段 2：动态滚动闭环（P1，论文 §6）

**这是当前最大的空白**，也是论文"动态滚动"标题的落点。

| #   | 任务                                                                                                                       |
| --- | -------------------------------------------------------------------------------------------------------------------------- |
| 3.1 | `rolling/horizon.py`：单周期编排<br>`begin_cycle → selection.run → muas solve → apply_solution → 反馈`                     |
| 3.2 | 状态反馈串联：`fleet.mark_fault(uid)` → `pool.release(ids, reason="uav_fault")`（两个状态机不互相 import，由本层显式串联） |
| 3.3 | `rolling/event_trigger.py`：事件驱动提前重规划<br>触发条件（§6）：UAV 故障 / 重要紧急任务到达 / 机巢不可用                 |
| 3.4 | 周期参数：规划周期长度、执行推进步长、重规划触发阈值                                                                       |
| 3.5 | 执行推进：按`UAVTour` 推进位置与剩余航程（`fleet.update_position` / `consume_range`），任务到达 `latest` 前完成判定        |

**验收标准**

- 多周期滚动跑通，`TaskPoolSnapshot.counts` 中 `n_remain / n_new / n_release` 随时间合理演化
- 注入 UAV 故障事件后，未完成任务在下一周期以 `RELEASE` 出现在 T_t
- `U_t^exec ⊆ U_t^avail`、`T_t^exec ⊆ T_t^sel` 两个包含关系始终成立

**依赖**：阶段 0（否则没有解可以执行）。

---

## 4. 阶段 3：动态任务到达过程（P2）

| #   | 任务                                                                                                  |
| --- | ----------------------------------------------------------------------------------------------------- |
| 4.1 | 任务到达模型：泊松过程 / 分批到达 / 紧急插单，产出`list[Task]` 交给 `pool.add_new`                    |
| 4.2 | 任务点数据：仿照机巢增加`data/task_location_data.csv`，复用 `methods/utils/coordinates.py` 的坐标链路 |
| 4.3 | 时间窗生成：按任务类型给`earliest` / `latest`（紧迫任务短窗、常规任务长窗）                           |
| 4.4 | 任务收益`reward` 设定：与输电巡检业务对齐（杆塔等级 / 缺陷等级）                                      |

**验收标准**：可配置任务到达率，重跑得到不同 `|T_t| / K_avail(t)` 比值的场景（覆盖 §1(4) 的"数量与能力不匹配"）。

---

## 5. 阶段 4：对照方法（P2）

| #   | 任务                                                                                |
| --- | ----------------------------------------------------------------------------------- |
| 5.1 | `baselines/greedy.py`：按代价/优先级贪心匹配，继承 `BaseOptimizer`，`name="greedy"` |
| 5.2 | `baselines/random_assign.py`：随机可行分配，`name="random"`                         |
| 5.3 | `baselines/registry.py`：注册进 `REGISTRY`（当前为空 dict）                         |
| 5.4 | 统一入口：所有求解器共用同一`CostProvider`，保证可比                                |

**验收标准**：`get_baseline("greedy")` 可取、可求解、输出 `SolverResult`，`cost_history=[]`、`total_generations=0`（无迭代）。

---

## 6. 阶段 5：实验体系（P2-P3）

按 `experiments/README.md` 约定，一实验一目录。

| #   | 实验                    | 验证内容                                                                                                             | 依赖   |
| --- | ----------------------- | -------------------------------------------------------------------------------------------------------------------- | ------ |
| 6.1 | `exp02_selection_scale` | 第一阶段规模压缩：`                                                                                                  | T_t    |
| 6.2 | `exp03_rolling_dynamic` | 滚动闭环：完成率、平均等待时间、`K_avail(t)` 利用率、故障恢复                                                        | 阶段 2 |
| 6.3 | `exp04_ablation`        | 消融：去掉等待补偿 W / 空间协同 G / 边际代价 ΔC / 3D 地形代价                                                        | 阶段 1 |
| 6.4 | `exp05_solver_compare`  | 对比：DMDE vs greedy vs random vs （可选）标准 GA/PSO                                                                | 阶段 4 |
| 6.5 | 指标规范化              | 固定`metrics.json` 字段：`n_completed`、`completion_rate`、`avg_wait`、`total_cost`、`solve_time_ms`、`K_avail_mean` | —      |

**规模提醒**：`build_pairwise_costs` 实测约 **260 μs/对**，O(n²)。任务数 100 → 约 2.7 s，300 → 约 24 s，500 → 约 66 s。设计 exp02 规模上限时先算这笔账；若要上更大规模，优先做代价矩阵缓存/增量更新，**不要**先去动坐标转换（那部分只占 234 ms 一次性）。

---

## 7. 阶段 6：可视化与论文产物（P3）

| #   | 任务                                                                       |
| --- | -------------------------------------------------------------------------- |
| 7.1 | 任务分配图：机巢 / UAV 航次 / 任务点在局部平面上的分布（`plot/`）          |
| 7.2 | 收敛曲线：DMDE 代数 vs 目标值（`cost_history`）                            |
| 7.3 | 滚动时间线：`T_t` 各来源规模、`K_avail(t)` 随 t 变化                       |
| 7.4 | 三维代价场：垂直切面剖面 + 地形叠加（已有`vertical_section` 输出可直接用） |

---

## 8. 建议推进顺序

```
阶段 0（打通断点）──┬─→ 阶段 1（约束补全）─→ 阶段 5 exp04 消融
                    │
                    └─→ 阶段 2（滚动闭环）─→ 阶段 3（任务到达）─→ 阶段 5 exp02/exp03
                                                      │
                              阶段 4（对照方法）──────┴─→ 阶段 5 exp05 对比
                                                      └─→ 阶段 6 可视化
```

**关键路径**：阶段 0 → 阶段 2 → 阶段 3 → exp02/exp03。
这条链上任何一环没通，论文的"动态滚动"实验就出不来。

**可并行**：阶段 4（baselines）不依赖阶段 1/2，随时可做；阶段 6 的收敛曲线可在阶段 0 完成后立即产出。

---

## 9. 风险与提示

1. **`repair_rules` 是质量瓶颈**。三个小文件看着不起眼，但它们决定连续个体如何稳定映射到可行离散解（§5(4)）。建议每个规则配单测，尤其是 `overloaded`（N>M）与 `balanced`（N=M）两种模式的差异。
2. **三维可达性工作量不确定**。§4.3 列了"三维地理环境约束"，但 `vertical_section.py` 明确只做代价近似。若完全实现三维可飞性搜索会显著增加计算量，建议先做**离地余量 + 禁飞区掩膜**的轻量版，把完整航迹搜索留给第 4 章。
3. **O(n²) 代价矩阵**是实验规模的天花板（见 §6 规模提醒）。
4. **`exp01_smoke` 的 `n_uavs: 32` 是推算值**（8 巢 × 容量 4），若机队规模不同需同步修改。
