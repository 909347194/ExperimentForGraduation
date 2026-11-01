# 中文核心期刊论文模板（manuscript1_journal_article）

多无人机**任务分配**方向的中文核心期刊论文写作工程（内容均为占位，待填充）。

> **样式参考**：《系统工程与电子技术》《信息与控制》等中文核心期刊的投稿版式
> （通栏排版、五号宋体正文、黑体标题、GB/T 7714—2015 顺序编码制参考文献、
> 文末收稿日期/基金项目/作者简介）。
> **投稿前务必以目标期刊官网的"稿件模板/征稿简则"逐项核对**，格式细节各刊不同。

## 论文核心

**只写任务分配（task allocation）**。航迹规划仅作为任务分配的输入假设
（如"航程/飞行时间由预估模块给出"）出现，不单独成章展开。

## 目录结构

```text
manuscript1_journal_article/
├── main.tex                  # 主文档入口（元信息、章节装配）
├── Makefile                  # 编译脚本（xelatex + biber）
├── .latexmkrc                # latexmk 配置
├── README.md                 # 本说明
├── setup/                    # 全局设置（与内容解耦）
│   ├── preamble.tex          #   宏包与依赖（含 biblatex-gb7714-2015）
│   ├── fonts.tex             #   字体（英文 = Times New Roman）
│   ├── commands.tex          #   \placeholder 占位命令 / 定理环境 / 算法汉化
│   └── format.tex            #   版面（页边距 / 标题 / 图表题 / 公式编号）
├── frontmatter/              # 首页信息
│   ├── title_info.tex        #   标题 / 作者 / 单位 / 中图分类号·文献标志码·DOI
│   ├── abstract_cn.tex       #   中文摘要 + 关键词
│   └── abstract_en.tex       #   英文题名 / Abstract / Keywords
├── sections/                 # 正文（按节拆分，一节一文件）
│   ├── s01_introduction.tex          # 1 引言
│   ├── s02_problem_formulation.tex   # 2 问题描述与系统模型
│   ├── s03_task_allocation.tex       # 3 多无人机任务分配方法（★核心）
│   ├── s04_experiments.tex           # 4 仿真实验与结果分析
│   └── s05_conclusion.tex            # 5 结论
├── backmatter/
│   └── author_bio.tex        #   收稿日期 / 基金项目 / 作者简介 / 通信作者
├── bib/
│   └── references.bib        # 参考文献（GB/T 7714—2015）
├── figures/                  # 插图（PDF/PNG/EPS）
└── tables/                   # 表格源文件（三线表，\input 引入）
```

## 编译

```bash
make            # xelatex -> biber -> xelatex -> xelatex
# 或
latexmk -xelatex main.tex
```

依赖：TeX Live（xelatex、biber）+ `ctex` + `biblatex-gb7714-2015`。
Linux/macOS 无 Windows 字体时，临时把 `main.tex` 中 `fontset=windows`
改为 `fontset=fandol` 调试（**正式投稿保持 windows 并在 Windows/Overleaf 编译**）。

## 占位约定

- 所有待写内容统一用 `\placeholder{...}` 标出（灰色斜体，形如【占位：…】）；
- 每个 `.tex` 文件头部的 `%` 注释块是**写作要点**，成稿后可保留或删除；
- 终稿前全文搜索 `【占位】` / `\placeholder` 清零。

## 写作顺序建议

1. `s02_problem_formulation`（模型先行，符号表定稿）
2. `s03_task_allocation`（核心方法，同步画框架图/伪代码）
3. `s04_experiments`（实验口径与模型约束一一对应）
4. `s01_introduction`（最后写，贡献点与正文对齐）
5. 摘要（中英）→ 结论 → 核对参考文献格式
