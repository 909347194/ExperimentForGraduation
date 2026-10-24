# 实验口径审查报告（exp01–exp06，对照《近期思路固定稿》2026-10-23）

- **审查时间**：2026-10-24
- **代码基线**：`origin/master aa88d5a`（工作区干净，277 个单测全绿，本次实测复核：`python3 -m pytest task_allocation/tests` = 277 passed；连 `tests/` 共 329 passed）
- **审查性质**：只读审查。未改任何代码/数据，未重跑完整实验；数据核对用只读脚本解析已落盘 JSON/CSV。
- **口径基准**：2026-10-23《近期思路固定稿》——
  1. 指标三层：**主表 = 本周期覆盖/吞吐、单位完成航程、等待时间、耗时**；过程层、方法诊断层另置；**淘汰率不当主失败指标（问题语义无淘汰）**；
  2. 单周期目标先覆盖后成本；shortfall 解释基准：门禁开 + 无 F1 时 shortfall↑ 属「尽量多做」压力弱化 + 有限窗口残留，**不是永久丢失**；
  3. 主表口径需与「**无超期淘汰、允许跨周期推迟**」的问题语义自洽（分母对齐、无口径混用）。

**总评**：aa88d5a 对 6f80996 提出的 4 个口径问题（A1/A2/A3/B1）在**代码与测试层**已落实，分母对齐、废飞航程/候选命中率补齐、峰值聚合均属实。但存在 **3 项 P0**：① 机器可读的「论文主表列序」与固定稿主表构成直接冲突（含淘汰率、缺等待/耗时）；② 问题语义「无超期淘汰、允许跨周期推迟」与 `TaskPool.expire` 超期出池（=永久丢失）机制不自洽，且真实数据中已发生；③「论文主表数据源」exp05 `summary.csv` 为旧口径单 seed 产物（旧键、无 ±std），照现状出表即口径错误。另有 P1 若干（文档/配置/代码三方口径描述漂移、结果产物缺口径自描述、门禁开关语义失真等）。

---

## 1. 三层指标体系：notes.md 定义 vs metrics.py 实现 vs 结果 JSON/表

### 结论

- **实现层（metrics.py）内部自洽**，`summary` 字典按「论文主表 v2 / 过程指标 v2 / 方法诊断 v2」分块注释组织，恒等式、单位、聚合语义均有测试锁定（277 passed）。
- **但三层的“主表”构成与固定稿不一致（P0）**：`metrics.json` 自描述字段 `problem.paper_main_table` 仍按 2026-10-22 v2 口径登记 6 列（含 `elimination_rate`），**不含固定稿主表要求的等待时间与耗时**；`report.tex` 的三层定义同样把「平均等待、求解耗时」放在过程层、把「淘汰数/率」放主表。
- **淘汰率不当主失败指标**在 exp01 层面基本落实（审计 A2 把 elim>0 定为硬 FAIL；outcome_summary 标注「机制缺陷信号，须为 0」），**但有两处残留（P1）**：exp06 自动生成报告把「淘汰 ≤ elim_max」写成**主判据**；exp05「论文主表数据源」CSV 把 `elimination_rate`/`n_eliminated` 作为主表列。
- **notes.md 与 config.yaml/代码三方矛盾（P1）**：notes.md 仍称「四层准字典序（含 −m·F1）、覆盖门禁双开关当前值均为 false」，而 config.yaml 双开关为 true、代码 `gate_mode()` 恒返回 True 且 `scalar_fitness` 已不含 −m·F1。
- **结果 JSON（现存仅 gateoff/gateon 两份）为旧 schema**，与 notes/metrics.py 现行定义不一致（见 §5）。

### 证据

1. **paper_main_table 列序（P0）**
   - `task_allocation/experiments/exp01_smoke/metrics.py:456-465`：
     ```
     # 论文主表必报列序（2026-10-22 指标体系 v2）：随数据自描述，
     # 表格生成按此序取数，不靠文档记忆
     "paper_main_table": [
         "completion_rate_final", "on_time_rate_final",
         "path_per_completion_m", "total_path_m",
         "elimination_rate", "unreachable_rate",
     ],
     ```
     → 含 `elimination_rate`（固定稿：淘汰率不当主失败指标）；缺 `avg_wait`（等待时间）、`solve_time_ms`/`wall_seconds`（耗时）、`throughput_per_cycle`/`s_coverage_rate`（本周期覆盖/吞吐）。该字段自称「表格生成按此序取数」，照此出表即违反固定稿主表口径。
   - `exp01_smoke/report.tex:87-105`（三层定义）：主表 = η_final、按时率、单位完成航程、**淘汰数/淘汰率**、不可达率；**「平均等待、求解耗时」列在过程指标**（report.tex:102）。与固定稿主表构成相反。
   - 固定稿主表要求的四项在 summary 中其实**都有实现**：`throughput_per_cycle`（metrics.py:568）、`path_per_completion_m`（:553）、`avg_wait`（:588，秒，实测复算一致）、`solve_time_ms`/`wall_seconds`（:586/:622）——问题只在“主表列序登记与图/表分层”上。
