# exp01–exp11 全面审查（2026-11-13）

> **性质**：只读静态审查。审查对象 = 11 个实验的 `config.yaml` 配置、代码装配链、
> 结果档实际生效参数、`notes.md` / `PIPELINE.md` / 报告文本、`experiments/README.md` 状态表，
> 以及与项目铁律、写作红线的一致性。
> **本轮未修改任何实验配置 / 代码 / 结果数据，未跑实验、未渲图、未提交 git**；
> 唯一新建文件是本报告。所谓「实际生效参数」均读自落盘 JSON 与代码，不是推测。
>
> 冲突裁决顺序：用户 11-13 裁定 > `docs/exp05_e1_design_2026-11-13.md` >
> `docs/stage2_final_scheme_2026-11-06.md` §11 > 各实验 notes。

---

## 0. 结论摘要

11 个实验里，**7 个有当前有效正式档**（exp01 / exp02 / exp07 / exp08 / exp09 / exp10 / exp11），
**4 个无结果档但性质完全不同**：exp03 从未跑、exp04 是空壳、exp05 是 E1 待跑批、
exp06 是 11-13 按裁定删除了作废档。把四者一律写成「缺数据」会掩盖真实风险。

配置—代码—结果档三者自洽且合规的是 **exp01 / exp02 / exp08 / exp09 / exp10**；
exp07 与 exp11 的正式档数值闭合但有文档/字段欠账；
**exp05 / exp06 / exp03 各有 P0 级问题**，其中 exp05 的问题落在 E1 正式档跑批之前，
是本报告最高优先级。

| 级别 | 条数 | 核心所指 |
|---|---:|---|
| P0（跑批前必修 / 会导出错误结论） | 5 | exp05 门禁、exp03 无档结论与平面口径、exp06 重跑自变量失效与淘汰口径 |
| P1（口径失实 / 潜在误用） | 16 | 状态表、存档标记、正式档覆盖记录、红线禁词、恒等式两套定义等 |
| P2（措辞 / 维护性） | 14 | 注释残留、死键、字段缺失、复制粘贴标题等 |

**最重要的三条**：
1. `exp05_baselines/config.py:232` 把 `coverage_gate` 传参整行注释，yaml 又无该键 ⇒
   **E1 的覆盖门禁实际为 False**，与 exp01/exp07–11 全为 True 不一致，也与
   「场景/协议与 exp01 生产口径逐项相同」的声明冲突。E1 20 单元尚未跑批，现在修还来得及。
2. `exp03_event_rolling/task_allocation_analysis.md` 在 **`results/` 根本不存在** 的情况下
   写出「完成率 100% / 提速 85.6% / 证明系统适应能力」等结论数字，同时 config 把
   DEM 与建筑都置 `null`（退平面），却仍挂 `guangzhou` 名义。该文件不得进论文。
3. exp06 的重跑链路当前是坏的：`--set problem.n_uavs=10` 被 exp01 的 `fleet_file` 装配
   短路，`solver.max_generations` 是死键（真正消费的是 `solver.max_iter`），
   且把「超期淘汰」写进恒等式与预注册判据——与铁律冲突。

---

## 1. 逐实验配置总览

