# exp06–exp11 当前代码口径重跑总报告

> 2026-11-20。因 README §9 记录 11-10 两处底座变更（DEM 采样/网格、RNG 单通道）
> 影响所有 guangzhou + DMDESolver 正式档的绝对值，本轮在**当前代码**上对 exp06–exp11
> 逐一重跑正式档，严格串行、求解期不出图。所有实验 codes/默认值零改动，仅 exp06
> 的 `run.py` 增加断点跳过保护（已完成格默认跳过、`--force` 强制）。
> 各实验 `notes.md` 已追加「当前代码口径重跑」章节，本节数字取代各实验旧档绝对值。

## 一、跑批总览

| 实验 | 规模 | 退出码 | 墙钟 | 铁律/纯度 |
|---|---|---|---|---|
| exp06 联合域 | 10 格 × 24 周期（4 积压格） | 0 | ~10h54m | 全格产出、无硬违约 |
| exp07 采纳判据 + L3b | 2 组 × 3 seed × 2 档 = 12 run | 0 | 33m51s | 12 次全 PASS、纯度 1 字段 |
| exp08 收益拆分 | 2×2 × 3 seed = 12 run | 0 | 34m30s | 全 PASS、纯度 3 字段 |
| exp09 终点巢层 | 2×2 × 3 seed = 12 run | 0 | 36m | 全 PASS、overflow 恒 0 |
| exp10 容量压力(burst) | 2×2 × 3 seed = 12 run | 0 | 49m24s | 全 PASS、overflow 恒 0 |
| exp11 异巢自由度 | 3 臂 × 4 梯度 × 7 seed = 84 run | 0 | 46m | 84 run 全 PASS、纯度 2 字段 |

实测发现：瓶颈是场景/代价矩阵一次性构建（20 任务 100 代约 207 s，其中 3 代也要
192 s），代数不是主要成本——故正式档预算（pop50/gen200）可行，未降档。

## 二、关键结论（当前代码口径，论文可用口径）

### exp06 联合域：标定结论在压力叠加下成立，λ_cost 规则翻盘
- **H1（可完成一定被完成）全 10 格成立**：淘汰=0、废飞=0、不可达=0。积压格
  λ=25/40 完成率 0.892/0.616 的根因是**产能饿死**（跑满 24 名额/周期，未完成
  留在池内），非适应度用成本换覆盖。
- **H2（供给不足压缩空转）成立**：λ=2.5/5/10 时 n_sel/cap≈0.67–0.89<1。
- **λ_cost 配对全 5 档「安全」**：λ=0.1 相对 1e-4 完成率不降、淘汰不增，积压格
  反而完成率更高（arr25 +0.065、arr40 +0.009）、航程更低（−169/−245 km）。
  ⇒ **推翻旧半截档「λ=0.1 更差」结论**，较高 λ_cost 可作积压域生产默认候选。
  代价：arr40 按时率由 0.771 降到 0.615，须并列报告。单 seed，仅描述性。

### exp07 交付后判据：path≡f（别名预期），L3b 方向一致但不显著
- path/f 三 seed 六指标逐位相同（代码落地后 path 为兼容别名，零分歧是预期）。
- L3b 关→开航程 42.89/41.28/39.01 → 42.72/40.14/38.70 km，均值 −540.7 m，
  **p=0.216 不显著**（df=2）；on 档动作 [2,4,6]、off 全 0，开关确被激活。

### exp08 收益拆分：显著性强度下降，不可再写「G@B1 唯一显著」
- 生产档 g1b1 四 seed f/航程最低、按时率最优。
- G@B1 = −55.04（**p=0.086398**），交互 −21.58（p=0.509）。
- **旧档 G@B1 p=0.0337 显著，当前口径不再显著**；3 seed 下所有效应 p>0.08。
  只可写「方向一致、生产档最低」，不可写显著或「独立收益」「协同效应」。

### exp09 终点巢层：分叉点从 seed11 移到 seed23，且机制不同
- mn≡mm 逐 seed 逐位同；层3只在 L1=投影档分叉，当前为 **seed23**（f −122.4，
  旧档是 seed11 −241.5）。L3@L1n=−40.81、交互 +21.40（p=0.423 不显著）。
- seed23 的 `end_nests_signature` 逐位 nn≡nm（终点分配未变）却在 f/返航段分叉
  ⇒ 是端到端搜索轨迹效应，**不能照搬旧档「纯成本改派、去程不变」归因**，须再审计。