2. **淘汰率残留（P1）**
   - `exp06_joint_regime/run.py:208`：`"主判据：淘汰 ≤ {elim_max}（排队中超期=可完成而未完成…）"`，并以 `ok = n_eliminated <= elim_max` 判「成立/被证伪」（run.py:213）——把淘汰率当假设 1 的主失败判据，与固定稿直接冲突。
   - `exp05_baselines/metrics.py:27-73`（CSV_COLUMNS = 「论文主表数据源」）含 `n_eliminated`、`elimination_rate` 列（无等待时间列，见 §2）。
   - `exp01_smoke/report.tex:162`（tab:seeds 主表行「废飞 / 淘汰 / 不可达」）。
   - 正面证据：`audit_correctness.py:204-207`「A2 淘汰须为零（淘汰>0 = 可完成任务机制性未完成 = 缺陷）硬 FAIL」；`plot/report/outcome_summary.py:215`「淘汰（机制缺陷信号，须为 0）」——这两处与「问题语义无淘汰」自洽。
3. **notes.md vs config.yaml vs 代码（P1）**
   - `exp01_smoke/notes.md:76`「### 适应度（四层准字典序，生产口径）」，:80 给出 `f = … − m·F1_eff + …`；:88-89「覆盖门禁双开关……**当前值均为 `false`**（= 上述四层口径）。门禁开启属**待修项**」。
   - `exp01_smoke/config.yaml:309-311`：`coverage_gate: true`、`lexicographic_coverage: true`；config 注释（:302-308）自述「2026-10-24 门禁口径适应度……唯一主口径，f 不含 −m·F1」。
   - 代码：`task_allocation/methods/muas/fitness_weights.py:26-39` `gate_mode()`「2026-10-24 起**覆盖门禁为唯一主口径**，故本方法恒返回 `True`——代码中不再存在非门禁（big-M 四层 −m·F1）评价路径」；`problem.py:402-450` `scalar_fitness` 固定表述「搜索用 f 不含 −m·F1；F1 只作事后指标」。
   - → notes.md 的「生产口径」描述（四层含 F1、门禁关）**已与当前代码/配置完全相反**；`report.tex:124`「口径标定台：四层准字典序适应度……」同病。
4. **图脚注口径常数过期（P1，「图会说谎」类）**
   - `plot/report/outcome_summary.py:255`：脚注写「有效奖励按迟到线性折减（**W=600s**）」，而生产口径 `muas.late_reward_decay_seconds: 1800.0`（config.yaml:224 及 exp05 config.yaml:224 同值）。
5. **「废飞航程」「候选命中率」的层级混用（P2）**
   - 定义均清晰：`infeasible_wasted_path_m`（metrics.py:156-176，`est_cost × n_bad/n_bound` 归属约定；notes.md:122-124）；`cand_hit_rate = Σselection.n_selected / Σcapability_n_cand`（metrics.py:328-345；notes.md:125-126）。
   - 但层级不严格：废飞航程既在 report.tex 主表行（:162）又在方法诊断清单（:104）；`cand_hit_rate` 在 metrics.py 归过程指标块（summary 键序「── 过程指标 v2 ──」区），却同时出现在 outcome_summary「(d) 关键指标」面板（outcome_summary.py:225）。二者**均不在** `paper_main_table`（✓），建议在 notes/图注中显式标注所属层。

---

## 2. 分母 / 口径对齐（含 aa88d5a 落实核查）

### 结论

