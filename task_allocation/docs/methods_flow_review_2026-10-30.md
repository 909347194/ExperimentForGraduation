# 方法层流程逻辑清晰度审查

日期：2026-10-30　｜　范围：`task_allocation/methods/`（rolling / selection / muas）
方法：静态追踪主干数据流（不作任何改动），逐段核对「注释契约」与「结构实际行为」是否一致

---

## 0. 结论一句话

> **主干逻辑自洽、分层清楚，「滚动 → 选择 → 装配 → 进化/修复 → 记账」五段一路到底没有断点。
> 但流程的清晰度是「注释在维护」，不是「结构在维护」——4 个接缝处的执行语义无法从任一处代码单独读出来。**

分层是对的，问题是**层内契约是隐式的**。

---

## 1. 主干数据流（已核实的完整链路）

```
TaskPool ──► RollingHorizon.run()                      horizon.py:662
             └─ run_cycle()                            horizon.py:388
                ① begin_cycle   : remain ∪ new ∪ release      :400
                ② 出池三闸       : 静态几何不可达 / 事件 / 服务停滞  :415, :422, :513
                ③ 阶段一 选择     run_selection → T_t^sel       :518
                ④ 阶段二 MUAS    stage_runner  → AllocationSolution
                   └─ DMDE 每代: inverse_phi → build_solution → 适应度 → lamarckian_reencode
                ⑤ 执行记账       _advance_execution / _fill_occupancy / _inject_faults
                ⑥ 收敛提前停     _converged                    :684
```

这条链路**每一步都有明确的输入输出类型**，且退化情形（无任务 / 无可用机 / 选择为空）都显式短路并记账，不靠异常兜。
这一点做得比多数同类型代码好。

---

## 2. 四个接缝（逻辑正确，但流程契约隐式）

### S1　`SelectionConfig.forced_task_ids`：三处口径不一致 ★★

| 层 | 是否存在 | 是否生效 |
|---|---|---|
| 数据类字段 | ✅ `pipeline.py:89` | ❌ 运行时被覆盖 |
| yaml 配置面 | ❌ `selection_config_from_dict` 完全不解析（`horizon.py:988-991`） | — |
| 运行时实际值 | ✅ `horizon.py:482` 每周期从 `replan_ctx.urgent_task_ids ∪ prior_fault_released` 重建 | ✅ |

**病灶**：用 *代码* 构造 `SelectionConfig(forced_task_ids={...})` 完全合法、看起来能用，但一旦交给 `RollingHorizon`，
`horizon.py:506` 用 `forced_task_ids=forced_ids` 把它整体覆盖掉，静默丢弃。

- 语义本身是**对的**：滚动场景下强制集应随时间逐周期重算，不该被静态配置钉死。
- 但**没有任何注释说明"此字段会被丢弃"**。`pipeline.py:138-148` 那段长注释解释的是"为什么要用局部副本"，
  恰恰没解释"入口值为什么进不来"。
- 我自己在 `experiments/ab_m21_patrol_writeback/run.py` 上踩过这个坑：设了 `forced_task_ids`，覆盖度纹丝不动（0.30）。

**这不是孤例，是复发性缺陷形态**：同一文件里已有三处同类记录——
Fix① `age_gamma`（`:953`）、Fix② `anti_starvation.enabled`（`:980`）、Fix②b 透传（`:501`），
全是"配置面接了线但中间漏传"。`forced_task_ids` 是第四例，只是这次是**反向**（字段存在但被有意忽略）。

### S2　M2-1 段内回写：五道闸门串联，前四道全部静默 ★★★（最需要改的）

要真正改写巡游序，需连续通过：

| # | 闸门 | 位置 | 失败结果 | 有信号？ |
|---|---|---|---|---|
| 1 | `cfg.lamarckian`（**借 M1 的门**） | `dmde_solver.py:228` | 整条回写不调用 | 无 |
| 2 | `lamarckian_patrol_writeback` | `evaluator.py:220` | 退回 M1 | 无 |
| 3 | `model_type == "srp"` | `evaluator.py:221` | 退回 M1 | 无 |
| 4 | 硬违约过滤 `n_hard_src == 0` | `evaluator.py:231` | 返回 `None` | 无 |
| 5 | `_segment_matches` 逐段集合相等 | `evaluator.py:354` / `:394` | 该段退回首任务 | 无 |
| — | 异常兜底 `except Exception` | `evaluator.py:239` | 退回 M1 | ✅ stderr |

**实测代价**：mount 场景 `seg_match = 0/660`，即闸门 5 全挂，而控制台**什么都没打印**。
这是上一轮 A/B 出现 Δ=0 时排查成本高的直接根因——我被迫用 monkeypatch 插计数器才定位到。

两个额外观察：

- **闸门 1 是"借用"来的**：M2-1 没有自己的调用门，复用 M1 的 `if cfg.lamarckian and ...`。
  因此 `lamarckian=False` ⇒ M2-1 永不触发，**M2-1 无法脱离 M1 单独开启**。
  语义合理（M2-1 是同一次回写调用的扩展），但"关 A 会连带关 B"这件事只在 `evaluator.py:217-219` 的注释里说，
  调用方 `dmde_solver` 里完全看不出。
- **闸门 5 的判据偏严**：`_segment_matches` 要求「段基因任务集 **==** 修复解航线集」。
  而修复链里 `_consolidate` / `_repair_range` 动不动就会剔除或改派一两个任务，
  于是**只要有一个任务被剔除，整段的巡游序回写就作废**。这就是 0/660 的机制解释。
  它是一种**保守正确但触发概率很低**的设计。

### S3　`n_sel` 双闸门：一个意图要动两处配置 ★★