| 实验 | 定位 | 场景 / 巢 | 机队 | 任务与到达 | 预算 | seed | 结果档 |
|---|---|---|---|---|---|---|---|
| exp01_smoke | 链路 + 口径标定 + 回归基线 | guangzhou / 3 | 异构 spec 10·8·6 km | 20 + [5,5] = 30，池空收敛 C4 | 50×200 | 42/43/44 | ✅ 23 文件 11-12 |
| exp02_selection_scale | 单周期规模可扩展性（描述性） | guangzhou / 3 | 同构 6×10 km @15 | N=10/50/100 单周期快照 | 20×40 | 42（单） | ✅ 21 文件 11-13，18 格 |
| exp03_event_rolling | 事件触发重规划 | guangzhou 名义，**实为平面** / 3（cap 3） | 异构 spec（exp01 副本） | 12 + Poisson λ=3，20 周期跑满 | 30×60 | 42/43/44 | ❌ 从未跑 |
| exp04_ablation | 消融矩阵收编 | — | — | — | — | — | ❌ 0 文件空壳 |
| exp05_baselines | E1 单主张检验台 | **lasa_chengguan / 6** | 同构 10×20 km | 30 + [10,20] = 60 | 50×100 | 42–46 | ⏳ `results_e1/` 待跑 20 单元 |
| exp06_joint_regime | 联合域（K × λ × 能力衰减） | **不自带**，继承 exp01 | 由 `--set` 注入（当前失效） | Poisson λ 5 档，24 周期 | 继承 exp01 | 42（单） | 🗑 11-13 已删作废档 |
| exp07_polish_accept | 采纳判据 + L3b 开关 | guangzhou / 3 | 同构 6×10 km | 正式 20+[5,5]=30 | 50×200 | 7/11/23 | ✅ 136 文件 11-07 |
| exp08_gain_decomposition | 2×2 G×B 收益拆分 | guangzhou / 3 | 同构 6×10 km | 正式 30，池空 C4 | 50×200 | 7/11/23 | ✅ 65 文件 11-07 |
| exp09_mcf_layers | 终点巢层1×层3 四档 | guangzhou / 3 | 同构 6×10 km | 正式 30 | 50×200 | 7/11/23 | ✅ 66 文件 11-07 |
| exp10_capacity_pressure | 容量压力四场景 | guangzhou / 3 | 同构 6×10 km | burst 正式 20+[10,10]=40 | 50×200 | 7/11/23 | ✅ 110 文件 11-08 |
| exp11_multi_depot | 三臂 × 任务阶梯 | guangzhou / 3 | 同构 6×10 km | 每周期 10，T2/4/6/10 = 20/40/60/100 | **24×60 标定档** | **7 个** 5/7/11/13/17/19/23 | ✅ 243 文件 11-13 |

### 1.1 门禁 / λ 对账（读 config.yaml 实际键值）

| 实验 | coverage_gate | coverage_audit | l3b_enabled | λ_cost | output_dir |
|---|---|---|---|---|---|
| exp01 | True | True | 生产默认 | 1e-1 | results |
| exp02 | True | 键缺省 | 生产默认 | 1e-1（键在其它段） | results |
| exp03 | True | True | 生产默认 | 1e-1 | results（**代码未接线**） |
| exp04 | — | — | — | — | — |
| **exp05** | **键缺省 + 传参被注释 ⇒ False** | True | 生产默认 | 1e-1 | results_e1 |
| exp06 | 继承 exp01 | 继承 exp01 | 继承 exp01 | 由矩阵注入两档 | 注入 exp01 |
| exp07 | True | True | **False**（对照基准档，合理） | 1e-1 | results |
| exp08–exp11 | True | True | True | 1e-1 | results |

**结论**：λ 全实验统一 1e-1 ✓；场景分野符合 11-09 用户裁定 ✓；
l3b 仅 exp07 显式关且属开/关对照实验 ✓；**唯一破口是 exp05 的 coverage_gate**。

### 1.2 正式档参数从哪来（重点澄清）

exp07–exp10 的 `config.yaml` 顶层确实写着冒烟档值（8 任务 / pop12 / gen20 / 8 周期 / `[2,2]`），
但 **结果档不是冒烟档**。落盘实证：

- exp07 `results/formal/f/seed_7/run_meta.json`：`solver_effective = {pop_size:50, max_generations:200}`、
  `arrivals_per_cycle = {1:20, 2:5, 3:5}`，`metrics.json` 为 `n_arrived:30 / n_cycles:4 / stop_reason:pool_empty`。
- exp08/09/10 的 `formal_run.log:2` 与 `run_meta.json` 同款：20 任务 / pop50 / gen200 / 60 上限；
  exp10 burst 档 `{1:20,2:10,3:10}` = 40 任务。
- 覆盖入口不是 `--set`（exp07 第 1 组除外），而是 `run.py` 内置 `--preset formal` 深合并；
  `SMOKE_SIGNATURE` 防呆会在冒烟参数下拒绝 formal 运行。exp11 的 `PRESETS["formal"] = {}`，
  其 config.yaml 本身就是 T10 正式基准，T2/4/6 由 `--tiers` 逐档覆盖。

⇒ **铁律「冒烟/mid 结论不可外推正式档」未被违反**。但覆盖记录不完整，见 §6.1。

---

## 2. P0 清单（跑批前必修 / 会导出错误结论）

### P0-1 · exp05 覆盖门禁实际为 False，E1 尚未跑批，现在修成本最低

