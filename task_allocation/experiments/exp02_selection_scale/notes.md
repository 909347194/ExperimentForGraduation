# exp02_selection_scale

**status:** active（2026-11-10 guangzhou 正式档已闭合：18/18 格，FORMAL_RC=0）

> **2026-11-10 场景切换**（用户裁定「广州数据，当前口径」）：
> `lasa_chengguan` → `guangzhou`（3 机巢 / DEM + 建筑垂直切面包络 / 10 km
> 满电航程），与 `exp01_smoke` 当前正式档同场景、同距离口径。
> 机队规模受总容量约束由 K=8 → **K=6**（3 巢 × 容量 2）。
> 旧档（2026-10-25，6 巢 / 8 机 / 20 km / λ=1e-2 / 含 F3）**全部数值作废**，
> 结果与图件已归档到 `_archive/exp02_selection_scale__results_2026-10-25/`。
> 跨实验、跨场景禁直接比绝对值，禁配对 t 检验。

## 目的

在 `|T_t| >> K` 的规模下，量化第一阶段「动态任务选择」的规模压缩收益，
并对候选/选择规模的标定口径（机数名额 vs 机队能力）做对照扫描。

`exp01_smoke` 是逐周期到达的滚动任务流（首周期 20 任务 / 6 机），候选预筛
不构成规模瓶颈，无法充分验证该机制；本实验固定 K=6，任务规模扫
10/50/100，使 N 相对机队规模形成压力。

对应论文 Chapter3_README §1(4) 与 §3.3、期刊稿 §4.5「可扩展性」。

## 观察项（不是预设结论）

1. 候选数 n_cand 的截断是否真实发生，候选规模随 `α·K` 还是随任务规模变化；
2. 第二阶段耗时是否随 `N` 增长；
3. 与「不经过第一阶段、直接全量 MUAS」的对照相比，耗时与质量的差额；
4. 名额族与能力族在「吞吐—耗时—质量」平面上的位置。

上述四项**均以本次正式档 `metrics.json` 实测为准**；旧档结论不得外推。

## 实验设置

- **场景**：`guangzhou`，3 机巢 × 容量 2，K=6；任务规模 N ∈ {10, 50, 100}；
  满电航程 10 km、速度 15 m/s；UAV 驻留其机巢（与 exp01 `build_uavs` 同口径）。
  任务撒点 = 广州研究区 `region.geojson` 内均匀撒点（`domain: region`），
  与 exp01 同款；垂直切面代价 = DEM + 建筑屋顶包络（20685 栋）。
- **变体矩阵**（`variants`，各规模全跑）：
  - `quota_a2.5` / `quota_a5`：机数名额族，候选 n_cand=⌈α·K⌉（15 / 30），
    选择数 n_sel=min(⌈max_ratio·K⌉, cap(t))，intersect 口径；
  - `cap_x0.25` / `cap_x0.5` / `cap_x1`：能力驱动族，`mode=primary`，
    n_sel 接到 cap(t)=⌊budget_scale·B/c̃⌋（B=机队总航程 = 6×10 km = 60 km，
    c̃=最近可用机三维航程中位数）；**cap(t) 实际量级以 metrics 的
    `selection.capability_cap` 为准**，不沿用旧档 42。
  - `full_baseline`：不经过第一阶段直接全量求解（对照，N=10/50/100 全测）。
- **选择封顶**：exp02 自持的 `selection_config_from()` 只读取 yaml 的
  `alpha / weights / synergy_radius / wait_scale`；`MarginalConfig` 用代码默认
  `max_ratio=2.0` ⇒ 封顶 ⌈2.0×6⌉ = **12**。yaml 里写
  `selection.marginal.max_ratio` 是死键，勿写。
- **计时口径**：T = 端到端（hint + sel + build + muas）；
  edges = 惰性表现算边数（按需算 + 缓存，值与全量 eager 建表逐位一致）。
- **求解预算**（全格同口径）：DMDE pop=20 × 40 代 + polish ≤800 评估；单 seed 42。

## 口径（与当前生产一致）

- 目标：`f = W_hard·n_hard + w_exc·excess_m + W_skip·n_skip + w_late·L_tot
  + λ·path`；**完成是约束、成本是目标**；搜索用 f 不含 −m·F1，F3 已移除；
  `λ_cost=1e-1`（全实验统一）、`w_violation=0.01`、
  `late_reward_decay_seconds=1800`。
