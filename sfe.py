#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import sys
import unicodedata
from pathlib import Path

from sfe_core import CompletionEngine, SyntaxHighlighter, TextBuffer, VimCommandProcessor

try:
    import curses
except ModuleNotFoundError:
    curses = None


CTRL_Q = 17
CTRL_S = 19
CTRL_O = 15
CTRL_F = 6
CTRL_N = 14
CTRL_P = 16
CTRL_SPACE = 0
TAB = 9
ESCAPE = 27
PAIRS = {"{": "}", "(": ")", "[": "]", '"': '"', "'": "'"}
SYSTEM_CONFIG_PATH = Path("/etc/sfe/config.json")
DEFAULT_CONFIG_PATH = Path("~/.config/sfe/config.json").expanduser()
KEY_SEQUENCE_TIMEOUT_MS = 25


@dataclass(frozen=True)
class EditorConfig:
    auto_pair: bool = True
    completion_key: str = "ctrl+space"
    indent_width: int = 4
    show_line_numbers: bool = True
    scan_local_headers: bool = True
    signature_help: bool = True


def load_config(
    path: Path | None = None,
    *,
    user_path: Path | None = None,
    system_path: Path | None = None,
) -> EditorConfig:
    if path is not None and user_path is None:
        user_path = path
        system_path = Path("missing-system-config.json") if system_path is None else system_path
    user_path = DEFAULT_CONFIG_PATH if user_path is None else user_path
    system_path = SYSTEM_CONFIG_PATH if system_path is None else system_path
    config = EditorConfig()
    for candidate in (system_path, user_path):
        config = _merge_config(config, _read_config(candidate))
    return config


def _read_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _merge_config(base: EditorConfig, raw: dict) -> EditorConfig:
    auto_pair = raw.get("auto_pair", base.auto_pair)
    completion_key = raw.get("completion_key", base.completion_key)
    indent_width = raw.get("indent_width", base.indent_width)
    show_line_numbers = raw.get("show_line_numbers", base.show_line_numbers)
    scan_local_headers = raw.get("scan_local_headers", base.scan_local_headers)
    signature_help = raw.get("signature_help", base.signature_help)
    if not isinstance(auto_pair, bool):
        auto_pair = base.auto_pair
    if not isinstance(completion_key, str) or not normalize_key_name(completion_key):
        completion_key = base.completion_key
    if not isinstance(indent_width, int) or not 1 <= indent_width <= 8:
        indent_width = base.indent_width
    if not isinstance(show_line_numbers, bool):
        show_line_numbers = base.show_line_numbers
    if not isinstance(scan_local_headers, bool):
        scan_local_headers = base.scan_local_headers
    if not isinstance(signature_help, bool):
        signature_help = base.signature_help
    return EditorConfig(
        auto_pair=auto_pair,
        completion_key=completion_key,
        indent_width=indent_width,
        show_line_numbers=show_line_numbers,
        scan_local_headers=scan_local_headers,
        signature_help=signature_help,
    )


