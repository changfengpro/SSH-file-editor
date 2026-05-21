from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER_RE = re.compile(r"\b(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?)\b")
FUNCTION_DECL_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_\s\*]*?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^;{}]*)\)\s*;"
)
FUNCTION_DEF_RE = re.compile(
    r"^\s*(?:static\s+|extern\s+|inline\s+)*([A-Za-z_][A-Za-z0-9_\s\*]*?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^;{}]*)\)\s*\{"
)
GLOBAL_RE = re.compile(
    r"^\s*(?:static\s+|extern\s+|const\s+|volatile\s+)*[A-Za-z_][A-Za-z0-9_\s\*]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*[^;]+)?;"
)
PAIRS = {"{": "}", "(": ")", "[": "]", '"': '"', "'": "'"}


class TextBuffer:
    def __init__(self, lines: list[str] | None = None):
        self.lines = list(lines or [""])
        if not self.lines:
            self.lines = [""]
        self.cursor_row = 0
        self.cursor_col = 0
        self.dirty = False

    @classmethod
    def from_text(cls, text: str) -> "TextBuffer":
        lines = text.splitlines()
        if text.endswith("\n"):
            lines.append("")
        return cls(lines or [""])

    def to_text(self) -> str:
        return "\n".join(self.lines)

    def current_line(self) -> str:
        return self.lines[self.cursor_row]

    def snapshot(self) -> "BufferSnapshot":
        return BufferSnapshot(tuple(self.lines), self.cursor_row, self.cursor_col, self.dirty)

    def restore(self, snapshot: "BufferSnapshot") -> None:
        self.lines = list(snapshot.lines) or [""]
        self.cursor_row = min(snapshot.cursor_row, len(self.lines) - 1)
        self.cursor_col = min(snapshot.cursor_col, len(self.current_line()))
        self.dirty = snapshot.dirty

    def move_left(self) -> None:
        if self.cursor_col > 0:
            self.cursor_col -= 1
        elif self.cursor_row > 0:
            self.cursor_row -= 1
            self.cursor_col = len(self.current_line())

    def move_right(self) -> None:
        if self.cursor_col < len(self.current_line()):
            self.cursor_col += 1
        elif self.cursor_row < len(self.lines) - 1:
            self.cursor_row += 1
            self.cursor_col = 0

    def move_up(self) -> None:
        if self.cursor_row > 0:
            self.cursor_row -= 1
            self.cursor_col = min(self.cursor_col, len(self.current_line()))

    def move_down(self) -> None:
        if self.cursor_row < len(self.lines) - 1:
            self.cursor_row += 1
            self.cursor_col = min(self.cursor_col, len(self.current_line()))

    def move_home(self) -> None:
        self.cursor_col = 0

    def move_end(self) -> None:
        self.cursor_col = len(self.current_line())

    def insert(self, text: str) -> None:
        if not text:
            return
        line = self.current_line()
        self.lines[self.cursor_row] = line[: self.cursor_col] + text + line[self.cursor_col :]
        self.cursor_col += len(text)
        self.dirty = True

    def indent(self, width: int = 4) -> None:
        self.insert(" " * width)

    def insert_pair(self, opener: str, closer: str) -> None:
        self.insert(opener + closer)
        self.cursor_col -= len(closer)

    def newline(self) -> None:
        line = self.current_line()
        before = line[: self.cursor_col]
        after = line[self.cursor_col :]
        self.lines[self.cursor_row] = before
        self.lines.insert(self.cursor_row + 1, after)
        self.cursor_row += 1
        self.cursor_col = 0
        self.dirty = True

    def newline_with_indent(self, indent_width: int = 4) -> None:
        line = self.current_line()
        before = line[: self.cursor_col]
        leading = re.match(r"\s*", before).group(0)
        stripped_before = before.rstrip()
        opener = stripped_before[-1:] if stripped_before else ""
        extra = " " * indent_width if opener in PAIRS else ""
        indent = leading + extra
        after = line[self.cursor_col :]
        self.lines[self.cursor_row] = before
        if opener in PAIRS and after.lstrip().startswith(PAIRS[opener]):
            self.lines.insert(self.cursor_row + 1, indent)
            self.lines.insert(self.cursor_row + 2, leading + after.lstrip())
        else:
            self.lines.insert(self.cursor_row + 1, indent + after)
        self.cursor_row += 1
        self.cursor_col = len(indent)
        self.dirty = True

    def backspace_smart(self, indent_width: int = 4) -> bool:
        if self.cursor_col <= 0:
            return False
        line = self.current_line()
        before = line[: self.cursor_col]
        after = line[self.cursor_col :]
        if before[-1:] in PAIRS and after.startswith(PAIRS[before[-1]]):
            self.lines[self.cursor_row] = before[:-1] + after[1:]
            self.cursor_col -= 1
            self.dirty = True
            return True
        if before.strip() == "" and len(before) >= indent_width and len(before) % indent_width == 0:
            self.lines[self.cursor_row] = before[:-indent_width] + after
            self.cursor_col -= indent_width
            self.dirty = True
            return True
        return False

    def align_closing_brace(self, indent_width: int = 4) -> bool:
        line = self.current_line()
        stripped = line.strip()
        if stripped != "}":
            return False
        leading = re.match(r"\s*", line).group(0)
        if len(leading) < indent_width:
            return False
        new_leading = leading[:-indent_width]
        self.lines[self.cursor_row] = new_leading + stripped
        self.cursor_col = min(len(new_leading) + 1, len(self.current_line()))
        self.dirty = True
        return True

    def backspace(self) -> None:
        if self.cursor_col > 0:
            line = self.current_line()
            self.lines[self.cursor_row] = line[: self.cursor_col - 1] + line[self.cursor_col :]
            self.cursor_col -= 1
            self.dirty = True
            return
        if self.cursor_row == 0:
            return
        previous_len = len(self.lines[self.cursor_row - 1])
        self.lines[self.cursor_row - 1] += self.current_line()
        del self.lines[self.cursor_row]
        self.cursor_row -= 1
        self.cursor_col = previous_len
        self.dirty = True

    def delete(self) -> None:
        line = self.current_line()
        if self.cursor_col < len(line):
            self.lines[self.cursor_row] = line[: self.cursor_col] + line[self.cursor_col + 1 :]
            self.dirty = True
            return
        if self.cursor_row < len(self.lines) - 1:
            self.lines[self.cursor_row] += self.lines[self.cursor_row + 1]
            del self.lines[self.cursor_row + 1]
            self.dirty = True

    def current_prefix(self) -> str:
        line = self.current_line()
        start = self.cursor_col
        while start > 0 and re.match(r"[A-Za-z0-9_]", line[start - 1]):
            start -= 1
        prefix = line[start : self.cursor_col]
        if prefix and re.match(r"[A-Za-z_]", prefix[0]):
            return prefix
        return ""

    def replace_current_prefix(self, replacement: str) -> None:
        prefix = self.current_prefix()
        line = self.current_line()
        start = self.cursor_col - len(prefix)
        self.lines[self.cursor_row] = line[:start] + replacement + line[self.cursor_col :]
        self.cursor_col = start + len(replacement)
        self.dirty = True

    def replace_current_prefix_with_snippet(self, snippet: str) -> None:
        prefix = self.current_prefix()
        line = self.current_line()
        start = self.cursor_col - len(prefix)
        before = line[:start]
        after = line[self.cursor_col :]
        cursor_marker = snippet.find("$0")
        clean_snippet = snippet.replace("$0", "", 1)
        replacement_lines = clean_snippet.split("\n")
        first_row = self.cursor_row
        indent = re.match(r"\s*", before).group(0)
        if len(replacement_lines) == 1:
            self.lines[first_row] = before + replacement_lines[0] + after
            marker = cursor_marker if cursor_marker >= 0 else len(clean_snippet)
            self.cursor_col = start + marker
        else:
            new_lines = [before + replacement_lines[0]]
            new_lines.extend(indent + item for item in replacement_lines[1:-1])
            new_lines.append(indent + replacement_lines[-1] + after)
            self.lines[first_row : first_row + 1] = new_lines
            if cursor_marker >= 0:
                before_marker = snippet[:cursor_marker]
                marker_row = before_marker.count("\n")
                marker_col = len(before_marker.rsplit("\n", 1)[-1])
                self.cursor_row = first_row + marker_row
                self.cursor_col = len(indent) + marker_col if marker_row else start + marker_col
            else:
                self.cursor_row = first_row + len(replacement_lines) - 1
                self.cursor_col = len(replacement_lines[-1])
        self.dirty = True