`pipeline.py:181-206` 的实际决定式：

```
marginal_quota = max_selected  or  ceil(max_ratio × K)
n_sel          = effective_cap(marginal_quota, capability_cap, mode)
```

`marginal`（名额）与 `capability`（机队能力）**各自独立卡一道**，取 `intersect`（默认）= 取小。
想要「这一周期尽量多装」，必须**同时**放松两个旋钮——我在 `run.py:220-221` 就被迫这么写：

```python
sel_cfg = SelectionConfig(
    marginal=MarginalConfig(max_selected=len(tasks)),
    capability=CapabilityScaleConfig(enabled=False),
)
```

讽刺的是，`pipeline.py:190-206` 有一段很长的注释专门解释 **n_cand 已经与 cap 解耦**——
这段注释的存在恰恰反衬出 `n_sel` 仍是双卡，而这一点没有被平铺直叙地写出来。
读者容易以为"两处都已解耦"。

### S4　修复链的顺序敏感：靠注释维持，不靠结构 ★★

`decode_repair.py:80-150` 的 `build_solution` 实际执行序列：

```
for _ in range(6):                       # 联合收敛不动点
    _consolidate → [_prune] → _repair_range → _choose_end_nests
_load_coverage_fixed_point | _complete_coverage        # 覆盖
_reorder_for_deadlines                                  # 纯调序
_repair_sequence                                        # 时序
[_prune_zero_reward]                                    # 经济剔除（默认恒关）
_repair_range                                           # ①
_choose_end_nests                                       # ②
_repair_range                                           # ③ 交付前最后闸
[_load_coverage_fixed_point]                            # coverage_first 时再装一轮
覆盖门禁 终态挂点
```

问题集中度：

- `_repair_range` 调 **3 次**（含循环内最多 6 次），`_choose_end_nests` 最少 **2 次**，
  `_load_coverage_fixed_point` 最多 **2 次**。同一算子重复出现本身说明约束是耦合的、需要迭代——合理，
  但**每一步为什么必须在某一步之前/之后，全部写在行内注释里**（`:70-72`、`:117-120`、`:129-131`、`:136-138`）。
  插入一行新算子的成本极低，后果极难发现。
- 有一扇**看起来开着实际默认上锁的门**：`economic_prune`（`:80-84`）依赖
  `not getattr(self.problem.config, "lexicographic", True)`，而 `lexicographic` 默认 `True`
  ⇒ **`_prune` 默认永不执行**。这是有意的（字典序下剔除与目标冲突），但变量名 `economic_prune`
  第一次读会以为它是活的。

---

## 3. 一个可核验的附加症状

`run_cycle` 的分步编号：`:399` 写 `1)`，`:410` 写 `2.5)`，`:421` 写 `2)`，
`:452` 写 `4)`，`:466` 写 `5)`，`:480` 写 `6)`，`:509` 写 `2.6)`，`:555` 写 `7)`。

即顺序为 **1 → 2.5 → 2 → 4 → 5 → 6 → 2.6 → 7**：缺 `3`，且 `2.5/2.6` 插在错位处。
无害，但它是「这段流程经历过多次增补、编号体系没跟着重构」的直接物证——与上面四个接缝同源。

---

## 4. 处置建议（按投入产出排序）

| 优先级 | 动作 | 改动量 | 是否改变行为 |
|---|---|---|---|
| **P0** | **让 M2-1 的降级可观测**：在 evaluator 上加一只写回诊断计数器（含各闸门的失败次数），默认关闭、开关打开才计。设为默认关，开关打开才计。 | ~30 行 | 否（bit-exact） |
| **P1** | `SelectionConfig` 加 `forced_task_ids` 的文档说明 + 显式告警：构造值时若非空且进入滚动层，记一条 warning，说明"滚动层按周期重建，此值将被忽略"。 | ~10 行 | 否 |
| **P1** | 在 `pipeline.py:181` 的 `n_sel` 决定处补一句直白注释：n_sel 是**双卡**（名额 ∩ 能力），想全放需同时动两个旋钮。 | 3 行注释 | 否 |
| **P2** | 把 `run_cycle` 的 `1) 2.5) 2) 4) 5) 6) 2.6) 7)` 编号理顺成连续整数。 | 纯注释 | 否 |
| **P2** | 修复链提取成显式 stage 列表（数据驱动）+ 一份顺序理由表，替代散落的行内注释。 | 大重构，**不建议现在做**——收益是长期可维护性，风险是动到 bit-exact 基线 |

> **不建议**为「清晰」而重构 `build_solution`。它是适应度的定义本身，任何顺序变动都会动整条实验基线。
> 把它拆成显式 stage 列表，收益是长期可维护性，但风险是动到 bit-exact 基线。

---

## 5. 与上轮《整洁性审查》的关系

上一轮（[methods_cleanliness_review_2026-10-30.md](methods_cleanliness_review_2026-10-30.md)）做的是**表面整洁**：
死代码、魔法数、常量重复、注释失准——共 6 项，全部已改毕且 bit-exact 验证通过（326 tests + 1080 例差分）。

本轮做的是**流程逻辑**：上面 4 个接缝在上轮的 4 项机器检查里**全部通过**——
没有未使用符号、没有魔法数、没有循环导入、注释也都存在。
换句话说：**整洁性检查抓不到这一类问题。** 这是两个不同层次，需要分开查。

---

## 6. 一句话给你

> 代码**写得懂**，但**跑起来会发生什么**不一定写得懂——差别全在这 4 个接缝里。
> 最该动手的是 S2：给 M2-1 的静默降级装个仪表盘，否则下次再出现 Δ=0 还得靠插桩排查。