class EditorApp:
    def __init__(self, stdscr, path: str | None, config: EditorConfig | None = None):
        self.stdscr = stdscr
        self.path = Path(path) if path else None
        self.config = config if config is not None else EditorConfig()
        self.buffer = self._load_buffer(self.path)
        self.completion = CompletionEngine()
        self.commands = VimCommandProcessor()
        self.highlighter = SyntaxHighlighter()
        self.row_offset = 0
        self.col_offset = 0
        self.mode = "NORMAL"
        self.command_line = ""
        self.status = "Vim mode: i insert | :w save | :q quit | :wq save quit"
        self.completions = []
        self.completion_index = 0
        self.quit_warning = False

    def run(self) -> None:
        curses.curs_set(1)
        curses.raw()
        curses.noecho()
        self.stdscr.keypad(True)
        self._init_colors()
        while True:
            self._draw()
            if self._handle_key_sequence(self._read_key_sequence()):
                break

    def _init_colors(self) -> None:
        if not curses.has_colors():
            self.syntax_attrs = {}
            return
        curses.start_color()
        curses.use_default_colors()
        pairs = {
            "keyword": (curses.COLOR_CYAN, -1),
            "preprocessor": (curses.COLOR_MAGENTA, -1),
            "function": (curses.COLOR_YELLOW, -1),
            "string": (curses.COLOR_GREEN, -1),
            "number": (curses.COLOR_YELLOW, -1),
            "comment": (curses.COLOR_BLUE, -1),
        }
        self.syntax_attrs = {"plain": curses.A_NORMAL}
        for index, (kind, colors) in enumerate(pairs.items(), start=1):
            curses.init_pair(index, colors[0], colors[1])
            attr = curses.color_pair(index)
            if kind == "function":
                attr |= curses.A_BOLD
            self.syntax_attrs[kind] = attr

    def _load_buffer(self, path: Path | None) -> TextBuffer:
        if not path or not path.exists():
            return TextBuffer()
        text = path.read_text(encoding="utf-8", errors="replace")
        return TextBuffer.from_text(text)

    def _save(self) -> None:
        if not self.path:
            self.status = "No filename. Start with: sfe <file>"
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.buffer.to_text(), encoding="utf-8")
        self.buffer.dirty = False
        self.status = f"Saved {self.path}"

    def _handle_key(self, key) -> bool:
        if self.mode == "COMMAND":
            return self._handle_command_key(key)
        if self.mode == "NORMAL":
            return self._handle_normal_key(key)
        return self._handle_insert_key(key)

    def _handle_key_sequence(self, keys) -> bool:
        key = parse_key_sequence(keys)
        if key is not None:
            return self._handle_key(key)
        should_quit = False
        for item in keys:
            should_quit = self._handle_key(item)
            if should_quit:
                break
        return should_quit

    def _read_key_sequence(self):
        first = self.stdscr.get_wch()
        if first not in (ESCAPE, "\x1b") or not hasattr(self.stdscr, "timeout"):
            return [first]

        sequence = [first]
        self.stdscr.timeout(KEY_SEQUENCE_TIMEOUT_MS)
        try:
            while len(sequence) < 16 and could_be_csi_u_sequence(sequence):
                try:
                    sequence.append(self.stdscr.get_wch())
                except curses.error:
                    break
                if parse_key_sequence(sequence) is not None:
                    break
        finally:
            self.stdscr.timeout(-1)
        return sequence

    def _handle_normal_key(self, key) -> bool:
        self.completions = []
        if key == ":":
            self.mode = "COMMAND"
            self.command_line = ""
            return False
        if key in ("i", "I"):
            self.mode = "INSERT"
            return False
        if key == "a":
            self.buffer.move_right()
            self.mode = "INSERT"
            return False
        if key == "A":
            self.buffer.move_end()
            self.mode = "INSERT"
            return False
        if key == "o":
            self.buffer.move_end()
            self.buffer.newline()
            self.mode = "INSERT"
            return False
        if key == "O":
            self.buffer.move_home()
            self.buffer.newline()
            self.buffer.move_up()
            self.mode = "INSERT"
            return False
        if key in ("h", curses.KEY_LEFT, "KEY_LEFT"):
            self.buffer.move_left()
        elif key in ("l", curses.KEY_RIGHT, "KEY_RIGHT"):
            self.buffer.move_right()
        elif key in ("k", curses.KEY_UP, "KEY_UP"):
            self.buffer.move_up()
        elif key in ("j", curses.KEY_DOWN, "KEY_DOWN"):
            self.buffer.move_down()
        elif key in ("0", curses.KEY_HOME, "KEY_HOME"):
            self.buffer.move_home()
        elif key in ("$", curses.KEY_END, "KEY_END"):
            self.buffer.move_end()
        elif key == "x":
            self.buffer.delete()
        elif key in (CTRL_F, "\x06", "/"):
            self._search()
        elif key in (CTRL_S, "\x13", CTRL_O, "\x0f") or is_function_key(key, 2):
            self._save()
        elif key in (CTRL_Q, "\x11") or is_function_key(key, 10):
            if self.buffer.dirty:
                self.status = "E37: No write since last change (:q! overrides)"
                return False
            return True
        return False

    def _handle_insert_key(self, key) -> bool:
        if key in (ESCAPE, "\x1b"):
            self.mode = "NORMAL"
            self.completions = []
            return False
        if key in (CTRL_Q, "\x11") or is_function_key(key, 10):
            if self.buffer.dirty:
                self.status = "Unsaved changes. Use :wq or :q! from NORMAL mode."
                if self.quit_warning:
                    return True
                self.quit_warning = True
                return False
            return True
        if key in (CTRL_S, "\x13", CTRL_O, "\x0f") or is_function_key(key, 2):
            self._save()
            return False
        if key in (CTRL_F, "\x06"):
            self._search()
            return False
        had_completions = bool(self.completions)
        if had_completions and self._handle_completion_key(key):
            return False
        if had_completions:
            self.completions = []
        if key in (curses.KEY_LEFT, "KEY_LEFT"):
            self.buffer.move_left()
        elif key in (curses.KEY_RIGHT, "KEY_RIGHT"):
            self.buffer.move_right()
        elif key in (curses.KEY_UP, "KEY_UP"):
            self.buffer.move_up()
        elif key in (curses.KEY_DOWN, "KEY_DOWN"):
            self.buffer.move_down()
        elif key in (curses.KEY_HOME, "KEY_HOME"):
            self.buffer.move_home()
        elif key in (curses.KEY_END, "KEY_END"):
            self.buffer.move_end()
        elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            self.buffer.backspace()
        elif key in (curses.KEY_DC, "KEY_DC"):
            self.buffer.delete()
        elif self._is_completion_trigger(key):
            self._open_completions()
        elif key in ("\n", "\r"):
            self.buffer.newline()
        elif self.config.auto_pair and isinstance(key, str) and key in PAIRS:
            self.buffer.insert_pair(key, PAIRS[key])
            self.quit_warning = False
        elif key in (TAB, "\t"):
            self.buffer.indent()
        elif isinstance(key, str) and key >= " " and key != "\x1b":
            self.buffer.insert(key)
            self.quit_warning = False
            if re_match_completion_char(key):
                self._open_completions(show_status=False)
        return False

    def _handle_command_key(self, key) -> bool:
        if key in (ESCAPE, "\x1b"):
            self.mode = "NORMAL"
            self.command_line = ""
            return False
        if key in ("\n", "\r"):
            command = self.command_line
            self.mode = "NORMAL"
            self.command_line = ""
            return self._execute_command(command)
        if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            self.command_line = self.command_line[:-1]
            return False
        if isinstance(key, str) and key >= " ":
            self.command_line += key
        return False

    def _execute_command(self, command: str) -> bool:
        result = self.commands.execute(command, self.buffer.dirty)
        if result.save:
            self._save()
        if result.message:
            self.status = result.message if not result.save else self.status
        if result.quit:
            return True
        return False

    def _handle_completion_key(self, key) -> bool:
        if key in (curses.KEY_DOWN, "KEY_DOWN", CTRL_N, "\x0e"):
            self.completion_index = (self.completion_index + 1) % len(self.completions)
            return True
        if key in (curses.KEY_UP, "KEY_UP", CTRL_P, "\x10"):
            self.completion_index = (self.completion_index - 1) % len(self.completions)
            return True
        if key in (TAB, "\t"):
            self._accept_completion()
            return True
        if key in (ESCAPE, "\x1b"):
            self.completions = []
            return True
        return False

    def _open_completions(self, show_status: bool = True) -> None:
        prefix = self.buffer.current_prefix()
        self.completions = self.completion.suggest(
            prefix,
            self.buffer.lines,
            self.buffer.cursor_row,
            self.buffer.cursor_col,
        )
        self.completion_index = 0
        if not self.completions and show_status:
            self.status = f"No completion for {prefix!r}" if prefix else "Type a prefix before completing"

    def _is_completion_trigger(self, key) -> bool:
        expected = normalize_key_name(self.config.completion_key)
        if not expected:
            return False
        actual_names = key_to_names(key)
        if expected == "ctrl+space" and actual_names & {"ctrl+space", "ctrl+@", "ctrl+_"}:
            return True
        return expected in actual_names

    def _accept_completion(self) -> None:
        if not self.completions:
            return
        item = self.completions[self.completion_index]
        self.buffer.replace_current_prefix(item.text)
        self.status = f"Completed {item.text} ({item.kind})"
        self.completions = []

    def _search(self) -> None:
        query = self._prompt("Search: ")
        if not query:
            return
        for index, line in enumerate(self.buffer.lines):
            found = line.find(query)
            if found != -1:
                self.buffer.cursor_row = index
                self.buffer.cursor_col = found
                self.status = f"Found {query!r}"
                return
        self.status = f"Not found: {query}"

    def _prompt(self, label: str) -> str:
        curses.echo()
        height, width = self.stdscr.getmaxyx()
        self.stdscr.move(height - 1, 0)
        self.stdscr.clrtoeol()
        self.stdscr.addnstr(height - 1, 0, label, max(0, width - 1))
        self.stdscr.refresh()
        try:
            raw = self.stdscr.getstr(height - 1, len(label), max(1, width - len(label) - 1))
            return raw.decode("utf-8", errors="replace")
        finally:
            curses.noecho()

    def _draw(self) -> None:
        self._scroll_to_cursor()
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()
        text_height = max(1, height - 2)
        gutter_width = len(str(len(self.buffer.lines))) + 2
        for screen_row in range(text_height):
            file_row = self.row_offset + screen_row
            if file_row >= len(self.buffer.lines):
                break
            line_no = f"{file_row + 1:>{gutter_width - 1}} "
            self.stdscr.addnstr(screen_row, 0, line_no, gutter_width, curses.A_DIM)
            self._draw_code_line(screen_row, gutter_width, self.buffer.lines[file_row], width)
        self._draw_completions(text_height, width, gutter_width)
        self._draw_status(height, width)
        cursor_y = self.buffer.cursor_row - self.row_offset
        cursor_prefix = self.buffer.current_line()[: self.buffer.cursor_col]
        cursor_x = gutter_width + display_width(cursor_prefix) - self.col_offset
        if 0 <= cursor_y < text_height and 0 <= cursor_x < width:
            self.stdscr.move(cursor_y, cursor_x)
        self.stdscr.refresh()

    def _draw_status(self, height: int, width: int) -> None:
        name = str(self.path) if self.path else "[No Name]"
        dirty = " +" if self.buffer.dirty else ""
        left = f" {self.mode} | {name}{dirty} | {self.buffer.cursor_row + 1}:{self.buffer.cursor_col + 1} "
        self.stdscr.addnstr(height - 2, 0, left.ljust(width), width - 1, curses.A_REVERSE)
        if self.mode == "COMMAND":
            bottom = ":" + self.command_line
        else:
            bottom = self.status
        self.stdscr.addnstr(height - 1, 0, bottom.ljust(width), width - 1)

    def _draw_code_line(self, screen_row: int, gutter_width: int, line: str, width: int) -> None:
        visible_start = self.col_offset
        visible_end = self.col_offset + max(0, width - gutter_width - 1)
        x = gutter_width
        position = 0
        for token in self.highlighter.tokenize(line):
            token_start = position
            token_end = position + len(token.text)
            position = token_end
            if token_end <= visible_start:
                continue
            if token_start >= visible_end:
                break
            start = max(visible_start, token_start)
            end = min(visible_end, token_end)
            text = token.text[start - token_start : end - token_start]
            attr = self.syntax_attrs.get(token.kind, curses.A_NORMAL)
            self.stdscr.addnstr(screen_row, x, text, max(0, width - x - 1), attr)
            x += display_width(text)

    def _draw_completions(self, text_height: int, width: int, gutter_width: int) -> None:
        if not self.completions:
            return
        start_y = min(max(0, self.buffer.cursor_row - self.row_offset + 1), max(0, text_height - len(self.completions)))
        start_x = min(max(gutter_width, gutter_width + self.buffer.cursor_col - self.col_offset), max(gutter_width, width - 28))
        for offset, item in enumerate(self.completions):
            attr = curses.A_REVERSE if offset == self.completion_index else curses.A_NORMAL
            label = f" {item.text:<18} {item.kind:<7} {item.detail:<18}"
            self.stdscr.addnstr(start_y + offset, start_x, label, min(len(label), width - start_x - 1), attr)

    def _scroll_to_cursor(self) -> None:
        height, width = self.stdscr.getmaxyx()
        text_height = max(1, height - 2)
        gutter_width = len(str(len(self.buffer.lines))) + 2
        if self.buffer.cursor_row < self.row_offset:
            self.row_offset = self.buffer.cursor_row
        elif self.buffer.cursor_row >= self.row_offset + text_height:
            self.row_offset = self.buffer.cursor_row - text_height + 1
        visible_cols = max(1, width - gutter_width - 1)
        if self.buffer.cursor_col < self.col_offset:
            self.col_offset = self.buffer.cursor_col
        elif self.buffer.cursor_col >= self.col_offset + visible_cols:
            self.col_offset = self.buffer.cursor_col - visible_cols + 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sfe",
        description="Small SSH-friendly terminal code editor with C completions.",
    )
    parser.add_argument("file", nargs="?", help="file to edit")
    return parser.parse_args(argv)


