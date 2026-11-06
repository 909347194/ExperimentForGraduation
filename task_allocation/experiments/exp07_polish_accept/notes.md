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
本冒烟档**无法区分** `path` 与 `f`，既不能据此翻转默认，也不能写成「两档等价」。
原因很直白：冒烟档规模下交付后成本 polish 几乎没有候选（3 seed 合计 1 次），
且该候选在两种判据下结论相同，因此没有产生任何行为差异。这验证了
「实验设计与统计口径可用」，但**没有回答**「默认是否该翻 f」——
后者必须靠正式档跑批（需单独发话）。

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

