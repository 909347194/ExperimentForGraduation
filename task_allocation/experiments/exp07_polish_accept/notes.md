# exp07_polish_accept —— 交付后成本 polish 采纳判据配对对照

> 阶段二 P4 的第一个实验脚手架。本轮只做**冒烟档**：验证实验设计、
> 配置纯度与统计口径；不产出可用于论文的数值结论。

## 1. 要回答什么

定稿 §4.2 与 P3-a 留下一个待裁定项：交付后成本 polish 的采纳判据，
当前默认仍是 `path`（只看航程分量严格下降），而统一判据应为
`f(cand) < f(cur) − ε` 且约束向量不退化。P3-a 已实现双视图并落盘漂移，
但**没有做同 seed 配对对照**，因此「默认由 `path` 翻 `f`」缺实证。

两档（不用「臂 / A/B 变体」这类黑话）：

| 档 | 采纳判据 | 说明 |
|---|---|---|
| `path` | `accept_cost_polish_view` | 旧语义，航程分量门（`cost_polish_eps=1.0` 米），与 `method-phase1` 逐位可比，当前默认 |
| `f` | `accept_f_view` | `cost_polish_eps_f=1e-6`（f 单位），且 `n_hard / 执行集 / 迟到 / 航程超限` 逐分量不退化 |

`1 f ≈ 10 m 航程 ≈ 100 s 迟到`（λ=0.1、w_late=0.01/s），两个 eps
**单位不同，禁止复用数值**。

## 2. 公平性协议

- **唯一差异**：`run.py` 用
  `dataclasses.replace(stage_model, cost_polish_accept_mode=mode)` 只翻这一个
  字段；`summary.json` 落盘两档配置对象的完整 diff，必须
  `only_accept_mode_differs=True`，否则报「配对结论不可用」。
- **同 seed 配对**：`experiment.seeds = [7, 11, 23]`（与 P2/P3 的 bit-exact
  硬核验同 seed）。每个 seed 先跑完两档再进下一个 seed，全程**串行**，
  任何时刻只有一个求解在跑（严禁多实验并行）。
- **任务实例**：每档每 seed 重新 `build_arrivals`，且传给滚动层前
  `deepcopy` 一份（TaskPool 会原位改写 status/meta）。求解器 seed 与任务
  生成 seed 同步，两档共享同一随机流。
- **场景复用**：机巢 / DEM / 建筑 / 代价估算器与 seed、档无关，只构建一次。
- **L3b 显式关**：`muas.l3b_enabled: false`。历史 yaml 里
  `cost_polish_relocate_enabled: true` 与 `polish_moves` 含 relocate/swap
  会触发旧 config 保义映射把总闸打开（§11.5），故必须显式写新键覆盖。

## 3. 口径与纪律

- 主指标沿用 exp01 / exp05 既有定义（完成率分母排除 UNREACHABLE 与停机时
  仍休眠的任务；按时率 = 交付集条件率；三维航程 = 各周期 `final_path_m`
  之和）。`metrics.py` 只抽子集，**不发明新口径**。
- 完成率铁律：`n_eliminating`（非不可达）必须为 0；`n_hard` 累计必须为 0。
  `n_remain` 是恒等式导出量，不是独立测量，如实单列。
- 采纳判据对账：两档**都**计算 path/f 双视图（stage_runner 原样落盘
  `cost_polish_accept`），所以每档都能观测 `drift` 次数；`mode` 只决定
  采纳哪一个。`cost_polish_n_accepted` / `cost_polish_n_drift` 逐档并列。
- **冒烟档结论不可外推正式档**（项目方法论铁律）。冒烟参数：
  `n_tasks=8`、`population=12`、`max_iter=20`、`n_cycles=8`、
  `arrivals.counts=[2,2]`。`--profile formal` 会拒绝在这些值仍未被
  `--set` 覆盖时运行，防止把冒烟数当正式结论。
- 配对 t 检验 n=3 时 df=2，检验效能极低；不显著必须写「不显著」，
  不得写成「无差异」。主效应与交互项并列报告，禁写「独立收益」。
