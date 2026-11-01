# LaTeX 写作入门（以本仓库期刊模板为例）

> **读者对象**：第一次用 LaTeX 写中文期刊论文的你。
> **讲解方式**：每个概念都用 `manuscript1_journal_article/` 里的真实代码举例，
> 学完可以直接在本模板上开写。已有基础可直接跳到附录 A/B 查速查表。

---

## 0. 5 分钟跑通模板

```bash
cd paper/manuscript1_journal_article
make                 # 或 latexmk -xelatex main.tex
```

得到 `main.pdf`。环境要求：TeX Live（含 `xelatex`、`biber`）+ `ctex` + `biblatex-gb7714-2015`。
Linux/macOS 无 Windows 字体时，把 `main.tex` 里 `fontset = windows` 临时改为 `fontset = fandol`。

编译报错是常态，先看第 12 节的排查表，别慌。

---

## 1. 为什么用 LaTeX 写论文

一句话：**内容与格式分离**。你只管写"这是标题、这是公式、这是引用"，
长什么样交给模板。好处在论文写作里尤其明显：

- 公式排版质量碾压 Word（第 6 节）；
- 参考文献引用自动编号、转投他刊换一个样式即可（第 9 节）；
- 图表交叉引用自动更新编号（第 8 节）；
- 纯文本文件，git 可追踪每次修改，适合反复改稿和多人协作。

代价：有学习成本，报错信息不友好。这份文档就是帮你把成本降下来。

---

## 2. 编译原理：为什么是 xelatex → biber → xelatex → xelatex

LaTeX 不是"打开就是 PDF"，而是一条流水线：

```text
main.tex ──xelatex──► main.aux / main.bcf（记录引用了哪些文献、label 在哪）
                    │
references.bib ──biber──► main.bbl（把文献排成 GB/T 7714 格式）
                    │
        xelatex ◄───┘（把 bbl 嵌进文档、解析 \ref）
        xelatex（再跑一遍，把所有交叉引用编号稳定下来）
```

所以：

- **只改文字**：跑一遍 xelatex 就够；
- **改了参考文献/交叉引用**：跑全套；
- **不想记顺序**：用 `make` 或 `latexmk`（模板已配好 `Makefile` 和 `.latexmkrc`），它们自动判断该跑几遍。

编辑器推荐：VS Code + LaTeX Workshop 插件（保存即编译），或 Overleaf（免安装，选 XeLaTeX 编译器）。

---

## 3. 文档骨架：从 main.tex 说起

任何 LaTeX 文件都是三段式：

```latex
\documentclass[选项]{文档类}   % ← 导言区开始：全局设置
\usepackage{宏包}               % ← 导言区：加载工具
\newcommand{...}                % ← 导言区：自定义命令

\begin{document}                % ← 正文开始
这里是正文
\end{document}
```

本模板的 `main.tex` 对应关系：

```latex
\documentclass[
  zihao   = 5,        % 默认字号五号
  fontset = windows,  % 中文字体集（宋体/黑体/楷体）
  scheme  = chinese   % 中文排版方案
]{ctexart}            % ctexart = 中文"文章"类（期刊论文用它）
```

**文档类怎么选**：期刊论文/短文用 `ctexart`；学位论文用 `ctexbook`（本仓库
`paper/manuscript1` 就是）。文章类只有"节"，书类才有"章"。

**导言区和正文的分工**：导言区放"这篇文档长什么样"的设置，正文放"这篇文档写什么"。
模板把导言区又拆成 4 个文件（见第 4 节），`main.tex` 只留论文元信息和装配清单：

```latex
\input{setup/preamble}          % 宏包
\input{setup/fonts}             % 字体
\input{setup/commands}          % 自定义命令
\input{setup/format}            % 版面

\begin{document}
\input{frontmatter/title_info}  % 首页
\input{frontmatter/abstract_cn} % 中文摘要
\input{sections/s01_introduction}  % 引言
...
```

`\input{路径}` 的作用就是"把这个文件原地粘进来"，路径相对于 `main.tex` 所在目录。
长文档必须拆文件——10 个 50 行的文件比 1 个 500 行的好写好查。

---

## 4. 中文排版基础：ctex、字体、字号

### 4.1 ctex 干了什么

