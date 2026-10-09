# baselines — 对比方法约定

本目录存放第 3 章任务分配相关的**对比算法**实现，与 `methods/` 中「本文方法」分离。

- `methods/`：本文提出的动态任务选择 + 选择性多机巢 MUAS + DMDE + 滚动框架  
- `baselines/`：经典或文献对比方法（随机、贪心、PMX-DE、Sort-DE、GA 等）

实验编排仍在 `experiments/`，本目录**只实现求解逻辑**，不写配置、不写画图。

---

## 1. 统一接口（必须遵守）

所有对比求解器必须：

1. 继承 `task_allocation.methods.muas.base.BaseOptimizer`
2. 实现 `name` 属性与 `solve(...)` 方法
3. 返回 `SolverResult`（必填字段不得缺失或改名）

```text
from task_allocation.methods.muas.base import BaseOptimizer, SolverResult
```

接口定义见：`methods/muas/base/base_optimizer.py`。

**禁止**：

- 私自返回 dict / 自定义 Result 类导致与主算法字段不一致  
- 在 baseline 内复制一份 DMDE 编码/映射实现后静默分叉（若需复用，从 `methods/muas` 导入）  
- 在 baseline 文件里读 `config.yaml` 或写 `results/`（由 `experiments/*/run.py` 负责）

---

## 2. 目录与命名

```text
baselines/
├── README.md              # 本约定
├── __init__.py            # 导出已实现求解器 + REGISTRY
├── registry.py            # 名称 → 类的注册表（配置驱动入口）
├── random_assign.py       # 示例/占位：随机分配
├── greedy.py              # 示例/占位：贪心
├── pmx_de.py              # 文献对比：PMX-DE（可选）
├── sort_de.py             # 文献对比：Sort-DE（可选）
└── genetic.py             # 文献对比：GA（可选）
```

- 文件名、`name` 属性：小写 + 下划线（如 `pmx_de`）  
- 一个文件一个主求解器类；类名建议 `XxxSolver`  
- 未实现的方法可只保留模块 docstring 与空类骨架，并在 `REGISTRY` 中暂不注册或标注 `status: planned`

---

## 3. 注册表约定

`registry.py` 维护：

```text
REGISTRY: dict[str, type[BaseOptimizer]]
```

- key 与配置里的 `method.name` / `methods[].name` 一致  
- 主算法 `dmde` **不**放在 baselines 注册表中（主算法从 `methods.muas.solvers` 导入）  
- 实验脚本通过注册表按名实例化，避免大量 if-else

建议实验侧合并方式：

```text
SOLVERS = {
    "dmde": DMDESolver,          # from methods
    **BASELINE_REGISTRY,        # from baselines
}
```

---

## 4. 与 experiments 的边界

| 职责 | 位置 |
|------|------|
| 算法实现 | `baselines/*.py`、`methods/muas/solvers/` |
| 参数、seed、重复次数 | `experiments/expXX/config.yaml` |
| 读配置、调求解器、写 metrics/图 | `experiments/expXX/run.py` |
| 统一结果结构 | `SolverResult`（base） |

对比实验目录命名建议：`expXX_baseline_compare` 或按论文表号命名。

配置示例（多方法一次跑）：

```yaml
methods:
  - name: dmde
    pop_size: 50
    max_generations: 500
  - name: greedy
  - name: random
    n_samples: 100
run:
  repeats: 10
```

---

## 5. 新增一个 baseline 的检查清单

1. 新建 `baselines/<name>.py`，继承 `BaseOptimizer`，实现 `name` / `solve`  
2. 返回完整的 `SolverResult`  
3. 在 `registry.py` 注册  
4. 在 `baselines/__init__.py` 导出（可选）  
5. 用 `exp01_smoke` 或专用对比实验跑通一次，确认 metrics 字段与 DMDE 可对齐  
6. 在本 README「当前列表」中补一行状态  

---

## 6. 当前列表

| 名称 | 文件 | 状态 | 说明 |
|------|------|------|------|
| （接口） | `methods/muas/base/` | active | BaseOptimizer / SolverResult |
| random | `random_assign.py` | planned | 占位，待实现 |
| greedy | `greedy.py` | planned | 占位，待实现 |
| pmx_de | — | planned | 文献对比，后续迁入 |
| sort_de | — | planned | 文献对比，后续迁入 |

---

## 7. 设计动机（简述）

- **接口统一**：主算法与对比算法同一 `solve → SolverResult`，表与图脚本只写一套  
- **目录分离**：`methods` = 本文贡献，`baselines` = 对照，论文叙事清晰  
- **配置驱动**：换方法只改 yaml，不改编排代码  
