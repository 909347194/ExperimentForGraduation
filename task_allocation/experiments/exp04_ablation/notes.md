# exp04_ablation — 单周期机制消融台（B1 构造式解码 / B2 三层选机 / B3 枚举参照）

**status:** 2026-11-19 从零搭建并跑通（A0/B1/B2/B3 × 3 seed 正式档，EXIT=0；9 求解格 + 3 枚举格）
**论文锚点:** §3.1 / §4.5（第二阶段解码与参与机选择的机制消融）
**不负责:** 方法排名、完成约束验收（exp01）、选择层规模（exp02）、事件响应（exp03）、外部基线竞赛（exp05）、显著性检验

---

## 1. 为什么从零搭建

README §6 原把本目录标为 `planned（仅 notes）`，但实际连 `notes.md` 都不存在——全仓与 git 历史（浅克隆 4 提交）均确认 `exp04_ablation` **从未落地**。方法层却早已为它备好完整资产：

| 消融臂 | 方法层实现 | 契约测试 |
|---|---|---|
| B1 构造式解码 | `stage_config.decode_mode="constructive"` + `decode_constructive.py` | `test_constructive_decode.py`（12 项全过） |
| B2 三层选机 | `POLICY_DEMAND_MARGINAL` + `_select_by_lexicographic_marginal` | 同上 |
| B3 近精确枚举 | `enumerate_reference_participating` | 同上 |

搭建时发现一处接口债务：`muas_stage.py` 门面未导出 `POLICY_DEMAND_MARGINAL`（e59c21c「清理死导入」遗留），导致契约测试收集失败。本次补两行 re-export 修复（零逻辑改动）。

---

## 2. 与其它实验的边界

| 实验 | 关系 |
|---|---|
| exp01_smoke | 提供问题/求解口径与滚动闭环；本实验**继承其 problem/solver 口径**，但只做单周期快照，不检验完成约束 |
| exp02_selection_scale | 同为 guangzhou 单周期快照；本实验的 A0 = exp02 同口径二阶段解，**不碰选择层** |
| exp03_event_rolling | 变的是环境事件；本实验固定环境，**变的是求解器内部机制** |
| exp05_baselines | 外部算法对照；本实验只比较本文方法的内部开关 |
| ab_* 机制诊断 | 那四个目录做优化工作归属/顺序边际/巡游回写诊断；本实验聚焦**解码与参与机选择**两个机制维度 |

---

## 3. 设计骨架

### 3.1 口径（四臂逐位相同，唯一自变量 = 机制开关）

- 场景：`guangzhou`（3 机巢 / DEM + 建筑垂直包络）
- 机队：6 架同构，10 km @ 15 m/s；问题口径 lambda_cost=1e-1、覆盖门禁开
- 任务：单周期 20 个（B3 例外，见 3.3）；求解预算 pop20 × gen40
- 多种子：42 / 43 / 44（描述性稳健性，**不做显著性检验**）

### 3.2 四臂

| 臂 | 自变量 | 语义 |
|---|---|---|
| **A0_baseline** | 无（生产口径） | `repair_chain` + `demand`，等价于 exp02 二阶段解 |
| **B1_constructive_decode** | `stage.decode_mode=constructive` | 只换解码方式：贪心构造式（内联可行 + 全机队救援网） |
| **B2_three_layer_select** | `stage.participating_policy=demand_marginal` | 只换参与机选择：设施选址预选 + 字典序边际精修 |
| **B3_enum_reference** | 小 N + 固定贪心解码枚举 | 量化启发式选机离同架次预算最优子集的 gap |

### 3.3 B3 为什么用小 N=8

`enumerate_reference_participating` 有组合爆炸防护（`max_tasks=10`）。B3 的语义本就是「小 N 近精确参照」，故该臂单独覆盖 `tasks.n_tasks=8`（其余臂 20）。**B3 与 A0/B1/B2 任务数不同，不可横向比绝对值**；B3 只回答「启发式选机是否已达同预算最优」这一独立问题。

### 3.4 指标与红线

- 落盘：逐臂逐 seed 的 `n_executed / f1_reward / f2_cost / final_fitness / n_evals / polish_* / 分段墙钟`，跨 seed 均值±标准差。
- **F1 只作描述性**——门禁口径下 F1 不进搜索标量 f（`scalar_fitness`），不得作优劣判据。
- **不宣称完成约束**：单周期快照，未执行任务记 deferred；完成约束由 exp01 滚动闭环检验。
- **不作机制归因**：墙钟受跨臂缓存先后偏倚影响，只作量级观测。
- B3 参照固定贪心解码器，度量**选机质量**而非全流程质量；gap≥0（适应度惩罚型，小者优）。

---

## 4. 复现

```bash
cd /workspace/ExperimentForGraduation
# 先验键
.venv/bin/python task_allocation/experiments/exp04_ablation/run.py --dry-run
# 正式档（4 臂 × 3 seed）
.venv/bin/python task_allocation/experiments/exp04_ablation/run.py
# 只跑某臂 / 某 seed
.venv/bin/python task_allocation/experiments/exp04_ablation/run.py --arms B1_constructive_decode --seeds 42
```

产物：`results/{metrics,run_meta,effective_config}.json`（gitignore）。
结果分析见 `results/RESULTS_ANALYSIS.md`。
