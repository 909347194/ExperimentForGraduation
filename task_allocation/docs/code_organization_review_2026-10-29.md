# task_allocation 代码组织审查报告

- 日期：2026-10-29
- 范围：`task_allocation/` 全包（不含 exp01_smoke 绘图清理细节，那轮已完成）
- 性质：**只读审查，未改任何代码**。所有结论附路径行号证据，改动待裁定。
- 评价维度：分层 / 职责单一 / 重复实现 / 命名一致性 / 死代码 / import 层级 / 测试对应。
- **不评价算法数值与公式正确性**，只看组织与可维护性。

---

## 0. 总评

**骨架合理，边角欠收拾。**

规模 156 个 `.py` / 约 38k 行（methods 61、experiments 51、tests 32 + 根 tests 3、common 5、baselines 6）。
三层分层意图清楚且基本立住：

- `methods/selection/` —— 一阶段（选谁上场）
- `methods/muas/` —— 二阶段（怎么飞）
- `methods/rolling/` —— 多周期编排

正面事实：`selection` 与 `muas` **互不 import**（层间无横向耦合）；`muas_stage.py` 已退化为 re-export 门面；
`tour_cost.py` 集中了航次代价原语，未散落各处。这说明此前几轮重构是有效的。

主要欠账集中在三处：**绘图/实验脚手架的逐字复制**、**下层反向 import 上层**、**experiments 层零单测**。

---

## 1. 严重级（结构性问题，值得优先处理）

### 1.1 绘图基建 `exp01` ↔ `exp02` 逐字节重复（已 md5 证实）

| 文件 | exp01_smoke | exp02_selection_scale | 相似度 |
|---|---|---|---|
| `plot/base/audit.py` | 384 行 | 384 行 | **md5 完全相同** |
| `plot/base/declutter.py` | 324 行 | 324 行 | **md5 完全相同** |
| `plot/theme.py` | 401 行 | 427 行 | **92.3%** |

这不是"风格相似"，是**同一份文件被复制了一份**。改一处忘另一处是迟早的事
（本轮就在 exp01 删了 2 个死函数，exp02 的副本还留着同样的死代码）。

### 1.2 实验脚手架（配置/路径/CLI）多份近似实现

函数级重复，证据确凿：

- `--set` 覆盖解析 `_coerce_set_value` / `_apply_dotted_set` —— **3 份**
- `load_config` —— **4 份**
- `_parse_seed_list` —— **2 份**
- `config_fingerprint` —— **2 份**
- 文件级：`build.py` exp01↔exp05 **74.3%**、`config.py` **67.1%**、`paths.py` **66.7%**

**但注意（避免误判）**：主编排 `run.py` 两两仅 **2.5%~25.7%** 相似，
`metrics.py` exp01↔exp05 仅 **10.2%**。
⇒ **实验主逻辑并没有被整体复制**，重复集中在"脚手架 + 绘图基建"这两块。
这一点决定了修法：抽公共基建即可，不必动各实验的核心逻辑。

### 1.3 分层倒置：下层 import 上层

| 位置 | 反向依赖 |
|---|---|
| `common/scenario.py:29` | → `methods.utils` |
| `common/task_scenario.py:56` | → `methods.utils` |
| `uav_state.py:38`、`horizon.py:44` | → `muas.constraints.nest_capacity` |
| `methods/utils/dem_geotiff.py:32,364` ↔ `muas/cost/vertical_section.py:46` | utils 与 muas.cost **双向纠缠** |

`common/` 定位是共享底座，却依赖 `methods/`——依赖方向反了。

---

## 2. 中等级

### 2.1 门禁字段的表述债（**不是 bug，勿按 bug 修**）

被初判为"严重、铁律被破坏"，经核验**下调为中等**，定性如下：

- `fitness_weights.py:26-39` 的 `gate_mode()` **恒返回 True** —— 这正是「完成是约束」铁律的实现，**未被破坏**。
- `lexicographic_coverage` 的 3 处读取分支：
  - `methods/muas/solvers/decode_repair.py:159`
  - `methods/muas/solvers/route_polish.py:488`
  - `methods/muas/solvers/local_search.py:214`
- 这 3 处都是 `if coverage_gate → … elif lexicographic_coverage → …`，
  exp01/02/05 生效配置 `coverage_gate=true` 恒开（见 `results/effective_config.json`），
  **`elif` 分支永不执行**。