@dataclass(frozen=True)
class BufferSnapshot:
    lines: tuple[str, ...]
    cursor_row: int
    cursor_col: int
    dirty: bool


class UndoManager:
    def __init__(self):
        self._undo: list[BufferSnapshot] = []
        self._redo: list[BufferSnapshot] = []

    def record(self, buffer: TextBuffer) -> None:
        snapshot = buffer.snapshot()
        if self._undo and self._undo[-1] == snapshot:
            return
        self._undo.append(snapshot)
        self._redo.clear()

    def undo(self, buffer: TextBuffer) -> bool:
        if not self._undo:
            return False
        self._redo.append(buffer.snapshot())
        buffer.restore(self._undo.pop())
        return True

    def redo(self, buffer: TextBuffer) -> bool:
        if not self._redo:
            return False
        self._undo.append(buffer.snapshot())
        buffer.restore(self._redo.pop())
        return True


@dataclass(frozen=True)
class VimCommandResult:
    save: bool = False
    quit: bool = False
    force: bool = False
    message: str = ""


class VimCommandProcessor:
    def execute(self, command: str, dirty: bool) -> VimCommandResult:
        command = command.strip()
        if command.startswith(":"):
            command = command[1:].strip()
        if command in ("w", "write"):
            return VimCommandResult(save=True, message="written")
        if command in ("q", "quit"):
            if dirty:
                return VimCommandResult(message="E37: No write since last change (add ! to override)")
            return VimCommandResult(quit=True)
        if command in ("q!", "quit!"):
            return VimCommandResult(quit=True, force=True)
        if command in ("wq", "x", "xit"):
            return VimCommandResult(save=True, quit=True, message="written")
        if command in ("wq!", "x!"):
            return VimCommandResult(save=True, quit=True, force=True, message="written")
        return VimCommandResult(message=f"E492: Not an editor command: {command}")