- **「删冗余 / 补废飞航程与候选命中率 / 对齐分母」在 exp01 代码层全部属实**，且有专项测试（`test_metrics_aggregation.py:317-403`：A3/B1/B2/D 五项；全量 277 passed）。
- **分母口径现状**（现状良好，各有名字、可复算）：
  | 指标 | 分子 | 分母 | 备注 |
  |---|---|---|---|
  | `completion_rate_final` | 最终交付（含迟到） | `n_arrived − n_unreachable`（`n_final_denom`） | metrics.py:262-272，唯一完成率键（A1 删 naive） |
  | `effective_completion_rate` | 交付 − 零奖励交付 | **同 `n_final_denom`**（A3 对齐） | metrics.py:573-580 |
  | `on_time_rate_final` | 按时交付 | 全部交付（交付集条件率） | metrics.py:273-276，与 exp05/exp06 同键对账 |
  | `correct_exec_rate` | 逐周期按时出动 | 逐周期出动，**空转周期不入均值** | metrics.py:300-320 |
  | `throughput_per_cycle` / `per_active_cycle` | 累计完成 | 实际周期数 / 活跃周期数 | 两个分母各自命名（I6 注释），不混用 |
  | `path_per_completion_m` | `total_cost`（直读 `final_path_m`） | 最终完成数 | metrics.py:134-152 直读 + 反解交叉校验 `path_split_residual_m` |
  | `avg_wait` | Σ(completed_at − arrival_time) | 完成任务数 | 秒；JSON 抽查复算逐位一致（§5） |
- **跨 seed 聚合**：`build_seed_summary` mean±std（ddof=0）；`max_backlog` 峰值语义（`_SEED_PEAK_KEYS`，mean 位=max、std=0，metrics.py:657-661）已落实；多 seed 报表分母取 `seed_summary["n_arrived"]["mean"]`（run.py:414-420，修复 P1-2）。
- **遗留不齐（P1）**：**等待时间没有进入 exp05「论文主表数据源」**——`exp05_baselines/metrics.py:277` 计算了 `avg_wait_s`，但 `CSV_COLUMNS`（:36-73）无此列；固定稿主表四项里「等待时间」在主表数据源缺失。
- 层级混用（废飞/候选命中率）见 §1-5（P2）。

### 证据（抽查复算，results_gateoff_s42 / results_gateon_s42）

```
path_per_completion_m × n_completed = 1611320.7 vs total_path_m 1611320.679   ✓
throughput_per_cycle × n_cycles     = 60.0     vs n_completed 60             ✓
avg_wait（solution.json 复算）        = 730.0    vs summary.avg_wait 730.0    ✓
completion_rate_final                = 60 / (60 − 0) = 1.0                    ✓
```

### 严重度

- P0：exp05 CSV 缺等待时间列 + 现存 CSV 为旧键单 seed 产物（合并入 §4/§5 及清单）。
- P2：废飞航程/候选命中率未显式标注所属层。

---

## 3. 6f80996 的「4 个口径问题」与 aa88d5a 的闭环情况

### 4 个问题原文（git show 6f80996，指标体系三层符合性审查第 13 条）

| # | 问题 | 内容 |
|---|---|---|
| A1 | 双口径冗余 | `completion_rate`（分母含物理不可达）与 `completion_rate_final` 双落盘，图/表直读旧键即漂移 |
| A2 | 同名不同义 | `on_time_rate`（出动池化）与 `on_time_rate_final`（交付集条件率）、`correct_exec_rate`（逐周期均值）三者混名 |
| A3 | 分母不一致 | `effective_completion_rate` 分母用 `n_arrived`，与 `completion_rate_final` 不可直接比较 |
| B1 | 自述原则缺指标 | 只落 `n_infeasible_delivery` 计数，缺 `infeasible_wasted_path_m`（废飞已飞航程） |

（另有 B2 建议补 `cand_hit_rate`、D 聚合注意项。）

### aa88d5a 落实核查：**代码/测试层全部落实 ✓**

| 项 | 落点 | 状态 |
|---|---|---|
| A1 | metrics.py:513-525 注释「删除 naive 完成率键」；`_SEED_AGG_KEYS` 删除 `completion_rate`（:668-675） | ✓ 旧键不再产生 |
| A2 | metrics.py:526-537「删除池化 on_time_rate 键」；保留 `correct_exec_rate` + `on_time_rate_final`；审计新增 A2 淘汰须为零（audit_correctness.py:204-207） | ✓ |
| A3 | metrics.py:573-580 `effective_completion_rate` 分母改 `n_final_denom`；测试 `test_effective_completion_rate_denominator_excludes_unreachable` | ✓ |
| B1 | metrics.py:156-176 归属实现 + summary 键 `infeasible_wasted_path_m`（:528-529）+ 逐周期 `infeasible_path_m` 落 cycles 行（:353-356） | ✓ |
| B2/D | `cand_hit_rate`（:328-345）、`_SEED_PEAK_KEYS` 峰值聚合（:657-661） | ✓ |
| 消费者 | exp05/metrics.py CSV_COLUMNS、exp06/run.py:156-157 改 final 键、4 张 exp01 绘图、theme 字体 | ✓（grep 确认无残留旧键引用） |
| 测试 | `test_metrics_aggregation.py:317-403` 新增 5 项；`python3 -m pytest task_allocation/tests` 277 passed（本次实测） | ✓ |

