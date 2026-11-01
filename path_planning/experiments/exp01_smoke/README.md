# exp01_smoke — 整城全链路验证（第4章 B 路线：可见性图 / 稀疏路图）

验证链路：**场景（广州真实建筑）→ 离线建图（visibility_graph / 稀疏路图，图只建一次、长期复用）→
OD 规划（auto 随机 + diag 强制横穿动态禁飞 + time_control 延迟起飞）→ 平滑 + 轨迹 + 独立复核
→ metrics.json + 图**。这是**正确性调试**，不是性能/灵敏度实验（那是 exp02/exp03）。

## 一个入口：run.py（三种跑法）

| 跑法 | 命令 | 用途 | 耗时 |
|------|------|------|------|
| 正档（整城） | `python experiments/exp01_smoke/run.py --plot` | 整城建图 + 多组 OD 正确性验证，出 `metrics.json` + 6 张标准图 | 建图约 15–20 min |
| 快速冒烟 | 同上追加 `--set scenario.crop=auto --set visibility_graph.max_nodes=4000` | 改在 600m 最密小窗跑同一套验证，用于迭代 | 秒级 |
| 整城演示 | `python experiments/exp01_smoke/run.py --full-city` | 粗网格全范围跨城 OD 示意图 | 约 10 s |

`--full-city` 会强制 `crop=full`，并套用 `config.yaml` 里 `full_city_viz.graph` 的粗网格参数
（跳过建筑壳节点 → 建图秒级），产出两张整城图：

- `fig_full_city_3od.png` —— matplotlib 静态俯视图（论文排版用）；
- `fig_full_city_plotly.html` —— **plotly 交互版**：全城 20,655 栋建筑栅格化底图 + 3 条跨城 OD
  矢量叠加，可缩放/平移/hover（hover 显示 route 距离 / path 弧长 / 绕行百分比）。

它是**示意性全城图**：路径绕开建筑本体但不带常规飞行余量；精细全图见 exp02_vg 的 104k 节点验证。

> 旧的第二入口 `full_city.py` 已于 2026-10-30 移除、能力并入 `--full-city`。它此前把 10 个参数
> 内联在脚本里，与 `config.yaml` 形成两套互不一致的标定（如 `d_build` 3 vs 5、`edge_margin` 0 vs 2），
> 容易分不清哪个是正端口径。旧文件在 git 里仍可找回。

配置：单一 `config.yaml`。`scenario.crop` 三态：`full`/`none`（整城，默认）| `auto`（自动最密小窗）|
显式 `[x0,y0,x1,y1]`。两个必知的坑：

- 整城建图要求 `visibility_graph.max_nodes` 足够大，否则节点被截断 → 图覆盖不全、大量 OD 不可达；
- 切回 `auto` 时必须保留 `scenario.max_buildings`（默认 350）。设成 `null` 会让窗口内建筑不限栋，
  吃光节点预算 → 大量 `ORIGIN_UNREACHABLE`。该项**只对小窗生效**（`crop: full` 不走裁剪，直接忽略）。

## 目录分工（run.py 只编排，config/metrics/plot 分离）

| 文件 | 职责 |
|------|------|
| `run.py` | 编排调度 + 专项正确性用例（U2/U3/U4）+ OD 分组生成 |
| `config.yaml` | 全部参数（场景 / 可见性图 / 禁飞 / OD / 检查开关） |
| `metrics.py` | 指标组装 + 终端报表（内存对象 → 可机读 dict，不跑算法） |
| `plot/` | 绘图：`registry.py` 图清单（唯一事实来源）+ `theme.py` 学术样式 + 分图模块 |
| `plot/full_city_3od.py` | 整城跨城 OD 静态俯视图。**故意不登记进 registry**——它是 `--full-city` 专属（那套粗网格参数下画标准图没意义），由 run.py 在该模式直接调用 |
| `plot/full_city_plotly.py` | 整城 plotly **交互**地图（建筑栅格化底图 + OD 矢量叠加）。同样不登记进 registry，`--full-city` 专属 |

绘图约定（沿用 task_allocation/exp01_smoke）：模块名 ≡ 图 id ≡ 绘制函数名去掉 `plot_`，
输出文件名 `fig_<图 id>.png`。新增一张标准图 = 新建模块 + registry 加一行。
注：`theme.setup_style()`（含 CJK 字体链）由 `make_plots` 统一调用；绕过 registry 直接调单图时
需自己先调一次，否则中文会渲染成方块（缺字形）。

