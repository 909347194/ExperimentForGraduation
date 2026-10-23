# exp02_selection_scale

**status:** active

## 目的

在 `|T_t| >> K` 的规模下，量化第一阶段「动态任务选择」的规模压缩收益，
并对候选/选择规模的标定口径（机数名额 vs 机队能力）做对照扫描。

`exp01_smoke` 跑在「机多任务少」一侧（10 任务 / 24 机，⌈α·K⌉=60），n_cand
截断从不触发，无法验证该机制；本实验固定 `K=8`（8 巢 × 容量 4），任务规模扫
10/50/100，机队满电航程 80 km（与 exp01 同口径）。

对应论文 Chapter3_README §1(4) 与 §3.3。

## 成功标准

1. 候选数 n_cand 的截断真实发生，候选规模随 `α·K` 而非任务规模变化；
2. 第二阶段耗时不随 `N` 增长；
3. 与「不经过第一阶段、直接全量 MUAS」的对照相比有可量化收益；
4. 规模标定口径可对比：机数名额族 vs 能力驱动族在「吞吐-耗时-质量」上的
   平衡点可定位。

## 实验设置

- **场景**：固定 K=8（8 巢 × 容量 4）；任务规模 N ∈ {10, 50, 100}；
  机队满电航程 80 km；UAV 驻留其机巢（与 exp01 `build_uavs` 同口径），
  任务锚定在机巢周围。
- **变体矩阵**（`variants`，各规模全跑）：
  - `quota_a2.5` / `quota_a5`：机数名额族，候选 n_cand=⌈α·K⌉（20 / 40），
    选择数 n_sel=min(⌈max_ratio·K⌉, cap(t))，intersect 口径；
  - `cap_x0.25` / `cap_x0.5` / `cap_x1`：能力驱动族，`mode=primary`，
    n_sel 接到 cap(t)=⌊budget_scale·B/c̃⌋（B=机队总航程，c̃=最近可用机
    三维航程中位数）；
  - `full_baseline`：不经过第一阶段直接全量求解（对照，只跑 N=10/50/100）。
- **计时口径**：T = 端到端（hint + sel + build + muas）；
  edges = 惰性表现算边数（按需算 + 缓存，值与全量 eager 建表逐位一致）。
- **求解预算**（全格同口径）：DMDE pop=20 × 40 代 + polish ≤800 评估。

## 口径（与生产一致）

- 适应度：`f = W_hard·n_hard + w_exc·excess_m − m·F1_eff + w_late·L_tot
  + λ·path + w_time·F3`，四层准字典序，T0 物理违约压倒一切；
  `λ_cost=1e-2`、`w_violation=0.01`、`late_reward_decay_seconds=1800`。
- 第一阶段选择注入统一距离尺子 `distance_fn`（depot 腿与代价矩阵同口径）。
- 二阶段参与政策 `participating_policy=demand`（按需求选机，非 cheapest）。
- 覆盖门禁双开关（`coverage_gate` / `lexicographic_coverage`）在 config 中
  显式暴露，默认关闭，行为与旧口径逐位一致；完整门禁口径 = 两者同开。

## 结果

待按当前口径跑完后回填（results/metrics.json → 表格 + 结论 + report.tex）。

## 图表与报告

`plot/` 按 exp01_smoke 同款骨架建成（registry 唯一事实来源、theme 唯一
import pyplot、base 只含 audit+declutter——本实验无逐周期数据故无 dem/geo）：

| 层 | 图 id | 说明 | 数据来源 |
|---|---|---|---|
| setup | `setup_params` | 实验设定总览：场景 / 变体矩阵 / 选择机制 / 求解预算 | `config.yaml 全段 + metrics.json 的 scales[].selection 字段` |
| report | `r01_tradeoff` | 吞吐—耗时—质量权衡：全变体扫描散点（x=二阶段耗时，y=交付收益 F1） | `metrics.json 的 scales[] 全字段（variant × N × F1 × t_muas）` |
| report | `r02_compression` | 规模压缩与 N 解耦：候选数 / 选择数 / 执行数 / 压缩比随 N 的演化 | `metrics.json 的 scales[].selection.{n_tasks_in,capability_n_cand,n_selected} + muas.n_executed + compression_ratio` |
| report | `r03_time_breakdown` | 耗时分解与惰性边：二阶段耗时与 N 无关，O(n²) 构建瓶颈已除 | `metrics.json 的 scales[].t_{hint,selection,build,muas}_ms + lazy_edges` |
| report | `r04_bottleneck` | 瓶颈定位：候选数 α 无感、选择数被 max_ratio·K 封顶 | `metrics.json 的 scales[]（quota_a2.5 vs quota_a5 同 n_sel 对照 + selection.capability_n_sel）` |
| report | `r05_capability` | 能力驱动标定：n_sel≈cap(t) 时质量保持率高、加速比大，cap(t) 是天然平衡点 | `metrics.json 的 scales[]（cap_x* 阶梯的 n_sel / F1 / t_muas + full_baseline 参考） |

用法（与 exp01 同口径）：

- `python run.py` 末尾自动出图（config 的 `plot.formats/dpi`；`--no-plots` 跳过；
  `--formats pdf --dpi 200` 覆盖）
- `python render_from_json.py png [--figure r01_tradeoff] [--group report]`
  从已落盘 metrics.json 补出图（不重跑求解）
- `python plot/report/r01_tradeoff.py [--pdf] [--dpi N]` 单图独立调试

`report.tex`（latexmk -xelatex 编译）：实验设置 → 主要结果（总表 + 结论 +
已知边界）→ 图表 → 附：数据与复现。