### 未闭环项

1. **重跑验证产物不在库/工作区（P1）**：aa88d5a 声称「重跑 3 seed 验证：审计 25/28 PASS、η_final=1.0、on_time_rate_final=0.867、cand_hit_rate=0.924」，但 `exp01_smoke/results/` 现为空目录（仅空 `figures/`），`git ls-files` 亦无任何 results 文件；现存数据只有未跟踪的 `results_gateoff_s42/`、`results_gateon_s42/`（2026-10-22 旧 schema）。**数据层无法对账本次口径清理**，只能采信审查文档记录。
2. **文档/配置/图的口径描述残留（P1，见 §1-3/1-4）**：notes.md 门禁+四层描述、report.tex v2 三层与「四层准字典序」、outcome_summary 脚注 W=600s。
3. **paper_main_table 列序未按固定稿重登记（P0，见 §1-1）**。
4. **件数口径漂移（P2）**：run.py:453 / report.tex:122,132,817 称「23 项不变量」，实际审计 25~28 项随数据形态浮动（aa88d5a 记录 25/28）；report.tex:122「FAIL 即阻断」与 run.py:453「FAIL 只报警不阻断」互相矛盾。
5. 代码审查（exp01_code_audit_2026-10-24.md）的 P0-1/P0-2/P0-3/P1-1/P1-2/P1-3 已在代码修复（audit_correctness.py:54-58 逐 seed 审计、:104-120 effective_config；run.py:303 `--group` choices、:399 落盘 effective_config.json、:406-411 逐 seed 落盘、metrics.py:253-258 推迟率分子限定 completed、:134-152 路径直读+残差校验、metrics.py:641-652 指纹剔 seed）——**但因 1 所述产物缺失，P0-1「3 seed 逐 seed 审计」无数据可证其运行过**。

### 严重度

- P1：重跑产物缺失（证据缺口）；文档/图口径残留（P1-4 类）。
- P2：审计项数与 FAIL 语义的文档矛盾。
- （A1/A2/A3/B1 本身：已闭环，无遗留代码问题。）

---

## 4. 实验设计口径（seeds / 规模档 / 门禁 / 基线公平 / 统计报告）

### 4.1 seeds 与统计协议

| 实验 | seeds | 报告方式 | 评价 |
|---|---|---|---|
| exp01 | 42/43/44（config.yaml:20-23，「正式口径 = 3 seed」） | mean±std（ddof=0），`seed_summary` | ✓ 但 n=3 偏少，审查文档已要求论文注明 ddof=0/n=3 |
| exp02 | **单 seed 42**（config.yaml `experiment.seed`，无 seeds 列表；run.py:227,304） | 单值，无 ±std | **P1**：规模压缩主结论（10/50/100 三档 × 6 变体）无重复实验，「候选截断/耗时与 N 解耦」类结论缺稳健性证据 |
| exp05 | 协议 5 seed（config.yaml:22 `seeds: [42,43,44,45,46]`） | summary.json 有 mean±std；**summary.csv 无 std 列** | **P1**：① CSV（自称论文主表数据源）25 列无 ±σ 列，出不了「均值±标准差」；② 现存 CSV/summary.json 只含 seed 45（`M0/summary.json: seeds=[45]`），均值行实为单 seed 值——盘上明明有 5 个 seed 的 per-seed 结果（B1/B2/B2e/D0/M0 各 seed_42…seed_46）。run.py:464-477 每次只聚合**本次调用**的 seeds 并整表重写 summary.csv，分批跑即互相覆盖（results_full_run.log 末尾「全部 1 臂 × 1 seed 运行完成」可证） |
| exp03/exp04 | planned，notes 建议「正式结果 ≥5」 | — | 设计口径 OK，未实施 |
| exp06 | 计划 42/43/44，首轮单 seed（notes:54） | 逐格 + 配对规则 | 首轮单 seed 明确标注，可接受 |

### 4.2 规模档

