# 任务分配实验目录约定

本目录存放第 3 章（动态滚动的多机巢多无人机协同任务分配）相关的**可复现实验**。
算法实现在 `task_allocation/methods/`，对比方法在 `task_allocation/baselines/`，共享数据在 `task_allocation/data/`。
实验目录只负责：**配置、编排、结果与图**。

---

## 1. 顶层原则

| 原则 | 说明 |
|---|---|
| 一实验一目录 | 每个可独立复现的实验对应一个 `expXX_意图/`，不共用 `results/` / `plot/` |
| 入口唯一 | 只通过该目录下的 `run.py` 启动；其它脚本由 `run.py` 组装调用 |
| 配置外置 | 可变参数写入 `config.yaml`，不写死在代码中 |
| 产物分离 | 代码 / 配置 / 数值结果 / 图 分开存放 |
| 结果入库 | `results/` 与 `plot/` 中的实验产物**默认提交**到 Git，便于对照与论文复现 |
| 可复现 | `config.yaml` 固定 `seed`、数据来源、方法开关；建议在结果中记录运行元信息 |

---

## 2. 标准目录树

```text
task_allocation/
├── methods/                 # 算法实现（选择 / MUAS / 滚动等）
├── baselines/               # 对比方法
├── data/                    # 共享场景与合成数据
└── experiments/
    ├── README.md            # 本约定
    ├── exp01_smoke/         # 编号 + 意图
    │   ├── config.yaml      # 本实验配置（必填）
    │   ├── run.py           # 编排入口（必填，只做装配与调度）
    │   ├── notes.md         # 目的、假设、观察（建议）
    │   ├── config.py        # 可选拆分：yaml → 配置对象
    │   ├── build.py         # 可选拆分：场景 / 实体装配
    │   ├── metrics.py       # 可选拆分：指标汇总与产物组装
    │   ├── plot/            # 可视化模块（只负责绘制，纯代码）
    │   │   ├── __init__.py  #   make_plots() 调度入口
    │   │   ├── theme.py     #   全局样式与保存约定
    │   │   └── *.py         #   每张图一个模块
    │   ├── results/         # 数值结果、日志、中间数据、图（入库）
    │   │   ├── figures/     #   图产物
    │   │   └── .gitkeep
    │   └── …                # 可选：本实验专用脚本（由 run.py 调用）
    └── exp02_xxx/
        └── …
```

---

## 3. 命名规则

- 目录名：`exp{两位编号}_{英文短名}`
  - 示例：`exp01_smoke`、`exp02_selection_scale`、`exp03_event_trigger`
- 短名：小写字母 + 下划线，**不使用中文路径**
- 编号只增不改；废弃实验保留目录，在 `notes.md` 标明 `status: deprecated`，避免论文图表路径失效

---

## 4. 各文件 / 目录职责

### `config.yaml`（必填）

只放本实验可变项，不写长文说明（说明放 `notes.md`）。

建议字段：

```yaml
experiment:
  name: exp01_smoke
  seed: 42
  output_dir: results          # 相对本实验目录
  figures_dir: results/figures # 图产物（同属 results/）

data:
  source: synthetic            # synthetic | file
  path: null                   # source=file 时指向 data/ 下路径

problem:
  n_tasks: 30
  n_uavs: 5
  n_nests: 2

method:
  selection: marginal_gain     # 对应 methods/selection
  muas: dmde                   # 对应 methods/muas
  rolling: false               # smoke 可先关闭滚动

solver:
  max_iter: 200
  population: 40

run:
  save_results: true
  make_plots: true
  verbose: true
```

### `run.py`（必填，唯一 CLI 入口）

**只做编排调度**，不在这里堆细节；超过 ~200 行就拆出同目录模块。

职责顺序：

1. 读取同目录 `config.yaml`
2. 设置随机种子
3. 构建问题实例（合成或从 `data/` 加载）
4. 调用 `methods` / `baselines`，得到解与指标
5. 写入 `results/`（如 `metrics.json`、`solution.json`、`run_meta.json`）
6. 若 `make_plots: true`，生成图到 `results/figures/`

**不要**在 `run.py` 中堆算法细节；算法放在 `methods/`。
同目录可增加 `prepare.py`、`metrics.py`、`config.py`、`build.py` 等，由 `run.py` import 或调度，外部不直接作为入口。

### `plot/`（可视化模块，只负责绘制）

- **只放绘图代码**：`__init__.py` 提供 `make_plots(metrics, solution, out_dir)` 调度入口，
  全局样式集中在 `theme.py`，**每张图一个模块**（如 `cycles.py` / `selection.py` /
  `allocation.py` / `assignment.py` / `convergence.py`），避免单文件臃肿
- **图产物不在这里**——落在 `results/figures/`（由 `experiment.figures_dir` 指定），
  只放 `png` / `pdf` / `svg`；`plot/` 保持纯代码，便于审阅与跨实验复用
- 不放原始数值表（表在 `results/`）；绘图只读已落盘的 `results/` 产物，**改图不必重跑实验**
- 文件名建议可读：`assignment_map.pdf`、`convergence.png`；同一张图建议同时出
  `png`（预览）与 `pdf`（矢量，直接进 LaTeX）
- **默认提交**到 Git；极大体积图可后续再考虑 Git LFS，但约定上仍属入库产物

### `results/`

- 数值与可机读产物，**默认提交**
- 建议固定文件名，便于跨实验扫描对比：
  - `metrics.json` — 汇总指标
  - `run_meta.json` — seed、耗时、配置快照、代码版本信息等
  - `solution.json`（或其它约定格式）— 分配 / 路径结果
  - `logs/` — 可选冗长日志
- 多次重复实验可用子目录：`results/seed_42/`、`results/run_001/`，或在文件名中带 seed

### `notes.md`（建议）

人读笔记：实验目的、对应论文小节、成功标准、已知问题、下一步。不替代 `config.yaml`。

### 同目录其它脚本（可选）

| 示例 | 职责 |
|---|---|
| `config.py` | `config.yaml` → 各模块的配置对象（类型转换、缺省值） |
| `build.py` | 场景 / 地形 / 机队 / 任务等实体装配 |
| `metrics.py` | 指标计算与 `results/` 产物组装（若未放入 methods） |
| `plot/` | 绘图（**只负责绘制**），图产物写到 `results/figures/` |
| `prepare.py` | 本实验数据预处理 |
| `ablate_xxx.py` | 消融子流程，仍由 `run.py` 按配置调度 |

原则：**没有 `run.py` 调不到的隐式入口**。

---

## 5. 与仓库其它部分的边界

```text
methods/              → 可被多个实验复用的算法实现
baselines/            → 对比算法（尽量统一接口）
data/                 → 跨实验共享的场景 / 数据集
experiments/expXX_*/  → 一次具体设定的编排、配置与产物
```

实验目录内**禁止**复制一份 `methods` 源码。改算法改 `methods/`，用配置切换方法与超参。

---

## 6. 新建实验检查清单

1. 复制现有实验目录或按本约定建 `expXX_意图/`
2. 填写 `config.yaml`（改 `experiment.name` 与关键参数）
3. 实现或调整 `run.py` 编排逻辑
4. 写清 `notes.md`（目的与成功标准）
5. 跑通后将 `results/`（含 `results/figures/`）中需要复现的产物一并提交
6. 提交信息示例：`exp(exp02_selection_scale): add scale study config and baseline metrics`

---

## 7. 当前实验列表

| 目录 | 状态 | 说明 |
|---|---|---|
| `exp01_smoke` | active | 接口与目录联调；不宣称算法性能结论 |
