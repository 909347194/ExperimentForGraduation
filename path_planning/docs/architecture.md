# 架构与关键实现口径

本文记录**分层结构**与**会静默出错的口径决策**。详细公式见
`conflict_model.md`（冲突判定）与 `time_mapping.md`（时间映射）；
周期／航次／航段／轨迹段四层术语见 `../README.md` §1.1。

---

## 1. 分层与数据流

```
data/guangzhou (DEM / building / nest / nofly)
  └─> common/scenario.py        场景装配（机巢、地形、建筑、禁飞区 → 局部 ENU）
       ├─> common/grid_spec.py     栅格坐标契约（轴对齐 + 斜栅格统一）
       ├─> methods/utils/          坐标变换、DEM 米制网格、建筑图层
       └─> methods/spatial/
            ├─ occupancy_sdf.py    障碍棱柱 → 占位栅格 → 两遍 EDT → SDF
            └─ world_field.py      **全域一次建场** + 窗口取样
                 ├─> corridor_window.py   走廊斜栅格（ROI + margin）
                 ├─> rrt_path_search.py   多初值几何路径
                 └─> methods/timeline/    单机时空联合 NLP（CasADi + IPOPT, min ΣΔt）
                      └─> methods/planning/tour_planner.py  按停靠点串段 + 悬停拼接（单段在 leg_planner.py）
                           └─> methods/planning/delivery_gate.py  交付闸（全域 0.2 m 稠密 + 真几何）
                                └─> 预算判定 → trajectory（带时间，经 api.py::PathPlanner 出面）
                                     └─> methods/trajectory_registry.py  注册 + 跨周期遗留
                                          ├─> methods/conflict/       冲突检测（时空解耦）
                                          │    └─> methods/coordination/  空间让路 + 局部重规划
                                          └─> busy_until / prune  →  orchestration  周期末结算
```

`api.py` 是唯一对外门面（`plan` / `plan_fleet` / `coordinate` / `busy_until` / `prune`）：上层不再拼
"当前在飞集合"，那是 `TrajectoryRegistry` 的事；也不再自己决定"先排后调还是边排边调"，
`plan_fleet` 已经按"先落地结算 → 排本批 → 取 Registry 在飞旧航迹 → 统一协调 → 只回写本批"走完。
旧侧冻结由 `movable_ids` 白名单显式指定（旧机号可大于新机号），不是"机号大者让路"的副作用；
协调前后证据经 `FleetPlan.existing / pre_report / frozen_ids` 交实验层审计。

两条不可混的口径：

| 层 | 时空关系 | 做什么 |
|---|---|---|
| 单机层（timeline） | **必然耦合** | 几何、速度、时间联合优化，目标 min ΣΔt |
| 协调层（conflict / coordination） | **必须解耦** | 冻结出发时刻与速度口径，只改空间几何；时间由单机层重解 |


失败码分两层（`common/plan_types.py` 的 `PlanResult` docstring 是唯一口径表）：

- **调用方填错**（坏数值、重复 stop id）→ 构造期 `ValueError`，不建窗口、不进流水线；
- **这条航段飞不了**（续航/航程下界就超）→ `ENDURANCE_EXCEEDED` / `RANGE_EXCEEDED`，
  由准入层（`planning/admission.py`）在候选初值之后、进 NLP 之前判死（`tried=0`），
  必要时走降级交付；
- `TIME_EXCEEDS_BUDGET` 只表示**求解预算**（wall-clock / 迭代数）超限。

---

## 2. SDF 为什么是全局的

参考实现 `path_planning1/core/world_field.py` 给出的结论（原注释）：

> 以前每个航段都把窗口内的楼重新栅格化 + 两遍 EDT（2.8 km 约 3–4 s）。这里把这一步
> 挪到**进程启动**：按全域轴对齐栅格算一次，之后每条 OD 只做一次三线性取样
> （约 0.2 s），RRT / NLP 的输入形状与以前完全一样。

按窗口现算时，一条 OD 要试多个初值、每个初值可能多次重规划，建场开销会压过优化本身。
本项目实测（广州 4.8 × 4.7 km，`spacing=(4,4,2)`）：