- exp02 `scales: [10, 50, 100]`（config.yaml:115-118）与任务设定「10/50/100」**一致** ✓；`full_muas_baseline_scales: [10,50,100]` 全档对照 ✓。
- 文档漂移（P2）：exp06/notes.md:22 称 exp02 是「N=50~200 单周期」，与 exp02 实际 10/50/100 不符；exp02/notes.md:10 称「exp01 跑在 10 任务 / 24 机，⌈α·K⌉=60」，与 exp01 实际（10 机、60 任务、⌈α·K⌉=25，见 results_gateon_s42 `selection.capability_n_cand=25`）不符；exp06/notes.md:21 称 exp01 为「Poisson λ=5 滚动」，exp01 实为 fixed 到达（30+10+20，notes.md「到达口径（fixed-60 冒烟）」）。

### 4.3 门禁 on/off

- exp01 生产：`coverage_gate: true` + `lexicographic_coverage: true`（config.yaml:309-311）＝完整门禁口径，与固定稿「门禁开 + 无 F1」一致 ✓（`scalar_fitness` 亦恒不含 F1）。
- **但开关语义已失真（P1）**：`gate_mode()` 恒 True、非门禁 big-M 路径已删（fitness_weights.py:26-39），`coverage_gate`/`lexicographic_coverage` 只剩「构造修复 / W_skip 分账」两层含义；而 exp02（run.py:253-254,288-290 默认 False）、exp05（config.yaml 无这两键，config.py:182,202-203 默认 False）仍按「默认关闭 = 旧口径逐位一致」设计与描述（exp02/notes.md:47-48）——**该描述在当前代码下不可能成立**，「关」也跑的是 f 不含 F1 的门禁适应度。
- gateoff/gateon 对照语义陷阱：现存 `results_gateoff_s42` 是 2026-10-22 非门禁 big-M 口径产物（含 −m·F1 的旧路径），**不等于**今天代码里的「coverage_gate=false」，跨语义对比会误导。

### 4.4 基线对照公平性（exp05）

- 公平协议四条（notes.md「公平性协议」）：同一滚动外壳 / 同一任务到达序列（同 seed 深拷贝）/ 同一硬约束与评价（`check_feasible`/`problem.evaluate`）/ 同一 seed 协议——**设计层成立** ✓；各臂差异仅「搜索引擎 or 代价模型」且逐条声明（含 B3 硬时间窗「故意不同」、欧氏臂 `distance_definition` 落 run_meta 不混加）✓。候选集共享由共享第一阶段选择保证（「给定同一候选集」）✓。
- **「同一预算」不成立但属声明过的本体差异**（P2 备忘）：M0/D0 走 DMDE pop=50×100 + polish，B1/B2/B3 为构造式一次成型——对照回答的是「搜索/建模差异」，notes 已声明；写作时勿称「同预算竞赛」。
- **「与 exp01 生产口径逐项相同、M0@seed42 可与 exp01 对账」不成立（P1）**：exp05 `lambda_cost: 1.0e-2`（config.yaml:195）vs exp01 `lambda_cost: 1.0e-1`（exp01 config.yaml「2026-10-24 用户裁定：1e-2 → 1e-1」，且明示「跨档对比须分列」）；门禁双开关 exp05 默认关 vs exp01 双开。λ 档与门禁档都不同 ⇒ M0 与 exp01 之间不存在逐字段对账基础。
- 统计报告：`solve_time_ms`「仅 MUAS 口径」已在图面板标注（outcome_summary.py:236「求解耗时（MUAS，不含选择阶段）」）✓，与 D 类审查要求一致。

### 严重度

- P0：exp05 summary.csv 作为主表数据源的统计口径错误（单 seed 当均值、无 ±std、旧键）——见 §5 与清单。
- P1：exp02 单 seed；exp05 与 exp01 的 λ/门禁档不一致破坏「逐项相同」声明；门禁开关语义失真。
- P2：规模档/工况描述的文档漂移；「同预算」表述注意。

---

## 5. 结果数据抽查（exp01 results*、exp05 results/）

### 5.1 exp01：`results_gateoff_s42` / `results_gateon_s42`（未跟踪，run_meta 时间 2026-10-22T15:53Z，seed 42）

**字段与定义一致性（数值层 ✓）**——单位、分母均可复算对上：