### exp10 容量压力：burst 场景层3完全退化，与 exp09 不再共享配对差
- nn≡nm、mn≡mm 逐 seed 全同，层3主效应/交互恒 0（zero_variance_no_test）；
  层1 +57.84（p=0.393 不显著）。overflow 恒 0、完成率恒 1。
- **旧档「exp10 与 exp09 配对差逐位相同」在当前口径下不成立**——两场景任务集
  不同（40 vs 30），禁跨场景配对检验，禁写「两场景双双验证」。

### exp11 异巢自由度：证据变强，四档全显著
- 返原巢−MCF 总航程差 **T2/T4/T6/T10 全档显著**（+801/+1122/+1517/+2504 m，
  p=0.048/0.010/0.013/0.003；旧档 T2 p=0.106 不显著）。占 MCF 航程 1.64%–2.68%。
- 累计额外返航段 0.35→1.10→2.15→3.00 km；每航次返航段 home−MCF T10 +2995 m
  （p=0.035），T2/T4/T6 方向一致但不显著。
- nearest−MCF 四档全不显著（符号混杂）⇒ **仍不得写「MCF 优于就近贪心」**；
  主张限定为「允许异巢+联合分配 vs 必须返原巢」。
- 同解换终点反事实保留率 20.1%/10.4%/11.7%/9.6%（返原巢臂吸收 80–90%）。
- 代价：按时率 home 1.000 vs MCF 0.979–0.986。

## 三、跨实验一致性与红线

1. **生产解三处独立复现**：exp07 `path`/exp08 `g1b0`/exp09 `mn` 逐位同
   （L3b 关/几何关/层1MCF）；exp07 `on`/exp08 `g1b1`/exp09 `mm` 逐位同
   （L3b 开/生产档/层1+层3 MCF）。三个独立实验在同一坐标系复现生产口径。
2. **效应量普遍缩小或显著性下降**（exp08 G@B1、exp09 层3、exp10 层3），
   根源是 DEM/RNG 变更改变搜索轨迹；**旧档绝对值与显著性一律不进主文**，
   以本轮 11-20 档为准（各 notes 已标注取代关系）。
3. **p 纪律**：df=2（3 seed）或 df=6（7 seed）均小样本；不显著只写「不显著」，
   不得写「无差异/等价」；零方差标 `zero_variance_no_test`；配对差不写「独立收益」。
4. **f 与航程非独立证据**：按时率分化后 `Δf ≠ 0.1·Δ航程`，禁写「双双验证」。
5. **不可跨场景横比**：exp09(30 任务)/exp10 burst(40 任务)/exp11(独立预算 24×60)
   任务集与预算不同，禁跨场景配对 t 检验与逐位复现断言。
6. **技术债**：`independent_audit` 几何重建重评未接入（RollingHorizon 未暴露钩子）；
   exp11 本轮未复跑前缀一致性审计（旧档一次性脚本曾测 63 格 39 格不等）。
7. 墙钟只作观测量，不跨实验横比；求解期不出图；所有结果目录被 gitignore。

## 四、复现入口（仓库根，`.venv/bin/python`）

```bash
# exp06（断点保护：已完成格跳过；--force 强制重跑）
python task_allocation/experiments/exp06_joint_regime/run.py
# exp07（两组严格串行）
python task_allocation/experiments/exp07_polish_accept/run.py --profile formal --seeds 7 11 23 \
  --set tasks.n_tasks=20 --set solver.population=50 --set solver.max_iter=200 \
  --set rolling.n_cycles=60 --set 'rolling.arrivals.counts=[5,5]'
python task_allocation/experiments/exp07_polish_accept/compare_l3b.py --preset formal --seeds 7 11 23
# exp08 / exp09
python task_allocation/experiments/exp08_gain_decomposition/run.py --preset formal --seeds 7,11,23
python task_allocation/experiments/exp09_mcf_layers/run.py --preset formal --seeds 7,11,23
# exp10 burst
python task_allocation/experiments/exp10_capacity_pressure/run.py --scenario burst --preset formal --seeds 7,11,23
# exp11（84 run）+ 出表
python task_allocation/experiments/exp11_multi_depot/run.py --preset formal \
  --seeds 7,11,23,5,13,17,19 --tiers 2,4,6,10
python task_allocation/experiments/exp11_multi_depot/build_tables.py --preset formal
```