| 指标 | 实测值 |
|---|---|
| 栅格形状 | `(1201, 1179, 121)` |
| 节点数 | 171,333,459 |
| 竖直范围 | 3.3 – 243.3 m（兜住 224.5 m 最高楼） |
| 建场耗时 | **85.4 s**（一次性） |
| 常驻内存 | **857 MB**（`sdf` float32 z-major + `occupancy` bool） |
| 走廊窗口取样 | `(710, 11, 40)` → **17 ms** |
| 占位比例 | 15.13%（与 `sdf<0` 比例一致，符号未接反） |

代价：全域栅格是**轴对齐**的，走廊窗口是**斜**的，取样必然跨格插值——同一几何在两种
基准下 SDF 数值不同。因此场参数（origin / spacing / shape / bias）必须进结果指纹
（`field_signature`），否则缓存会把"另一套 SDF 基准"的解当成同一条 OD 的解静默复用。

`world_field.check_coverage()` 在启动自检与每次取样前验证窗口八角点落在场内（容差半格）。
不能靠 `map_coordinates(mode="nearest")` 兜底：出界时它会静默取边缘值，返回一个看起来
正常的解。

---

## 3. 离散化与约束口径

1. **第 2 轴是高度**。`(4,4,2)` 意为水平 4 m、竖直 2 m。轴序接反不抛异常，只会让竖直方向
   按 4 m 离散、水平按 2 m，净空判据静默偏移。
2. **正值 = 自由空间**，单位米。`edt(~occ)` 给自由区距离、`edt(occ)` 给障碍内部深度；
   接反后整张 SDF 退化为 0，路径笔直穿楼。
3. **在完整高度上算完再裁带**。若先裁飞行带再算 EDT，带底/带顶的距离被栅格边界截断成 0，
   贴带飞的路径会被判为"全程贴障碍"。

### 3.1 保守膨胀

每个 footprint 先 `buffer(半格对角线 / cos(π/(4·quad_segs)))`，竖直区间各外扩半格，再判
**格心**。两个理由：

- **整栋漏检**：只测格心时，比格距还窄的房子可能一个格心都不落进去。膨胀半径
  ≥ 格点最大空隙 `0.5·√(sx²+sy²)` 后，任何棱柱至少含一个格心。
- **障碍被缩**：裸格心判据让障碍最多"缩"半格，SDF 比真几何乐观。膨胀后障碍只大不小，
  SDF 成为保守下界。

实测印证：屋顶上方 2 m 名义几何应为 `+2.00`，实际 `+2.55`（膨胀抬高顶面 0.5 m 后为
`2.5`，再由三线性插值微调）。

### 3.2 节点密度与段内约束（2.8 km 航段的实测教训）
**节点满足净空 ≠ 航迹满足净空。** NLP 的净空约束写在节点与每段的
`clearance_samples_per_segment` 个内插点上；Hermite 在节点之间仍可鼓出。2.8 km 航段、
60 节点（间距 ~48 m）、段内 3 点时，独立复核实测最小净空 **−9.7 m**：节点上都是 +6，
曲线在节点之间穿进了楼。把节点数提到 120（间距 ~24 m）仍不够（−5.4 / −7.5 / −7.8 m），
必须同时把段内约束加密到 **8 点（间距 ~2.6 m，与复核的 2 m 采样同量级）**才通过。
代价是单段求解从几秒涨到 4–5 分钟——这是"2.8 km 一条腿"的固有成本，不是可以随手
调小的参数。

推论（写进配置注释）：`n_nodes` 与 `clearance_samples_per_segment` 必须按**段长**定，
不是全局常数。短段（≤300 m）用 60/3 即可；城市长航段（km 级）必须 120/8。

**飞行带是真高 `[10, 120] m`，不是绝对高度。** 口径来自 `PlanParams.alt_agl_min_m/alt_agl_max_m`
（10 m 是低空作业的行业惯例下限、120 m 是真高天花板）。NLP/RRT 需要的是**绝对** z 区间，
由运行时按"全城最低地面"派生：`lo = ground_min + 10`、`hi = min(场顶 − 1 格, ground_min + 120)`
（本场景 `ground_min = 3.302` ⇒ `[13.302, 123.302]`）。三条必须分开看：