## OD 分组设计（三组对照）

| 组 | 设计意图 |
|----|----------|
| `auto` | 窗口内随机 OD、t_dep=0，贴近真实任务形态 |
| `diag` | 每动态禁飞区一条**强制横穿** OD（端点在区外、窗口内），把动态时间窗判定真正跑起来 |
| `time_control` | 与 diag **同端点同几何**，仅把起飞时刻推到该禁飞区全部时段结束之后——验证时间窗判定不过度阻断 |

## 正确性用例（checks.enable_unit_checks，可见性图语义）

- **U2 建筑穿越判定**：穿建筑的线段 `segment_clear=False`
- **U3 动态禁飞「擦边」**：飞行途中禁飞生效 → `dynamic_blocked=True`（整边不可省）
- **U4 完全在时窗之前/之后飞行** → `dynamic_blocked=False`

另有 `checks.enable_path_audit`：平滑后路径用**零余量"地面真值"**独立复核建筑 + 静/动态禁飞，
避免与搜索期判定同源错误。

## 三者口径（results 里严格分存，不合并）

| 名词 | 含义 |
|------|------|
| `route` | 可见性图节点折线走廊（仅几何、无平滑） |
| `path` | 平滑后的曲线（仅几何、无时间；Chaikin 圆角 / Catmull-Rom 样条，侵入约束则回退原始折线） |
| `trajectory` | path + 匀速时间占位（**仅 ETA 辅助 / 可视化时间色标**，非 claimed 轨迹；time-optimal 轨迹生成不在本研究范围，交下游飞控） |

## 图清单（叙事位 + 绘制口径）

所有带地图的图都把**建筑足迹作为背景层**（`plot/scene.draw_buildings`，单次 `PolyCollection`
填充 + 描边；逐栋 `ax.fill` 在 2 万栋尺度会慢一个量级，且密集区无描边就糊成一片）。

| 图 | 叙事位 | 背景/主体 |
|----|--------|-----------|
| `region_overview` | 区域态势：全城建筑密度热度 + 研究区边界 + 禁飞区 + 裁剪窗（左上放大窗内含真实建筑与路径） | hexbin 密度 + 放大窗多边形 |
| `scene_3d` | 三维场景：建筑棱柱（按楼高着色）+ 禁飞区半透明棱柱 + 3D 轨迹 | 棱柱体，真三维 |
| `overview` | 建图视角：建筑 / 可见性图节点 / 各 OD 路径 | 建筑多边形 |
| `route_vs_path` | route（虚线+×=图节点序列）vs path（实线平滑） | 建筑多边形 |
| `trajectory` | trajectory = path + 时间维（LineCollection 按到达时刻渐变） | 建筑多边形 |
| `side_view` | 竖直剖面：弧长-高度 + 近旁建筑 + 禁飞参照 | 剖面灰矩形 |

两个渲染要点（踩过的坑）：

- **轨迹图必须用全分辨率 path**。`metrics.path_xy` 为压体积做过降采样（每条只剩 9–18 点），
  拿它 `scatter` 出来是一团噪点、看不出轨迹形状；本图改读 `ctx.planned` 的 `res.path.pts`
  与 `res.trajectory.times`，按段着色连线。
- **多 OD 同图必须分色**。颜色表示 OD 身份时用 `theme.OD_CYCLE`（overview / route_vs_path /
  full_city）；`trajectory` 的颜色让给时间维，改用 OD 编号标注。

## 产物

- `results/metrics.json` —— 全部数值指标（**唯一数值产物**；`--full-city` 的 OD 结果也写在这里，不再单独出 json）
- `results/figures/fig_*.png` —— 6 张标准图（overview / region_overview / route_vs_path / trajectory / side_view / scene_3d）
- `results/figures/fig_scene_3d.png` + `.html` —— 同一份数据的两种形态：PNG 供论文排版，
  HTML 供人工旋转核对（plotly 静态导出依赖 kaleido，本环境未安装，故静态图走 mplot3d）
- `results/figures/fig_full_city_3od.png` —— 仅 `--full-city` 产出，整城跨城 OD 静态俯视图
- `results/figures/fig_full_city_plotly.html` —— 仅 `--full-city` 产出，整城 plotly 交互地图（建筑栅格底图 + OD 矢量叠加，可缩放/hover）