- 跑批不渲图；`plot/compare.py` 事后单独执行。

## 4. 本轮明确**未**裁定 / 未改动

1. `cost_polish_accept_mode` 的**代码默认值仍是 `path`**，本实验不预先改默认；
   翻转与否由正式档配对 A/B 结果决定（定稿 §4.2「须开关对照，不得静默替换」）。
2. §4.3 第 2 条「`coverage_gate` / `lexicographic_coverage` 双开」与 §3.3
   「门禁只作终态校验」的张力：本实验沿用 exp01 既有 `coverage_gate: true`
   / `coverage_audit: true`，**不擅自改口径**。
3. `coverage_first` 单调 polish 的跨机 relocate（第三归属来源）当前不受
   `l3b_enabled` 总闸；本实验 `coverage_first=false`，该来源恒 0，
   由 `l3b_ungated_monotone_relocate` 如实单列。
4. **本轮新发现（待裁定）**：复制骨架时核对到代码默认
   `projection_only=False` / `compare_mode="penalty"`，而定稿 §6 表格写
   `projection_only=True` / `compare_mode="deb"`。本实验如实沿用代码默认，
   并在 `run_meta.json` 的 `stage_effective` 逐项留痕，**未擅自改代码或文档**。

## 5. 目录与复现

```
config.yaml / config.py / build.py / paths.py / metrics.py / run.py  自持
plot/compare.py                                                    自持，事后绘图
results/<profile>/<mode>/seed_<s>/{metrics,run_meta}.json
results/<profile>/{summary.json, summary.csv}
```

```bash
# 冒烟档（本轮）
python run.py
# 只校验生效配置
python run.py --dry-run
# 正式档（需单独发话；必须显式覆盖冒烟参数）
python run.py --profile formal \
  --set tasks.n_tasks=20 --set solver.population=50 \
  --set solver.max_iter=200 --set rolling.n_cycles=60 \
  --set rolling.arrivals.counts=[5,5]
```

`--set` 必须写全嵌套路径（如 `solver.max_iter`），平铺死键会静默不生效。

## 6. 本轮冒烟结果（2026-11-06）

命令：`python run.py`（2 档 × 3 seed，共 6 次运行，串行、未渲图）。

| 项目 | 结果 |
|---|---|
| 场景 | 3 机巢 / DEM 接入 / 建筑 20685 栋；3 seed 均于**周期 4 池空自然收敛** |
| 两档配置 diff | `n_differing_fields=1`、`only_accept_mode_differs=True`（只有 `cost_polish_accept_mode`） |
| 各项检查 | 6 次运行 `invariants_passed=True`（`n_hard=0`、生效 mode 一致） |
| 完成率 / 按时率 | 两档逐 seed 均 1.000 |
| 航程（seed 7/11/23） | 27.27 / 28.01 / 20.71 km，两档逐位相同 |
| `final_fitness_sum` | 2726.901 / 2801.242 / 2070.883，两档逐位相同 |
| 采纳候选 | seed 11 有 1 次（两档都采纳、path/f 两视图都 True）；seed 7/23 为 0 |
| 判据漂移 | 3 seed × 2 档全为 0 |
| 墙钟 | 差值均值 −0.026 s，配对 t=−1.596、p=0.251（**不显著**，且墙钟非质量指标） |

**verdict（写入 `summary.json`）**：两档在所有质量指标上逐 seed 完全一致；
本次跑批**无法区分** `path` 与 `f`，既不能据此翻转默认，也不能写成「两档等价」。

> ⚠️ **原解释已作废（2026-11-06 审查 A1）**：初版把零分歧归因于「冒烟规模
> 下几乎没有候选，正式档跑大就有」。这个解释是**错的**。真正的原因是
> 结构性缺陷：修复前局部搜索的成本门恒为「航程严格下降」、迟到门又要求
> 迟到不增，产出的候选必然同时满足两个视图 ⇒ 双视图永远同真同假，
> **跑多大都测不出判据差异**。修复过程与证据见 §7。