| 核对项 | 复算 | 落盘值 | 判定 |
|---|---|---|---|
| `avg_wait`（秒） | solution.json Σ(completed_at−arrival_time)/60 | gateoff 730.0 / gateon 700.0 | ✓ |
| `path_per_completion_m`（米） | total_path_m / n_completed | 26855.345 / 24895.185 | ✓ |
| `throughput_per_cycle` | n_completed / n_cycles（8 / 7） | 7.5 / 8.571 | ✓（分母=实际周期数，早停后 8 vs 7 周期） |
| `completion_rate_final` | 60/(60−0) | 1.0 / 1.0 | ✓ |

**schema 层 ✗（P1）**：这两份产物生成于 aa88d5a 口径清理**之前**：

- 仍含**已删除键**：`summary.completion_rate`（=1.0）、`summary.on_time_rate`（0.9167/0.8333）——正是 A1/A2 判为冗余/同名不同义而删除的键；
- **缺新增键**：`infeasible_wasted_path_m`（B1）、`cand_hit_rate`（B2）、逐周期 `infeasible_path_m`；
- `problem` 段**缺口径自描述**：无 `lambda_cost / w_violation / w_time / cost_polish_enabled`（2026-10-24 才补），故这两跑的 λ 档**无法从数据对账**；
- `run_meta` **无 `config_fingerprint`**（旧版），无法按指纹归类口径；
- `problem.scalarization` 自述错标：gateon 双开关为 `coverage_gate=True, lexicographic_coverage=True`，却标注「**字典序（big-M，收益优先）**」——即审计 P1-4 类「图/表标注与实际口径分裂」的历史实证（该修已在 aa88d5a 的 metrics.py:387-403 落地，但本产物是修复前的）。

### 5.2 gateoff vs gateon 对「shortfall 不是永久丢失」解释基准的支撑度：**数据不足，只能部分支撑**

- **无 shortfall 样本**：两跑 `n_capacity_shortfall = 0`；gateon 活跃周期 `gate_stats = {n_skip:0, n_shortfall:0, n_rescued:0, …}`，gateoff `gate_stats = {}`（未插桩，计 0 属「缺字段」而非「观测为 0」）。**不存在 shortfall↑ 的场景，无法验证/证伪解释基准**。
- **能支撑的是「跨周期推迟不丢任务」**：gateoff `n_deferred_delivery=12`（`deferred_delivery_ratio=0.2`）、gateon 10（0.167），两跑均 `n_completed=60/60`、`n_remaining_pool=0`、`n_eliminated=0`——窗口内推迟的任务最终全部交付，方向上支持「残留不是永久丢失」。
- **但存在语义级反证风险（P0）**：`TaskPool.begin_cycle` 每周期调用 `expire(current_time)`（task_pool.py:288 → :439-461），**超过 `latest` 仍在池的任务被判 EXPIRED 出池 = 永久丢失**；这与固定稿「无超期淘汰、允许跨周期推迟、shortfall 不是永久丢失」**不自洽**——只要任务滞留越过其 latest，就从「可推迟残留」变成「淘汰」。实证：exp05 存量数据中 `B1/seed_42 n_eliminated=1`、`B3/seed_45 n_eliminated=3`、`M0/seed_46 n_eliminated=1`（results/*/seed_*/metrics.json），即永久丢失在现行机制下真实发生。exp06/notes.md:91 也预期「λ=10 积压场景下『淘汰』会首次成为非零指标」——在固定稿语义下这些应是「排队残留」而非「淘汰」。**需裁定**：要么 expire 不再出池（超期任务允许迟到交付，归入迟到口径），要么问题语义改回「带超期淘汰」，否则主表分母/失败构成（`elimination_rate`、恒等式 `arrived = done + unreach + elim + remain`）都建立在与固定稿冲突的语义上。

### 5.3 exp05 `results/`（未跟踪，run_meta 时间 2026-10-21，无 config_fingerprint）

