# 方法口径审查：第 3 章 vs《近期思路固定稿》7 条决策（2026-10-23）

> 审查性质：**只读审查**，不改任何代码/文稿。对象 = `origin/master aa88d5a`（277 单测全绿）。
> 审查基准 = 用户 2026-10-23《近期思路固定稿》7 条决策（下称「固定稿」），逐条核对。
> 对照旧审计：`docs/method_audit_vs_refs_2026-10-23.md`、`docs/objective_caliber_two_approaches_archive_2026-10-24.md`（下称 ARCHIVE）、`docs/fitness_path_optimization_diagnosis_2026-10-24.md`。

**一句话总结**：门禁口径（禁 voluntary_skip）已落成唯一主口径、`−m·F1` 彻底退场、路径/迟到/返航口径基本对齐；但**适应度第一比较键里没有覆盖项**（既无字典序 `(-n_delivered, ·)`，也无 `W_cov` 门槛标量）——固定稿决策 2/3/4 的核心要求在 master 上缺失；且**超期出池（expire）机制仍在**、淘汰率仍进论文主表必报列，与决策 1/6/7 存在冲突或模糊。另发现一处**注释-实现不符**（「F1 回归纯完成计数」实为 ΣR 加权）和一处**文稿-代码不符**（README 仍写两套可切换口径）。

**重要背景（须用户仲裁）**：git 本地分支 `backup/local-c06f532-20261024`（commit c06f532，2026-10-23 20:32）**曾经实现过**固定稿要求的「覆盖个数第一键」：`f = … + W_cov·(N − n_delivered)`，且 `W_cov = 1 + 次目标上界`（门槛权重，正是决策 3「W_cov 只需 > 最大可能成本差，不是 2.7e5 big-M」的形式）。该分支**不是 master 的祖先**；master 随后在 `31c8974`（2026-10-24 16:59，"覆盖门禁作为唯一主口径"）按 ARCHIVE §1「完成是约束、成本是目标 ⇒ 多完成不应写进搜索目标」的裁定把覆盖项整体移出 f。**该裁定与固定稿决策 2/3/4 直接冲突**，两份口径文件并存，必须由用户裁定以哪份为准。以下判定均以固定稿为准（任务指定）。

---

## a) 适应度第一键 = 覆盖个数 n_delivered？

**结论：【偏离 P0】搜索适应度中不存在任何覆盖项；实现是「门槛系数标量」骨架但缺了覆盖层。big-M `m·F1` 已彻底退场（符合）；「W_cov > 最大可能成本差」的论证在 master 上无对象（W_cov 不存在）。**

### 证据

1. **第一键缺失**。`task_allocation/methods/muas/problem.py:402-447`（`scalar_fitness`，唯一搜索适应度）：

```python
        n_hard, excess_m = self.hard_stats(solution)
        n_skip = int((solution.meta.get("gate_stats") or {}).get("n_skip", 0))
        skip_pen = self.skip_weight() * n_skip
        late_pen = 0.0 if wv is None else float(wv) * self.soft_violation(solution)
        return float(
            self.hard_weight() * n_hard
            + self.range_excess_weight() * excess_m
            + skip_pen
            + late_pen
            + self.config.lambda_cost * self.path_cost(solution)
            + self.config.w_time * bt
        )
```

   f 中**没有** `n_delivered` / `(N − n_delivered)` / `W_cov` 任何覆盖项。全仓 grep `W_cov|n_delivered|第一比较键` 只命中 `.openclaw/tmp/backup-20261024/`（备份）而无生产代码。DMDE 的选择是纯标量比较（`dmde_solver.py:215` `if child.fitness < population[i].fitness`），亦无字典序 tuple 比较器。

2. **实现形态 = 门槛系数标量（T0/T0.5 支配权重 + 成本/迟到），且覆盖层缺位**。固定稿决策 3 允许「字典序结构 **或** W_cov 门槛系数的标量」——当前连后者都不满足（W_cov 项整体缺失）。决策 2 的 `(-n_delivered, path + w_late·L)` 字典序亦未实现。

3. **`−m·F1` 彻底退场（符合决策 3）**。`fitness_weights.py:26-39`：

