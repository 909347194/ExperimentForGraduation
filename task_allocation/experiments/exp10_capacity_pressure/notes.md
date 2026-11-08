# exp10_capacity_pressure — 容量压力场景 × 终点机巢层 1 / 层 3 对照

> 定稿 `docs/stage2_final_scheme_2026-11-06.md` §11.11「后续待办」第 1 项。
> 本实验自持 config/build/paths/metrics/plot，不建 `_shared/`、不抽公共包；
> **不改任何生产默认值**，场景差异全部由实验层显式覆盖。

## 1. 为什么要做

exp09 的基准场景是 3 机巢 × 泊位 2 = 6 个泊位、机队 6 架（初始恰好满载）。
冒烟 / mid 档层 3 主效应与交互项**全 0**（`nn≡nm`、`mn≡mm`），
正式档只在 seed 11、层 1 用投影时出现一次分叉。
`end_nests_signature` 逐位相同 + MCF 重解计数相同证明开关确实生效，
所以退化原因是**场景缺少终点巢容量竞争**：DE 收敛到的路由让每架机回自己
起飞的巢，逐机「最近合法巢」投影与联合 MCF 给出同一分配。

本实验制造压力，回答「有竞争时层 3 的 MCF 解决什么问题」。
判据沿用定稿原文：**终点巢分配签名是否分叉 + 返航段同 seed 配对差**，
铁律不变（完成率=1 / overflow=0 / n_hard=0 / eliminating=0）。

## 2. 场景因子（只覆盖 `problem.*`）

| 场景档 | 机巢 × 泊位 | 总泊位 | 机队 | 初始空位 | 到达流 | 说明 |
|---|---|---|---|---|---|---|
| `loose` | 3 × 3 | 9 | 6 | 3 | 标准 | 竞争最弱，负对照 |
| `baseline` | 3 × 2 | 6 | 6 | 0 | 标准 | = exp09 场景，锚点 |
| `tight` | 3 × 1 | 3 | 3 | 0 | 标准 | 每巢仅 1 位，跨巢降落必须等量让位 |
| `burst` | 3 × 2 | 6 | 6 | 0 | 5+5 → **10+10** | 并发压力：活跃周期与末态同时返航次数增多 |

**初始不超容是前置条件**：`n_uavs <= n_nests × nest_capacity`，
否则 `check_nest_capacity` 在 t=0 就报 overflow、铁律失效、归因失真。
`run.py` 在跑批前做硬拒绝（已用 `problem.n_uavs=8` 反例验证）。

两个压力旋钮的**实测发现**：单纯把泊位压到 1 必须同时把机队减到 3，
结果可行分配空间反而缩小（冒烟四档完全同解，见 §7）；因此正式档以
`burst`（定稿并列的另一条路径「提高并发架次」）为主压力档，
`tight` 只作结构性对照。

注意不同场景的**任务集、可行域与机队规模都不同**，`burst` 还改变了总任务数
⇒ 跨场景只允许描述性对照，**禁止**把两个场景的指标做同 seed 配对 t 检验，
也禁止写成「容量压力带来 X 收益」或与 exp08/exp09 逐位复现。

## 3. 2×2 四档（与 exp09 完全一致，便于对照）

|  | 层 3 投影 `n` | 层 3 MCF `m` |
|---|---|---|
| **层 1 投影 `n`** | `nn` | `nm` |
| **层 1 MCF `m`** | `mn` | `mm`（生产口径） |

- 标签第 1 位 = 解码期层 1（`end_nest_l1_mode`），第 2 位 = 交付期层 3（`end_nest_l3_mode`）。
- `n` = 最近合法巢确定性投影；`m` = **给定路径下的条件最优**（非全局最优）MCF。
- 不用 `projection_only` 做对照：它是复合档，同时剥掉成本合并、NN 航程裁决与终点巢 MCF，归因不纯。
- 其余字段逐位不动，用 `dataclasses.replace` 锁定；配置纯度逐档核验。

## 4. 指标与统计

- 配对质量：`final_fitness_sum`、`total_path_m`、`total_return_leg_m`、
  `nest_overflow_total`、`on_time_rate_final`、`total_lateness_s`、
  `n_hard_violations_total`、`completion_rate_final`。
- 过程量（不进 t 检验）：`cost_polish_n_mcf_resolve`、L3a / L3b 三本账、
  `cost_polish_n_accepted`、`l3b_ungated_monotone_relocate`。
- 效应：`main_L1_at_L3n` / `main_L1_at_L3m` / `main_L3_at_L1n` /
  `main_L3_at_L1m` / `interaction`，公式 `(f_nn − f_nm) − (f_mn − f_mm)`。
- p 值：环境无 scipy，用正则化不完全 β 精确计算 Student t 双尾；
  零方差标 `zero_variance_no_test`，不冒充「不显著」。
- 单场景 3 seed ⇒ df=2 效能极低；p 不显著如实声明，**也不得写成「无差异」**。

## 5. 口径红线

