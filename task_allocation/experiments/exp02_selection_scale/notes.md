# exp02_selection_scale

**status:** active（2026-11-10 场景切换，正式档待回填）

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

## 结果

待本次正式档跑完后回填（`results/metrics.json` → `results/tables/*.csv` +
`*.tex`）。图件由 `render_from_json.py` 从已落盘 json 补出，不重跑求解。

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

`results/tables/` 为本次正式档的表格产物。旧 `report.tex` / `report.pdf`
属 10-25 旧档，已随旧结果一并归档，不参与本次结论。