`ctex` 文档类帮你处理了中文的所有麻烦事：首行缩进 2 字符、中英文之间自动加空隙、
中文标点、`\heiti` 这类字体命令。所以**不需要**再 `\usepackage{ctex}`（会报 Option clash）。

### 4.2 字号：中文"几号字"对照

| 命令 | 字号 | 磅值 | 用在哪 |
|---|---|---|---|
| `\zihao{3}` | 三号 | 16pt | 论文标题 |
| `\zihao{4}` | 四号 | 14pt | 节标题 |
| `\zihao{-4}` | 小四 | 12pt | 学位论文正文 |
| `\zihao{5}` | 五号 | 10.5pt | **期刊正文（本模板默认）** |
| `\zihao{7}` | 七号 | 5.5pt | 脚注（少用） |

记忆法：**带负号的"小X"比"X"小一号**。`\zihao{-4}` 读作"小四"。

### 4.3 字体

```latex
{\songti 宋体正文} {\heiti 黑体标题} {\kaishu 楷体作者名} {\fangsong 仿宋}
```

花括号 `{...}` 限定作用范围——这是 LaTeX 最重要的规则：**一切修改都只在自己的
花括号内有效**。英文用 Times New Roman（`setup/fonts.tex` 里 `\setmainfont`）。

### 4.4 行距与缩进（setup/format.tex）

```latex
\linespread{1.3}              % ≈ Word 1.5 倍行距（系数 × 1.2 倍字号）
\setlength{\parindent}{2em}   % 首行缩进 2 字符
\setlength{\parskip}{0pt}     % 段间不加空行
```

换算口诀：系数 1.0 ≈ 单倍，1.3 ≈ 1.5 倍，1.6 ≈ 2 倍。

---

## 5. 写正文：章节、段落、强调

### 5.1 章节

```latex
\section{引言}                  % 自动编号 → "1 引言"
\subsection{问题描述与想定}      % → "2.1 问题描述与想定"
\section*{致谢}                 % * 表示不编号（参考文献、致谢用）
```

只需写标题文字，编号和目录都是自动生成的。**期刊论文只有节（section），
不要用章（chapter）**——那是 `ctexbook` 的东西。

### 5.2 段落

- **空行 = 分段**（首行自动缩进）；
- `\\` 只是**换行不分段**（不缩进），少用；
- 连续两个空行和一个空行效果相同。

初学最常见的错：在 Word 里习惯按回车分行，在 LaTeX 里按回车**不会**换行，
它只是把源码断开。真正控制排版的是空行和 `\\`。

### 5.3 强调与常用文本命令

```latex
\textbf{加粗} \textit{斜体} \texttt{等宽} \underline{下划线}
```

自定义命令（`setup/commands.tex`）：

```latex
\newcommand{\placeholder}[1]{\textcolor{gray}{\textit{【占位：#1】}}}
```

意思是：定义一个叫 `\placeholder` 的命令，带 1 个参数（用 `#1` 表示）。
调用 `\placeholder{这里是待写内容}` 就会渲染出灰色斜体的【占位：这里是待写内容】。
自己写命令的好处：样式只写一处，全文一致，改一处全局生效。

### 5.4 列表：分条陈列与“行内不换行”的贡献点

条目陈列用 `enumerate`（有序号）/ `itemize`（无序号），都必须有配对的 `\begin`/`\end`：

```latex
\begin{enumerate}
  \item 第一点
  \item 第二点
\end{enumerate}
```

**行内不换行**（如引言贡献点写成 “(1)(2)(3)” 连排）需要 `enumitem` 的**行内变体** `enumerate*`，而它默认**不启用**：

```latex
\usepackage[inline]{enumitem}   % ← 必须带 inline 选项，否则 enumerate* 未定义
...
\begin{enumerate*}[label=(\arabic*), itemjoin=\quad]
  \item 第一点 \item 第二点 \item 第三点
\end{enumerate*}
```

要点：
- 忘了 `inline` 选项会报 `enumerate* undefined`；只写 `\begin{enumerate*}` 却漏掉 `\end{enumerate*}` 会报 `Lonely \item`——两者都源于“行内列表没配对好 + 没开 inline”。
- `label=(\arabic*)` 控制序号样式；`itemjoin=\quad` 控制条目间间隔（不设会挤在一起）。
- 行内列表适合“贡献点/要点”这类短句连排；长篇分点仍用普通 `enumerate`。

