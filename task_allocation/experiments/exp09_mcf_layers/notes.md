# exp09_mcf_layers —— 终点机巢「层 1 投影 vs 层 3 MCF」两层 × 四档对照

定稿 §4.1「MCF 对照方案（P3 实验）」与 §11.4 P4 待办第 2 项。
实验自持 config/build/paths/metrics/plot，不建 `_shared/`、不抽公共绘图包。

## 1. 要回答什么

给定每架机的访问序列（路径）后，终点机巢由最小费用流
（`solve_end_nest_assignment`）联合分配，是**给定路径下的条件最优**
（非「归属 + 顺序 + 巢」的联合最优，更不是全局最优）。本实验要回答：

1. 把解码期（层 1）的 MCF 换成「最近合法巢」确定性投影，端到端代价怎么变？
2. 把交付期（层 3）的 MCF 换成同一投影，返航段航程怎么变？
3. 两层策略是否可分开归因，还是存在不可忽略的联动？

重点不是让每层都取得收益，而是确认**每层解决什么问题**。

## 2. 2×2 同 seed 配对

|  | 层 3 投影 | 层 3 MCF |
|---|---|---|
| **层 1 投影** | `nn` | `nm` |
| **层 1 MCF** | `mn` | `mm`（当前生产口径） |

四档唯一差异用 `dataclasses.replace(stage_model, ...)` 锁定，只允许
`end_nest_l1_mode` / `end_nest_l3_mode` / `cost_polish_accept_mode`
三个字段不同（后者四档统一 `f`，实际只差前两个）；逐 seed 核验配置纯度。
同一 seed 跑完四档再进下一个 seed，按 seed 交替档序抵消缓存预热偏差；
**严格串行，不并行任何实验**。

报告口径：逐档质量指标、**主效应 + 交互项并列**
（`interaction = (nn − nm) − (mn − mm)`）、配对 t 检验
（精确 Student t，正则化不完全 β；零方差标 `zero_variance_no_test`）。
**禁写「独立收益」「协同效应」**：差值一律是同 seed 配对的条件差。

## 3. 为什么不能用 `projection_only`

`projection_only` 是 P1 的**复合档**，一次同时剥掉三处解码期目标改写：

1. `_consolidate` 成本合并跳过；
2. `_best_order` 剔 NN 候选、选择键退为 `(迟到数, 迟到量)`（航程裁决移出每体路径）；
3. 终点机巢降级为「最近合法巢」确定性投影。

拿它做 MCF 对照，会把三处改动一起归因到 MCF，口径不纯。
因此本实验新增 `MUASStageConfig.end_nest_l1_mode` /
`end_nest_l3_mode`（默认 `"auto"` ⇒ 零行为变化）并给
`_choose_end_nests` 加 `phase` 参数：

- `phase="l1"`：`decode_repair.py` / `decode_constructive.py` 的 6 个调用点；
- `phase="l3"`：`route_polish.py` 的 2 个调用点（`post_cost_polish` / `post_polish`）；
- 不传 `phase`：诊断性调用（`stage_runner` 的 subset-optimality proxy）恒走 auto。

取值 `"nearest"` = `_choose_end_nests_projection`（最近合法巢、零费用比较），
`"mcf"` = `solve_end_nest_assignment`（给定路径下条件最优）。
`projection_only` 保持默认 `False`，四档都不动它（运行期硬断言）。

**默认档零行为变化已实测**：改动后全量 538 passed；exp07 两组 mid 各 3 seed
重跑与改动前基线逐字段比对，26 份 JSON（2 summary + 12 metrics + 12 run_meta）
除 `wall_seconds` / `timestamp_utc` 外**零差异**。

## 4. 指标口径