| | 取值 | 由谁卡 |
|---|---|---|
| 飞行带（真高） | 真高 ∈ [10, 120] m | `PlanParams.alt_agl_*`；端点由 `plan_tour` 逐点准入 |
| 飞行带（绝对 z，派生） | `[ground_min+10, ground_min+120]` 截到场顶 | `NlpConfig.alt_range_m`、`VerificationLimits.alt_range_m`、RRT 采样包络 |
| 建场上界 | `max(120, 最高地面, 最高楼顶)+dz`（实测到 243 m） | 只为盖住全部障碍、让 SDF 不乐观，**不放宽飞行带** |

广州全域最高楼顶绝对高程 221.87 m，但**航路区域最高楼顶只有 112.98 m**，真高 120 m 带留 6–7 m
净空仍可跨越；远端超高楼不在航路上，建场覆盖它们只为 SDF 正确。若将来任务点落到 120 m 以上的
楼区，应绕行而非抬高飞行带。

**派生的绝对带是"全城最低地面"近似，不是逐点真高约束。** 用全域最低地面换算保证"任何点
z ≥ lo 时 `AGL(worst case) ≥ 10`"，但**不蕴含**逐点 `AGL ≥ 10`：飞过 16 m 高的地形时，
z=13.3 m 的点位真高只有 −2.7 m。要逐点严判必须把 DEM 作为 z 的可变下界塞进 CasADi（每节点一次
地面插值），代价是 NLP 规模与收敛风险都上一个量级，当前不采用。补偿手段是端点处**逐点**判
（见 §4），航段中段由 `clearance_m` 与"地面在 SDF 中是实体"共同兜住。

**上界必须写在交付曲线上，不能只钉节点。** 交付的是 Hermite 段，节点 z 全在带内时
曲线仍会鼓出去：节点贴顶、`v_z≠0` 时段中点偏移 = `Δt·(v_z0−v_z1)/8`，实测节点
≤103.30、曲线到 **110.42**。故 z 带按交付曲线在段内 `s∈{0.2,0.4,0.6,0.8}` 上
一起约束（`trajectory_assembly.hermite_axis`，与交付段同一基函数）。这与"节点满足净空 ≠ 航迹满足净空"
是同一个结构性问题：NLP 内部用节点线性插值，交付是 Hermite。

### 3.3 净空三层纪律与走廊半宽

净空不是一个数字，而是**三个必须分开的口径**；混用会让"报告变好看"和"真安全"脱钩：

| 层 | 取值 | 用途 | 落点 |
|---|---|---|---|
| 交付 / 审计 | `clearance_m = 6.0` m | 论文指标、验收判据（真几何） | `delivery_gate`、`VerificationLimits`、实验 audit |
| 求解（RRT 与 NLP） | `clearance_m + sdf_quant_tolerance_m = 6 + 2` | 只在优化器里用，给 SDF 量化留余量 | `RRTConfig.clearance_m`、`NlpConfig.clearance_m` |
| 重解（真几何不达标时） | 再叠 `local_replan_clearance_fix_m = 2` | 逐级抬阈值直到真几何过关 | `leg_planner` 的净空重解档 |

`+2 m` 就是竖直格距 `field.spacing_m[2]`：SDF 量的是**到体素中心**的距离，屋顶被夹在两格心之间时
读数最多乐观一格，不加容差等于把这一步乐观误差直接交付。反过来说，交付阈值**不能**直接抬到 8 m
——那是把"余量"写进论文的指标，会在审计里替求解器的乐观背书；交付一律走真几何（`prism_clearance`）。