---

## 6. 数学公式

### 6.1 三种形态

```latex
行内公式：质量为 $m$ 的无人机        % $...$ 夹在文字中
行间公式（不编号）：\[ E = mc^2 \]
行间公式（编号）：
\begin{equation}
  \min \sum_{i=1}^{n} \sum_{j=1}^{m} c_{ij} x_{ij}
  \label{eq:obj}
\end{equation}
```

模板里公式按节编号（`(2.1)`、`(2.2)`），由 `setup/format.tex` 的
`\numberwithin{equation}{section}` 实现。

### 6.2 常用语法

| 想写 | 代码 | 注意 |
|---|---|---|
| 上下标 | `x_{ij}`、`x^{10}` | 多于一个字符必须加 `{}` |
| 分式 | `\frac{a}{b}` | |
| 求和/积分 | `\sum_{i=1}^{n}`、`\int_0^1` | |
| 希腊字母 | `\alpha \beta \gamma \lambda` | 大写首字母大写：`\Gamma` |
| 粗体（矩阵/向量） | `\bm{X}`、`\bm{x}` | 两刊要求矩阵向量黑斜体 |
| 转置 | `\bm{X}^{\transpose}` | 模板已定义 `\transpose`（正体 T） |
| 黑板粗体 | `\mathbb{R}` | 模板已定义 `\R` |

### 6.3 期刊的正斜体约定（写公式时就要对）

变量斜体（`$x$`）、矩阵向量黑斜体（`\bm{X}`）、常数与函数名正体
（`\mathrm{e}`、`\ln`）、集合黑正体。评审真的会看这个。

---

## 7. 图表与浮动体

### 7.1 插图

```latex
\begin{figure}[htbp]
  \centering
  \includegraphics[width=0.8\textwidth]{figures/frame.pdf}
  \caption{算法总体框架}\label{fig:frame}
\end{figure}
```

- 图片文件放 `figures/` 目录，推荐 PDF（矢量图清晰）或高分辨率 PNG；
- `[htbp]` 是"放哪"的候选顺序：here / top / bottom / page，让 LaTeX 自己挑；
- `\caption` 写图题（自动编号"图 1"），紧跟 `\label`。

### 7.2 三线表

```latex
\begin{table}[htbp]
  \centering
  \caption{符号定义}\label{tab:symbols}
  \begin{tabular}{ccc}
    \toprule
    符号 & 含义 & 单位 \\
    \midrule
    $x_{ij}$ & 分配决策变量 & — \\
    $c_{ij}$ & 航段代价 & s \\
    \bottomrule
  \end{tabular}
\end{table}
```

`\toprule \midrule \bottomrule`（`booktabs` 宏包）就是"三线表"的三条线。
表格文件也可以放 `tables/` 单独维护，正文 `\input{tables/tab_symbols}` 引入。

### 7.3 浮动体心态

`figure`/`table` 是"浮动体"：LaTeX 会把它们挪到页面合适的位置，
**不保证紧跟在你的源码位置**。两条经验：图放引用它的段落附近；太挤时先不管，
终稿前再调 `[htbp]` 和图片大小。

---

## 8. 交叉引用：\label 与 \ref

```latex
如图~\ref{fig:frame} 所示，式~\eqref{eq:obj} 为目标函数（见第~\ref{sec:model} 节）。
```

规则：

- `\label{...}` 必须放在**被编号的对象之后**：`\section{...}` 后面、`\caption{...}` 后面；
- 命名习惯：`fig:` / `tab:` / `eq:` / `sec:` 前缀，一眼识别；
- `~` 是不断行空格，防止"图"和编号被拆到两行；
- 改动后编号自动更新——但**需要编译两遍**（这就是第 2 节流水线的由来）。

初学最容易忘的：加了新图/新公式后 `\ref` 显示 `??`，不是坏了，跑两遍编译就好。

---

## 9. 参考文献：bib 文件 + biber

### 9.1 文献库（bib/references.bib）

```bibtex
@article{alqefari2025,
  author  = {Alqefari, Shahad and Menai, Mohamed El Bachir},
  title   = {Multi-UAV Task Assignment in Dynamic Environments},
  journal = {Drones},
  year    = {2025},
  volume  = {9},
  number  = {1},
  pages   = {75},
}
```

