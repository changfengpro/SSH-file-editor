# SSH File Editor

SSH File Editor（命令名：`sfe`）是一个面向 SSH 场景的轻量终端代码编辑器。它保留 Vim 常用的保存、退出和模式切换习惯，同时提供类似 VS Code 的 C 语言自动补全，适合在远程服务器上快速编写 `.c`、`.h` 等源码文件。

项目不依赖 `clangd`、`ctags`、Neovim 插件或第三方 Python 包。只要服务器有 Python 3 和标准库 `curses`，就可以直接运行。

## 功能特性

- **SSH 友好：** 纯终端界面，适合通过 SSH 在服务器上直接编辑文件。
- **Vim 风格操作：** 支持 Normal、Insert、Command 3 种模式，常用 `:w`、`:q`、`:wq`、`:q!` 命令可直接使用。
- **C 语言补全：** 支持 C 关键字、常用标准库函数、当前文件标识符和头文件名补全。
- **C 语法高亮：** 区分预处理指令、关键字、函数名、字符串、数字和注释。
- **类似 VS Code 的候选逻辑：** 插入模式下自动弹出补全，支持模糊匹配和当前文件符号优先排序。
- **零外部依赖：** 使用 Python 标准库实现，便于放到远程机器上直接运行。
- **可测试核心逻辑：** 文本缓冲区、补全引擎和 Vim 命令解析都有单元测试。

## 环境要求

- Linux 或其他支持 `curses` 的终端环境。
- Python >= 3.10。
- SSH 终端建议设置 `TERM=xterm-256color`。

在目标服务器上已验证的环境：

```bash
python3 --version
# Python 3.10.12
```

## 快速开始

进入项目目录：

```bash
cd "/home/rmer/project/SSH file editor"
```

编辑一个 C 文件：

```bash
python3 sfe.py hello.c
```

可选：创建本地快捷命令。

```bash
chmod +x sfe.py
mkdir -p "$HOME/.local/bin"
ln -sf "$PWD/sfe.py" "$HOME/.local/bin/sfe"
```

之后可以直接运行：

```bash
sfe hello.c
```

如果 `$HOME/.local/bin` 不在 `PATH` 中，请把下面这行加入 `~/.bashrc` 或 `~/.zshrc`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 基本使用

编辑器启动后默认处于 Normal 模式。常见流程如下：

```text
i        进入 Insert 模式
输入代码  自动出现补全候选
Esc      回到 Normal 模式
:w       保存
:q       退出
```

保存并退出：

```text
Esc
:wq
```

放弃修改并退出：

```text
Esc
:q!
```

## Vim 风格按键

### 模式切换

| 按键 | 说明 |
| --- | --- |
| `i` | 在光标前进入 Insert 模式 |
| `a` | 在光标后进入 Insert 模式 |
| `o` | 在当前行下方新建一行，并进入 Insert 模式 |
| `O` | 在当前行上方新建一行，并进入 Insert 模式 |
| `Esc` | 返回 Normal 模式，或关闭补全菜单 |
| `:` | 进入 Command 模式 |

### 移动和编辑

| 按键 | 说明 |
| --- | --- |
| `h` / `j` / `k` / `l` | 左、下、上、右移动 |
| 方向键 | 移动光标 |
| `0` | 移动到行首 |
| `$` | 移动到行尾 |
| `x` | 删除光标下的字符 |
| `/` 或 `Ctrl-F` | 搜索文本 |
| `Tab` | 补全菜单未打开时插入 4 个空格 |

### 命令模式

| 命令 | 说明 |
| --- | --- |
| `:w` | 保存当前文件 |
| `:q` | 退出；如果有未保存修改，会阻止退出 |
| `:q!` | 强制退出，不保存修改 |
| `:wq` | 保存并退出 |
| `:x` | 保存并退出 |

备用快捷键：

| 按键 | 说明 |
| --- | --- |
| `Ctrl-O` / `F2` | 保存 |
| `Ctrl-Q` / `F10` | 退出 |

