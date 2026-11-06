# P0 四层判据漂移率基线（供 P1/P2 收敛用）

> 2026-11-06 | 第二阶段重构 P0 交付物之一（`stage2_final_scheme_2026-11-06.md`
> §8 P0「另需报告一份『四层判据漂移率』基线供 P1/P2 使用」）。
> 测量库：`task_allocation/methods/muas/constraints/criteria_drift.py`
> （视图定义在 `constraints/spec.py`，现语义原样包一层、零行为变化）。
> 复现：合成采样探针（不依赖 DEM 场景数据），seed=20261106。

## 0. 一句话结论

四处「另写判据」在**生产口径数据下漂移率为 0**；但三条漂移通道是**结构性
存在**的（时间窗元组分叉、时序 1e-9 松弛、采纳判据迟到门缺失 + 阈值单位
混用），一旦数据/算子触碰即生效。P1/P2 消解顺序：采纳判据（P3，已被
`stage_runner` 缺陷钉死）> 时序松弛（P1）> 时间窗元组（P1）。

## 1. 逐项测量结果

### ① 时间窗 deadline 字段（model vs decode 两视图）

| 采样组 | n | 不一致 | 漂移率 | 说明 |
|---|---|---|---|---|
| A 仅 `earliest`/`latest`（滚动层生产口径） | 50 | 0 | **0** | 两视图同源，恒等 |
| B `time_window` 元组与 `latest` 一致 | 50 | 0 | **0** | 元组优先但值相同 |
| C 元组与 `latest` **分叉**（病态构造） | 50 | 50 | **100%** | model 取元组[1]、decode 直读 `latest` |

**通道**：`decode_repair._best_order.latest_of` 直读 `task.latest`，绕过
`task_window` 元组口径。当前生产数据不构造 `time_window` 元组 ⇒ 实测漂移
0；一旦参考项目格式数据（`time_window` 元组）入场即 100% 命中。
**P1 消解**：`window_deadline_decode` 收敛为 `window_deadline_model` 别名
（一行），配开关对照。

### ② 时序（decode 单调判定 vs model 逆序对计数）

| 采样组 | n | 违约样本 | 判罚不一致 | 漂移率 |
|---|---|---|---|---|
| 整数序号排列（单链 3!/4! + 双链交错） | 54 | 46 | 0 | **0** |
| 浮点边界（序号差 < 1e-9，×25） | 100 | 75 | 75 | **75%** |

**通道**：`_seq_violation` 带 `1e-9` 松弛（`order < prev - 1e-9`），
`check_sequence_constraint` 严格比较（`orders[i] > orders[j]`）。整数/正常
浮点下两视图判罚**逻辑等价**（存在下降 ⇔ 存在逆序对，实测 0 不一致）；
仅当同链序号差落在 (0, 1e-9) 内时 decode 放行、model 判罚。
**P1 消解**：统一松弛量为单一常量（两视图共用），或按模型口径去松弛
（须开关对照——松弛是防浮点噪声的有意设计，去除有改判风险）。

### ③ 机巢容量三份账（gate loads / check_nest_capacity / MCF）

| 对照 | n | 不一致 | 漂移率 |
|---|---|---|---|
| 门禁 `_nest_loads` 账 vs `check_nest_capacity` 账 | 200（含 147 显式 `None` 终点采样） | 0 | **0** |
| MCF 输出过 `check_nest_capacity` 复核 | 200 | 0 | **0** |

**结论**：三份账在采样口径下**逐位一致**（`solve_end_nest_assignment`
docstring 声称的「容量口径与 check_nest_capacity 完全一致」实测成立）。
显式 `end_nest=None` 的语义差（gate 回落 home、model 记 departure）不改变
超容巢集合。**保留观察**：两账各自独立实现在漂移风险上仍是隐患（P2 把
gate 账改为委托 `judge_nest_capacity` 时重测本项）。

### ④ 采纳判据（accept_cost_polish_view 航程门 vs accept_f_view 目标口径）

采样 400 条候选记录（执行集/硬违约/超限均不退化，航程严格下降 >1m；
一半记录迟到 +10s）：

| 对照口径 | n | 判定不一致 | 漂移率 | 其中「航程门收、f 视图拒」 |
|---|---|---|---|---|
| 单位一致（eps_f = λ·eps_m = 0.1）全量 | 400 | 200 | **50%** | 200 |
| 单位一致 + 迟到门满足子集 | 200 | 0 | **0** | 0 |
| 同数值 eps=1.0（现值口径，单位混用）全量 | 400 | 290 | **72.5%** | 290 |

**两个独立通道**：
1. **迟到门缺失**（200/400）：`stage_runner` 的 cost_polish 采纳判据
   （`accept_cost_polish_view`）不含迟到分量——`f` 变差（迟到上升吃掉航程
   收益）仍可被采纳。生产中 `_cost_polish_routes` 内部四道门含「迟到数/量
   不增」，把该通道挡在算子内；但**接口层不设防**，任何绕过算子内部门的
   调用方都会踩中（正是 plan §4.5 第 4 条钉死的 `stage_runner.py:425`
   `cand_fitness` 未进判据缺陷）。
2. **阈值单位混用**（+90 条）：`cost_polish_eps = 1.0` 是**航程单位（米）**，
   `accept_f_view` 的 ε 是 **f 单位**（λ=0.1 ⇒ 1 单位 f ≈ 10 m ≈ 100 s 迟到）。
   同数值直接比较 = 跨量纲，产生 290 vs 200 的差额。

**P3 消解**（定稿 §4.2）：采纳判据统一为 `f(cand) < f(cur) − ε`（f 单位）且
约束向量不退化；`cand_fitness` 进判据；三个阈值（`monotone_polish_eps` /
`cost_polish_eps` / `polish_eps`）逐一迁移并留新旧对照。

## 2. 与台账（DecisionLedger）的配合

- 采纳事件已全部入台账（`layer=polish`，`post_polish_monotone` /
  `post_cost_polish`，含 `delta_f`）——本节漂移可用台账离线复算，无需
  monkeypatch；
- 台账 `reason` 覆盖率 100%（硬性纪律，非法 reason 拒绝入账且计数可见）；
- `_consolidate` 成本合并与 `_best_order` 航程裁决记 `reason=opt`——
  「`reason=opt` 动作数归零」即 P1 剥离解码期目标改写的验收量。

## 3. 边界声明

- 全部为**合成采样**（无 DEM 场景数据；仓库大文件不在 git）；结论不外推
  真实场景分布，但漂移通道是**结构性**的，与数据分布无关；
- 时序 ε 边界组是病态构造（人为制造 <1e-9 序号差），度量的是通道存在性，
  不是生产发生率；
- 采纳判据组的「迟到 +10s」同理是通道探针；生产发生率待 P3 接线后用
  台账实测（`post_cost_polish` 事件的 `delta_f ≤ 0` 计数）。