- 唯一距离 = 垂直切面 DEM + 建筑包络的三维**有向**航程；第一阶段选择注入
  统一距离尺子 `distance_fn`（depot 腿与代价矩阵同口径）。
- 二阶段参与政策 `participating_policy=demand`。
- 覆盖门禁 `coverage_gate=true` / `lexicographic_coverage=true`（config 显式）。
- 旧档「−m·F1_eff / w_time·F3 / 四层准字典序 / λ=1e-2」表述全部作废。

## 结果（2026-11-10 `guangzhou` 正式档，18/18 格，`status=ok`）

**跑批与产物**：`python -u run.py --no-plots`，单 seed 42，18 格全部完成后一次落盘，
`FORMAL_RC=0`，总墙钟 3h34m16s。产物为 `results/metrics.json`、`results/run_meta.json`、
`results/figures/`（6 张图 × png/pdf = 12 文件）与 `results/tables/`（4 文件，
由 `build_tables.py` 从 `metrics.json` 单源生成，脚本内不内置任何实测值）。

**口径边界（先写清楚，避免误读）**

- `F1 = muas.f1_reward` 是**事后交付收益计数**，不是搜索目标；搜索目标仍是
  单一加权标量 `f`（完成是约束、成本是目标；F1 事后计数、F3 已移除）。
- `n_executed` 是**单周期交付任务数**；未交付者记为 `n_deferred` 留待后续周期，
  本实验不模拟后续周期 ⇒ **不能读作完成率**，也不检验完成率 / 恒等式 /
  eliminating 等实验层硬断言（那些在 exp01、exp05 检验）。
- 墙钟与 `perf_counter` 分段计时是本机观测，**不可跨实验横向比较**；
  单 seed 42、未做统计检验、未做多重校正，**所有数值只作描述性记录，不宣称显著性**。
- 旧档（`lasa_chengguan` / K=8 / 80 km / λ=1e-2 / 含 F3）数值全部作废；
  **跨场景、跨实验禁直接比绝对值、禁配对 t 检验**。
- 完整 18 格数值以 `results/tables/exp02_main_results.csv` 为准，本节只摘关键格。

**逐项观察（对应上文「观察项」，均为本次实测）**

1. **候选截断确实发生，且规模随变体而非随 N 走**：N=10 时五个变体的
   `n_cand` 全被 N 本身限制为 10；N=50→100 时分别稳定在
   15 / 30 / 15 / 23→24 / 47→48。压缩比 `N/n_sel` 在 `quota_a2.5` 上为
   1.000 → 4.167 → 8.333，在 `cap_x1` 上为 1.000 → 1.111 → 2.083。
   名额族两档在 N=50/100 的 `n_sel` 逐位同为 **12**，`n_cand` 从 15 增到 30
   并未改变选择数——与代码默认 `max_ratio=2.0` 给出的 ⌈2.0×6⌉=12 封顶一致。
2. **端到端计时由二阶段主导**：`t_muas` 在名额族与 `cap_x0.25` 上跨三个 N
   保持 6.877–10.990 s；`cap_x0.5` 为 6.989 → 48.977 → 53.774 s；
   `cap_x1` 为 6.986 → 412.187 → 386.885 s；`full_baseline` 为
   5.684 → 462.403 → 11371.223 s。一阶段分段计时（hint 0.04–0.55 s、
   selection 0.07–2.98 s、build 0.03–8.04 s）在每一格都远小于二阶段。
   惰性表现算边数 N=10 全为 208，N=100 从 861（`quota_a2.5` / `cap_x0.25`）
   到 10918（全量）。
3. **与全量对照的差额不能单因素归因**：N=100 时 `cap_x1` 与
   `full_baseline` 的 F1 分别为 372.922 / 377.430，端到端计时为
   390.40 s / 11379.26 s。该计时差额**同时包含「参与求解的任务数不同
   （48 vs 100）」与「覆盖门禁在大规模解码上的实现代价」**，不能归因于
   选择层单一因素，也不是「性能提升」证据；若要分离须另做固定任务集规模、
   只切选择层的单因素消融，本实验不产生这类因果证据。