def re_match_completion_char(key: str) -> bool:
    return key.isalnum() or key == "_"


def display_width(text: str) -> int:
    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
    return width


def normalize_key_name(name: str) -> str:
    value = name.strip().lower().replace("-", "+")
    value = "+".join(part for part in value.split("+") if part)
    aliases = {
        "ctrl+space": "ctrl+space",
        "ctrl+spacebar": "ctrl+space",
        "control+space": "ctrl+space",
        "control+spacebar": "ctrl+space",
        "ctrl+j": "ctrl+j",
        "control+j": "ctrl+j",
        "ctrl+@": "ctrl+@",
        "control+@": "ctrl+@",
        "ctrl+_": "ctrl+_",
        "control+_": "ctrl+_",
        "tab": "tab",
        "enter": "enter",
        "return": "enter",
    }
    if value in aliases:
        return aliases[value]
    if len(value) == 6 and value.startswith("ctrl+") and value[-1].isalpha():
        return value
    return ""


def key_to_names(key) -> set[str]:
    if key in (CTRL_SPACE, "\x00"):
        return {"ctrl+space", "ctrl+@"}
    if key == "\x1f":
        return {"ctrl+_"}
    if key in (TAB, "\t"):
        return {"tab", "ctrl+i"}
    if key == "\n":
        return {"enter", "ctrl+j"}
    if key == "\r":
        return {"enter", "ctrl+m"}
    if isinstance(key, str) and len(key) == 1:
        code = ord(key)
        if 1 <= code <= 26:
            return {f"ctrl+{chr(code + 96)}"}
    return set()