```python
    def gate_mode(self, solution=None) -> bool:
        """…
        2026-10-24 起**覆盖门禁为唯一主口径**，故本方法恒返回 ``True``——
        代码中不再存在非门禁（big-M 四层 ``−m·F1``）评价路径。
        …
        固定表述：**搜索用 ``f`` 不含 ``−m·F1``；``F1`` 只作事后指标。**"""
        return True
```

   `problem.py:446` 的 f 中无 `−m·F1` 项；`m` 仅作 `W_hard`/`W_skip` 推导基数（`fitness_weights.py:93-107`）。F1 只作指标——**符合**。

4. **W_cov 取值论证无对象；但 W_skip/W_hard 仍是 big-M 推导**。`fitness_weights.py:60-72`：

```python
    def skip_weight(self) -> float:
        """T0.5 覆盖门禁违约的支配权重 W_skip（默认同 W_hard 推导 κ·m·R_pool）。"""
        if self.config.w_skip_violation is not None:
            return max(0.0, float(self.config.w_skip_violation))
        return self.hard_weight()
```

   `hard_weight = κ·m·R_pool`（`fitness_weights.py:43-58`，κ=10，`config.py:104`），m 自动推导可达 1e5~1e6 量级（ARCHIVE 自述旧 m·F1≈2.7e7）——即固定稿点名反对的「2.7e5 那种 big-M」量级以 W_hard/W_skip 的形式回来了。对 W_hard（硬约束支配）这可辩护（等价否决），但若按决策 3 的精神给覆盖定权（哪怕将来把 W_skip 换成 W_cov 语义），应取「> 最大可能成本差」的门槛量级而非 κ·m·R_pool。**偏离 P1**。

5. **指标 F1 并非「纯个数」而是加权收益和**。`problem.py:232-255` 注释称「F1 回归纯完成计数」，但实现：

```python
        for tid in solution.executed_task_ids:
            t = self.task_by_id.get(tid)
            …
            r = max(t.reward, 0.0)
            …
            total += r
```

   任务收益是 `U(1,10)` 异质随机数（`common/task_scenario.py:280` `rewards = rng.uniform(config.reward_min, config.reward_max, size=n)`；exp01 `config.yaml` `reward_min: 1.0 / reward_max: 10.0`）。因此指标 F1 = **ΣR_j 加权收益**，与注释「纯完成计数、不掺迟到折减」的固定定义只对了一半（迟到折减确实砍了：`problem.py:241-242`）。**注释-实现不符 P1**。

6. **「F1 恒为常数、对解无区分度」的内部论证不成立**（P2）。ARCHIVE §2.2 与 `problem.py` 注释以「合法解中 S 内可装载任务已全部交付 ⇒ F1 恒常数」论证不需要覆盖项；但门禁/`coverage_first` 装载只保证**一阶极大**可行集（`coverage_gate.py:28-33` 自认「一阶不完备…不是全局不可行的证明」），maximal ≠ maximum：不同指派/顺序解出的「最大可装载集」大小可以不同，交付少但便宜的解在 f 中严格占优。这正是固定稿决策 4「门禁必要但不充分，须配合覆盖进第一比较键」预言的缺口，也正是 `muas_stage.py:145-150` 注释记录的 E1E2 现象根因（"可行域被成本压满后丧失装载余量 ⇒ 剩余任务一阶判 shortfall"）。

---

## b) 门禁是否唯一主口径？「门禁 + 覆盖第一键」是否同时成立？W_skip 两层旧口径残留？

**结论：【部分符合】门禁确已唯一化（非门禁 big-M 路径删除、gate_mode≡True）；但「门禁 + 覆盖第一键」二者并未同时成立（第一键缺失，见 a）；W_skip 门禁罚层作为旧「两层口径」的第二层原样保留。**

### 证据

1. **门禁唯一主口径 = 是**。`fitness_weights.py:26-39`（`gate_mode` 恒 True）；`config.py:120` `lexicographic` 字段「历史遗留、已失效…设任何值均不改变行为」；`config.py:131-141` `lexicographic_coverage`「2026-10-24 起恒为 True…保留字段仅为向后兼容 yaml」。exp01 `config.yaml:309-312` `coverage_gate: true / lexicographic_coverage: true / w_skip_violation: null`。生产钩子：`muas_stage.py:1023-1035`——`coverage_gate=True → repair_coverage`（能救必救、skip 构造归零），否则 `audit_coverage` 纯分账。

2. **合法解定义与固定稿决策 1/4 语义一致**。`coverage_gate.py:4-10`：