条目类型常用 `@article`（期刊）、`@book`（书）、`@inproceedings`（会议）。
`alqefari2025` 是引用键，你自己起名，好认就行。

### 9.2 正文引用

```latex
现有方法可分为三类\cite{alqefari2025,ming2017,wang2023}。
```

渲染成 `[1-3]`（GB/T 7714 顺序编码制，按首次引用顺序编号）。

### 9.3 为什么需要 biber

`biblatex` + `biber` 负责把 `.bib` 里的原始数据排成国标格式（作者超过 3 人省略为
"等"、中英文文献格式差异、DOI 处理等）。所以 `.bib` 里只写**数据**，
格式不用管。投稿他刊如果参考文献体例不同，通常只换样式参数，文献库不动。

### 9.4 文献库管理：单一权威库 + 避坑纪律

- **单一权威库**：全篇只 `\addbibresource{bib/references.bib}` 一个文献库（`setup/preamble.tex` 第 27 行）。新增文献**直接往这个文件追加**，切勿再建第二个 `.bib` 或在别处 `\addbibresource`——多库会导致键冲突、`\cite` 解析不到。
- **引用键大小写敏感、须与 `.bib` 完全一致**：`\cite{Zhang2026}` 里哪怕大小写错一个字母都会 `undefined`。
- **粘贴文献务必核对“作者/年/DOI”三件套**：曾出现把一篇论文的 DOI 错贴到另一篇作者名下（如 `liu2026disaster` 的 DOI 与 `pu_dynamic_2026` 完全相同但作者不同）。处理原则是**不加重复条目**，而是按真实归属补正确的那篇（本例补 `liu2024dawn`）。
- **删旧句要删干净**：从别处整段复制引用时，残留的旧长句若还带着一串 `\cite`，会因键不存在而 `undefined`。改完用 `grep -n '\\cite' sections/` 核对一遍。
- **改了 `.bib` 或引用键后必须重跑 biber**（见第 12 节“只显示 [1]”那条）。

---

## 10. 特殊字符转义（必背 8 个）

LaTeX 里这些字符有特殊含义，正文里想显示它们本身必须加反斜杠：

| 字符 | 写法 | 说明 |
|---|---|---|
| `%` | `\%` | 不转义会**把后面整行变注释**（最阴险） |
| `_` | `\_` | 数学里是下标，文本里必须转义 |
| `# $ & { }` | `\# \$ \& \{ \}` | |
| `~` | `\~{}` 或 `\textasciitilde` | 裸 `~` 是不断行空格 |
| `^` | `\^{} ` 或 `\textasciicircum` | |
| `\` | `\textbackslash` | |
| `«引号»` | ``` `` '' ``` | 直引号 " 会渲染成方向错误的引号 |

注释符 `%` 之外：**成对的 `{}` 是作用域**，忘了闭合会报 `Too many }'s` 之类的
怪错。写公式/命令时先打完一对 `{}` 再填内容，能省很多排查时间。

---

## 11. 模板专属：占位符工作流

本模板约定：**所有没写完的内容都用 `\placeholder{...}` 标出**（灰色斜体）。

```bash
# 写作中：看看还剩多少没写
grep -rn '\\placeholder{' sections/ frontmatter/
# 投稿前：清零检查
grep -rn '【占位' . --include=*.tex
```

各 `.tex` 文件头部的 `%` 注释块是**写作要点**（比如 `sections/s01_introduction.tex`
顶部的四段式提示），跟着要点写就行，成稿后可保留（不影响输出）。

---

## 12. 常见报错排查表

