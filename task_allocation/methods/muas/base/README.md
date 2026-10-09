# muas/base — 求解器接口约定

本目录只定义**统一接口**，不包含具体优化算法。

| 符号 | 含义 |
|------|------|
| `BaseOptimizer` | 抽象基类：`name` + `solve(...)` |
| `SolverResult` | 统一返回结构 |

## 谁必须遵守

- 主算法：`muas/solvers/dmde_solver.py`
- 所有对比算法：`task_allocation/baselines/*`

## 依赖方向

```text
baselines/*  ──imports──►  muas/base
muas/solvers ──imports──►  muas/base
experiments  ──uses────►  SolverResult 字段写 metrics
```

`base` 不依赖 `operators` / `representation` / `baselines`。

## 与参考实现的关系

接口形状对齐学位论文仓库中的 `algorithm_dmde/base/base_optimizer.py`，
保证主算法与文献对比方法（PMX-DE、Sort-DE 等）可共用同一套实验脚本。
