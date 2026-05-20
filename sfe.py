#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from sfe_core import CompletionEngine, TextBuffer, VimCommandProcessor

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
ESCAPE = 27


class EditorApp:
    def __init__(self, stdscr, path: str | None):
        self.stdscr = stdscr
        self.path = Path(path) if path else None
        self.buffer = self._load_buffer(self.path)
        self.completion = CompletionEngine()
        self.commands = VimCommandProcessor()
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
        curses.use_default_colors()
        while True:
            self._draw()
            key = self.stdscr.get_wch()
            if self._handle_key(key):
                break

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
        if self.completions and self._handle_completion_key(key):
            return False
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
        elif key in ("\n", "\r"):
            self.buffer.newline()
        elif key in ("\t", CTRL_SPACE, "\x00"):
            self._open_completions()
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
        if key in (curses.KEY_DOWN, "KEY_DOWN", CTRL_N, "\x0e", "\t"):
            self.completion_index = (self.completion_index + 1) % len(self.completions)
            return True
        if key in (curses.KEY_UP, "KEY_UP", CTRL_P, "\x10"):
            self.completion_index = (self.completion_index - 1) % len(self.completions)
            return True
        if key in ("\n", "\r"):
            self._accept_completion()
            return True
        if key == ESCAPE:
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
            text = self.buffer.lines[file_row][self.col_offset :]
            self.stdscr.addnstr(screen_row, gutter_width, text, max(0, width - gutter_width - 1))
        self._draw_completions(text_height, width, gutter_width)
        self._draw_status(height, width)
        cursor_y = self.buffer.cursor_row - self.row_offset
        cursor_x = gutter_width + self.buffer.cursor_col - self.col_offset
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
    curses.wrapper(lambda stdscr: EditorApp(stdscr, args.file).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