- **旧 schema**：`metrics.json` 含 `completion_rate`/`on_time_rate`（已删键），**无** `completion_rate_final`/`on_time_rate_final`/`path_per_completion_m`/gate 分账等现行主表键；`summary.csv` 表头为旧 25 列（`completion_rate,on_time_rate,…`），与现 `CSV_COLUMNS`（metrics.py:36-73，含 `completion_rate_final`、`path_per_completion_m` 等）不一致。
- **统计口径错误（P0）**：CSV 12 行 = 6 臂 × (seed 45 + mean)，**均值行逐值等于 seed 45 单值**（如 M0 mean `completion_rate=1.0, total_path_m=1569965.2` 与 seed 45 行完全相同），而盘上 6 臂各有 seed 42–46 五份结果；`M0/summary.json: {"seeds": [45], … "std": 0.0}`。该 CSV 自称「论文主表数据源」（metrics.py:27），照现状出表 = 单 seed 冒充 5 seed 均值。
- 表现层差异提示：B3（VRPTW 硬时间窗）`n_eliminated=3`、按时率 1.0、航程 1255.1km；M0 按时率 0.93、航程 1570.0km——若把 `elimination_rate` 当主失败指标，会把 B3 的方法假设差（硬窗拒载）记成「失败」，正是固定稿「淘汰率不当主失败指标」要避免的误读。
- 复用安全性 ✓：run.py:395-421 复用前比对 `config_fingerprint`（本存量无指纹 ⇒ 现行代码会强制重跑，不会静默沿用旧口径）。

### 严重度

- **P0**：① expire 语义与「无超期淘汰/非永久丢失」冲突（5.2）；② exp05 summary.csv 单 seed 冒充均值 + 旧键（5.3）。
- **P1**：gateoff/gateon 与 exp05 存量产物均为旧 schema、缺口径自描述与指纹，不可直接引用。

---

## 口径问题清单

| # | 问题 | 位置 | 现状 | 建议 | 严重度 |
|---|---|---|---|---|---|
| 1 | 「论文主表必报列序」含淘汰率、缺等待时间与耗时，与固定稿主表（本周期覆盖/吞吐、单位完成航程、等待时间、耗时）冲突 | `exp01_smoke/metrics.py:456-465`（paper_main_table）；`report.tex:87-105` | 仍为 2026-10-22 v2 列序，机器可读、供出表直读 | 按固定稿重登记：主表 = throughput/cov、path_per_completion_m、avg_wait、solve/wall；淘汰率降为诊断层「缺陷监视项」 | **P0** |
| 2 | 问题语义「无超期淘汰、允许跨周期推迟」与 `expire` 超期出池（永久丢失）机制不自洽 | `methods/task_pool.py:288, 439-461`；恒等式含 elim（audit A 项）；exp05 数据 elim=1/3/1 实证 | shortfall/残留一旦越过 latest 即变淘汰，「不是永久丢失」仅在窗口内成立 | 裁定二选一：expire 不出池（超期转迟到交付口径）或改问题语义；同步修恒等式与主表失败构成 | **P0** |
| 3 | exp05「论文主表数据源」summary.csv：均值行=单 seed、无 ±std 列、旧键 schema | `exp05_baselines/results/summary.csv`、`M0/summary.json (seeds=[45])`；`metrics.py:27, 36-73` | 5 seed 结果在盘但汇总被分批调用覆盖；CSV 无 std 列 | 汇总改为扫描盘上全部 seed_{id} 重聚合；CSV 补 mean±std 双列（或 σ 列）；旧结果按指纹不复用（现行代码已会重跑） | **P0** |
| 4 | notes.md 口径描述与 config/代码相反（四层含 −m·F1、门禁双开关 false） | `exp01_smoke/notes.md:76-89` vs `config.yaml:309-311`、`fitness_weights.py:26-39` | notes 述「门禁=待修项、四层=生产」，实为门禁唯一主口径 | 重写 notes 适应度节为门禁口径固定表述；同步 report.tex:124 | P1 |
| 5 | 图脚注迟到折减常数过期（W=600s） | `plot/report/outcome_summary.py:255` | 生产 W=1800（config.yaml:224） | 脚注改读 `problem` 自描述或更新常数 | P1 |
| 6 | exp02/exp04/exp06 notes 仍以「四层准字典序 / lexicographic 开关」为设计口径 | `exp02/notes.md:42-48`、`exp04/notes.md:40-41`、`exp06/notes.md:31,70,96` | 「门禁默认关、行为与旧口径逐位一致」「A1 改回旧加权和 lexicographic:false」等在当前代码下不可实现（gate_mode 恒 True、lexicographic 字段失效） | 按门禁口径重述各实验评价口径；exp04 A1 臂改用仍有效的开关（如 w_hard_violation=0 消融） | P1 |
| 7 | exp05 与 exp01「生产口径逐项相同」声明不成立（λ 1e-2 vs 1e-1；门禁默认关 vs 双开） | `exp05/config.yaml:195` vs `exp01/config.yaml`（λ=1e-1 裁定注释）；exp05/notes.md「M0@seed42 可与 exp01 对账」 | 跨档数据不可混用（exp01 config 注释亦要求分列） | 对齐 exp05 至 1e-1 + 门禁双开，或把声明改为「独立档、不对账」并全量重跑 | P1 |
| 8 | 门禁开关语义失真：exp02/exp05 仍按「关=旧口径」配置与描述 | `exp02/run.py:253-254,288-290`；`exp05/config.py:182,202-203` | 「关」不再产生非门禁口径 | 配置注释与 notes 明确两开关现仅控制「构造修复 / W_skip 分账」；或移除误导性开关 | P1 |
| 9 | aa88d5a「重跑 3 seed 验证」产物不在库/工作区 | `exp01_smoke/results/`（空） | 数据层无法对账口径清理，仅审查文档留数 | 重跑一次 3 seed 并把 metrics/run_meta/solution（JSON）入库或归档；或在文档注明产物获取方式 | P1 |
| 10 | exp02 单 seed 出规模结论 | `exp02/config.yaml`（仅 seed 42） | 10/50/100 × 6 变体无重复 | 至少 3 seed（42/43/44）重跑，报 mean±std | P1 |
| 11 | exp05 存量结果（含 gateoff/gateon）旧 schema、缺口径自描述与 config_fingerprint | `exp05/results/*/metrics.json`、`exp01_smoke/results_gate{off,on}_s42/` | 含已删键、缺 B1/B2 键、problem 段无 λ 等自描述、scalarization 错标 | 均按现行口径重跑后作废旧目录；引用旧数须标注「旧口径（含 −m·F1 / W=…）」 | P1 |
| 12 | exp06 把「淘汰 ≤ elim_max」设为主判据 | `exp06/run.py:208-215`；`criteria.assumption1` | 与「淘汰率不当主失败指标」冲突（叠加 #2 语义问题） | 主判据换固定稿主表口径（覆盖/吞吐、单位完成航程、等待、耗时），淘汰降为缺陷监视 | P1 |
| 13 | 废飞航程 / 候选命中率层级未标注（主表行 vs 诊断清单 vs 关键指标面板混用） | `report.tex:104,162`；`outcome_summary.py:225`；`metrics.py` summary 块 | 定义清晰、不在 paper_main_table，但出现层不唯一 | 在 notes 与图注标注所属层（诊断/过程），report.tex 主表行移除废飞 | P2 |
| 14 | 审计件数与 FAIL 语义文档矛盾 | `run.py:453`、`report.tex:122,132,817`（「23 项」「FAIL 即阻断」）vs `PIPELINE.md`（「报警不阻断」）、实际 25~28 项 | 文档互相矛盾 | 统一为「约 25–28 项、FAIL 报警不阻断（退出码 1）」 | P2 |
| 15 | 规模/工况描述漂移 | `exp02/notes.md:10`（10 任务/24 机）；`exp06/notes.md:21-22`（exp01=Poisson λ=5、exp02=N=50~200） | 与实际配置不符 | 按 config 实值修订 | P2 |
| 16 | 「同预算」表述风险 | `exp05/notes.md` 公平性协议 | 预算不等属声明过的方法本体差异 | 写作时避免「同预算」措辞，沿用「同外壳/同候选集/同评价」 | P2 |