1. 层 1 作用于每个候选个体的解码 ⇒ 改变**整条 DE 搜索轨迹**，层 1 主效应是端到端效应，不是「同一解上换巢」的局部收益。
2. 层 3 只在 `post_cost_polish` / `post_polish` 真正被调用的周期生效，零精修周期两档相同（算子可得性差异）；交互项**禁写协同效应 / 独立收益**。
3. 层 3 改善若全部落在返航段、去程不变，只能写成「给定路径下联合最优 vs 逐机最近合法巢投影」的**纯成本差异**，不是可行性修复、不是可行率提升。
4. `Δf = 0.1·Δ航程` 是线性缩放，f 与航程的 t / p 相同，**不是两条独立证据**，禁写「双双验证」。
5. 冒烟 / mid 结论不可外推正式档（exp09 已实测：mid 层 3 全零被正式档推翻）。
6. **禁写**：独立收益、协同效应、可行率提升、MCF 无用 / 可删、容量压力带来收益、跨场景配对检验。
7. 完成率 / 恒等式 / eliminating 为实验层硬断言；`independent_audit` 的几何重建重评仍属技术债（`RollingHorizon` 未暴露审计钩子）。

## 6. 目录与复现

```
exp10_capacity_pressure/
    config.yaml / config.py / build.py / paths.py / metrics.py / run.py
    plot/compare.py
    results/<scenario>/<preset>/{<mode>/seed_<s>/metrics.json,run_meta.json,
                                 summary.json, summary.csv, figures/}
```

```bash
# 诊断（四个场景档各跑冒烟 1 seed，严格串行）
for s in loose baseline tight burst; do
  .venv/Scripts/python.exe -u task_allocation/experiments/exp10_capacity_pressure/run.py \
    --scenario "$s" --preset smoke --seeds 7
done
# 冒烟 → mid → 正式档，严格串行（正式档单独跑批）
... run.py --scenario burst --preset smoke  --seeds 7
... run.py --scenario burst --preset mid    --seeds 7,11,23
... run.py --scenario burst --preset formal --seeds 7,11,23
# 事后单独渲图（跑批阶段不渲图）
... plot/compare.py --scenario burst --profile formal
```

`--set` 优先级高于场景档，可写全嵌套路径微调；改动会进配置指纹。
`--preset formal` 有冒烟参数防呆。

## 7. 结果

### 7.1 场景诊断扫描（冒烟 1 seed，`DIAG_RC=0`）

四个场景档严格串行，各跑 1 seed × 4 档，全部 `RC=0`：
配置纯度 True（差异字段恒 2 = 两个分层开关）、铁律全过、
`nest_overflow_total=0`、完成率 1、硬违约 0、按时率 1（`burst` 的 `mn/mm` 为 0.964）。

| 场景 | 层 1 主效应 f（L1@L3n = L1@L3m） | 层 3 主效应 / 交互 | 交付期 MCF 重解次数 |
|---|---|---|---|
| `loose` | +201.258 | 全 0 | `nn/nm` 2、`mn/mm` 0 |
| `baseline` | −60.994（= exp09 冒烟，交叉复现） | 全 0 | `nn/nm/mn/mm` 均 2 |
| `tight` | **0（四档完全同解，f=1772.439）** | 全 0 | 四档均 2 |
| `burst` | −44.194 | 全 0 | **四档均 0** |

**判读**：① `baseline` 的层 1 主效应与 exp09 冒烟档 −60.993861 逐位相同 ⇒
场景档 `{}` 确实是干净锚点；② `tight` 四档完全同解，证实「泊位压到 1
且机队减到 3」缩小了可行分配空间，**不是**竞争增强；
③ `burst` 四档 MCF 重解次数全 0 ⇒ 冒烟规模下 `post_cost_polish`
根本没有被调用，层 3 天然无动作，这是**算子可得性**而非「容量无竞争」。
按铁律，**冒烟档结论不得外推正式档**（exp09 的 mid 层 3 全零已被正式档
seed 11 分叉推翻），因此本节只作场景合法性与设计自检，不作结论。

### 7.2 mid 档（`burst`，3 seed × 4 档，`MID_RC=0`）

12 次全 PASS、配置纯度 True（差异字段 2）、铁律全过、配对 `[7,11,23]`、
`nest_overflow_total` 恒 0、完成率恒 1、硬违约 0；8 min。

| 效应 | f | 航程 (m) | 返航段 (m) |
|---|---|---|---|
| L1@L3n | −16.110142 | −157.439 | −626.907667 |
| L1@L3m | −16.110142 | −157.439 | −626.907667 |
| L3@L1n / L3@L1m / 交互 | **0（零方差）** | 0 | 0 |

层 3 在 3 seed 下逐位无差异（`zero_variance_no_test`），`nn≡nm`、
`mn≡mm`；层 1 两档主效应完全相同 ⇒ 交互恒 0。
`cost_polish_n_mcf_resolve` 在 mid 档并非全 0（`nn/nm` seed 23 = 2、
`mn/mm` seed 11/23 = 1）⇒ **交付期 MCF 确实被调用过，但仍与最近合法巢
投影同解**，这不是「算子没跑」，只是这些周期没有值得改派的分配差异。

**口径辨析**：层 1 主效应上 `Δf = −16.110142` 而 `0.1·Δ航程 = −15.7439`
并不相等，差额来自迟到 / 按时变化 ⇒ 层 1 改变整条搜索轨迹，
f 与航程**不是**简单线性缩放；反过来，层 3 零差异时 f / 航程 / 返航段
三者同 0，也不构成「三条独立证据」。禁写「独立收益」。

**按铁律不作外推**（exp09 的 mid 层 3 全零已被正式档 seed 11 推翻），
正式档单独跑批。

### 7.3 正式档

（跑批后回填：主效应与交互项、签名分叉位置、返航段与去程配对差、
三本账、与基准场景及 exp09 正式档的描述性对照、结论。）