**走廊半宽 = `max(0.5·width_ratio·L, half_width_min_m)`**，`width_ratio = 0.20`（与参考实现 pp1 同口径），
`half_width_min_m = 20 m`。纯比例在短航段上会退化成"一条直线"：本实验八条航段（30.0 / 77.9 /
123.8 / 250.0 / 250.0 / 360.9 / 549.6 / 77.2 m）的 `0.5·0.2·L` 只有 3.0 / 7.8 / 12.4 / 25 / 25 /
36.1 / 55.0 / 7.7 m；加 20 m 下限后为 20 / 20 / 20 / 25 / 25 / 36.1 / 55.0 / 20 m。
两道配套闸门：半宽 ≤ `clearance_m` 的走廊是几何空集，`leg_planner` 在建 RRT 前直接报
`CORRIDOR_TOO_NARROW`（而不是让 NLP 报 `OPT_FAILED`）；`margin_m = 10` **只用于取窗时外扩建场**，
不是交付约束（`corridor_window` 已把它与半宽严格分开）。

**SDF 轴序：内层与复核必须读同一个场。** `WorldField.sdf` 存 **z-major
`(nz,nx,ny)`**（按层取样快一个量级），而 `SdfOnGrid` 要按 `(nx,ny,nz)` 展平成
"i 变化最快"喂 `interpolant`。局部重规划现先按折线外接盒取**与全域格点对齐**的
窗口场（`WorldField.window_for_points`），再送进内层；窗口节点值来自全域 SDF，
不是另一套近似场。早先漏了转置，等于整场换了坐标：同一点内层读 68.62 m、复核读 53.01 m。症状极具
误导性——局部让路候选在 120/8 下全部 `Maximum_Iterations_Exceeded` /
`Infeasible_Problem_Detected`（同一候选 40/3 却能收敛），节点被推到 155 m、弦上读到
−60 m，看着像"求解器不行"。现由 `SdfOnGrid` 归一化两种布局并对形状不符抛错，
`test_sdf_on_grid_matches_world_field` 用不对称小场逐点对拍守住。

---

## 4. 端点：模型假设 A4、真高准入、净空不豁免

**A4（模型假设，写进论文）**：机巢作业（地面→巡航高度的垂直起降、悬停装卸）**不在本模块
规划域内**。规划区间为 \([t_0, t_0+T]\) 内**自起点悬停点至终点悬停点**的空中航段；起降安全由
机巢选址（巢周净空、巢高落真高带内）与离巢时序（时隙）保证。`request.t0` 的语义因此是
"已离巢并稳定悬停于起点"的时刻，**不是**离地瞬间。

**点位高度由任务产生侧解出，不在本库**（`experiments/exp01_smoke/support/waypoint_policy.py`）：
基线 = 巢址所在表面（地面与屋顶取高——巢在楼顶就按楼顶）或作业面（屋顶，无楼即地面）；
加间隙（离巢 15 m、作业面上方 10 m）；再按**净空阶梯**（步长 = 竖直格距 2 m）抬到 SDF 与
棱柱净空都 ≥ **求解阈值**（交付 6 + 量化容差 2 m）为止，抬到绝对上界仍不达标就报错换点。
`methods` 只接收已合规的三维点并做上面的入口校验，不做任何高度兜底（用户裁定 2026-11-23：
点位选得对不对是调用方的职责）。本实验由此得到的端点绝对高度 20.3–44.7 m（AGL 10–31.9 m）。

**端点准入（`plan_tour` 入口，先于任何求解）**：起点与每个停靠点必须同时满足

1. 是**空中悬停点**：`v0 = vN = 0`（NLP 与复核两端都钉死零速，故"悬停"是硬约束不是口号）；
2. 绝对高度落在 `NlpConfig.alt_range_m`（`[13.302, 123.302]`）内，且真高逐点落在
   `[10, 120] m`：`AGL = z − ground_height(x, y)`，**逐点**取局部地面。

违反即返回 `ENDPOINT_OUT_OF_BAND`（`INFEASIBLE` 级，不进 RRT/NLP，`detail` 写明标签、
z、地面、真高与区间）。必须逐点而非"全城最低地面"：航线跨高地形时，用全域最低地面会把
"贴山顶飞"判成合格（§3.2 已给出量化例子）。实验侧 `run.py::precheck_points` 用同一份
`ctx.agl_range_m` 做开跑前预检，口径与准入同源。