| 指标 | 来源 | 说明 |
|---|---|---|
| `total_return_leg_m` | `stage_runner` 新增纯观测诊断 `return_leg_m` 逐周期求和 | Σ(末任务→终点巢的**有向**三维 DEM 代价)，与 `tour_path_cost` 末段同口径；禁对称 / 欧氏近似 |
| `nest_overflow_total` | 新增诊断 `nest_overflow` 逐周期求和 | `check_nest_capacity` 逐巢 `overflow` 之和；0 = 终点巢分配可行 |
| `wall_seconds` | 实验层计时 | 机器负载相关，不作质量结论 |
| `final_fitness_sum` / `total_path_m` / 完成率 / 按时率 / 迟到 / 硬违约 | 既有 `collect_run_metrics` | 与 exp07 / exp08 同源，可交叉验证 |
| `cost_polish_n_mcf_resolve` | 既有诊断 | 交付期 MCF 重解次数，用于判断层 3 开关是否真正被触发 |

两个新诊断**纯观测**，不参与任何采纳 / 约束判定。

## 5. 口径限制（论文须如实声明）

1. **层 1 策略作用于每个候选个体的解码**，改变的是整条 DE 搜索轨迹，
   不是「同一解上换巢」。因此层 1 主效应是端到端效应，
   **不能解释成「给定路径下 MCF 相对最近巢的收益」**。
2. **层 3 策略只在 `post_cost_polish` / `post_polish` 真正被调用的周期生效**
   （前者要求 `n_accepted > 0`）；零精修周期两档相同。
   这是算子可得性差异，交互项**不能解释成纯二阶交互**。
3. `nest_overflow` 若四档恒 0，说明两种策略都满足容量约束，
   差异只体现在返航代价，**不得写成「可行率提升」**。
4. 终点机巢 MCF 是**给定路径下的条件最优（非全局最优）**。
5. 冒烟 / mid 档结论**不可外推正式档**；正式档须单独跑批。
6. 单一场景（广州 3 机巢）、固定到达流、3 seed ⇒ df=2 效能极低；
   p 不显著须如实声明，**也不得写成「无差异」**。
7. 完成率 / 恒等式 / eliminating 为实验层硬断言；
   `independent_audit` 的几何重建重评仍是技术债
   （`RollingHorizon` 未暴露审计钩子）。

## 6. 目录与复现

```bash
python run.py --preset smoke --seeds 7            # 设计自检
python run.py --preset mid --seeds 7,11,23        # 中间档（不可外推）
python run.py --preset formal --seeds 7,11,23     # 正式档
python run.py --preset formal --seeds 7 --dry-run # 只看生效配置
python plot/compare.py --profile formal           # 跑批后单独出图
```

产物 `results/<preset>/`：`summary.json`（2×2 报告）、`summary.csv`、
逐档逐 seed 的 `metrics.json` / `run_meta.json`。跑批不渲图；
`results/` 被 `.gitignore` 忽略。

## 7. 结果

三档均 `RC=0`，冒烟 1 seed、mid 3 seed、正式档 3 seed（正式档单独跑批，
`FORMAL_RC` 待回填）。**只有正式档可作结论依据**。

### 7.1 冒烟档（1 seed，仅设计自检）

| 档 | f 之和 | 总航程 | 返航段 | overflow | 完成率 |
|---|---|---|---|---|---|
| `nn` | 1913.736 | 19.14 km | 5.62 km | 0 | 1.000 |
| `nm` | 1913.736 | 19.14 km | 5.62 km | 0 | 1.000 |
| `mn` | 1852.743 | 18.53 km | 7.43 km | 0 | 1.000 |
| `mm` | 1852.743 | 18.53 km | 7.43 km | 0 | 1.000 |

配置纯度 True（差异字段=2）、铁律全过，`run_meta.stage_effective`
四档 `end_nest_l1_mode` / `end_nest_l3_mode` 逐档正确、
`projection_only` 全 False、`cost_polish_accept_mode_effective` 恒 `f`。
层 3 主效应与交互项全 0（`nn≡nm`、`mn≡mm`）。
层 1 换到 MCF 使总航程 −0.61 km 但**返航段 +1.81 km**（方向相反），
正是「整条搜索轨迹被改写」的证据，不得解释为局部换巢收益。

### 7.2 mid 档（3 seed，不可外推正式档）

