# SSH File Editor

SSH File Editor（命令名：`sfe`）是一个面向 SSH 场景的轻量终端代码编辑器。它保留 Vim 常用的保存、退出和模式切换习惯，同时提供类似 VS Code 的 C 语言自动补全，适合在远程服务器上快速编写 `.c`、`.h` 等源码文件。

项目不依赖 `clangd`、`ctags`、Neovim 插件或第三方 Python 包。只要服务器有 Python 3 和标准库 `curses`，就可以直接运行。

## 功能特性

- **SSH 友好：** 纯终端界面，适合通过 SSH 在服务器上直接编辑文件。
- **Vim 风格操作：** 支持 Normal、Insert、Command 3 种模式，常用 `:w`、`:q`、`:wq`、`:q!` 命令可直接使用。
- **C 语言补全：** 支持 C 关键字、常用标准库函数、当前文件标识符、同目录 `.h` 符号和头文件名补全。
- **C 语法高亮：** 区分预处理指令、关键字、函数名、字符串、数字和注释。
- **类似 VS Code 的候选逻辑：** 插入模式下自动弹出补全，支持模糊匹配、当前文件符号优先排序和简单函数签名提示。
- **编辑体验增强：** 支持行号、状态栏、智能缩进，以及 Normal 模式下的撤销和重做。
- **可配置编辑行为：** 支持配置手动补全快捷键、缩进宽度、行号、头文件扫描、签名提示，以及是否启用成对符号自动补全。
- **安装体验完整：** deb 包提供系统默认配置、`man sfe` 和 `sfe-upgrade` 升级脚本。
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

### 使用 deb 包安装

下载 release 中的 `sfe_0.4.2_all.deb` 后安装：

```bash
sudo apt install ./sfe_0.4.2_all.deb
```

安装后可以直接运行：

```bash
sfe hello.c
```

查看版本：

```bash
sfe --version
```

卸载：

```bash
sudo apt remove sfe
```

查看手册：

```bash
man sfe
```

升级到最新 release：

```bash
sfe-upgrade
```

### 从源码运行

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

## v0.3.0 C IDE 功能

`sfe` 会扫描当前文件所在目录下的 `.c` 和 `.h` 文件，建立项目符号索引。补全候选现在会标出来源，例如 `snippet`、`keyword`、`file`、`header`、`project`，项目内函数、宏、结构体、typedef 和简单全局变量都可以参与补全。

新增代码片段补全：输入 `main`、`for`、`if`、`switch` 后按 `Tab` 可以展开多行 C 模板，光标会落在模板中间的可编辑位置。

新增导航和诊断：

```text
Ctrl-]       跳转到光标下符号的定义
Ctrl-O       返回跳转前的位置
:symbols     查看当前文件符号列表
:diag        查看 C 诊断列表
:goto 42     跳转到第 42 行
:help        查看内置帮助
Ctrl-N/P     在诊断之间前后跳转
```

内置轻量 C 诊断会提示括号不匹配、字符串未闭合、重复 include 和明显缺少分号的行。状态栏会显示当前版本和诊断数量。

新增文件和配置命令：

```text
:e path/to/file.c              打开文件
:w path/to/file.c              另存为
:set auto_pair on|off          开关自动补全括号
:set completion_key ctrl-g     修改手动补全快捷键
:set number on|off             开关行号
```

`:set` 命令会写入 `~/.config/sfe/config.json`。

## v0.4.2 项目工作流

`sfe` 现在会通过 `Makefile`、`.git` 或 `compile_commands.json` 向上识别项目根目录，并围绕项目提供文件、构建和错误导航能力：

```text
:tree                打开/关闭左侧项目文件树
:tree open|close     明确打开或关闭项目文件树
:open main           模糊打开匹配的项目文件
:recent              查看最近打开的项目文件
:make                执行构建命令，并解析编译器错误
:run                 运行配置的命令或上次构建产物
:errors              查看 :make 捕获的错误和警告
Ctrl-N/P             在内置诊断和构建错误之间跳转
```

项目文件树默认只显示顶层目录和文件，不会一次性展开整个项目。树面板获得焦点时，可以用 `j` / `k` 或方向键移动光标；选中目录按 `Enter` 展开，再按一次收起；选中文件按 `Enter` 在右侧编辑窗口打开。`Ctrl-W` 在树面板和编辑窗口之间切换焦点，`q` 或 `Esc` 关闭树面板。

构建命令选择规则：

- 如果配置了 `build_command`，`:make` 直接执行该命令。
- 如果项目根目录有 `Makefile`，`:make` 默认执行 `make`。
- 如果当前文件是 `.c` 文件且没有 `Makefile`，`:make` 会使用 `gcc <file> -o <stem>` 作为 fallback。

编译器输出支持常见 GCC/Make 格式，例如 `src/main.c:4:12: error: ...`。解析出的错误会进入 `:errors`，也可以用 `Ctrl-N` / `Ctrl-P` 跳转到对应文件和行列。

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
| `u` | 撤销上一次编辑 |
| `Ctrl-R` | 重做上一次撤销 |
| `/` 或 `Ctrl-F` | 搜索文本 |
| `n` / `N` | 重复上一次搜索，分别跳到下一个 / 上一个匹配 |
| `Tab` | 补全菜单未打开时插入配置的缩进空格，默认 4 个 |
| `{` / `(` / `[` / `"` / `'` | 自动补全右半边，并把光标留在中间；右半边会以半透明占位符显示，再次输入对应右半边时会跳过它 |

