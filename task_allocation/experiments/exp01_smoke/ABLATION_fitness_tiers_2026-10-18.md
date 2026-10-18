# 适应度四层准字典序重构 —— 消融实验记录（2026-10-18）

## 1. 动机与设计

**用户裁定口径**：违反物理航程约束 = 严重违规（硬）；迟到可以接受但最好不迟到（软）。

**旧口径缺陷**（探针实测，seed=42 前 3 周期 22423 次评估）：
- 物理违约 0 次（修复链评估前已 100% 消除），迟到 22419 次（99.98%）——「可行域窄」窄在**零迟到**，不在物理不可行；
- penalty 桶 100% 是迟到秒数（中位 4063 s/次）；misc 违约 ×1e3×wv=0.01 只折 10 单位，而单任务收益 m·R≈2.7e5——「纸面可行、执行弹回」的幽灵 F1 无人拦截（修 uav_9 前 16 次弹回属此类）；
- W=600 折减使 7/12 个迟到任务奖励归零（均值保留 27.8%）——与「迟到可接受」冲突。

**新适应度**（`problem.py::scalar_fitness`，lexicographic=True 且 w_violation 给定时）：

```
f = W_hard·n_hard + w_exc·excess_m − m·F1_eff + w_late·L_tot + λ·path + w_time·F3
    T0 硬(物理)        T1 交付收益       T2 迟到(软)    T3 航程/负载
```

- `W_hard = κ·m·R_pool`（κ=10，自动推导；显式值可锁，0=关闭）⇒ 任何物理违约压倒任意交付收益；
- `w_exc = 1/m`（自动）⇒ 超程米数仅在不可行解间做层内排序；
- `m = 1 + S_soft/r_min`（次目标上界只含软项）；
- `F1_eff` 含迟到折减 `eff=R·max(0,1−L/W)`，W=1800；
- `is_feasible` 改为只认硬违约（与执行闸门 `range_uav_*`/`tour_infeasible_*` 弹回、`tw_task` 不弹回的标签口径一致）。

## 2. 消融结果（exp01，seed=42，60 任务，max_iter=100）

| 指标 | A0 旧口径<br>(W=600) | A1 四层重构<br>(W=600) | A2 +W=1800<br>(剔除关) | A3 +W=1800<br>(剔除开) |
|---|---|---|---|---|
| 完成数 | 60/60 | 60/60 | 60/60 | 60/60 |
| 废飞（物理不可行） | 0 | 0 | 0 | 0 |
| 淘汰 | 0 | 0 | 0 | 0 |
| 不可达 | 0 | 0 | 0 | 0 |
| 按时率 | 80.0% | 80.0% | **81.7%** | 78.3% |
| 迟到交付数（均深） | 12（691s） | 12（691s） | 11（741s） | 13（761s） |
| **零奖励交付** | **7** | 7 | **0** | 0 |
| 有效完成率 | 88.3% | 88.3% | **100.0%** | 100.0% |
| **奖励产出率** | 88.0% | 88.0% | **94.4%** | 91.1% |
| 总奖励 | 291.9 | 291.9 | **313.1** | 302.1 |
| 总航程 | 1,772,749 m | 1,772,749 m | **1,734,510 m** | 1,751,632 m |
| 求解耗时 | 316 s | 484 s | 486 s | 470 s |

（A0 = 重构前 results/ 存档口径；A1 与 A0 逐位一致——T0 层在无硬违约时行为中性，同时证明重构零回归。）

## 3. 结论

1. **A1（纯重构）= A0**：四层化在不触发 T0 时完全不改变搜索轨迹——与探针预言一致（22423 次评估 0 硬违约）。重构是语义/保险丝修复，不是行为变更。
2. **A2（W=600→1800）全面占优**：零奖励交付 7→0、奖励产出率 88.0%→94.4%（+6.4pp）、有效完成率 88.3%→100%、按时率 +1.7pp、总航程 −2.2%，完成/废飞/淘汰不变。**已定为 exp01 生产口径并同步 results/（含图表重出）。**
3. **A3（零奖励剔除开启）净亏**：产出率 94.4%→91.1%、按时率 81.7%→78.3%。机理：剔除在**搜索期**每个候选都生效，把「硬塞必然迟发任务」的候选救回可交付形态，搜索偏向「周期 1 多塞一个（20 vs 19）但迟到更深（6 个/均~1000s vs 2 个）」的盆地。W=1800 已单独消灭靶病（零奖励交付=0），剔除只亏不赚。**exp01 默认关闭**（`prune_zero_reward: false`）；实现与测试保留，供 |T|≫K 压力场（exp02）评估。

## 4. 落地清单

- `methods/muas/constraints/evaluator.py`：hard/soft 分桶（`add_hard`/`n_hard`/`hard_violation`/`soft_violation`/`range_excess`），`is_feasible` 只认硬违约，`as_details`/`violation_breakdown` 透出分解；
- `methods/muas/problem.py`：`scalar_fitness` 四层公式；`w_hard_violation`/`w_range_excess`/`hard_violation_kappa` 配置；`hard_weight()`/`range_excess_weight()`/`hard_stats()`/`soft_violation()`；`_secondary_upper_bound` 只含软项；`evaluate` 写 `meta["fitness_weights"]` 权重快照（治 auto big_m 漂移）；
- `methods/muas/solvers/muas_stage.py`：`_prune_zero_reward`（零奖励剔除）+ `_task_latest`；`build_solution` 在 `_reorder_for_deadlines` 后接入；
- `experiments/exp01_smoke/config.yaml`：`late_reward_decay_seconds` 600→1800、`late_tolerance_seconds` 同步、`w_hard_violation`/`w_range_excess`/`hard_violation_kappa`/`prune_zero_reward` 旋钮及注释；
- `experiments/exp01_smoke/config.py`：新字段映射；`run_chunked.py`：`--set dotted.path=value` 覆盖（进存档指纹）；
- 测试：`tests/test_fitness_tiers.py` 新增 10 条（T0 支配 T1、excess tie-break、T1 支配次级、T2 软计价、W_hard 公式、权重快照重算、零奖励剔除四态）；更新 3 条旧测试到新语义；全量 229 passed。

## 5. 遗留

- `report.tex`（11 页实验报告）中的汇总数字仍是 A0 口径，需按 A2 重写（工作量：改汇总表 + 逐周期表 + 适应度设计小节 + 消融表）；
- exp02（|T|≫K）场待验证：T0 保险丝与零奖励剔除在违约密集区的行为；
- 收敛曲线早平问题**不因本修改改变**（尺度分离的必然地貌），补救仍是解码后局部搜索 polish。