`MID_RC=0`；配置纯度 True（只差 2 个预期字段）、`all_invariants_passed=True`、
`unpaired_seeds=[]`、`paired_seeds=[7,11,23]`。

| 档 | f 之和 (7 / 11 / 23) | 总航程 km | 返航段 km | overflow |
|---|---|---|---|---|
| `nn` | 3107.689 / 3204.267 / 2546.967 | 31.08 / 32.03 / 25.47 | 9.00 / 9.09 / 6.43 | 0 / 0 / 0 |
| `nm` | 同 `nn` | 同 `nn` | 同 `nn` | 0 / 0 / 0 |
| `mn` | 3114.193 / 3174.935 / 2534.519 | 31.14 / 31.75 / 25.35 | 8.70 / 9.33 / 5.27 | 0 / 0 / 0 |
| `mm` | 同 `mn` | 同 `mn` | 同 `mn` | 0 / 0 / 0 |

配对效应（f 之和 / 总航程 m / 返航段 m）：

| 效应 | f | p | 总航程 | p | 返航段 | p |
|---|---|---|---|---|---|---|
| L1@L3n | −11.759004 | 0.373722 | −113.928 | 0.367874 | −404.722 | 0.427883 |
| L1@L3m | −11.759004 | 0.373722 | −113.928 | 0.367874 | −404.722 | 0.427883 |
| **L3@L1n** | **0** | 零方差不检验 | **0** | 零方差不检验 | **0** | 零方差不检验 |
| **L3@L1m** | **0** | 零方差不检验 | **0** | 零方差不检验 | **0** | 零方差不检验 |
| 交互 | 0 | 零方差不检验 | 0 | 零方差不检验 | 0 | 零方差不检验 |

`nest_overflow_total` 四档 12 次运行恒 0。
层 1 主效应在 3 seed 上 p ≈ 0.37~0.43，**未达统计显著**；
df=2 效能极低，不得写成「无差异」。

### 7.3 层 3 为何全零：终点巢分配签名诊断

`metrics.json` 的 `end_nests_signature`（逐周期 `uav>巢` 串）显示：
逐 seed 逐位 `nn ≡ nm`、`mn ≡ mm`，例如 seed 7 的 `nn/nm` 都是
`0>2,4>0,5>1|0>2,1>1,3>0|0>2,3>0|`；`mn/mm` 都是
`0>1,4>2,5>0|0>1,2>2,3>0|2>2,3>0|`。
`cost_polish_n_mcf_resolve` 四档相同且 overflow 恒 0 ⇒ 层 3 的
`_choose_end_nests` **确实被调用**，但本场景（3 机巢、容量充足）
**没有终点巢容量竞争**，「最近合法巢」与 MCF 返回同一分配，
层 3 因子因此完全退化。

这是**场景限制**，不是开关失效：四档生效配置已逐档核验，
`test_p4_end_nest_phase.py` 也证明 `nearest` 与 `mcf` 确实分流到
不同实现。结论只能写成「**当前场景 / 机巢规模下** MCF 与最近合法巢
等价」，**不得写成「MCF 无用」**，也不得外推到容量竞争场景。

### 7.4 论文可写 / 不可写

可写：① 生产场景无容量竞争时，层 3 的 MCF 与最近合法巢投影给出
同一分配，交付解逐位相同；② 层 1 策略改变整条 DE 搜索轨迹，
其端到端效应在 3 seed 下未达统计显著；③ 两策略都满足容量约束
（overflow 恒 0），差异若存在只体现在返航代价。

不可写：「层 3 MCF 的独立收益」「可行率提升」「MCF 无用 /
可删」「协同效应」「无差异」。正式档结论以 §7.5 为准。

### 7.5 正式档

（正式档 3 seed × 4 档串行跑批中，结果待回填。）

### 7.6 后续待办（不属本实验）

若要回答「容量竞争下 MCF 解决什么问题」，须另设**容量压力场景**
（减小机巢泊位或提高并发架次）并重新核对铁律口径，
本实验未擅自改动场景；该项单列，不在本实验结论内。