- 证据：`exp05_baselines/config.py:232` 整行是注释
  `# 覆盖门禁：终态修到不动点；默认关 coverage_gate=bool(m.get("coverage_gate", False)),`；
  `config.yaml` 全文无 `coverage_gate` 键（只有 `:202 coverage_audit: true`）；
  `methods/muas/solvers/stage_config.py:79` 类默认 `coverage_gate: bool = False`。
- 对照：exp01 `config.yaml:202`、exp07:204、exp08–11 同类键均为 `true`；
  而 `exp05/config.yaml:18-19`、`build.py:4`、`notes.md:136` 三处声称「与 exp01 生产口径逐项相同」。
- 后果：`coverage_gate` 是「终态修到不动点、能救必救」的完成约束保障。E1 的 4 方法将在一个
  与生产不同的开关口径下产出 `path_m_audit`，并直接影响 `claims.py:118-120` 的
  `completion_rate_is_one` 门槛断言。
- **关联观察（不是结论）**：exp05 v3 正式档 seed43 遗留 1 个可完成任务（待办 #1，
  当前归因为早停/预算截断）与「能救必救」被关闭可能同源。建议先做单因素核查，
  不要直接把早停当唯一原因，也不要在核查前断言因果。
- 建议：在 `exp05/config.yaml` 的 `muas:` 段显式补 `coverage_gate: true` 并恢复传参，
  使 `run_meta.json` 可核验；**此项须在 E1 20 单元跑批前裁定**。

### P0-2 · exp03 没有任何结果档，却有一份写满结论数字且多处触犯红线的分析报告

- 证据：`exp03_event_rolling/results/` 不存在；`task_allocation_analysis.md:3-4`
  却写「基于 exp03_event_rolling 实验数据」「生成日期 2026-11-13」，
  `:175-183` 直接给出「完成率 100% / 按时率 97.1% / 事件数 27 / 总路径 136,663 m」，
  `:204`「所有因子下完成率均保持 100%，**证明**系统对动态事件的适应能力」，
  `:214`「N=200 时**提速 85.6%**」、`:208`「核心算法优势」、`:269`「实验证明…可靠的解决方案」。
- 违反：`README.md:127`「结果表单源生成、禁手写数字」；禁写「完成率恒 100%」；
  禁写「性能提升 / SOTA」；p 值/置信区间全无；`:61` 出现「协同效应」红线词。
- 建议：在补跑并落盘 `results/` 之前，把该文件整体降级为纯设计说明，
  或首部显式标注「本文数值无结果档支撑、未经验证、不得进入论文或任何对外结论」，
  并删除 §5.2/§5.3/§6/§8 的数值与断言。

### P0-3 · exp03 距离口径退化为平面，却挂 guangzhou 名义

- 证据：`config.yaml:18-19` `dem: null` / `buildings: null`（注释称「数据不在 git」）；
  `build.py:90-92,119-123,136-149` 把两个 `None` 传入 `VerticalSectionCostEstimator`。
- 反证：数据确实存在且别的实验已加载——exp01 `config.yaml:24-25`、exp02 `:40-41`
  同场景都用 `dem.tif` + `building`，`exp01/results/run_meta.json:7-16` 有实际 DEM 网格 45×45。
- 与自身文档矛盾：`exp03/notes.md:66` 声称「DEM+建筑垂直切面」。
- 后果：其 `total_path_m` 与全库唯一 DEM 有向三维尺不同尺，禁与 exp01/exp02 并列、禁进主文。
- 建议：改回 `dem.tif`/`building` 重新装配复跑；若保留平面档，必须在 config 与 notes
  显式标注「非唯一距离口径、仅机制协议演示、不与其它实验比绝对值、不进论文主表」。

### P0-4 · exp06 重跑自变量失效（3 处叠加），当前不可直接重跑

- `run.py:60` 注入 `--set problem.n_uavs=10`，但 `exp01/build.py:218-228`
  只要 `fleet_file` 存在就直接 `return build_uavs_from_spec(...)`，`n_uavs` 不参与装配
  ⇒ **K 轴整条塌缩为固定机队**；且 10 > 3×2=6 泊位，容量口径混乱。