@dataclass(frozen=True)
class SyntaxToken:
    text: str
    kind: str


class SyntaxHighlighter:
    KEYWORDS = {
        "auto",
        "break",
        "case",
        "char",
        "const",
        "continue",
        "default",
        "do",
        "double",
        "else",
        "enum",
        "extern",
        "float",
        "for",
        "goto",
        "if",
        "inline",
        "int",
        "long",
        "register",
        "restrict",
        "return",
        "short",
        "signed",
        "sizeof",
        "static",
        "struct",
        "switch",
        "typedef",
        "union",
        "unsigned",
        "void",
        "volatile",
        "while",
    }

    def tokenize(self, line: str) -> list[SyntaxToken]:
        tokens: list[SyntaxToken] = []
        index = 0
        while index < len(line):
            if line.startswith("//", index):
                tokens.append(SyntaxToken(line[index:], "comment"))
                break
            if line.startswith("/*", index):
                end = line.find("*/", index + 2)
                if end == -1:
                    tokens.append(SyntaxToken(line[index:], "comment"))
                    break
                end += 2
                tokens.append(SyntaxToken(line[index:end], "comment"))
                index = end
                continue
            char = line[index]
            if char in ('"', "'"):
                end = self._string_end(line, index, char)
                tokens.append(SyntaxToken(line[index:end], "string"))
                index = end
                continue
            if char == "#":
                match = re.match(r"#[A-Za-z_][A-Za-z0-9_]*", line[index:])
                if match:
                    text = match.group(0)
                    tokens.append(SyntaxToken(text, "preprocessor"))
                    index += len(text)
                    continue
            number_match = NUMBER_RE.match(line, index)
            if number_match:
                text = number_match.group(0)
                tokens.append(SyntaxToken(text, "number"))
                index += len(text)
                continue
            word_match = WORD_RE.match(line, index)
            if word_match:
                text = word_match.group(0)
                kind = self._word_kind(line, word_match.end(), text)
                tokens.append(SyntaxToken(text, kind))
                index += len(text)
                continue
            tokens.append(SyntaxToken(char, "plain"))
            index += 1
        return self._merge_plain(tokens)

    def _string_end(self, line: str, start: int, quote: str) -> int:
        index = start + 1
        escaped = False
        while index < len(line):
            char = line[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                return index + 1
            index += 1
        return len(line)

    def _word_kind(self, line: str, word_end: int, text: str) -> str:
        if text in self.KEYWORDS:
            return "keyword"
        after_word = word_end
        while after_word < len(line) and line[after_word].isspace():
            after_word += 1
        if after_word < len(line) and line[after_word] == "(":
            return "function"
        return "plain"

    def _merge_plain(self, tokens: list[SyntaxToken]) -> list[SyntaxToken]:
        merged: list[SyntaxToken] = []
        for token in tokens:
            if merged and token.kind == "plain" and merged[-1].kind == "plain":
                merged[-1] = SyntaxToken(merged[-1].text + token.text, "plain")
            else:
                merged.append(token)
        return merged


@dataclass(frozen=True)
class CompletionItem:
    text: str
    kind: str
    detail: str = ""
    source: str = ""
    insert_text: str = ""


@dataclass(frozen=True)
class CProjectSymbol:
    name: str
    kind: str
    detail: str = ""
    signature: str = ""


@dataclass(frozen=True)
class HeaderIndex:
    headers: set[str]
    symbols: list[CProjectSymbol]


@dataclass(frozen=True)
class ProjectSymbol:
    name: str
    kind: str
    path: Path
    row: int
    col: int
    detail: str = ""
    signature: str = ""


@dataclass(frozen=True)
class ProjectIndex:
    root: Path
    symbols: list[ProjectSymbol]

    def find_definition(self, name: str) -> ProjectSymbol | None:
        for symbol in self.symbols:
            if symbol.name == name:
                return symbol
        return None


class ProjectScanner:
    IGNORED_DIRS = {".git", ".hg", ".svn", ".worktrees", "build", "dist", "__pycache__"}
    SUFFIXES = {".c", ".h"}

    def scan(self, root: Path) -> ProjectIndex:
        root = Path(root)
        symbols: list[ProjectSymbol] = []
        try:
            paths = sorted(path for path in root.rglob("*") if self._should_scan(path))
        except OSError:
            return ProjectIndex(root, [])
        for path in paths:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            symbols.extend(self._scan_file(path, lines))
        return ProjectIndex(root, symbols)

    def _should_scan(self, path: Path) -> bool:
        if not path.is_file() or path.suffix not in self.SUFFIXES:
            return False
        return not any(part in self.IGNORED_DIRS for part in path.parts)

    def _scan_file(self, path: Path, lines: list[str]) -> list[ProjectSymbol]:
        symbols: list[ProjectSymbol] = []
        for row, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            macro = re.match(r"#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\b", stripped)
            if macro:
                symbols.append(ProjectSymbol(macro.group(1), "macro", path, row, line.find(macro.group(1)), path.name))
                continue
            typedef = re.match(r"typedef\b.+\b([A-Za-z_][A-Za-z0-9_]*)\s*;", stripped)
            if typedef:
                symbols.append(ProjectSymbol(typedef.group(1), "typedef", path, row, line.find(typedef.group(1)), path.name))
                continue
            struct = re.match(r"struct\s+([A-Za-z_][A-Za-z0-9_]*)\b", stripped)
            if struct:
                symbols.append(ProjectSymbol(struct.group(1), "struct", path, row, line.find(struct.group(1)), path.name))
                continue
            function = FUNCTION_DEF_RE.match(stripped) or FUNCTION_DECL_RE.match(stripped)
            if function:
                return_type = " ".join(function.group(1).split())
                name = function.group(2)
                params = " ".join(function.group(3).split())
                signature = f"{return_type} {name}({params})"
                symbols.append(ProjectSymbol(name, "function", path, row, line.find(name), f"{path.name}:{row + 1}", signature))
                continue
            global_match = GLOBAL_RE.match(stripped)
            if global_match and "(" not in stripped.split("=", 1)[0]:
                name = global_match.group(1)
                if name not in SyntaxHighlighter.KEYWORDS:
                    symbols.append(ProjectSymbol(name, "global", path, row, line.find(name), f"{path.name}:{row + 1}"))
        return symbols


@dataclass(frozen=True)
class Diagnostic:
    row: int
    col: int
    message: str
    severity: str = "warning"


class CDiagnosticEngine:
    OPENERS = {"(": ")", "[": "]", "{": "}"}
    CLOSERS = {")": "(", "]": "[", "}": "{"}

    def analyze(self, lines: list[str]) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        diagnostics.extend(self._duplicate_includes(lines))
        diagnostics.extend(self._strings_and_brackets(lines))
        diagnostics.extend(self._missing_semicolons(lines))
        diagnostics.sort(key=lambda item: (item.row, item.col, item.message))
        return diagnostics

    def _duplicate_includes(self, lines: list[str]) -> list[Diagnostic]:
        seen: dict[str, tuple[int, int]] = {}
        diagnostics: list[Diagnostic] = []
        for row, line in enumerate(lines):
            match = re.match(r'\s*#\s*include\s*[<"]([^>"]+)[>"]', line)
            if not match:
                continue
            include = match.group(1)
            if include in seen:
                diagnostics.append(Diagnostic(row, line.find(include), f"duplicate include: {include}"))
            else:
                seen[include] = (row, line.find(include))
        return diagnostics

    def _strings_and_brackets(self, lines: list[str]) -> list[Diagnostic]:
        stack: list[tuple[str, int, int]] = []
        diagnostics: list[Diagnostic] = []
        for row, line in enumerate(lines):
            in_string = ""
            escaped = False
            index = 0
            while index < len(line):
                if not in_string and line.startswith("//", index):
                    break
                char = line[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == in_string:
                        in_string = ""
                    index += 1
                    continue
                if char in ('"', "'"):
                    in_string = char
                elif char in self.OPENERS:
                    stack.append((char, row, index))
                elif char in self.CLOSERS:
                    if stack and stack[-1][0] == self.CLOSERS[char]:
                        stack.pop()
                    else:
                        diagnostics.append(Diagnostic(row, index, f"unmatched {char}"))
                index += 1
            if in_string:
                diagnostics.append(Diagnostic(row, len(line), "unterminated string literal"))
        for opener, row, col in stack:
            diagnostics.append(Diagnostic(row, col, f"unmatched {opener}"))
        return diagnostics

    def _missing_semicolons(self, lines: list[str]) -> list[Diagnostic]:
        diagnostics: list[Diagnostic] = []
        for row, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue
            if stripped.endswith((";", "{", "}", ":", ",")) or stripped in {"else", "do"}:
                continue
            if re.match(r"(if|for|while|switch)\s*\(", stripped):
                continue
            if re.match(r"(return|break|continue)\b", stripped) or re.match(
                r"(?:static\s+|const\s+|unsigned\s+|signed\s+|long\s+|short\s+)*[A-Za-z_][A-Za-z0-9_\s\*]*\s+[A-Za-z_][A-Za-z0-9_]*(?:\s*=.+)?$",
                stripped,
            ) or "=" in stripped:
                diagnostics.append(Diagnostic(row, len(line), "missing semicolon"))
        return diagnostics


class HeaderScanner:
    def scan(self, directory: Path) -> HeaderIndex:
        headers: set[str] = set()
        symbols: list[CProjectSymbol] = []
        try:
            header_paths = sorted(directory.glob("*.h"))
        except OSError:
            return HeaderIndex(headers, symbols)
        for path in header_paths:
            headers.add(path.name)
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            symbols.extend(self._scan_lines(path.name, lines))
        return HeaderIndex(headers, symbols)

    def _scan_lines(self, header_name: str, lines: list[str]) -> list[CProjectSymbol]:
        symbols: list[CProjectSymbol] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            macro = re.match(r"#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\b", stripped)
            if macro:
                symbols.append(CProjectSymbol(macro.group(1), "macro", header_name))
                continue
            typedef = re.match(r"typedef\b.+\b([A-Za-z_][A-Za-z0-9_]*)\s*;", stripped)
            if typedef:
                symbols.append(CProjectSymbol(typedef.group(1), "typedef", header_name))
                continue
            struct = re.match(r"struct\s+([A-Za-z_][A-Za-z0-9_]*)\b", stripped)
            if struct:
                symbols.append(CProjectSymbol(struct.group(1), "struct", header_name))
                continue
            function = FUNCTION_DECL_RE.match(stripped)
            if function:
                return_type = " ".join(function.group(1).split())
                name = function.group(2)
                params = " ".join(function.group(3).split())
                symbols.append(CProjectSymbol(name, "function", header_name, f"{return_type} {name}({params})"))
        return symbols


class CompletionEngine:
    C_KEYWORDS = {
        "auto",
        "break",
        "case",
        "char",
        "const",
        "continue",
        "default",
        "do",
        "double",
        "else",
        "enum",
        "extern",
        "float",
        "for",
        "goto",
        "if",
        "inline",
        "int",
        "long",
        "register",
        "restrict",
        "return",
        "short",
        "signed",
        "sizeof",
        "static",
        "struct",
        "switch",
        "typedef",
        "union",
        "unsigned",
        "void",
        "volatile",
        "while",
    }
    STD_SYMBOLS = {
        "calloc",
        "fclose",
        "fgets",
        "fopen",
        "fprintf",
        "free",
        "fscanf",
        "malloc",
        "memcpy",
        "memset",
        "perror",
        "printf",
        "puts",
        "scanf",
        "snprintf",
        "sprintf",
        "stderr",
        "stdin",
        "stdout",
        "strcmp",
        "strcpy",
        "strlen",
        "strncpy",
    }
    HEADERS = {
        "assert.h",
        "ctype.h",
        "errno.h",
        "limits.h",
        "math.h",
        "stdbool.h",
        "stddef.h",
        "stdint.h",
        "stdio.h",
        "stdlib.h",
        "string.h",
        "time.h",
    }
    SNIPPETS = {
        "main": CompletionItem("main", "snippet", "int main(void)", "snippet", "int main(void) {\n    $0\n}"),
        "for": CompletionItem("for", "snippet", "for loop", "snippet", "for ($0; ; ) {\n    \n}"),
        "if": CompletionItem("if", "snippet", "if block", "snippet", "if ($0) {\n    \n}"),
        "switch": CompletionItem("switch", "snippet", "switch block", "snippet", "switch ($0) {\ncase 0:\n    break;\ndefault:\n    break;\n}"),
    }

    def suggest(
        self,
        prefix: str,
        lines: list[str],
        row: int,
        col: int,
        limit: int = 12,
        header_index: HeaderIndex | None = None,
        project_index: ProjectIndex | None = None,
    ) -> list[CompletionItem]:
        if not prefix:
            return []
        candidates: dict[str, CompletionItem] = {}
        if self._is_include_context(lines, row, col):
            if self._is_local_include_context(lines, row, col) and header_index is not None:
                for header in header_index.headers:
                    candidates[header] = CompletionItem(header, "local header", "local header", "header")
            else:
                for header in self.HEADERS:
                    candidates[header] = CompletionItem(header, "header", "C header", "header")
        else:
            for snippet in self.SNIPPETS.values():
                candidates[snippet.text] = snippet
            for keyword in self.C_KEYWORDS:
                candidates.setdefault(keyword, CompletionItem(keyword, "keyword", "C keyword", "keyword"))
            for symbol in self.STD_SYMBOLS:
                candidates[symbol] = CompletionItem(symbol, "stdlib", "C standard library", "std")
            for word in self._buffer_identifiers(lines):
                candidates.setdefault(word, CompletionItem(word, "buffer", "current file", "file"))
            if header_index is not None:
                for symbol in header_index.symbols:
                    candidates.setdefault(symbol.name, CompletionItem(symbol.name, symbol.kind, symbol.detail or self._detail_for(symbol.kind), "header"))
            if project_index is not None:
                for symbol in project_index.symbols:
                    candidates.setdefault(symbol.name, CompletionItem(symbol.name, symbol.kind, symbol.detail or f"{symbol.path.name}:{symbol.row + 1}", "project"))

        scored = []
        for text, item in candidates.items():
            score = self._match_score(prefix, text, item.kind)
            if score is not None and not (text == prefix and item.kind != "snippet"):
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0])
        return [item for _, item in scored[:limit]]

    def _buffer_identifiers(self, lines: list[str]) -> set[str]:
        words: set[str] = set()
        for line in lines:
            words.update(WORD_RE.findall(line))
        return words

    def _is_include_context(self, lines: list[str], row: int, col: int) -> bool:
        if row < 0 or row >= len(lines):
            return False
        before_cursor = lines[row][:col]
        return before_cursor.lstrip().startswith("#include")

    def _is_local_include_context(self, lines: list[str], row: int, col: int) -> bool:
        before_cursor = lines[row][:col]
        return '"' in before_cursor and "<" not in before_cursor

    def _match_score(self, prefix: str, text: str, kind: str) -> tuple[int, int, int, int, str] | None:
        needle = prefix.lower()
        haystack = text.lower()
        if haystack.startswith(needle):
            match_group = 0
            match_rank = 0
        else:
            pos = 0
            gaps = 0
            for char in needle:
                found = haystack.find(char, pos)
                if found == -1:
                    return None
                gaps += found - pos
                pos = found + 1
            match_group = 1
            match_rank = 1 + gaps
        if match_rank == 0:
            kind_rank = {
                "snippet": 0,
                "keyword": 0,
                "buffer": 1,
                "macro": 2,
                "typedef": 2,
                "struct": 2,
                "function": 2,
                "stdlib": 3,
                "header": 0,
                "local header": 0,
            }.get(kind, 9)
        else:
            kind_rank = {
                "buffer": 0,
                "snippet": 1,
                "macro": 1,
                "typedef": 1,
                "struct": 1,
                "function": 1,
                "stdlib": 2,
                "keyword": 3,
                "header": 0,
                "local header": 0,
            }.get(kind, 9)
        return (match_group, kind_rank, match_rank, len(text), haystack)

    def _detail_for(self, kind: str) -> str:
        return {
            "buffer": "current file",
            "stdlib": "C standard library",
            "keyword": "C keyword",
            "header": "C header",
            "local header": "local header",
            "macro": "local header macro",
            "typedef": "local header typedef",
            "struct": "local header struct",
            "function": "local header function",
        }.get(kind, kind)