另一个必要条件：本冒烟场景迟到恒为 0（`total_lateness_s=0`、按时率 1.000），
此时 `f = λ·path + 常数`，两个判据在数学上必然等价 —— 即使修复了候选门，
也必须存在迟到才可能分歧。用 `--set problem.uav_speed=4.0` 制造迟到后
按时率降到 0.750，但该规模交付后 polish 仍零接受（12 周期全零），
仍观测不到分歧。这两条都是**设计层面的必要条件**，不是样本量问题。

统计口径如实声明：所有质量指标差值为零方差 ⇒ 配对 t 检验**不适用**
（`p_reason=zero_variance_no_test`），不是「p 值缺失」也不是「不显著」；
只有墙钟有变异，其 p=0.251 不显著。venv 未装 scipy，p 值由
`metrics.py` 内的正则化不完全 β 精确计算 Student t 双尾分布得到，
已用 `t(1)=1→0.5`、`t(2)=2.228→0.0500` 等临界值校验，**未用正态近似**。

**三条口径边界（已写入 `summary.json.caveats`）**：

1. `cost_polish_accept` 是每周期**最后一次**候选的快照，不是全候选累计；
   低频候选场景会低估判据分歧次数。要精确统计须扩展 `stage_runner`
   落盘累计计数（属代码改动，本轮未做）。
2. `n_eliminating` 不是 diagnostics 字段：0 是「TaskPool 已无超期出池路径」的
   **结构性事实**，非独立测量；独立验收须走
   `methods/muas/independent_audit.py`（本实验未接入，如实标注
   `eliminating_independently_verified=False`）。
3. 冒烟档结论不可外推正式档。

**结论**：P4 第一个实验脚手架、配置纯度校验、同 seed 配对与统计报告链路
均已跑通；**默认 `cost_polish_accept_mode` 仍为 `path`，本轮没有任何
代码默认值被改动**。

## 7. 审查后的修复（2026-11-06 第二轮）

用户裁定「先修」。只读审查列出 A/B/C 三级问题，本轮按 A → B → C 顺序修：

**A1（要命的那个）｜判据下沉到局部搜索。**
`RoutePolishMixin._cost_polish_criterion()` 返回 `(mode, score, eps)`：

* `mode=path`：`score = 航程(米)`、`eps = cost_polish_eps`（米），调用点保留
  原短路顺序 ⇒ **与修复前逐位一致**；
* `mode=f`：`score = λ·航程 + w_late·迟到`、`eps = cost_polish_eps_f`
  （exp07 生效值 `λ=0.1`、`w_late=0.01`），允许「航程略增、迟到降得更多」。
  新诊断 `cost_polish_n_f_only_gain` 记录这类「只有 f 判据会采纳」的移动数。

`_cost_polish_routes` 与 `_cost_polish_relocate` 的成本门、插入位最优选择
都改读该评分；`path` 档走原分支。归因计数固定用航程（米）阈值，
不与 `eps_f` 混用单位。硬核验：`git archive 5f60589` 同场景同 seed
{7,11,23}，`path` 档 **78 项公共字段 × 3 seed 零差异**，唯一新增是诊断字段。

`tests/test_p4_accept_criterion.py`（4 项）用注入的 tour 代价构造最小分歧：
基线 `[0,1]` = 1000 m / 200 s 迟到，2-opt 候选 `[1,0]` = 1005 m / 0 s 迟到。
`path` 档拒绝且解逐位不变、`f` 档接受且 `n_f_only_gain=1`，两档交付解不同
⇒ 判据对照**在结构上不再冗余**。

**A3｜铁律从「观察」改成「硬断言」。**
`collect_run_metrics` 现在断言完成率恒 1（分母排除不可达/休眠）、
`arrived` 恒等式残差非负（旧版 `max(0,…)` 钳位会掩盖失配）、
`n_eliminating=0`、`n_hard=0`、生效判据一致；`run.py` 在硬失败时返回
**非零退出码**（旧版只打印警告仍返回 0）。
`independent_audit` 的几何重建重评**仍未接入**：`problem` 实例是
`run_selective_muas` 的局部对象，`RollingHorizon` 未暴露审计钩子；
本轮不擅自改核心层，如实记为技术债。