- `run.py:68` 冒烟写 `--set solver.max_generations=3`、`config.yaml:35` 写
  `overrides.solver.max_generations: 100`，但 `exp01/config.py:293` 消费的是 `solver.max_iter`
  ⇒ **死键，冒烟实际仍跑 200 代**。
- `config.yaml:35,37` 的 `overrides.solver.max_generations` / `overrides.tasks.n_tasks:30`
  根本不在 `run.py:57-66` 的 6 个 `--set` 里 ⇒ 重跑继承 exp01 的 pop50/gen200/20 任务，
  与 `notes.md:42-43`「pop=20/gens=100」全部不符。
- 附带：`criteria`/`notes.md` 的设计骨架仍写「8 巢、满电 80 km / K∈{6,10,14} / seeds 42/43/44 /
  主表 9 格」，而 config 是 K:[10]、λ 五档（含 25/40）、seeds [42]、24 周期，
  且当前继承的是 guangzhou 3 巢 + fleet_file。
- 建议：重跑前统一 K 注入方式、把键改为 `solver.max_iter`、实际注入 `overrides`，
  并用 `--dry-run` 打印生效值核对。

### P0-5 · exp06 把「超期淘汰」写进恒等式与预注册判据，与铁律冲突

- 证据：`notes.md:84`「恒等式 `arrived=done+unreach+elim+remain` 成立」、
  `:91`「λ=10 积压场景下『淘汰』会首次成为非零指标」；`config.yaml:44` `n_eliminated_max: 0`、
  `:49`「arr25 λ=1e-4 格完成率 0.787、**淘汰 61**」、`:55-58` 把「淘汰增多」当 λ 比较判据；
  `JOINT_REGIME_REPORT.md:15,22-25` 同类表述，并把「产能饿死」写成结论。
- 冲突：铁律 `arrived = done + unreachable + remain`；**不存在「超期淘汰」**，
  出现 eliminating（非不可达）= bug 须修到 0；禁用该术语、禁解释成「假设被证伪」。
  `exp05/claims.py:108-121`、`exp05/metrics.py:219-221` 已用正确三项式。
- 后果：会把机制缺陷（可完成任务未完成）解释成合法现象，与「完成是约束」相反。
- 建议：重跑前统一恒等式与淘汰口径；`JOINT_REGIME_REPORT.md` 的 0.788/0.571
  不得作为「完成率下降」的有效结论引用，须先查早停/预算截断与 P0-1 关联。

---

## 3. P1 清单（口径失实 / 潜在误用）

### 状态表与存档标记

| # | 位置 | 问题 |
|---|---|---|
| P1-1 | `README.md:89` / `exp03/notes.md:3,136-171` | exp03 标「planned（仅 notes）」失实：实有 10 个顶层文件 + 12 个 plot 脚本，`run.py`(364 行)/`config.py`(340 行) 完整；notes 仍写「run.py/config.yaml 待实现」。静态判断可直接跑，但受 P0-2/P0-3 制约 |
| P1-2 | `README.md:90,102` | exp04 标「planned（仅 notes）」且 §7 写「planned 目录只放 notes.md，不要删」，实测 **0 文件空壳、连 notes.md 都没有** |
| P1-3 | `README.md:85-100` | §6 实验清单**没有 exp11 状态行**（全文件仅 `:197` 顺带提及一次） |
| P1-4 | `exp05/notes.md:70-78` | 「正式档结论速览」把 C1–C5 / `ruler_effect p=0.201956` / `549 passed` 写在 E1 当前有效区，**无历史存档标记** |
| P1-5 | `exp05/notes.md:114-156,281-356,360-447,516-533` | 大段旧主体无存档标记：6 方法表含 B3/B2e、「是否明显好于」、两把尺子、8 巢 80 km、「完成率恒 1.000」、四项式恒等式、B4–B7 验收标准、欧氏臂称「建模贡献证据」——与 E1 定稿直接冲突 |
| P1-6 | `exp05/notes.md:107-109` | 仍给 `--claims .../results_v3/claims_report.json`，而顶层 `results_v3/` 11-13 已删；`:55,:63`「当前唯一产物目录是 results_v3」易误读 |
| P1-7 | `exp06/JOINT_REGIME_REPORT.md:7-11,19-23,41` | 10-26 旧数字（0.788/0.571、判定「成立」）以现在时呈现，全文无作废标记；其数据源 `results/` 已被删除 |
| P1-8 | `exp06/config.yaml:48-52` | 把已作废的「淘汰 61、完成率 0.787」写成「实测修正」的预注册依据 |