```
合法解定义（用户 2026-10-22 裁定，与物理硬约束等价）：
    1. 物理硬约束；
    2. 覆盖门禁：不存在「任务 j∈S 未进入任何 tour，且存在空闲机可开班
       或某航次在硬约束下可插入 j」——违者 voluntary_skip，解非法；
       已选但经一阶检验确无任何可行插入的任务允许不交付，记
       capacity_shortfall（能力不足，不算 voluntary_skip）。
```

3. **「门禁 + 覆盖第一键」不成立**：第一键不存在（a 节 P0）。现口径是「门禁 + 无覆盖目标层」，即固定稿决策 5 描述的「门禁开 + 无 F1」构型——该构型下「尽量多做压力弱化」是被预告过的已知缺口。

4. **W_skip 两层旧口径残留 = 有**。旧「两层」= 构造修复（repair）+ W_skip 罚层（audit/防御）。master 保留了完整第二层：`problem.py:438` `skip_pen = self.skip_weight() * n_skip`；`fitness_weights.py:60-72`（默认 `W_skip = κ·m·R_pool`）。其地位自述为防御层（`fitness_weights.py:66-68`：「gate 修复模式下 n_skip 恒 0（构造性归零），本层不咬合，仅 audit 模式/防御路径生效」）。生产中确实不咬合（exp01 `results_gateon_s42/metrics.json`：`n_voluntary_skip=0`、`n_gate_rescued=0`）。若按固定稿把覆盖改成第一比较键，该层的去留需一并裁决（见 f-3）。**残留 P1（口径分层与固定稿不同构）**。

---

## c) 第二键成本：path（含返航？）+ w_late·L；硬约束否决方式

**结论：【基本符合】path 含返航（且空航次幻影腿已修）；迟到软约束 w_late·L 生效；硬约束在交付层/打磨层是真否决、在搜索层是支配权重（门槛标量）近似否决——非字典序不可达，但有可证支配性。**

### 证据

1. **path 含返航**。`problem.py:257-279` `tour_path_cost`：

```python
        cost = 0.0
        prev = -(1 + tour.uav_id)
        for tid in tour.task_ids:
            cost += self.costs.cost(prev, tid)
            prev = tid
        if tour.end_nest_id is not None:
            cost += self.costs.cost(prev, self.costs.nest_key(tour.end_nest_id))
```

   末任务→终点巢返航腿入 path（README §4.3「返航代价进入完整 path，不在评价外事后补巢」一致）；空航次恒 0（幻影「机→巢」腿已修，`problem.py:258-266` 注释量化过 403.2m 偏差）。

2. **迟到软约束**。`problem.py:439` `late_pen = … w_violation * soft_violation`；exp01 `config.yaml:180` `w_violation: 0.01`（迟到 300s≈3 点）。迟到不否决：`selection/feasibility.py:108-115`「时间窗不再作为拒绝理由…迟到由适应度定价」；迟到交付计完成（`horizon.py` `late_ids`）。**符合**决策 2。
   - ⚠️ P2：`w_violation=None` 时 `scalar_fitness` 直接给 `late_pen=0`，而 `config.py:86-93` 注释说 None = 「沿用历史口径 λ·w_penalty」——注释与行为不一致（exp01 显式给 0.01 未踩坑）。

3. **硬约束否决**：
   - 交付层 = **真否决**：`horizon.py:768-775` 完成闸门「仅**物理不可行**的违约任务不顺延完成」（解绑回池、不计完成）。
   - polish 层 = **否决门**：`muas_stage.py:3129-3133` / `local_search.py:34`「候选的 executed 任务集必须**包含**当前最优解集」+「硬违约不增」。
   - 搜索层 = **支配罚项而非字典序不可达**：`W_hard·n_hard`（`problem.py:441-442`），`W_hard = κ·m·R_pool`（`fitness_weights.py:43-58`）≥ 10×次目标上界，可证任何硬违约解严格劣于可行解——行为等价否决，但它是 big-M 数值构造，依赖推导正确性，不是结构上不可比。决策 3 允许「门槛系数标量」形态，故判**符合（附注）**；若论文要写「硬约束否决（字典序不可达）」，须改为写「支配权重构造」或改实现。