**B 级（逐条）**：B1 质量判定只用 6 个质量指标，排除诊断计数/墙钟；
B2 用未 round 的原始差值 + 显式容差 `1e-9` 判零；
B3 caveats 补全三重截断；B4 两档执行顺序按 seed 交替；
B5 formal 防呆补 `rolling.arrivals.counts` 且改类型归一比较；
B6 去掉 verdict 里不存在的「交互项」；B7 指纹含 seed、
配置 diff 逐 seed 核验、`coverage_audit` 归位到 `problem_effective`。
另修一处对照脚本口径错误：L3b 对照的 `mode` 是 off/on，
不能拿去校验 `cost_polish_accept_mode`（新增 `expected_accept_mode` 参数）。

## 8. 两组同规模配对数据（去留判断用）

用户裁定：「我们的方法需要什么就做什么，不需要什么去掉什么；
不确定的话就做实验对比」。正式档实测 **1 seed × 2 档 > 10 分钟**，
本轮改用可控中间档 `preset=mid`（12 任务 / pop25 / gen60 /
n_cycles20 / counts[3,3]），3 seed 配对、串行、两组实验分开跑。
⚠️ **mid 不是正式档，结论不可外推正式档。**

**第 1 组｜采纳判据 `path` vs `f`（`run.py --profile mid`）**
3 seed 所有质量指标逐位相同；`cost_polish_n_f_only_gain` 全 0；
判据漂移全 0。即：修复在结构上有效，但**该规模候选域里不存在
「航程增、迟到降」的解**。`cost_polish_accept_mode` 在测过的两组规模
（冒烟 8 任务 + mid 12 任务，各 3 seed）都表现为**冗余开关**。

**第 2 组｜L3b 开 / 关（`compare_l3b.py --preset mid`）**
两档唯一差异 `l3b_enabled`，采纳判据固定 `path`；配置 diff 纯度通过、
6 次铁律全过。

| seed | 关档航程 | 开档航程 | L3b 归属动作 | 完成率 / 按时率 |
|---|---|---|---|---|
| 7 | 34.42 km | **31.14 km**（−9.5%） | 3 | 两档同为 1.000 / 0.944 |
| 11 | 31.75 km | 31.75 km | 1 | 两档同为 1.000 / 1.000 |
| 23 | 25.35 km | 25.35 km | 0 | 两档同为 1.000 / 1.000 |

配对均值差 −1093.7 m（航程）、−109.37（f），**配对 t 检验
p=0.423 不显著**（df=2，效能极低，不能写成「有显著收益」，
也不能写成「无差异」）。冒烟档 1 seed 自检里开档降幅更大
（27.27→18.53 km），说明效果随场景波动，更不可外推。

**判断（写给定稿 §11.7，本轮不擅自改默认值或删代码）**：

1. **L3b 是方法需要的**：它是**交付后**精修（在选择之后，不参与 DE
   竞争、不回写基因），因此不违反「评价与遗传传递一致性」契约；
   实测能在不损完成率/按时率的前提下压低航程。它不该继续挂在
   「默认关 / 待裁定」的过渡状态里。但把默认翻回开属于**数值口径变更**，
   且 `c39d607` 的「默认 False」是用户上一轮明确拍板，本轮不擅自推翻；
   消融仍用显式 `l3b_enabled=False`（`compare_l3b.py` 已验证可用）。
2. **`cost_polish_accept_mode` 更像不需要的复杂性**：定稿 §4.2 要求
   「采纳判据统一到 f」本是**口径一致性**问题而非收益问题；两组规模
   零分歧说明保留双视图开关没有换来可观测信息。但它是否该删，
   仍缺正式档证据（方法论铁律①），本轮只标注、不删。
3. **下一步唯一缺的是正式档跑批**：`--preset formal` /
   `run.py --profile formal`，两组各 3 seed 配对。正式档跑完才能
   把上面两条从「方向性判断」升级为「可执行的代码去留」。
   严禁多实验并行；须单独发话。

