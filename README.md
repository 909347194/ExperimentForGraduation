# ExperimentForGraduation

多无人机协同任务分配（第 3 章）与航迹规划（第 4 章）实验代码。

**第 3 章方法说明（只写当前做法）：**  
[`task_allocation/Chapter3_README.md`](task_allocation/Chapter3_README.md)

---

## 方法（极简）

1. **选择**（`methods/selection/`）— 从任务池得到本周期已选集 \(S\)（\(n_{\mathrm{cand}}\) 与 \(n_{\mathrm{sel}}\) 解耦，\(n_{\mathrm{sel}}\) 接 `cap(t)`）。
2. **MUAS**（`methods/muas/`）— 对 \(S\) 做离散映射 DE（DMDE）：指派 + 顺序；终点巢内层分配；返航计入总航程；解码修复后做**交付后成本 polish**（组内重排 + 跨机改派；执行集不减且航程严格变好才接受）。
3. **滚动**（`methods/rolling/`）— 写回完成/滞留与机队状态。

代价为 DEM 三维航程。可选覆盖门禁（已选可装任务禁止漏装）。

---

## 仓库结构

```text
task_allocation/
  methods/            选择 + MUAS + 滚动
  Chapter3_README.md  方法正文
  experiments/        exp01_smoke / exp02 / exp05 / …
  baselines/          构造式基线
  common/             类型与场景
  tests/              单测
path_planning/        第 4 章
paper/                论文稿
```

---

## 快速开始

```bash
uv sync
python3 -m pytest task_allocation/tests -q
python3 task_allocation/experiments/exp01_smoke/run.py --seeds 42 43 44
```

实验口径见各 `task_allocation/experiments/*/notes.md`。