class SignatureHelpEngine:
    BUILTIN_SIGNATURES = {
        "printf": "int printf(const char *format, ...)",
        "fprintf": "int fprintf(FILE *stream, const char *format, ...)",
        "scanf": "int scanf(const char *format, ...)",
        "malloc": "void *malloc(size_t size)",
        "free": "void free(void *ptr)",
        "strlen": "size_t strlen(const char *s)",
        "strcpy": "char *strcpy(char *dest, const char *src)",
        "memcpy": "void *memcpy(void *dest, const void *src, size_t n)",
        "fopen": "FILE *fopen(const char *path, const char *mode)",
        "fclose": "int fclose(FILE *stream)",
    }

    def __init__(self, signatures: dict[str, str] | None = None):
        self.signatures = dict(self.BUILTIN_SIGNATURES)
        if signatures:
            self.signatures.update(signatures)

    @classmethod
    def from_header_index(cls, header_index: HeaderIndex | None) -> "SignatureHelpEngine":
        signatures = {}
        if header_index is not None:
            for symbol in header_index.symbols:
                if symbol.kind == "function" and symbol.signature:
                    signatures[symbol.name] = symbol.signature
        return cls(signatures)

    def signature_for(self, line: str, col: int) -> str:
        before_cursor = line[:col]
        call_name = self._nearest_call_name(before_cursor)
        if not call_name:
            return ""
        return self.signatures.get(call_name, "")

    def _nearest_call_name(self, text: str) -> str:
        depth = 0
        for index in range(len(text) - 1, -1, -1):
            char = text[index]
            if char == ")":
                depth += 1
            elif char == "(":
                if depth:
                    depth -= 1
                    continue
                prefix = text[:index].rstrip()
                match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)$", prefix)
                return match.group(1) if match else ""
        return ""