4. **第二键多出一项 `w_time·F3`**（最坏机航迹时间，`problem.py:446`）。默认 `w_time=0.0`（`config.py:78-79`、exp01 `config.yaml`），不激活时第二键恰为 `λ·path + w_late·L`，与决策 2 一致；激活时是决策 2 之外的附加项（论文 §5(3) 负载均衡），建议在文稿中显式声明。**P2**。

---

## d) 淘汰 / 生命周期逻辑：超期淘汰、voluntary_skip、铁律术语残留

**结论：【偏离】超期出池（expire）机制仍在且每周期生效，与决策 1「无超期淘汰」、决策 7「淘汰逻辑要么去掉要么改问题定义」冲突；现状是第三条路——机制保留 + 解释改写为「淘汰=机制缺陷信号须为 0」，而 README 根本不载此机制，两套解释并存。**

### 证据

1. **超期淘汰机制仍在**。`task_pool.py:288`（`begin_cycle` 无条件调用）：

```python
        # 3) 过期检查（执行中的任务不受影响）
        expired = self.expire(self._current_time)
```

   `task_pool.py:439-461`：

```python
    def expire(self, current_time=None) -> list[Task]:
        """超过 ``latest`` 的任务判过期并出池（EXPIRED）。"""
        …
            task.status = TaskStatus.EXPIRED
            self._tasks.pop(task_id)
```

   即：任务一旦滞留越过 `latest` 且未在执行，即被出池=淘汰。注意**迟到交付是合法的**（迟到软约束、计完成），滞留任务却因同一 `latest` 被丢——语义自相矛盾（任务晚交付可以，晚被挑走不行）。固定稿决策 1「允许跨周期推迟…单周期名额不足留池是合法推迟…无超期淘汰」在此机制下不成立于「推迟到越过 latest」的情形。

2. **解释层改写但机制未删**。`metrics.py:193-194`：

```python
    # 淘汰：deadline 已过仍在池中、被 expire 出池的任务（机制缺陷信号，须为 0）
    n_eliminated = sum(len(r.expired_ids) for r in records)
```

   `audit_correctness.py:204-209`「A2 淘汰须为零（铁律：完成率对可完成集合恒 100%）…`n_eliminated == 0` 硬 FAIL」。生产数据确为 0（exp01 `results_gateon_s42`：`n_eliminated=0`），说明防饥饿（`config.yaml` `anti_starvation: enabled: true`）下机制不触发——但**机制存在即可能触发**，与决策 7 的二选一要求不符。严重度 **P1**（实验可运行、审计兜底，但口径混用；一旦某配置下触发，论文语义无法解释）。

3. **淘汰率仍进论文主表必报列**。`metrics.py:460-466`：

```python
            "paper_main_table": [
                "completion_rate_final",
                "on_time_rate_final",
                "path_per_completion_m",
                "total_path_m",
                "elimination_rate",
                "unreachable_rate",
            ],
```

   决策 6「淘汰率不当主失败指标（语义上不存在）」。现状：它被定性为「缺陷归零审计」列（`metrics.py:267-270`「淘汰(elim)是机制缺陷信号、须为 0、不单列合法失败类」，η_final 分母不为它开口子）——语义上已不作失败指标，**部分符合**；但作为主表必报列仍把「淘汰」当结果量展示，与「语义上不存在」不完全相容。**P1**。

4. **「铁律」术语残留**。commit `31c8974` 自称「清理生命周期铁律术语」，但 `audit_correctness.py:204` 仍写「铁律」；ARCHIVE §1 标题即「生命周期铁律」（存档文件可容忍，审计代码不宜）。**P2**。`voluntary_skip` 术语保留但语义合法（门禁判据名，非旧淘汰概念），无残留问题。

---

## e) 文稿-代码一致性：Chapter3_README vs 实现

**结论：【偏离 P1】README §4.4 仍描述「两套可切换口径（A 含 −m·F1）」，与代码（非门禁路径已整体删除、gate_mode≡True、`lexicographic` 字段失效）不符；§5 滚动闭环不载超期出池机制。其余（返航入 path、polish 四门、门禁三分账、终点巢内层分配）一一对应。**

### 证据

1. **两套口径描述过时**。`Chapter3_README.md:141-163`：