---

## 附：本次核对可复跑命令

```bash
# 测试基线
python3 -m pytest task_allocation/tests -q          # 277 passed

# 主表列序与三层定义
grep -n "paper_main_table" -A8 task_allocation/experiments/exp01_smoke/metrics.py

# gateoff/gateon 字段与分母复算（§5.1 表格来源）
python3 - <<'EOF'
import json
for d in ("results_gateoff_s42","results_gateon_s42"):
    m=json.load(open(f"task_allocation/experiments/exp01_smoke/{d}/metrics.json"))
    s=m["summary"]
    print(d, "旧键:", [k for k in ("completion_rate","on_time_rate") if k in s],
          "缺新键:", [k for k in ("infeasible_wasted_path_m","cand_hit_rate") if k not in s],
          "problem缺自描述:", [k for k in ("lambda_cost","w_violation","w_time") if k not in m["problem"]])
EOF

# exp05 汇总覆盖度
python3 -c "
import json,csv
print(json.load(open('task_allocation/experiments/exp05_baselines/results/M0/summary.json'))['seeds'])
print(sorted({r['seed'] for r in csv.DictReader(open('task_allocation/experiments/exp05_baselines/results/summary.csv'))}))"

# expire 语义
sed -n '283,290p;439,461p' task_allocation/methods/task_pool.py
```