def parse_key_sequence(keys) -> int | str | None:
    if len(keys) == 1 and isinstance(keys[0], str) and len(keys[0]) > 1:
        return parse_key_sequence(list(keys[0]))
    if keys == [ESCAPE] or keys == ["\x1b"]:
        return "\x1b"
    if len(keys) < 3 or keys[0] not in (ESCAPE, "\x1b") or keys[1] != "[":
        return None
    if keys[-1] == "~":
        body = "".join(str(part) for part in keys[2:-1])
        parts = body.split(";")
        if len(parts) == 3 and parts[0] == "27" and parts[1].isdigit() and parts[2].isdigit():
            modifiers = int(parts[1])
            codepoint = int(parts[2])
            ctrl_pressed = bool((modifiers - 1) & 4)
            if ctrl_pressed and codepoint == 32:
                return CTRL_SPACE
        return None
    if keys[-1] != "u":
        return None
    body = "".join(str(part) for part in keys[2:-1])
    parts = body.split(";")
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    codepoint = int(parts[0])
    modifiers = int(parts[1])
    ctrl_pressed = bool((modifiers - 1) & 4)
    if ctrl_pressed and codepoint == 32:
        return CTRL_SPACE
    if ctrl_pressed and 65 <= codepoint <= 90:
        return chr(codepoint - 64)
    if ctrl_pressed and 97 <= codepoint <= 122:
        return chr(codepoint - 96)
    return chr(codepoint) if 0 <= codepoint <= sys.maxunicode else None


def could_be_csi_u_sequence(keys) -> bool:
    if not keys:
        return False
    if keys[0] not in (ESCAPE, "\x1b"):
        return False
    if len(keys) == 1:
        return True
    if keys[1] != "[":
        return False
    if len(keys) == 2:
        return True
    allowed = set("0123456789;")
    if keys[-1] == "~":
        return True
    return all(isinstance(part, str) and len(part) == 1 and part in allowed for part in keys[2:])


def is_function_key(key, number: int) -> bool:
    if key == f"KEY_F({number})":
        return True
    if curses is None or not isinstance(key, int):
        return False
    key_f0 = getattr(curses, "KEY_F0", None)
    return key_f0 is not None and key == key_f0 + number


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if curses is None:
        print("sfe requires Python curses. It is available on the target Linux SSH server.", file=sys.stderr)
        return 1
    if not os.environ.get("TERM"):
        os.environ["TERM"] = "xterm-256color"
    curses.wrapper(lambda stdscr: EditorApp(stdscr, args.file, load_config()).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