```
实现上并存两套可切换口径（由配置开关控制）：
**A. 四层准字典序（含完成项，兼容/常用）**
f = W_hard·n_hard + w_exc·E_m − m·F1 + w_late·L_tot + λ·C_path + w_time·T_max
…
- 搜索用 f 可去掉 −m F1，由 W_skip n_skip 与修复承担覆盖；F1 作指标。
```

   代码事实：非门禁（A）路径已从 `scalar_fitness`/`gate_mode` 移除（`fitness_weights.py:26-39`、`problem.py:423-426` 注释「非门禁 big-M 四层口径…已于 2026-10-24 从代码移除」）；「可去掉」实为「已去掉」。README 开头自称「只描述**当前实现中的方法**（与 `methods/` 一致）」——A 段违反其自身承诺。**P1**。

2. **README §4.3 解码链未提「覆盖优先装载（coverage_first）/ 单调成本 polish」组件**（`muas_stage.py:974-987`、`1895+` `_load_coverage_fixed_point`；`muas_stage.py:3123-3140` 采纳判据）。exp01 `config.yaml:315` `coverage_first: false`（生产未启用），但 README 自称描述当前实现，开关组件也应入册。**P2**。

3. **README §5 滚动闭环不载超期出池**（`Chapter3_README.md:188-195`「3. 按完成闸门确认交付；未完成且仍合法的任务留池」），而 `task_pool.py:288` 每周期 expire——文稿与代码对生命周期的描述是两套（呼应 d 节）。**P1**。

4. **一致的部分（核对通过）**：
   - §4.3「返航代价进入完整 path」↔ `problem.py:257-279`（含返航腿、空航次 0）；
   - §4.5 polish 接受条件「执行集不减少；硬违约不增加；迟到不劣化；总航程严格下降」↔ `muas_stage.py:3152-3180`（`new_exec >= old_exec`、`cand_n_hard <=`、迟到门、`cand_path < path_before − eps`）；
   - §4.4 B 三分账（voluntary_skip / capacity_shortfall / 一阶不完备声明）↔ `coverage_gate.py:4-33`；
   - §4.3「终点机巢…内层分配…不在评价外事后补巢」↔ `muas_stage` `_choose_end_nests`；
   - 「交付后成本 polish：组内重排 + 跨机改派」（§2、§4.5）↔ `cost_polish_enabled`/`cost_polish_relocate_enabled`（exp01 均 true）。

---

## f) 昨天遗留 3 个待确认问题的代码现状

### f-1. 第一键：纯个数 vs 加权收益？

**答案：master 上两者都不是——搜索 f 里没有第一覆盖键（P0）；作为指标的 F1 是 ΣR_j 加权收益（reward U[1,10] 异质），不是纯个数（P1，注释误称「纯完成计数」，`problem.py:237-254`）。**
补充：本地备份分支 `backup/local-c06f532-20261024` 的 `problem.py` 曾实现纯个数第一键 `W_cov·(N − n_delivered)`（W_cov=1+次目标上界，门槛权重），与固定稿决策 2/3 完全同构；该实现未进 master（`git merge-base --is-ancestor c06f532 master` = 否）。

### f-2. 淘汰逻辑去留？

**答案：保留。** `task_pool.expire()`（`task_pool.py:439-461`）每周期生效（`task_pool.py:288`），EXPIRED 状态、`expired_ids` 记账（`horizon.py:171,341`）、`n_eliminated`/`elimination_rate` 指标（`metrics.py:193-194,276`）与论文主表必报列（`metrics.py:460-466`）俱在；仅解释被改写为「机制缺陷信号、须为 0」（`metrics.py:267-270`），审计 A2 断言归零（`audit_correctness.py:204-209`）。既未「去掉」也未「改问题定义」（README 不载、固定稿说不存在）——与决策 7 的二选一要求不符，待裁决。

### f-3. W_skip 门禁层去留？

**答案：保留。** `fitness_weights.py:60-72`（`skip_weight`，默认同 W_hard = κ·m·R_pool）+ `problem.py:438`（`skip_pen`）。定位 = 门禁口径的防御/对账层：gate 修复下 `n_skip` 构造归零、不咬合（生产实测 0），仅 audit/防御路径生效。其 big-M 推导量级与固定稿决策 3「门槛权重即可」的精神不符（P1）；若引入 W_cov 第一键，该层与 W_cov 的关系需一并定义。

---

## 总判定表

### 按固定稿 7 条决策

