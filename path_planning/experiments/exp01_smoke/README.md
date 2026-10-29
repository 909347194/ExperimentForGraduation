# exp01_smoke — 全链路正确性调试（冒烟）

验证链路：**离线控制网络建立 → 在线 L1/L2/L2.5 分层寻路 → Dubins 平滑 → 路径独立复核**，
在广州真实建筑 + 真实禁飞区时序上跑通并逐项验证正确性。**不是性能/灵敏度实验**（那是 exp02/exp03）。

## 运行

```bash
# 依赖见仓库根 pyproject.toml（numpy / shapely / pyshp / matplotlib / pyyaml）
python experiments/exp01_smoke/run.py            # 只出 metrics.json
python experiments/exp01_smoke/run.py --plot     # + 图件（results/figures/）
python experiments/exp01_smoke/run.py --set offline.d_star=200   # 覆盖任意配置
```

结果：`results/metrics.json`（唯一数值产物）+ `results/figures/fig_*.png`（5 张图）。
报告：`report.tex`（latexmk -xelatex 编译，数字均出自 metrics.json）。

## 目录分工（docs/architecture.md §5：run.py 只编排，config/metrics/plot 分离）

| 文件 | 职责 |
|------|------|
| `run.py` | 编排调度 + 专项正确性用例（U1--U5）+ OD 分组生成 |
| `config.yaml` | 全部参数（场景/无人机/离线/在线/禁飞/OD/检查开关） |
| `metrics.py` | 指标组装 + 终端报表（内存对象 → 可机读 dict，不跑算法） |
| `plot/` | 绘图：`registry.py` 图清单（唯一事实来源）+ `theme.py` 学术样式 + 分图模块 |

绘图约定（沿用 task_allocation/exp01_smoke）：模块名 ≡ 图 id ≡ 绘制函数名去掉 `plot_`，
输出文件名 `fig_<图 id>.png`。新增一张图 = 新建模块 + registry 加一行。

## OD 分组设计（三组对照，共 10 条）

| 组 | 条数 | 设计意图 |
|----|------|----------|
| `auto` | 4 | 窗口内随机 OD、t_dep=0，贴近真实任务形态 |
| `diag` | 3 | 每动态禁飞区一条**强制横穿** OD（端点在区外、窗口内）、t_dep=0，把 L2/L2.5 真正跑起来 |
| `time_control` | 3 | 与 diag **同端点同几何**，仅把起飞时刻推到该禁飞区全部时段结束之后——验证时间窗判定不过度阻断 |

## 正确性用例（checks.enable_unit_checks）

- U1 链路代价序关系：t(0,0) ≥ t(0,s) ≥ t(s,s)=D/v_u，且 t(s,s) 与 Eq.(4) 一致
- U2 建筑穿越判定：穿建筑的链路 link_feasible=False
- U3 整边判定"擦边"：飞行途中禁飞生效 → dynamic_blocked=True（整边不可省）；U3b 端点安全 ≠ 整边安全
- U4 完全在时窗之前/之后飞行 → dynamic_blocked=False
- U5 保守判据 ⊇ 整边判据：整边判冲突 ⇒ 保守必判冲突，时窗外不误判

另有 `checks.enable_path_audit`：平滑后路径用**零余量"地面真值"**（与搜索判定解耦的
CollisionQuery）独立复核建筑 + 静/动态禁飞，避免同源错误。

## 三者口径（results 里严格分存，不合并）

| 名词 | 含义 | 对应 |
|------|------|------|
| `route` | 控制网络上的虚拟链路序列（走廊），仅几何、无平滑 | Lin2021 Fig.1(a) |
| `path` | Dubins 平滑后的三维空间曲线，仅几何、无时间 | Lin2021 Fig.1(b) |
| `trajectory` | path + 时间参数化；**当前为匀速占位**（time_optimal.py 待实现） | Lin2021 Fig.1(c) |