说明：某些 SSH 终端会把 `Ctrl-S` / `Ctrl-Q` 用作流控快捷键，因此推荐使用 Vim 命令或 `Ctrl-O` 保存。

## 自动补全

在 Insert 模式下输入标识符时，补全菜单会自动弹出。示例：

```c
pr
```

可能出现的候选包括：

- `printf`
- `print_total`
- `prepare_value`

补全操作：

| 按键 | 说明 |
| --- | --- |
| `Ctrl-Space` | 手动打开补全菜单 |
| `↓` / `Ctrl-N` | 切换到下一个候选 |
| `↑` / `Ctrl-P` | 切换到上一个候选 |
| `Tab` | 接受当前候选 |
| `Enter` | 换行并关闭补全菜单 |
| `Esc` | 关闭补全菜单 |

补全来源：

- C 关键字，例如 `int`、`return`、`struct`。
- 常用 C 标准库符号，例如 `printf`、`malloc`、`strlen`。
- 当前文件里的变量名、函数名和其他标识符。
- `#include` 场景下的头文件名，例如 `stdio.h`、`stdlib.h`。

排序规则：

- 当前文件中的标识符优先。
- 支持模糊匹配，例如 `pt` 可以匹配 `print_total` 和 `printf`。
- 前缀完全匹配优先于普通模糊匹配。

## 项目结构

```text
.
├── README.md
├── sfe.py
├── sfe_core.py
├── tests/
│   └── test_sfe_core.py
└── docs/
    └── superpowers/
        └── plans/
            └── 2026-05-20-sfe-editor.md
```

核心文件说明：

| 文件 | 说明 |
| --- | --- |
| `sfe.py` | 终端 UI、按键处理、文件读写和编辑器入口 |
| `sfe_core.py` | 文本缓冲区、补全引擎和 Vim 命令解析 |
| `tests/test_sfe_core.py` | 核心逻辑单元测试 |
| `docs/superpowers/plans/2026-05-20-sfe-editor.md` | 实现计划记录 |

## 测试和验证

运行单元测试：

```bash
python3 -m unittest tests.test_sfe_core -v
```

运行语法检查：

```bash
python3 -m py_compile sfe.py sfe_core.py
```

查看命令帮助：

```bash
python3 sfe.py --help
```

## 开发说明

当前项目使用 Git 管理。建议新功能使用 worktree 隔离开发：

```bash
git worktree add .worktrees/<task-name> -b <branch-name>
```

在 worktree 中完成修改后，至少运行：

```bash
python3 -m unittest tests.test_sfe_core -v
python3 -m py_compile sfe.py sfe_core.py
```

提交信息建议使用 Conventional Commits，例如：

```bash
git commit -m "docs(readme): 完善项目使用说明"
```

## 常见问题

### 为什么不用 Vim 插件或 clangd？

这个工具的目标是在远程服务器上「拿来就能用」。很多服务器没有安装 Neovim、插件管理器或 `clangd`，临时配置成本较高。`sfe` 先提供轻量可用的内置补全，适合快速写 C 代码。

### `Ctrl-S` 不能保存怎么办？

部分终端会把 `Ctrl-S` 当作暂停输出的流控快捷键。推荐使用 `:w` 保存，或者使用 `Ctrl-O` / `F2`。

### 这个编辑器会完全兼容 Vim 吗？

不会。它只实现常用编辑、保存、退出和移动操作。目标是让熟悉 Vim 的用户不用重新适应基本流程，同时保留轻量补全能力。

### 补全不够准怎么办？

当前补全基于内置词表和当前文件标识符，不做语义分析。后续可以扩展：

- 读取同目录 `.h` 文件中的符号。
- 对项目目录建立符号索引。
- 可选接入 `clangd` 或 `ctags`。

## 许可证

当前仓库尚未添加许可证文件。如需开源发布，请先补充 `LICENSE`。