| # | 决策 | 判定 | 关键证据 | 严重度 |
|---|------|------|---------|--------|
| 1 | 滚动/跨周期推迟/**无超期淘汰**/可完成必完成 | **部分符合** | 滚动+推迟+防饥饿 ✓（`horizon.py:541` 留池、`config.yaml` anti_starvation）；但 `task_pool.py:288,439` expire 超期出池机制仍在，可丢「推迟越过 latest」的物理可达任务 | P1（机制残留，触发即 P0） |
| 2 | 单周期字典序 `(-n_delivered, path + w_late·L)` | **偏离** | f 无覆盖键（`problem.py:440-447`）；第二键=path(含返航)+w_late·L ✓（+可选 w_time·F3） | **P0** |
| 3 | 适应度字典序/W_cov 门槛标量；不要 m·F1；F1 只作指标 | **部分符合** | `−m·F1` 退场 ✓、F1 只作指标 ✓（`fitness_weights.py:26-39`）；但覆盖层缺失、W_skip/W_hard 仍 κ·m·R_pool big-M 推导（`fitness_weights.py:43-72`） | **P0**（第一键缺失）+ P1 |
| 4 | 门禁必要但不充分，须配合覆盖进第一比较键 | **偏离** | 只有门禁（`muas_stage.py:1023-1035`、`coverage_gate.py`）；第一比较键不存在；E1E2 已实证门禁单上场 shortfall↑ | **P0** |
| 5 | shortfall↑ 解释 = 压力弱化+一阶残留、非永久丢失 | **符合** | `muas_stage.py:138-150`、ARCHIVE §5、`coverage_gate.py:28-33,295-302`（留池下周期重试、一阶不完备显式声明） | — |
| 6 | 淘汰率不当主失败指标 | **部分符合** | 定性为缺陷归零审计、η_final 不开口子 ✓（`metrics.py:267-276`）；但仍算 elimination_rate 且进 paper_main_table 必报列（`metrics.py:460-466`） | P1 |
| 7 | 淘汰逻辑去掉或改问题定义，禁两套解释混用 | **偏离** | expire 机制保留 + README §5 不载 + 解释改写为「缺陷信号」；「铁律」残留 `audit_correctness.py:204` | P1 |

### 按审查项 a–f

| 项 | 判定 | 严重度 |
|----|------|--------|
| a) 第一键 = n_delivered / big-M 退场 / W_cov 论证 / 字典序 vs 门槛标量 | 偏离（无第一键；m·F1 已退场；实现=门槛标量缺覆盖层；指标 F1 加权非个数） | **P0** + P1 |
| b) 门禁唯一主口径 / 门禁+覆盖第一键 / W_skip 残留 | 部分符合（门禁唯一化 ✓；第一键缺；W_skip 层保留） | **P0**(连带) + P1 |
| c) 第二键 path(含返航)+w_late·L / 硬约束否决 | 基本符合（返航✓ 迟到✓；硬约束=交付层真否决 + 搜索层支配罚项） | P2 |
| d) 淘汰/生命周期残留 | 偏离（expire 在、淘汰率进主表、铁律残留） | P1 |
| e) README-代码一致性 | 偏离（§4.4 两套口径过时、§5 不载 expire；polish/返航/门禁分账 ✓） | P1 |
| f) 三问答案 | 见上文 f-1/f-2/f-3（第一键：无→备份分支有纯个数版；淘汰：保留；W_skip：保留） | — |

### 建议处置（供裁决，本次未改动任何文件）

1. **P0**：以固定稿为准 → 把「覆盖进第一比较键」恢复进 `scalar_fitness`（可直接移植 `backup/local-c06f532-20261024` 的 `W_cov·(N − n_delivered)`，W_cov=1+次目标上界即满足决策 3），或由用户明确废止固定稿决策 2/3/4、以 ARCHIVE §1 裁定为准——两份口径文档必须收敛为一份。
2. **P1**：expire 机制按决策 7 二选一（删除，或把问题定义/README 明确写上「latest 越过即出池」并同步改固定稿）；`paper_main_table` 中 elimination_rate 降为审计字段或删除。
3. **P1**：`problem.py:237` 「F1 回归纯完成计数」注释与 `f1_reward` 实现（ΣR_j）二选一改齐；README §4.4 删除 A 口径或标注「已归档（见 ARCHIVE）」。
4. **P2**：`w_violation=None` 时 late_pen=0 与 config 注释「λ·w_penalty」不一致；w_time·F3 项在论文口径中显式声明；`audit_correctness.py:204` 去「铁律」字样。
