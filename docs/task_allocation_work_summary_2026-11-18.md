# task_allocation 工作总结（2026-11-18）

> 本文件是 `docs/` 的**唯一留存文档**：本次整理规整的总结 + `task_allocation/` 现行结构导航。
> 单个实验的口径、结果、标定史在各自 `notes.md`；实验层通用约定在
> `task_allocation/experiments/README.md`；方法章节说明在 `task_allocation/Chapter3_README.md`。

---

## 1. 本次整理（2026-11-18）

**口径**：仓库不留一次性脚本与过程性文稿；实验以「可复现脚手架 + notes 记录」形态归档；
一切被删内容走 git 历史回溯。

### 1.1 `task_allocation/docs/` 收敛为编号实验文档

- **留（4 份）**：`exp01_current_code_reconcile`、`exp05_e1_design`、
  `exp05_redesign`、`exp05_v3_formal_results`
- **删（21 份）**：评审 / 审计 / 设计 / 阶段方案类过程文稿，含
  `code_organization_review`、`experiments_full_audit`、`exp_design_cleanliness_review`、
  `stage2_*`（4 份）、`methods_*_review`（2 份）、`design_fleet_spec`、`design_nest_state`、
  `operator_contracts`、`objective_caliber_*`、`research_positioning`、
  `reading_lu2026_*`、`p0_criteria_drift_baseline`、`m2_gene_writeback_design`、
  `fitness_path_optimization_diagnosis`、`modification_plan_20261104_hetero_claims`、
  `remaining_work_review`、`rolling_review`

### 1.2 一次性脚本与临时稿清理（8 件）

| 删除 | 性质 | 留存产物 |
|---|---|---|
| `exp11_multi_depot/analyze_makespan.py` | 完工时间档一次性汇总 | §5.9 表格 |
| `exp11_multi_depot/audit_correctness.py` | 独立一次性核对（非 run.py 门禁） | notes 结论 |
| `exp11_multi_depot/build_probe_tables.py` | 一次性探针表 | `tables_baseline.*`（results/） |
| `exp11_multi_depot/probe_greedy_baseline.py` | 一次性贪心对照探针 | §5.14 结论 |
| `exp11_multi_depot/rebuild_summary.py` | 一次性汇总重建 | notes 结论 |
| `ab_mechanism_3steps/probe_selection_gap.py` | 搜索—交付口径一次性探针 | `selection_gap_report.md`、`selection_gap.png` |
| `ab_mechanism_3steps/verify_pop_sensitivity.py` | 种群敏感性一次性交叉核查 | `SYNTHESIS.md` 叙述与数值 |
| `exp03_event_rolling/task_allocation_analysis.md` | results/ 缺失期的临时分析稿 | — |

**注**：`exp01_smoke/audit_correctness.py`、`exp03_event_rolling/audit_events.py` 是
`run.py` 内建审计门禁，`summarize_ab.py` / `aggregate.py` / `make_paper_figure.py` /
`build_tables.py` / `build_reference_table.py` 是报告与论文图表生成器——均属脚手架，保留。

### 1.3 引用同步

- `exp11_multi_depot/notes.md` §5.9 / §5.14 / §7 复现清单、`Chapter3_README.md` §4.10
  已标注「一次性脚本已清理」，不再指向不存在的可执行文件。

### 1.4 回溯方法

```bash
git log --all --oneline -- task_allocation/docs/          # 被删设计/评审文档
git log --all --oneline -- task_allocation/experiments/   # 被删一次性脚本
git show <commit>:<path>                                  # 取回任意版本
```

---

## 2. 结构总览

```
task_allocation/
├── methods/            # 方法实现（唯一被要求复用的层）
│   ├── muas/           #   选择性多机巢 MUAS 求解器
│   │   ├── constraints/ cost/ operators/ representation/ solvers/
│   │   └── compare_spec / coverage_gate / feasibility / independent_audit ...
│   ├── rolling/        #   滚动时域与事件触发（horizon / event_trigger / sequence_group）
│   ├── selection/      #   第一阶段任务选择（priority → feasibility → marginal_gain）
│   ├── utils/          #   DEM / 坐标 / 建筑层
│   └── task_pool.py / uav_state.py
├── baselines/          # 基线：greedy / construct / random / vrptw（registry 注册）
├── common/             # 场景与机队规格（scenario、fleet_spec）
├── data/               # 广州 / 拉萨城关场景数据
├── experiments/        # 实验层（通用约定见 experiments/README.md）
│   ├── exp01_smoke … exp11_multi_depot   # 编号实验
│   └── ab_*            # 消融 / 机制实验
├── tests/              # 回归测试
└── docs/               # 编号实验文档（exp01 / exp05）
```

**实验脚手架同构**：`config.yaml`（自变量唯一入口，`--set` 覆盖）→ `build.py`（装配，
不发明数值）→ `run.py`（编排）→ `metrics.py` + `plot/`（一图一脚本，registry 调度）→
`notes.md`（口径 / 结果 / 标定史）。产物：`metrics.json`（数值）、`solution.json`（结构）、
`run_meta.json` + `effective_config.json`（复现核对）；`results*/` 不入库，可重跑再生成。

## 3. 实验导航

| 目录 | 主题 |
|---|---|
| `exp01_smoke` | 全链路冒烟 + 5×5 km 研究区异构机队正式档 |
| `exp02_selection_scale` | 单周期任务规模可扩展性台 |
| `exp03_event_rolling` | 事件滚动：事件响应 / 重规划时间线（论文核心图 R00） |
| `exp05_baselines` | 基线对照：E1 参考表 + v3 主张检验 |
| `exp06_joint_regime` | 动态滚动 × 选择层标定的联合域验证 |
| `exp07_polish_accept` | 交付后成本 polish 采纳判据配对对照 |
| `exp08_gain_decomposition` | 收益拆分 2×2 |
| `exp09_mcf_layers` | 终点机巢分层（MCF 分配，两层四档） |
| `exp10_capacity_pressure` | 容量压力场景 |
| `exp11_multi_depot` | 多机巢三臂对照（home / nearest / mcf） |
| `ab_m21_patrol_writeback` | M2-1 巡航回写消融 |
| `ab_mechanism_3steps` | 优化器机制三步诊断 |
| `ab_optimization_attribution` | 优化收益归属（进化 / 精修占比） |
| `ab_order_marginal` | 基因顺序边际贡献 |

细节以各目录 `notes.md` 为准。

## 4. 归档说明

- 设计 / 评审文档曾被代码 docstring 引用（`design_nest_state.md`、`m2_gene_writeback_design.md`、
  `stage2_final_scheme.md` 等）。文档已移出仓库，**代码中的引用语义不变**（记录设计裁决的
  出处），原文按 §1.4 的命令回溯。
- 实验结果产物（`results*/`、`figs/`、`smoke/`）自 2026-10-26 起不入库（`.gitignore`），
  可由代码重跑再生成。