### 正式档 / 口径记录

| # | 位置 | 问题 |
|---|---|---|
| P1-9 | `exp07/config.yaml:226-229`、`run.py:6-8,76`、`notes.md:17,60-61` | 仍写「`path` 当前默认」；而 `notes.md:290-299` 定稿「统一按 f 裁决、`path` 实现分支已删、现为兼容别名」 |
| P1-10 | `exp07/notes.md:3-4` | 顶部「本轮只做冒烟档、不产出可用于论文的数值结论」与 §9 正式档已完成、§10 落地结论冲突 |
| P1-11 | `exp07/metrics.py:9-11,125` | 恒等式写成 `arrived=done+unreachable+dormant+remain`（四项），与铁律三项式及 `exp05/metrics.py:219-221` 不一致；同名 `n_remain` 跨实验不可比。exp08–11 的 `metrics.py:9` 同款 |
| P1-12 | `exp08/09/10 config.yaml:314-337` 等 | 注释描述正式档（20/5/5、周期 4 收敛），yaml 值却是冒烟档（8 任务、8 周期、`[2,2]`）；注释自相矛盾 |
| P1-13 | `README.md:143-150` §8 | 运行示例**没有 `--preset`**，而 exp08/09/10 的 `--preset` 默认 `mid`（12/25/60/20/[3,3]）。叠加「平铺 --set 是死键」已知坑，按 §8 直接重跑会静默得 mid 档 |
| P1-14 | `exp11/config.yaml:8-9,320-322` | 写「阶梯是同一到达流的前缀、同 seed 嵌套可加」，但 `run.py:140-141` CAVEATS、`notes.md:31-33`、`results/formal/audit.json:7` 实测 **39/63 格短梯度解 ≠ 长梯度解前缀**。按注释写论文会把「独立跑批之差」误写成「同一轨迹增量」 |
| P1-15 | `exp11/config.yaml:22` | `seeds: [7,11,23]`，而正式档 `results/formal/summary.json` 的 `seeds = [5,7,11,13,17,19,23]`（7 个），磁盘 7 个 seed 目录；不带 `--seeds` 重跑只会有 3 个 |
| P1-16 | `exp03/config.yaml:25` vs `notes.md:66` vs `uav_config.json:3` | 巢容量三处不一致（3 / 2 / 2）；`uav_config.json` 是 exp01 逐字副本，description 仍写「exp01_smoke」 |

### 红线措辞

- **`exp09/notes.md:221`（§7.5「论文可写」段）** 出现「另 2 seed …**无差异**」，
  与同文件 `:86,:222,:228` 自设禁令矛盾；`:167` 冒烟段「MCF 与最近合法巢**等价**」禁词残留。
- **`exp11/notes.md:138,222,262,361`** 出现「无可辨差异」「MCF ≡ 就近」「无差异」「等价」，
  而 `:171,:448` 自己声明禁写这两个词。
- 建议统一改为「不显著 / 逐位相同 / 同解条件下费用不高于」，并明确「零方差不检验」。
- **未发现**把 p>0.18/0.19 写成「收益/提升」的情况（exp08/09/10/11 均如实声明不显著）；
  exp10 更明确禁止跨场景配对 t 与「第二条独立证据」，合规。

---

## 4. P2 清单（措辞 / 维护性，摘要）

- **复制粘贴标题**：`exp07/config.yaml:1` 写「exp01_smoke」；`exp09/config.yaml:1` 写 exp08 的「2×2 收益拆分」。
- **引用不存在的文件**：exp08–exp11 四处 `config.yaml:8` 写「见 `notes_archive.md`」，四目录均无此文件。
- **exp05 注释失配**：`config.yaml:362-364` 写「24 周期 + Poisson 到达流」（实为 exp06 口径），
  本实验是 fixed 60 任务；`:326-336` 标题写「max_generations：500」而实际 `max_iter: 100`。
- **exp05 帮助文本残留**：`run.py:425` `--arms` 仍列 `Q0 B3 B2e`；`:438` `--force` 写 `results/<ARM>/`。
- **exp05 死键**：`evaluation.distance_definition` / `coordinate_precision`、`claims.alpha` 无代码消费
  （`:426-427` 自己刚按「无消费」删过 `audit_failure_policy`，标准不一）。