4. **两族在平面上的位置**：`cap_x*` 三档在 N=100 给出
   `n_sel` = 12 / 24 / 48、F1 = 107.584 / 208.424 / 372.922、
   T = 11.50 / 54.80 / 390.40 s，同向变化；名额族两档 `n_sel` 同为 12，
   F1 = 107.584 / 92.481、T = 11.86 / 8.65 s。实测 `capability_cap`
   在 10–48 之间随 `budget_scale` 与规模变化（中位数代表值 23）。
   本节只记录位置，**不判定「平衡点」「最优档位」，也不写两族等价或无差异**。

**运行期发现（方法学限制）**：`full_baseline` 的 N=100 一格耗时
11371.223 s（3h09m），而 N=50 同格为 462.403 s。原因是 `coverage_gate=true`
下每次适应度评估都调用 `repair_coverage`，其 `_check_insert` 对
「未交付任务 × 机 × 插入位」重算整条航次代价（约 O(N²) 量级），
再乘 8 轮门禁与全格同一求解预算。**该数受覆盖门禁实现代价影响，
不能当作「全量 MUAS 的固有复杂度」外推**。此外 `run.py` 要到 18 格
全部跑完才一次写 `metrics.json`，故跑批期间未中止、未改 `run.py`
（避免数值产物与代码版本不对账）。

**出图后逐张目检的版面修正**：修掉 S01 面板 (c) 长值文本压住参数名、
R01/R05 画布级形状图例压住脚注、R03 同值数据标签重复叠字、
以及 R02/R03/R04 图题残留的旧档预设结论措辞；另替换 Microsoft YaHei
缺字形的 `⌈ ⌉`。最终 12 个图文件无缺字、无叠字、无溢出。

## 图表与报告

`plot/` 按 exp01_smoke 同款骨架建成（registry 唯一事实来源、theme 唯一
import pyplot；本实验无逐周期数据，故无 dem/geo 底图）。
**2026-11-10 已把图注里写死的旧场景数值与旧档结论全部中性化**：
候选名额、选择封顶、cap(t)、机队总航程 B 一律从本次 `metrics.json` 现算，
图注只描述画了什么，不预设结论。

| 层 | 图 id | 说明 | 数据来源 |
|---|---|---|---|
| setup | `setup_params` | 实验设定总览：场景 / 变体矩阵 / 选择机制 / 求解预算 | `config.yaml 全段 + metrics.json 的 K / uav_max_range_m` |
| report | `r01_tradeoff` | 吞吐—耗时—质量权衡：全变体扫描散点（x=二阶段耗时，y=交付收益 F1） | `metrics.json 的 scales[] 全字段` |
| report | `r02_compression` | 规模压缩：候选数 / 选择数 / 执行数 / 压缩比随 N 的演化 | `scales[].selection.{n_tasks_in,capability_n_cand,n_selected} + muas.n_executed + compression_ratio` |
| report | `r03_time_breakdown` | 耗时分解与惰性边：分段计时与现算边数的规模对照 | `scales[].t_{hint,selection,build,muas}_ms + lazy_edges` |
| report | `r04_bottleneck` | 瓶颈定位：名额族候选规模与选择数封顶 | `scales[]（quota_a2.5 vs quota_a5 + selection.capability_n_sel）` |
| report | `r05_capability` | 能力驱动标定：cap_x* 阶梯的质量—耗时对照 | `scales[]（cap_x* 阶梯的 n_sel / F1 / t_muas + full_baseline 参考）` |

用法（与 exp01 同口径）：

- `python run.py --no-plots` 求解落盘（跑批纪律：求解与出图分离）
- `python render_from_json.py png,pdf` 从已落盘 metrics.json 补出图
  （`--figure r01_tradeoff` / `--group report` 可单出）
- `python plot/report/r01_tradeoff.py [--pdf] [--dpi N]` 单图独立调试
- `python build_tables.py` 从 metrics.json 生成汇总 CSV 与 LaTeX 三线表片段
  （单源生成，禁手写数字）

`results/tables/` 为本次正式档的表格产物，共 4 个：
`exp02_main_results.csv`（18 格明细）、`exp02_main_results.tex`（三线表片段，
按 N 分组）、`exp02_scalability.csv`（分段耗时长表）、
`exp02_scalability.tex`（N × 变体二阶段耗时三线表）。
LaTeX 表使用 `booktabs` 的 `\toprule/\midrule/\bottomrule`，表注的 K 与
满电航程从 `metrics.json` 现算，不写死场景数值。
旧 `report.tex` / `report.pdf` 属 10-25 旧档，已随旧结果一并归档，不参与本次结论。