**本实验端点净空不豁免**：`endpoint_exempt_radius_m = 0.0`、`endpoint_clearance_m = 6.0`，
即端点与全域同判真几何 6 m（实测起点/停靠点 SDF ≥ 8.35 m、棱柱 ≥ 8.31 m——比交付阈值多出的
那一格正是生产侧按求解阈值抬点的余量）。
理由：端点现在是**空中悬停点**，不在巢面，没有"机巢贴地导致净空天然为 0"的结构性问题。

### 4.1 离巢错峰：A4 的"时序"落成一个参数

同刻同起点的两条航迹，\(t=0\) 处两个悬停点**距离恒为 0**，检测器必判冲突；而端点被
`x[:,0]` 钉死、协调层的候选只动中间节点，动不了那个重合点——实测平移 `t0` 到同刻即
`safe=False, min_distance=0.0`。A4 已把起降移出规划域，起降安全本来就该由离巢时序保证，
所以把它做成配置：**同一周期内起点相同的航次按机号升序错峰**，
\[
t_0^{(k)} = t_0^{\text{cycle}} + k \cdot \texttt{depart\_stagger\_s},
\]
默认 \(10\) s（工作区间 10–20 s；置 0 即关闭，同刻同起点的问题立刻复现）。不同起点不参与
——它们不共享悬停点，推迟只是白等。

**为什么不能靠"把时间窗错开"**：C1 时间分离证书只比各航迹的**整体**时间窗
（`a_lo/a_hi` 对 `b_lo/b_hi`），错开 10 s 时窗口照样重叠（实测 \(b.t_0=10 < a.t_\text{end}=23\)），
C1 根本不触发，判定落到 C3 的逐弦 CPA。真正让结果安全的是**同一时刻的位置差**：10 s ×
10.87 m/s ≈ **108.7 m**，判定半径只有 **20.50 m**（椭球 20/20/10 + \(e_{geo}=0.5\) 的合成，
比"30 m 口径"更保守）；实测 6 s 就够（65.2 m），10 s 留约 5 倍余量。同速同向才适用这个
估算：一前一后同向爬升/巡航时两者相对距离近似恒等于 \(v\,\Delta t\)。

**边界**：错峰只解决"同时刻同起点"。若两机**共享终点**、且先到者在终点悬停的时间超过错峰量，
仍会在终点重合（机7 与机4 正是同起点同终点，本实验靠**分周期**拉开 240 s 规避）。
这类"同点等待"要靠服务窗口/时隙，不是相位错峰能解决的；同样，相向或交叉航路仍须走协调层。

**本实验的实测**：seed 0/1/2 的两个周期里都没有"同周期同起点"组（三台的起点互不相同，
机7 与机4 同起点但分属两个周期），因此所有偏移都是 0，`t0` 与上一版逐位一致；机制由
`tests/test_depart_stagger.py` 覆盖（错峰语义、同起点三台 0/10/20 s、跨周期不叠加、
以及"6 s/10 s 都能靠几何过、C1 不参与"）。

**豁免机制仍然保留**，只对"从巢面/地面起飞"的场景有意义：`delivery_gate` 与实验的
"稠密净空"判据都支持剔除距端点 `endpoint_exempt_radius_m` 球内的采样点，球内按
`endpoint_clearance_m` 单独判、球外仍按 `clearance_m` 判；NLP 侧对应的窗口是首末
`k` 段（`feasibility.py` 的 `i<k or i>=n-k` 跳过）。两级不能互相顶替：NLP 豁免是为了
"解得出"，交付豁免是为了"别把机巢地面那几个点算成违规"。

下面是"若从巢面直接规划"时实测到的机巢净空，可见豁免在这类场景中是必需的（本实验不用）：

| 机巢 | 位置 z | SDF(点) | SDF(z+6) |
|---|---|---|---|
| nest0 | 13.1 | **+1.63** | +7.82 |
| nest1 | 12.0 | **−0.63** | +6.69 |
| nest2 | 11.0 | **+1.47** | +7.56 |