- **`CLEANUP_2026-11-13.md:25-26`** 说 `--analyze-only` 与 `plot_joint.py`「会报错」，与代码不符：
  `exp06/run.py:176-178`、`plot_joint.py:27-29,189-192` 均为打印提示后**正常返回**（空跑、不重建目录）。
- **exp03 门禁与装配**：`run.py:338-339` 丢弃审计返回值（FAIL 不阻断）、无恒等式/淘汰门禁、
  `output_dir` 三处硬编码「results」未接线、`--dry-run` 仍建场景且只打印 events 段、
  burst 位置固定 `rng(42)` 不随 seed 变化。
- **exp01**：`metrics.py:705` 把 DEM bounds `round(v,1)`，与同文件 `:86-89`「坐标不做 round」字面不符
  （bounds 不参与航程复算，影响有限）；`:211-214` `n_eliminated` 结构性写死 0。
- **exp02**：`tasks.region_geojson` 无消费者死键；`run.py:376-377` `coverage_gate` 代码默认 False
  （靠 yaml 显式 true 开启，漏配会静默关）；目录缺 README §4 声称的自持文件（实质未跨实验 import，合规）。
- **exp07**：`results/l3b_formal/*/run_meta.json` 缺 `solver_effective`，该档无法直接核对预算。
- **exp08–11**：`n_eliminating=0` 是结构性保证且代码已诚实标
  `structural_zero_unverified` / `independently_verified: False`，论文引用时**不得包装成「独立验证」**。
- **exp11**：`run_meta.json:56` `stop_reason: ""`（主动关早停却落空串），停机判据不可审计。
- **exp01 `config.yaml:21`** 仍注「lasa_chengguan 可切回」，与「lasa 仅 exp05 v3/E1 使用」口径相左。

---

## 5. 跨实验共性问题（比单点缺陷更值得先处理）

1. **`effective_config.json` 几乎全库缺失。** `README.md:51` 承诺「本次真正生效的配置（含覆盖结果）」
   是复现核对依据；实际全库 21 份里 **19 份在 `_archive/`**，当前实验只有 exp01、exp02 有。
   exp07–exp11 的正式档用 `--preset formal` / `--set` 覆盖，却只把参数留在 `.log` 文本和
   `run_meta.json` 的零散字段里（exp08/09/10 的 run_meta 连 `n_tasks`/`population`/`max_iter`
   都没有）。⇒ **正式档预算只能靠日志文本复原，无法结构化审计**。
2. **`n_seed_failed` / `consistency` 在所有当前正式档 `summary.json` 里都是 0 命中。**
   用户口径「失败 seed 不静默重采样、单列 `n_seed_failed`」未落地；
   `unpaired_seeds` / `paired_seeds` / `config_purity` / `all_invariants_passed` 倒都齐全
   （exp10 `results/_interrupted/` 有 6/12 半截档，只在日志里声明未参与结论）。
3. **恒等式两套定义并存**：铁律三项式（exp01/exp02/exp05）vs 四项式含 `dormant`
   （exp07/exp08–exp11 `metrics.py`）。代码更细不算 bug，但论文写恒等式时会对不上账，
   且 `n_remain` 同名不同义（残差 vs 含休眠池大小）。
4. **config.yaml 是冒烟基准、正式档靠覆盖**：exp07–exp10 设计如此且有 `SMOKE_SIGNATURE` 防呆，
   但 README §8 没写 `--preset`、config 注释又半写正式档，构成重跑陷阱。
5. **状态表与磁盘脱节**：exp03 标 planned 实为已实现、exp04 标「仅 notes」实为空壳、
   exp11 无状态行、exp06 与 exp05 的「已删 / 待跑」需读者去 README §7 才知道。
6. **作废数字残留**：exp06 报告与预注册、exp05 notes 旧主体、exp03 分析报告——
   三处都是「数字还在、数据源已删或从未存在」，是论文误引的最高风险点。

---

## 6. 合规确认（避免只报负面）

- **λ 全实验统一 1e-1**（exp06 通过 `--set` 注入 1e-4 / 1e-1 两档属该实验自变量，合规）。
- **场景分野正确**：exp01/02/03/07–11 为 guangzhou 3 巢 DEM+建筑；
  exp05 为 lasa_chengguan 6 巢（E1 明确不换场景），跨实验禁比绝对值的口径在 notes 中已写。
