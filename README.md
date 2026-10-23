# ExperimentForGraduation — 多无人机智能调度中的任务分配与航迹规划方法研究

硕士学位论文代码库。**第 3 章（动态滚动的多机巢多无人机协同任务分配）为主线**，
方法口径以 `task_allocation/Chapter3_README.md` 为权威；第 4 章航迹规划见 `path_planning/`。

## 论文章节

| 章 | 内容 | 代码 / 文档 |
|---|---|---|
| 第 1 章 | 绪论 | `paper/manuscript1/chapters/` |
| 第 2 章 | 相关理论与技术 | 同上 |
| 第 3 章 | 动态滚动的多机巢多无人机协同任务分配方法（主线） | `task_allocation/`（权威文本 `Chapter3_README.md`） |
| 第 4 章 | 顾及风险时空异质性的航迹规划方法 | `path_planning/` |
| 第 5 章 | 智能调度系统实现与应用验证 | `paper/manuscript1/` |
| 第 6 章 | 总结与展望 | 同上 |

## 仓库结构

```
task_allocation/                  第 3 章：任务分配方法与实验
  Chapter3_README.md              方法文本（权威口径：公式与固定表述以此为准）
  methods/                        方法实现
    selection/                    第一阶段：动态任务选择（priority / capability /
                                  marginal_gain / feasibility / pipeline）
    muas/                         第二阶段：选择性多机巢 MUAS（problem / config /
                                  fitness_weights / coverage_gate / constraints /
                                  cost / representation / solvers）
    rolling/                      滚动闭环（horizon / event_trigger）
    utils/                        三维代价与 DEM 工具
  baselines/                      外部基线（占位）
  common/                         共享类型与场景装配
  data/                           机巢 CSV、DEM 地形
  experiments/                    exp01_smoke … exp06_joint_regime
  tests/                          方法单测（253 例）
path_planning/                    第 4 章：航迹规划
paper/manuscript1/                LaTeX 论文（chapters / figures / tables / bib）
UAV协同任务分配方法-进展汇报/      导师进展汇报 PPT（方法专版 12 页）
  slides/                         页面源（.slide）
  STORY.md                        叙事与口径自查（页面大纲 / 公式锚点 / 禁则）
tests/                            全局单测（52 例）
main.py                           项目占位入口
```

## 第 3 章方法要点（与代码对应）

- **两阶段分层 + 滚动闭环**：动态任务选择（`methods/selection/`）→ 选择性多机巢
  MUAS 精优化（`methods/muas/`）→ 执行反馈滚动（`methods/rolling/`）。
- **候选 / 选择规模解耦**（论文 3.3 修订）：候选池 `n_cand = ⌈α·K⌉` 该富（能力
  无关，仅受 `max_candidate_pool` 计算上限）；选择数 `n_sel = min{⌈max_ratio·K⌉, cap(t)}`
  该紧（cap(t) 是唯一能力卡口）；`cap(t) = max(min_cap, ⌊B(t)/c_med⌋)`，B(t) 只计
  **可起飞子集**剩余航程（充电中的机是幻影容量）——`selection/capability.py`。
- **覆盖门禁分层评价**（主口径，`muas/problem.py::scalar_fitness`）：

  ```
  f = W_hard·n_hard + w_exc·excess_m + W_skip·n_skip + w_late·L_tot + λ·path + w_time·F3
  ```

  四层准字典序：T0 物理硬约束 / T0.5 覆盖门禁 / T2 迟到软计价 / T3 航程与时间；
  **固定表述：搜索用 f 不含 −m·F1，F1 只作事后指标**（「能装必交付」由门禁承担，
  不靠目标层加权近似）。big-M 旧式（−m·F1_eff + 迟到折减）保留为兼容口径。
- **任务选择**：五因素 min-max 归一化优先级 P_i + 边际收益增量选择
  `Δ_i = ΔB_i/(ΔC_i+ε)`（锚点对插入、统一 DEM 三维航程）+ 两道物理闸门
  （prefilter / postcheck；拒绝理由只有物理原因，迟到不是参选资格）。
- **改进 DMDE**：统一编码（起始基因 + 巡游基因，基因总数恒 N；**方案 A 终点机巢
  不进基因**）→ 温度自适应反映射（首任务 softmax + 巡游边「迭代行替换」）→
  swap/insert/reverse 扰动 → 段结构规范化（SRP 不变式）；Lamarckian 回写只写
  起始基因（`muas/representation/`、`muas/solvers/`）。
- **覆盖优先两段式解码**：覆盖优先装载（EDD，修到不动点）→ 单调成本 polish
  （四道门：航程 / 自身时间窗 / 迟到数不增 / 成本严格降）；修复优先、罚则兜底。
- **终点机巢**：给定路线后内层最小费用流精确求容量可行最优，返航代价进入完整
  评价（论文 §5(2)）。

## 快速开始

```bash
uv sync                                                   # 依赖：pyyaml / numpy / pillow / matplotlib
python3 -m pytest task_allocation/tests tests/ -q          # 305 例
python3 task_allocation/experiments/exp01_smoke/run.py     # 端到端冒烟（多 seed：--seeds 42 43 44）
```

实验入口与口径说明见各 `task_allocation/experiments/expXX_*/notes.md`；
进展汇报的叙事边界与公式锚点见 `UAV协同任务分配方法-进展汇报/STORY.md`。
