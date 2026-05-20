# SSH File Editor 实现计划

> **面向 AI 代理的工作者：** 按 TDD 小步实现。先验证核心逻辑测试失败，再实现最小代码通过测试，最后补 curses 界面和远端验证。

**目标：** 在 `/home/rmer/project/SSH file editor` 提供一个可通过 SSH 直接运行的轻量终端代码编辑器，并带有 C 代码补全能力。

**架构：** 核心编辑缓冲区和补全引擎放在 `sfe_core.py`，便于自动化测试；`sfe.py` 只负责 curses 终端 UI、按键处理和文件读写入口。补全不依赖 clangd/ctags，先使用 C 关键字、常用标准库符号、头文件名和当前文件标识符。

**技术栈：** Python 3.10 标准库、curses、unittest。

---

### 任务 1：核心编辑缓冲区

**文件：**
- 创建：`tests/test_sfe_core.py`
- 创建：`sfe_core.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_insert_newline_and_backspace_merge_lines(self):
    buf = TextBuffer(["int main() {", "return 0;"])
    buf.cursor_row = 0
    buf.cursor_col = 3
    buf.newline()
    self.assertEqual(buf.lines, ["int", " main() {", "return 0;"])
    buf.backspace()
    self.assertEqual(buf.lines, ["int main() {", "return 0;"])
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m unittest tests.test_sfe_core -v`
预期：失败，提示 `sfe_core` 模块不存在或 `TextBuffer` 未定义。

- [ ] **步骤 3：实现最小缓冲区代码**

```python
class TextBuffer:
    def __init__(self, lines=None):
        self.lines = list(lines or [""])
        self.cursor_row = 0
        self.cursor_col = 0
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m unittest tests.test_sfe_core -v`
预期：PASS。

### 任务 2：C 补全引擎

**文件：**
- 修改：`tests/test_sfe_core.py`
- 修改：`sfe_core.py`

- [ ] **步骤 1：编写失败测试**

```python
def test_c_completion_uses_keywords_stdlib_and_buffer_identifiers(self):
    engine = CompletionEngine()
    lines = ["int print_total = 0;", "void prepare_value(void);"]
    names = [item.text for item in engine.suggest("pr", lines, 0, 2)]
    self.assertIn("printf", names)
    self.assertIn("prepare_value", names)
    self.assertIn("print_total", names)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m unittest tests.test_sfe_core -v`
预期：失败，提示 `CompletionEngine` 未定义。

- [ ] **步骤 3：实现最小补全代码**

```python
@dataclass(frozen=True)
class CompletionItem:
    text: str
    kind: str
```

- [ ] **步骤 4：运行测试验证通过**

运行：`python -m unittest tests.test_sfe_core -v`
预期：PASS。

### 任务 3：curses 编辑器入口

**文件：**
- 创建：`sfe.py`
- 创建：`README.md`

- [ ] **步骤 1：实现入口和按键循环**

```python
def main(argv=None):
    argv = argv or sys.argv[1:]
    path = argv[0] if argv else None
    curses.wrapper(lambda stdscr: EditorApp(stdscr, path).run())
```

- [ ] **步骤 2：支持基础快捷键**

`Ctrl-S` 保存、`Ctrl-Q` 退出、`Ctrl-Space`/`Tab` 打开补全、方向键移动、`Enter` 换行、`Backspace` 删除。

- [ ] **步骤 3：语法和导入验证**

运行：`python -m py_compile sfe.py sfe_core.py`
预期：exit 0。

### 任务 4：上传远端并验证

**文件：**
- 上传：`sfe.py`
- 上传：`sfe_core.py`
- 上传：`tests/test_sfe_core.py`
- 上传：`README.md`

- [ ] **步骤 1：远端运行自动化测试**

运行：`ssh rmer@192.168.1.145 'cd "/home/rmer/project/SSH file editor" && python3 -m unittest tests.test_sfe_core -v'`
预期：所有测试 PASS。

- [ ] **步骤 2：远端运行语法验证**

运行：`ssh rmer@192.168.1.145 'cd "/home/rmer/project/SSH file editor" && python3 -m py_compile sfe.py sfe_core.py'`
预期：exit 0。

- [ ] **步骤 3：远端 smoke test**

运行：`ssh rmer@192.168.1.145 'cd "/home/rmer/project/SSH file editor" && python3 sfe.py --help'`
预期：打印用法。