- **exp05 E1 收窄落地完整且一致**：4 方法 M0/B1/B2/D0、单一 `path_m_audit`、
  `claims.primary_pair=[M0,B1]`、`design_version` 三处一致、
  `build_reference_table.py:110-154` 多重门禁确实会拒旧 7 臂/v3 档、
  `run.py:470` 兜底已是 `results_e1`、`results_e1/` 不存在属已知待跑状态而非缺陷。
- **正式档与冒烟档未混**：exp07–exp10 落盘实证为 20/40 任务、pop50/gen200、池空 C4；
  四个 notes 均标注冒烟/mid 不可外推。
- **写作红线多数已内嵌**：exp08「逐位相同」不写「无差异」、exp09 所有 p>0.19 如实声明、
  exp10 明确「不是第二条独立证据」「禁止跨场景配对 t」「未制造容量竞争」、
  exp11 明写 24×60 为标定档且禁与 exp01 横比。违规集中在 exp03 分析报告与
  exp09/exp11 的个别禁词。
- **结果数据完好**：11-13 清理后 7 个 exp 正式档 + 2 个 `ab_*` 证据链文件数与体积未变；
  127 个脚本零删除；`_archive/` 50 项 / 788 文件保留。
- **门禁**：`coverage_audit` 全 true；exp07 `l3b_enabled: false` 是开/关对照的显式基准档，
  由 `compare_l3b.py:144-154` 硬断言保证单因素，不构成「默认关」冲突。

---

## 7. 优先级建议（按「什么时候必须做完」排序）

### A. E1 正式档跑批前（最高，阻断性）
1. 裁定并修 **P0-1**（exp05 `coverage_gate`）；顺带单因素核查 seed43 遗留反例是否同源。
2. 补齐 exp05 `notes.md` 的存档标记与已删路径（P1-4/5/6），确保跑批后出表只认 `results_e1/`。
3. 清理 exp05 config 注释与帮助文本（P2），避免 `--arms` 误用旧 7 方法名。

### B. 写论文前（防误引 / 防红线）
4. exp03 分析报告整体降级或加醒目标注（P0-2）；exp06 报告加作废标记（P1-7/8）。
5. 修 exp09/exp11 的「无差异/等价」禁词（P1 红线项）。
6. 修 exp11 `config.yaml` 前缀可加推论（P1-14）与 seeds（P1-15），避免把「独立跑批之差」写成轨迹增量。
7. 统一恒等式三项式口径或给出字段映射（P1-11 / §5.3）；exp07 补「path 为兼容别名」（P1-9/10）。
8. 更新 `README.md` §6 状态表（P1-1/2/3）与 §8 的 `--preset`（P1-13）。

### C. 重跑前（exp03 / exp06 恢复可用）
9. exp06：修 K 注入、键名、`overrides`、淘汰口径、设计骨架（P0-4/5），再用 `--dry-run` 核对。
10. exp03：恢复 DEM+建筑口径、补恒等式/淘汰门禁、接线 `output_dir`、修审计返回值、
    统一巢容量与 uav_config 描述（P0-3 / P1-16 / P2）。
11. 两者均须 **单独发话、严格串行 `-u`、求解期不出图**。

### D. 工程维护（不阻断）
12. 全库补 `effective_config.json`、`n_seed_failed`、`consistency` 三个结构化字段（§5.1/5.2）。
13. 清理 P2 注释残留与死键；`CLEANUP_2026-11-13.md` 的「会报错」改为「空跑」。

---

## 8. 本轮边界

- 审查覆盖 `task_allocation/experiments/exp01`–`exp11` 全部 11 个目录 +
  `experiments/README.md` + `docs/exp05_e1_design_2026-11-13.md` +
  `docs/stage2_final_scheme_2026-11-06.md` §11 + `_archive/CLEANUP_2026-11-13.md`。
- **未运行任何求解、`pytest`、出图或出表**；所有结论来自静态代码与落盘 JSON 读取。
- **未修改任何实验配置、代码、结果数据或既有文档**；未提交 git。
- 本报告只做事实判定与优先级建议，**所有修复须用户单独发话**；尤其 A 组第 1 项
  与 C 组第 9/10 项涉及口径裁定，不得由审查方擅自改动。