| 报错 | 原因 | 处理 |
|---|---|---|
| `Undefined control sequence` | 命令拼错 / 宏包没加载 | 核对命令拼写；查它属于哪个宏包 |
| `Missing $ inserted` | 文本里用了 `_` `^` | 转义，或放进公式 |
| `File not found` | `\input`/`\includegraphics` 路径错 | 路径相对 main.tex，注意大小写 |
| `Option clash for package xxx` | 同一宏包加载两次且选项不同 | 删掉多余的 `\usepackage` |
| `Missing \begin{document}` | 导言区混进了正文文字 | 检查 preamble 里是否有裸文本 |
| `Citation 'xxx' undefined` / 全文只显示 `[1]` | ① 首次编译第一遍正常；② **陈旧 `.bbl`/`.bcf`**：改了 `.bib` 或引用键后 biber 没重跑，旧 bbl 仍指向旧条目 | 情况①跑完 biber+两遍 xelatex 即消失；情况②先 `rm -f main.bbl main.bcf` 再 `latexmk -xelatex main.tex` 强制完整重编 |
| `Overfull \hbox` | 一行太长溢出边界 | 警告可暂忍；终稿微调措辞或加 `\allowdisplaybreaks` |
| 中文编译失败 / 字体缺失 | Linux 用 `fontset=windows` | 临时改 `fontset=fandol` |
| `Too many }'s` | 花括号不配对 | 数对 `{}`；或二分法定位 |
| 参考文献不出现 | 没跑 biber 或 .bib 有语法错 | 看 `.blg` 日志定位条目 |
| `enumerate* undefined` / `Lonely \item` | 行内列表 `enumerate*` 需 `\usepackage[inline]{enumitem}` 且漏了 `\end{enumerate*}` | preamble 加 `inline` 选项并补 `\end{enumerate*}` |
| `*.bbl` / `*.blg` 进了版本库 | 它们是 biber 生成物，可由 `.bib` 重建 | 勿 `git add`；建议把 `*.bbl *.blg` 加入 `.gitignore`（本仓库已加） |

排查心法：**看报错的第一行 + 出错的行号**，日志（`.log`）里上下文最全。
改一处、编译一次，别一次改一堆。

---

## 13. 写作流程建议（配合模板）

1. **先骨架后血肉**：按 `sections/` 的写作要点把每个 `\placeholder` 换成提纲句，
   再逐步扩写；先写第 2、3 节（模型和方法），最后写引言和摘要；
2. **公式先行**：符号表（2.2 节）定稿后再写模型，符号全篇一致；
3. **随写随引**：每写一个论断就补 `\cite`，别留到最后补；
4. **提交前检查单**：
   - [ ] `grep '【占位'` 清零；
   - [ ] 全文 `\ref` 无 `??`（编译两遍后检查）；
   - [ ] 图表有编号、有中文题、坐标轴有量和单位；
   - [ ] 参考文献条目字段完整（作者/题名/刊名/年/卷期/页码）；
   - [ ] 摘要字数与关键词个数符合目标刊（见 README 对照表）；
   - [ ] 用目标刊的"征稿简则"逐项过一遍。

---

## 附录 A：常用命令速查

| 用途 | 命令 |
|---|---|
| 节 / 小节 / 不编号节 | `\section{}` `\subsection{}` `\section*{}` |
| 加粗 / 斜体 | `\textbf{}` `\textit{}` |
| 行内 / 行间 / 编号公式 | `$..$` `\[..\]` `\begin{equation}` |
| 插图 / 表 / 算法 | `\begin{figure}` `\begin{table}` `\begin{algorithm}` |
| 引用文献 | `\cite{key1,key2}` |
| 交叉引用 | `\ref{}` `\eqref{}` |
| 标签 | `\label{fig:|tab:|eq:|sec:|alg:...}` |
| 脚注 / 首页页脚 | `\footnote{}` / `\blfootnote{}`（模板自定义） |
| 换行 / 分段 | `\\` / 空一行 |
| 转义 | `\$ \& \% \# \_ \{ \} \textbackslash` |

## 附录 B：模板文件 → 写什么

| 文件 | 写什么 |
|---|---|
| `main.tex` | 标题、作者、单位、基金、通信作者等元信息 |
| `frontmatter/title_info.tex` | （一般不动）首页排版 |
| `frontmatter/abstract_cn/en.tex` | 中英文摘要与关键词 |
| `sections/s01_introduction.tex` | 1 引言 |
| `sections/s02_problem_formulation.tex` | 2 问题描述与系统模型（符号表、优化模型） |
| `sections/s03_task_allocation.tex` | 3 任务分配方法（★核心章节） |
| `sections/s04_experiments.tex` | 4 仿真实验与结果分析 |
| `sections/s05_conclusion.tex` | 5 结论 |
| `backmatter/acknowledgements.tex` | 致谢（可选） |
| `backmatter/author_bio.tex` | 收稿日期、作者简介 |
| `bib/references.bib` | 参考文献数据 |
| `figures/` `tables/` | 图片、表格源文件 |
| `setup/` | 全局设置（一般不动，动了看注释） |