Insert 模式下，`Backspace` 在空配对符号中会一次删除一对，例如 `{|}` 变为空；在空缩进行会按 `indent_width` 退一级缩进。输入 `}` 时，如果当前行只有缩进和 `}`，会自动回退一级，让闭合花括号与外层块对齐。

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

说明：不同 SSH 终端对 `Ctrl-Space` 的编码不完全一致，编辑器会兼容常见的
`NUL`、控制字符、CSI-u 和 xterm modifyOtherKeys 编码。

补全来源：

- C 关键字，例如 `int`、`return`、`struct`。
- 常用 C 标准库符号，例如 `printf`、`malloc`、`strlen`。
- 当前文件里的变量名、函数名和其他标识符。
- 同目录 `.h` 文件中的宏、`typedef`、`struct` 和简单函数声明。
- `#include` 场景下的头文件名，例如 `stdio.h`、`stdlib.h`、`local.h`。

排序规则：

- 前缀完全匹配优先于普通模糊匹配，例如输入 `in` 时 `int` 会排在 `main`、`printf` 之前。
- 在同为前缀匹配时，C 关键字优先，其次是当前文件标识符和标准库符号。
- 在同为模糊匹配时，当前文件中的标识符优先，其次是同目录头文件符号。
- 支持模糊匹配，例如 `pt` 可以匹配 `print_total` 和 `printf`。
- 输入函数名和 `(` 后，如果能识别函数签名，底部会显示简短签名提示。

## 配置

系统默认配置文件路径为 `/etc/sfe/config.json`，用户配置文件路径为 `~/.config/sfe/config.json`。用户配置优先于系统配置。如果文件不存在，编辑器会使用内置默认配置：

```json
{
  "auto_pair": true,
  "build_command": "",
  "completion_key": "ctrl+space",
  "indent_width": 4,
  "project_root_markers": [
    "Makefile",
    ".git",
    "compile_commands.json"
  ],
  "recent_files_limit": 20,
  "run_command": "",
  "show_line_numbers": true,
  "scan_local_headers": true,
  "signature_help": true
}
```

关闭成对符号自动补全：

```bash
mkdir -p ~/.config/sfe
cat > ~/.config/sfe/config.json <<'EOF'
{
  "auto_pair": false,
  "build_command": "",
  "completion_key": "ctrl+space",
  "indent_width": 4,
  "project_root_markers": [
    "Makefile",
    ".git",
    "compile_commands.json"
  ],
  "recent_files_limit": 20,
  "run_command": "",
  "show_line_numbers": true,
  "scan_local_headers": true,
  "signature_help": true
}
EOF
```

修改手动补全快捷键，例如改成 `Ctrl-G`：

```json
{
  "auto_pair": true,
  "build_command": "",
  "completion_key": "ctrl-g",
  "indent_width": 4,
  "project_root_markers": [
    "Makefile",
    ".git",
    "compile_commands.json"
  ],
  "recent_files_limit": 20,
  "run_command": "",
  "show_line_numbers": true,
  "scan_local_headers": true,
  "signature_help": true
}
```

关闭行号、同目录头文件扫描或签名提示时，把对应值改为 `false`。`indent_width` 支持 1 到 8 的整数。

项目工作流相关配置：

- `build_command`：`:make` 使用的构建命令，留空时自动选择 `make` 或 `gcc` fallback。
- `run_command`：`:run` 使用的运行命令，留空时尝试使用上次构建产物。
- `project_root_markers`：用于识别项目根目录的标记文件或目录。
- `recent_files_limit`：`:recent` 最多保留的项目文件数量。

`completion_key` 支持 `ctrl+space`、`ctrl-a` 到 `ctrl-z`、`ctrl-@`、`ctrl-_`、`tab` 和 `enter`。
终端可能无法区分某些组合键，例如 `Ctrl-J` 通常等同于 Enter，`Ctrl-I` 通常等同于 Tab；
`Ctrl-S` 和 `Ctrl-Q` 也可能被终端流控占用。

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
python3 -m unittest tests.test_sfe_core tests.test_sfe_ui tests.test_config tests.test_packaging -v
```

运行语法检查：

```bash
python3 -m py_compile sfe.py sfe_core.py scripts/build_deb.py
```

构建 deb 包：

```bash
python3 scripts/build_deb.py
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
python3 -m unittest tests.test_sfe_core tests.test_sfe_ui tests.test_config tests.test_packaging -v
python3 -m py_compile sfe.py sfe_core.py scripts/build_deb.py
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

当前补全基于内置词表、当前文件标识符和同目录 `.h` 文件的轻量扫描，不做完整语义分析。后续可以扩展：

- 对项目目录建立符号索引。
- 可选接入 `clangd` 或 `ctags`。

## 许可证

当前仓库尚未添加许可证文件。如需开源发布，请先补充 `LICENSE`。