⇒ 所谓"注释说恒 True、代码却读 False 分支"不构成行为分歧，是**表述/命名债**：
一个事实常 True 的字段仍留着读取分支，容易被误读成"可切换"。

**处理约束（重要）**：只统一注释或移除死分支；**不动 `gate_mode()`、不改任何行为**。

另注：不带后缀的 `lexicographic` 字段在 `decode_repair.py:82` **仍然生效**
（`economic_prune = prune_unprofitable ∧ ¬coverage_first ∧ ¬lexicographic`），
exp01 的 `prune_unprofitable=false` 故不触发。
它"已失效"的注释**仅限门禁口径**，消融实验仍在用它，**勿一并删除**。

### 2.2 超大文件与超长函数

| 文件 | 行数 |
|---|---|
| `methods/rolling/horizon.py` | 982 |
| `methods/muas/solvers/decode_repair.py` | 831 |
| `methods/muas/…/inverse_mapper.py` | 593 |
| `methods/muas/stage_runner.py` | 592 |
| `methods/utils/coordinates.py` | 568 |

全库最长函数：

- `run_selective_muas` — `stage_runner.py:137-591`（≈455 行）
- `run_cycle` — `horizon.py:379-623`（≈245 行）

`experiments/exp01_smoke/metrics.py` 976 行里塞了 4 类职责（逐周期 / 汇总 / 多 seed 聚合 / 审计）。

### 2.3 测试盲区

- `experiments/` **零单测覆盖**：`tests/` 中仅 2 处引用 exp01 路径
  （`test_event_replan.py:18`、`test_metrics_aggregation.py:41`），
  且是**读取结果文件做回归**，不是 import 实验模块。
- `methods/utils/coordinates.py`(568)、`dem_geotiff.py`(432) **无单测** ——
  这两处是坐标/地形入口，出错会**静默污染全程距离**，风险与覆盖不匹配。
- **测试口径漂移**：`test_coverage_gate.py:99,129` 仍断言「往返 20km ≤ 80km」，
  而 `task_scenario.py:36-40` 生产口径已改为 20km 并声明旧 80km 作废。
- 未发现确凿孤儿测试。

### 2.4 引用的文档不存在

`experiments/README.md` 被 **11 处**注释引用，但**文件不存在**：
`common/scenario.py:4`、exp01 的 `paths/metrics/plot/config.yaml`、
exp05 的 `config/paths/metrics/notes` 等。

---

## 3. 轻微级

- **实验目录**：`exp03_event_rolling/`、`exp04_ablation/` 各仅一个 `notes.md`，
  `status: planned` —— 属有意占位，**不要删**。
- **绘图脚本三名并存**：`render_from_json` / `plot_arms` / `plot_joint`，
  命名无统一约定。
- **命名不一致**：`problem.py:147` 用 `selective_srp`，encoder 侧用 `srp`。
- **`__pycache__`**：cpython-312 / 313 混存，曾被误入库。

---

## 4. 建议的规整优先级（按性价比排序，均未执行）

| # | 动作 | 理由 | 风险 |
|---|---|---|---|
| ① | 把 100% 相同的 `audit.py`/`declutter.py` + 92% 的 `theme.py` 抽到共享绘图包 | 一次性消掉 ~1100 行逐字重复 | 零风险（内容相同） |
| ② | 建 `experiments/_shared/`，抽 `ConfigBundle` / `build_*` / `paths` / CLI / fingerprint | 消掉 3~4 份脚手架副本 | 低（不动 run.py 主逻辑） |
| ③ | 补测试：`experiments` 最小冒烟 + `coordinates`/`dem_geotiff` 单测 + 根 `conftest.py` 统一 sys.path | 当前风险最高的两个模块无覆盖 | 低 |
| ④ | 修分层倒置：`common` 不再 import `methods.utils` | 依赖方向回到单向 | 中（需动导入面） |
| ⑤ | 统一模型类型命名 `selective_srp` / `srp` | 一致性 | 低 |
| ⑥ | 拆 `run_selective_muas`(455行) / `run_cycle`(245行) | 可读性 | 中（核心路径） |
| ⑦ | 补 `experiments/README.md`（11 处引用悬空） | 文档债 | 零 |

**执行前置约束**：涉及门禁（`coverage_gate` / `gate_mode` / `lexicographic*`）的改动，
必须先确认不触碰「完成是约束」铁律与完成率 100% 口径。
