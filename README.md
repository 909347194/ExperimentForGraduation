# ExperimentForGraduation

多无人机协同任务分配（第 3 章）与航迹规划（第 4 章）实验代码。

**第 3 章方法说明（只写当前做法）：**  
[`task_allocation/Chapter3_README.md`](task_allocation/Chapter3_README.md)

**第 4 章方法说明：**  
[`path_planning/README.md`](path_planning/README.md)（分层架构与职责定义：[`docs/architecture.md`](path_planning/docs/architecture.md)、[`docs/module_map.md`](path_planning/docs/module_map.md)）

**闭环协议（上下层集成）：**  
[`closed_loop/README.md`](closed_loop/README.md)

---

## 方法（极简）

1. **选择**（`methods/selection/`）— 从任务池得到本周期已选集 \(S\)（\(n_{\mathrm{cand}}\) 与 \(n_{\mathrm{sel}}\) 解耦，\(n_{\mathrm{sel}}\) 接 `cap(t)`）。
2. **MUAS**（`methods/muas/`）— 对 \(S\) 做离散映射 DE（DMDE）：指派 + 顺序；终点巢内层分配；返航计入总航程；解码修复后做**交付后成本 polish**（组内重排 + 跨机改派；执行集不减且航程严格变好才接受）。
3. **滚动**（`methods/rolling/`）— 写回完成/滞留与机队状态。

代价为 DEM 三维航程。可选覆盖门禁（已选可装任务禁止漏装）。

---

## 仓库结构

```text
task_allocation/          第 3 章：上层任务分配
  methods/                选择 + MUAS + 滚动
  Chapter3_README.md      方法正文
  experiments/            exp01_smoke / exp02 / exp05 / …
  baselines/              构造式基线
  common/                 类型与场景
  tests/                  单测
path_planning/            第 4 章：下层轨迹规划
  common/ utils/          L0 领域模型 + L1 通用工具
  methods/                tpr/ 风险模型 + control_network_planning/（constraints|cost|offline|online）
  interface/              对外契约：TrajectoryPlanner 门面 + 事件/反馈
  baselines/              Lin 原始基线 / 欧氏距离对照（registry）
  docs/                   分层架构与模块职责
  experiments/            exp01_smoke … exp05_joint_sim
closed_loop/              跨层集成：事件触发 update–reassign–replan 环驱动（不含算法）
paper/                    论文稿
```

依赖方向：`task_allocation/` 与 `path_planning/` **互不 import**，只经
`closed_loop/`（调用各自公开接口）协作；详见 [`closed_loop/README.md`](closed_loop/README.md)。

---

## 快速开始

```bash
uv sync
python3 -m pytest task_allocation/tests -q
python3 task_allocation/experiments/exp01_smoke/run.py --seeds 42 43 44
```

实验口径见各 `task_allocation/experiments/*/notes.md`。