> 这三个数是**当前数据集**的实测值，会随机巢位置调整而变化（换巢流程见 `README.md §7.1`）。
> 代码不写死坐标：机巢来自 `data/guangzhou` 的场景数据，实验只声明 `home_nest`。

同时实测：机巢 0 → 机巢 1 的**直线**最小 SDF 为 **−34.29 m**，即直线必然穿过多栋建筑。
这就是 RRT 与连续优化的必要性证据（不是"可能穿"，是"一定穿"）。这条同样是数据集结论，
换巢后要重测（`exp03` 的"巢2↔巢0 走廊被楼群切断"同理）。

---

## 5. 协调层的四处结构性坑

都是"看起来实现了、跑起来等于没做"的静默失效，属性各不相同，必须分开记：

1. **位置是自由变量，让路位移会被 min ΣΔt 抹平**。内层 NLP 只钉住两端点，其余节点是
   决策变量，目标是最短时间——不给约束时它必然把折线拉回直线段，于是"候选几何"只活在
   初值里。修法是 `NlpConfig.guide_radius_m`（几何引导管：``‖x_i − path_i‖ ≤ r``），
   协调层把它设成 `params.local_replan_guide_radius_m`；**初规划保持 None**，因为 RRT
   折线本来就该被拉直。判据：所有候选时长几乎相同 + 让路量在轨迹里查不到。
2. **候选必须对“对手航迹”取证，不能只验自身可行性**。只做自身复核时 24 档全 accepted，
   而竖直 8 m 不够椭球要求的 10 m（当前 T 空间口径）——于是每次协调迭代都挑最便宜那档，冲突永远不动。
   `replan_locally(..., others=..., detection=...)` 把同批次其余航迹一起送进证书判定。
3. **初值几何必须"沿当前轨迹采样"，不是取节点**。单段轨迹的 `trajectory_nodes` 只有两个
   端点，而端点冻结，于是让路候选中没有任何可动节点，`replan_locally` 永远返回
   `NO_LOCAL_SOLUTION`；跨协调迭代时还必须以"上一次迭代已经让开的那条线"为起点，否则改完撞回去。
   `global_recheck._polyline_of` 按 5 m 步长沿轨迹采样。
4. **一次 NLP 只能解"一段"，多段航次必须按航段重解再拼接**。早期实现把整条 tour 的采样
   折线交给一次局部位移求解，从起点一路解到终点：途经停留点与悬停被当成"可抹平的曲折"
   压掉（实测 3 段/含 120 s 悬停 → 1 段/飞行时长 229 s → 87 s），压段后交付的航次不再是
   同一条任务。修法：`coordination/leg_stitching.py` 按悬停切块（`leg_blocks` / `legs_of`），
   `local_replan` 只取含冲突时刻的那一段（`target_index(legs, target_time)`），
   新旧段端点按 1e-6 校验后 `stitch_leg` 拼接、后段整体 `_shift(dt)` 顺移（时长不变 ⇒
   速度剖面不变）；`meta` 记 `legs` 与 `replanned_leg` 留痕。判据：交付航迹的悬停总时长
   必须等于清单里 `service_time_s` 之和（压段时这条必红）。

另有一条**场景可解性前提**（不是算法缺陷）：椭球水平半轴 20 m（2026-11-23 起的生产默认）时，
若被让路航线的端点离对方航线不足 20 m，则任何"端点冻结"的局部位移都消不掉冲突——
起点本身就在安全泡内。旧文档基于 30 m 的算例须按新半轴重新标定。
合成测试的场景必须让端点落在泡外（例如 90 m 长的十字交叉），否则测的是"无解场景"。

---

## 6. 文件职责与命名口径

命名统一用**完整的词**，不用缩写或过于宽泛的单字（`field.py` 这类会被改成
`occupancy_sdf.py` / `world_field.py`）。

