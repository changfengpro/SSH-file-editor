from __future__ import annotations

from dataclasses import dataclass
import re


WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER_RE = re.compile(r"\b(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?)\b")


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

    def newline(self) -> None:
        line = self.current_line()
        before = line[: self.cursor_col]
        after = line[self.cursor_col :]
        self.lines[self.cursor_row] = before
        self.lines.insert(self.cursor_row + 1, after)
        self.cursor_row += 1
        self.cursor_col = 0
        self.dirty = True

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

    def suggest(self, prefix: str, lines: list[str], row: int, col: int, limit: int = 12) -> list[CompletionItem]:
        if not prefix:
            return []
        candidates: dict[str, str] = {}
        if self._is_include_context(lines, row, col):
            for header in self.HEADERS:
                candidates[header] = "header"
        else:
            for keyword in self.C_KEYWORDS:
                candidates[keyword] = "keyword"
            for symbol in self.STD_SYMBOLS:
                candidates[symbol] = "stdlib"
            for word in self._buffer_identifiers(lines):
                candidates.setdefault(word, "buffer")

        scored = []
        for text, kind in candidates.items():
            score = self._match_score(prefix, text, kind)
            if score is not None and text != prefix:
                scored.append((score, CompletionItem(text, kind, self._detail_for(kind))))
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

    def _match_score(self, prefix: str, text: str, kind: str) -> tuple[int, int, int, str] | None:
        needle = prefix.lower()
        haystack = text.lower()
        if haystack.startswith(needle):
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
            match_rank = 1 + gaps
        kind_rank = {"buffer": 0, "stdlib": 1, "keyword": 2, "header": 0}.get(kind, 9)
        return (kind_rank, match_rank, len(text), haystack)

    def _detail_for(self, kind: str) -> str:
        return {
            "buffer": "current file",
            "stdlib": "C standard library",
            "keyword": "C keyword",
            "header": "C header",
        }.get(kind, kind)