| 文件 | 职责 |
|---|---|
| `common/vec3.py` | 三维向量与有限值校验（`check_vec3`） |
| `common/segment.py` | `HermiteSegment`：两端位置/速度/**指令加速度**（`a0/a1`）、悬停段校验 |
| `common/trajectory.py` | `Trajectory` + `TimeSource`：时间量属性与 `validate()` |
| `common/plan_types.py` | 请求/结果（`PlanRequest` / `PlanResult` / `PlanStop`）+ 上述类型的再导出；失败码或状态维度表在 docstring |
| `common/plan_params.py` | 参数体系（空间 / 时间 / 检测 / 协调 / 策略）与校验 |
| `common/failure_codes.py` | 13 个失败码、严重度分级与处置建议 |
| `common/flight_budget.py` | 续航 / 航程双口径预算判定 |
| `common/time_intervals.py` | 半开区间与区间集合运算 |
| `common/vehicle_spec.py` | 机体规格与推力/加速度派生量 |
| `common/scenario_types.py` | 场景/机巢/禁飞区数据类，高程同源查询 |
| `common/scenario_loader.py` | GeoJSON / CSV / 地形装配（禁飞区 id 加文件前缀） |
| `common/terrain_hole_filling.py` | 地形 NaN 空洞四邻扩散填充 |
| `common/scene_html.py` | 全域三维**交互** HTML 后端（Plotly：地形/建筑/航迹/悬停球；exp01 `plot/p03` 与 exp07 `--html` 共用，静态投稿图仍走各实验 `scene_3d.py` 的 VTK） |
| `common/grid_spec.py` | 规则三维栅格契约（轴对齐 / 斜栅格） |
| `methods/utils/*` | 坐标、DEM GeoTIFF、米制地形、建筑图层（自包含复制） |
| `methods/spatial/occupancy_sdf.py` | 棱柱栅格化 + 两遍 EDT + 单栅格查询 |
| `methods/spatial/world_field.py` | 全域场对象、三线性取样、覆盖契约 |
| `methods/spatial/world_field_builder.py` | 建场（竖向包络、禁飞区、节点上限） |
| `methods/spatial/field_contract.py` | 场契约异常 |
| `methods/spatial/field_artifact.py` | 场指纹（可复现性） |
| `methods/spatial/corridor_window.py` | 走廊半宽规则、斜栅格窗口、建场外扩 |
| `methods/spatial/rrt_path_search.py` | RRT 采样（窗内、SDF 自适应步长、端点豁免、多初值） |
| `methods/spatial/rrt_config.py` | RRT 参数结构 + `from_params` 派生（无独立默认值） |
| `methods/spatial/initial_paths.py` | 候选评估 / 去重 / NLP 初值排序 |
| `methods/spatial/sdf_casadi.py` | SDF → CasADi 三线性插值转接头（NLP 净空读数；窗口场 `(nx,ny,nz)` 与全域场 z-major `(nz,nx,ny)` 都归一化后再展平）；缓存键按场对象 + 栅格契约，缓存本身挂在 `TourPlanningContext` |
| `methods/spatial/prism_clearance.py` | 真几何净空闸（STRtree + 方框粗筛 + 竖直区间，`sqrt(水平²+竖直²)`） |
| `methods/timeline/splines.py` | Hermite 求值 / 解析导数 / Gauss 弧长 / 按时刻查询；`segment_acceleration`（曲线 `p''`）与 `segment_command_acceleration`（按节点 `a` 端点插值，**判据用这个**） |
| `methods/timeline/initial_guess.py` | 弧长重采样 + 逐节点 (v,a,Δt) 初值 |
| `methods/timeline/nlp_config.py` | NLP 旋钮 `NlpConfig`（几何引导管 `guide_radius_m` + 绝对 z 飞行带） |
| `methods/timeline/trajectory_optimizer.py` | 主 NLP（min ΣΔt、口径 B、三级退让）；求解与约束 |
| `methods/timeline/trajectory_assembly.py` | 节点解 → 交付段：三次 Hermite 装配（带 `a0/a1`）、z 带采样点 `hermite_axis` |
| `methods/timeline/feasibility.py` | 独立复核：速度三项/加速度/推力/净空/走廊/端点/z 飞行带/Δt/弧长相容（加速度与推力按**指令加速度**判；走廊名义半宽只告警，越出计算域 `half+margin` 才报 `CORRIDOR_OUT_OF_DOMAIN`） |
| `methods/timeline/fallback.py` | `OPT_FAILED` 降级巡航剖面（`time_source=estimate`，保守 1.2） |
| `methods/conflict/chord_sampling.py` | Hermite → 匀速弦（递归细分 + 实测偏差） |
| `methods/conflict/cpa_geometry.py` | 匀速直线对闭式 CPA、时间窗交集 |
| `methods/conflict/ellipsoid_metric.py` | 椭球 → T 空间球（包围盒余量、判定半径） |
| `methods/conflict/pair_certificates.py` | 三级证书 + 合并 CPA（阈值严格互补） |
| `methods/conflict/conflict_detector.py` | 全对检测、弦化缓存、证书统计 |
| `methods/conflict/conflict_clusters.py` | 并查集连通域（单点改 vs 多点同时改） |
| `methods/coordination/local_region.py` | 局部域（CPA 中点 + 余弦钟形过渡权重） |
| `methods/coordination/detour_candidates.py` | 让路候选几何（垂直优先、端点冻结、边界权重归零） |
| `methods/coordination/local_replan.py` | 候选逐一重解时间 → 复核 → 真几何净空 → 对手证书（首个通过即停） |
| `methods/coordination/global_recheck.py` | 检测-聚类-让路-全对复检的迭代编排（默认机号大者让路；跨周期按 `movable_ids` 显式指定可动侧） |
| `methods/planning/leg_planner.py` | 单航段：走廊 → 场 → RRT 多初值 → **准入** → 逐候选 NLP（含净空重解）→ 复核 → 真几何闸 |
| `methods/planning/admission.py` | 准入 0 秒拒：`leg_lower_bounds`（时长/航程必要条件）+ `admit_leg`（`ENDURANCE_EXCEEDED` / `RANGE_EXCEEDED`） |
| `methods/planning/tour_planner.py` | 单航次编排：按停靠点串段 → 降级 → 全域交付闸 → 预算判定 |
| `methods/planning/delivery_gate.py` | 交付闸：整条航迹在全域场 0.2 m 稠密净空 + 全域真几何；不过即 `CLEARANCE_VIOLATION`（不带航迹） |
| `methods/planning/leg_segments.py` | 航段原语：纯竖向段（`v_up`/`v_dn`）、悬停段（`is_hover`）、沿弧长采点 |
| `methods/trajectory_registry.py` | 跨周期航迹注册表（`busy_until`、在飞集合、落地注销、整体平移） |
| `api.py` | 顶层唯一门面 `PathPlanner`：`plan` / `plan_fleet` / `coordinate` / `busy_until` / `prune` |
| `experiments/<exp>/support/` | 每个实验私有：配置解析、建场缓存、机队清单、指标、审计、质量报告、主流程（实验间不共享） |

> 多周期派活（`CyclicTaskAllocator`）不属于本模块：它是环的上层端口实现，已迁到
> `orchestration/allocators/cyclic.py`，实验层只负责把配置里的任务点转成 `LoopTask`。

---

## 7. 数据侧已修正的坑

记录一次，避免重复踩：

- `building/*.dbf` 是 **GBK**；pyshp 默认 UTF-8 会抛 `dbfFileException`。
- 机巢 CSV 是**单字段** `"23.111467N 113.307078E"`（**纬度在前**，列名 `Coordinate`，
  文件带 BOM）。顺序搞反不报错，只得到无意义高程。
- DEM `nodata=255`，`to_metric_terrain` 置零并记权重，四邻全无效时得 `0/0 = NaN`。
  实测 158 × 161 网格有 **10.89%** 空洞（含内部一整块约 900 格）。不补的后果是
  **静默放行**：净空判据 `z_clear + clearance ≤ ground` 与 NaN 比较恒为 False。
  已由 `terrain_hole_filling.fill_terrain_holes()` 以四邻扩散补齐（13 次迭代，残量 0）。
- `meta.bounds` 的 right/top 是"节点外沿"，`query` 在 `x == right` 处越界返回 NaN。
  可规划范围必须收到**节点包络**（`data_bounds_xy`），否则最外 30 m 一圈整圈是空洞。




