#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

from sfe_core import (
    BuildCommandResolver,
    BuildDiagnostic,
    BuildOutputParser,
    CDiagnosticEngine,
    CompletionEngine,
    HeaderIndex,
    HeaderScanner,
    ProjectFileScanner,
    ProjectFiles,
    ProjectIndex,
    ProjectScanner,
    ProjectTreeEntry,
    RecentFilesStore,
    SignatureHelpEngine,
    SyntaxHighlighter,
    TextBuffer,
    UndoManager,
    VimCommandProcessor,
    build_project_tree_entries,
    find_project_root,
    fuzzy_match_files,
)

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
CTRL_W = 23
CTRL_SPACE = 0
TAB = 9
ESCAPE = 27
PAIRS = {"{": "}", "(": ")", "[": "]", '"': '"', "'": "'"}
PAIR_CLOSERS = set(PAIRS.values())
KEYBINDABLE_COMMANDS = {
    "buffers",
    "bn",
    "bnext",
    "bp",
    "bprevious",
    "bprev",
    "bd",
    "bdelete",
    "files",
    "tree",
}
SYSTEM_CONFIG_PATH = Path("/etc/sfe/config.json")
DEFAULT_CONFIG_PATH = Path("~/.config/sfe/config.json").expanduser()
KEY_SEQUENCE_TIMEOUT_MS = 25
CTRL_ARROW_NAMES = {"left", "right", "up", "down"}
MODIFIED_ARROW_KEY_CODES = {
    526: "ctrl+down",
    546: "ctrl+left",
    561: "ctrl+right",
    567: "ctrl+up",
}
CTRL_ARROW_SEQUENCE_SUFFIXES = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
}
TERMINAL_ARROW_NAMES = {
    "UP": "up",
    "DN": "down",
    "DOWN": "down",
    "LFT": "left",
    "LEFT": "left",
    "RIT": "right",
    "RIGHT": "right",
}
KEYNAME_ARROW_ALIASES = {
    "KEY_CUP": "ctrl+up",
    "KEY_CDOWN": "ctrl+down",
    "KEY_CLEFT": "ctrl+left",
    "KEY_CRIGHT": "ctrl+right",
}
CONFLICTING_KEYBINDING_REASONS = {
    "ctrl+a": "common shell/readline line-start shortcut",
    "ctrl+b": "common shell/readline backward shortcut and tmux prefix",
    "ctrl+c": "terminal interrupt",
    "ctrl+d": "terminal EOF",
    "ctrl+e": "common shell/readline line-end shortcut",
    "ctrl+f": "SFE search shortcut and common shell/readline forward shortcut",
    "ctrl+g": "common terminal/readline abort shortcut",
    "ctrl+h": "terminal backspace",
    "ctrl+i": "terminal Tab equivalent",
    "ctrl+j": "terminal Enter equivalent",
    "ctrl+k": "common shell/readline kill-line shortcut",
    "ctrl+l": "common terminal clear-screen shortcut",
    "ctrl+m": "terminal Enter equivalent",
    "ctrl+n": "SFE next diagnostic/completion navigation shortcut",
    "ctrl+o": "SFE save/jump-back shortcut",
    "ctrl+p": "SFE file picker/completion navigation shortcut",
    "ctrl+q": "SFE quit shortcut and terminal flow-control shortcut",
    "ctrl+r": "SFE redo shortcut and common shell history-search shortcut",
    "ctrl+s": "SFE save shortcut and terminal flow-control shortcut",
    "ctrl+t": "common shell/readline transpose shortcut",
    "ctrl+u": "common shell/readline kill-line shortcut",
    "ctrl+v": "common terminal literal-next shortcut",
    "ctrl+w": "SFE tree shortcut and common shell/readline delete-word shortcut",
    "ctrl+x": "common shell/readline command prefix",
    "ctrl+y": "common shell/readline yank shortcut",
    "ctrl+z": "terminal suspend",
    "ctrl+[": "terminal Escape equivalent",
    "ctrl+\\": "terminal quit",
    "ctrl+]": "SFE definition-jump shortcut",
    "ctrl+^": "common terminal/readline shortcut",
    "ctrl+_": "common shell/readline undo shortcut",
    "ctrl+space": "SFE manual completion shortcut",
    "enter": "terminal Enter",
    "f1": "common help shortcut",
    "f2": "SFE save shortcut",
    "f10": "SFE quit shortcut",
    "tab": "terminal Tab and SFE completion/indent shortcut",
}


def read_version() -> str:
    for candidate in (Path(__file__).with_name("VERSION"), Path.cwd() / "VERSION"):
        try:
            version = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if version:
            return version
    return "unknown"


@dataclass(frozen=True)
class EditorConfig:
    auto_pair: bool = True
    completion_key: str = "ctrl+space"
    indent_width: int = 4
    show_line_numbers: bool = True
    scan_local_headers: bool = True
    signature_help: bool = True
    build_command: str = ""
    run_command: str = ""
    project_root_markers: tuple[str, ...] = ("Makefile", ".git", "compile_commands.json")
    recent_files_limit: int = 20
    keybindings: dict[str, str] = field(default_factory=dict)


@dataclass
class EditorBuffer:
    path: Path | None
    buffer: TextBuffer
    row_offset: int = 0
    col_offset: int = 0
    diagnostics: list[object] = field(default_factory=list)
    undo: UndoManager = field(default_factory=UndoManager)
    auto_pair_placeholders: list[tuple[int, int, str]] = field(default_factory=list)


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
    build_command = raw.get("build_command", base.build_command)
    run_command = raw.get("run_command", base.run_command)
    project_root_markers = raw.get("project_root_markers", base.project_root_markers)
    recent_files_limit = raw.get("recent_files_limit", base.recent_files_limit)
    keybindings = raw.get("keybindings", base.keybindings)
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
    if not isinstance(build_command, str):
        build_command = base.build_command
    if not isinstance(run_command, str):
        run_command = base.run_command
    if not (
        isinstance(project_root_markers, list | tuple)
        and project_root_markers
        and all(isinstance(item, str) and item for item in project_root_markers)
    ):
        project_root_markers = base.project_root_markers
    if not isinstance(recent_files_limit, int) or not 1 <= recent_files_limit <= 200:
        recent_files_limit = base.recent_files_limit
    if isinstance(keybindings, dict):
        normalized_bindings = {}
        for raw_key, raw_command in keybindings.items():
            key_name = normalize_key_name(str(raw_key)) if isinstance(raw_key, str) else ""
            command = normalize_bind_command(raw_command) if isinstance(raw_command, str) else ""
            if key_name and key_name not in CONFLICTING_KEYBINDING_REASONS and command:
                normalized_bindings[key_name] = command
        keybindings = normalized_bindings
    else:
        keybindings = dict(base.keybindings)
    return EditorConfig(
        auto_pair=auto_pair,
        completion_key=completion_key,
        indent_width=indent_width,
        show_line_numbers=show_line_numbers,
        scan_local_headers=scan_local_headers,
        signature_help=signature_help,
        build_command=build_command,
        run_command=run_command,
        project_root_markers=tuple(project_root_markers),
        recent_files_limit=recent_files_limit,
        keybindings=dict(keybindings),
    )


class EditorApp:
    def __init__(self, stdscr, path: str | None, config: EditorConfig | None = None):
        self.stdscr = stdscr
        initial_path = Path(path) if path else None
        self.user_config_path = DEFAULT_CONFIG_PATH
        self.config = config if config is not None else EditorConfig()
        self.completion = CompletionEngine()
        self.commands = VimCommandProcessor()
        self.highlighter = SyntaxHighlighter()
        self.syntax_attrs = {"plain": 0}
        self.path: Path | None = initial_path
        self.buffer = self._load_buffer(initial_path)
        self.row_offset = 0
        self.col_offset = 0
        self.diagnostic_engine = CDiagnosticEngine()
        self.diagnostics = self.diagnostic_engine.analyze(self.buffer.lines)
        self.undo = UndoManager()
        self.auto_pair_placeholders: list[tuple[int, int, str]] = []
        self.buffers = [
            EditorBuffer(
                path=initial_path,
                buffer=self.buffer,
                diagnostics=list(self.diagnostics),
                undo=self.undo,
                auto_pair_placeholders=self.auto_pair_placeholders,
            )
        ]
        self.current_buffer_index = 0
        self.project_root = self._find_project_root()
        self.header_index = HeaderIndex(set(), [])
        self.project_index: ProjectIndex | None = None
        self.project_files: ProjectFiles | None = None
        self.header_index_loaded = False
        self.project_index_loaded = False
        self.project_files_loaded = False
        self.build_diagnostics: list[BuildDiagnostic] = []
        self.build_output = ""
        self.last_build_command = ""
        self.last_run_command = ""
        self.build_runner = self._run_shell_command
        self.recent_store_path = self._default_recent_store_path()
        if self.path is not None and self.stdscr is not None:
            self._remember_recent_file(self.path)
        self.signature_help = SignatureHelpEngine.from_header_index(None)
        self.mode = "NORMAL"
        self.command_line = ""
        self.binding_command: str | None = None
        self.status = "Vim mode: i insert | :w save | :q quit | :wq save quit"
        self.completions = []
        self.completion_index = 0
        self.quit_warning = False
        self.last_search_query = ""
        self.list_title = ""
        self.list_lines: list[str] = []
        self.list_cursor = 0
        self.list_row_offset = 0
        self.list_actions: list[object] = []
        self.jump_stack: list[tuple[Path | None, int, int]] = []
        self.tree_visible = False
        self.tree_focused = False
        self.tree_expanded: set[str] = set()
        self.tree_entries: list[ProjectTreeEntry] = []
        self.tree_cursor = 0
        self.tree_row_offset = 0

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

    def _current_editor_buffer(self) -> EditorBuffer:
        return self.buffers[self.current_buffer_index]

    def _sync_current_buffer_state(self) -> None:
        if not self.buffers:
            return
        current = self._current_editor_buffer()
        current.path = self.path
        current.buffer = self.buffer
        current.row_offset = self.row_offset
        current.col_offset = self.col_offset
        current.diagnostics = list(self.diagnostics)
        current.undo = self.undo
        current.auto_pair_placeholders = list(self.auto_pair_placeholders)

    def _activate_buffer(self, index: int) -> None:
        if not self.buffers:
            self.buffers = [EditorBuffer(None, TextBuffer())]
            self.current_buffer_index = 0
        index = max(0, min(index, len(self.buffers) - 1))
        current = self.buffers[index]
        self.current_buffer_index = index
        self.path = current.path
        self.buffer = current.buffer
        self.row_offset = current.row_offset
        self.col_offset = current.col_offset
        self.diagnostics = list(current.diagnostics)
        self.undo = current.undo
        self.auto_pair_placeholders = list(current.auto_pair_placeholders)
        self.completions = []
        self.completion_index = 0
        self._reload_file_context()

    def _normalized_path(self, path: Path | None) -> Path | None:
        if path is None:
            return None
        try:
            return path.expanduser().resolve()
        except OSError:
            return path.expanduser().absolute()

    def _find_buffer_index(self, path: Path) -> int | None:
        normalized = self._normalized_path(path)
        for index, item in enumerate(self.buffers):
            if item.path is not None and self._normalized_path(item.path) == normalized:
                return index
        return None

    def _current_buffer_label(self, item: EditorBuffer | None = None) -> str:
        item = self._current_editor_buffer() if item is None else item
        return str(item.path) if item.path else "[No Name]"

    def _load_header_index(self) -> HeaderIndex:
        if not self.config.scan_local_headers or self.path is None:
            return HeaderIndex(set(), [])
        return HeaderScanner().scan(self.path.parent)

    def _load_project_index(self) -> ProjectIndex | None:
        if self.project_root is None:
            return None
        return ProjectScanner().scan(self.project_root)

    def _find_project_root(self) -> Path | None:
        if self.path is None:
            return None
        start = self.path if self.path.exists() else self.path.parent
        return find_project_root(start, self.config.project_root_markers)

    def _load_project_files(self) -> ProjectFiles | None:
        if self.project_root is None:
            return None
        return ProjectFileScanner().scan(self.project_root)

    def _invalidate_file_context_indexes(self) -> None:
        self.header_index = HeaderIndex(set(), [])
        self.project_index = None
        self.project_files = None
        self.header_index_loaded = False
        self.project_index_loaded = False
        self.project_files_loaded = False
        self.signature_help = SignatureHelpEngine.from_header_index(None)
        self.tree_entries = []

    def _ensure_header_index(self) -> HeaderIndex:
        if not self.header_index_loaded:
            self.header_index = self._load_header_index()
            self.header_index_loaded = True
            self.signature_help = SignatureHelpEngine.from_header_index(self.header_index)
        return self.header_index

    def _ensure_project_index(self) -> ProjectIndex | None:
        if not self.project_index_loaded:
            self.project_index = self._load_project_index()
            self.project_index_loaded = True
        return self.project_index

    def _ensure_project_files(self) -> ProjectFiles | None:
        if not self.project_files_loaded:
            self.project_files = self._load_project_files()
            self.project_files_loaded = True
        return self.project_files

    def _reload_file_context(self) -> None:
        self.project_root = self._find_project_root()
        self.recent_store_path = self._default_recent_store_path()
        self._invalidate_file_context_indexes()
        if self.tree_visible:
            self.project_files = self._ensure_project_files()
            self._refresh_tree_entries()
        self._refresh_diagnostics()

    def _refresh_diagnostics(self) -> None:
        self.diagnostics = self.diagnostic_engine.analyze(self.buffer.lines)
        self._sync_current_buffer_state()

    def _save(self, path: Path | None = None) -> None:
        if path is not None:
            self.path = path
        if not self.path:
            self.status = "No filename. Start with: sfe <file>"
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.buffer.to_text(), encoding="utf-8")
        self.buffer.dirty = False
        self.status = f"Saved {self.path}"
        self._sync_current_buffer_state()
        self._reload_file_context()
        self._remember_recent_file(self.path)

    def _handle_key(self, key) -> bool:
        if self.mode == "BIND":
            return self._handle_bind_key(key)
        if self.mode == "COMMAND":
            return self._handle_command_key(key)
        if self.mode == "LIST":
            return self._handle_list_key(key)
        if self.tree_visible and self.tree_focused:
            return self._handle_tree_key(key)
        if self.mode == "NORMAL":
            return self._handle_normal_key(key)
        return self._handle_insert_key(key)

    def _handle_key_sequence(self, keys) -> bool:
        key = parse_key_sequence(keys)
        if self.mode == "BIND":
            return self._handle_bind_key(key if key is not None else keys[0])
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
        if self._run_keybinding(key):
            return False
        if key in (CTRL_W, "\x17") and self.tree_visible:
            self._toggle_tree_focus()
            return False
        if key in (CTRL_W, "\x17"):
            self._show_project_tree()
            return False
        if key in (CTRL_P, "\x10"):
            self._show_project_files()
            return False
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
            self._record_edit()
            self.buffer.move_end()
            self.buffer.newline_with_indent(self.config.indent_width)
            self.mode = "INSERT"
            return False
        if key == "O":
            self._record_edit()
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
            self._record_edit()
            self.buffer.delete()
        elif key == "u":
            if self.undo.undo(self.buffer):
                self.status = "Undo"
            else:
                self.status = "Already at oldest change"
        elif key in (18, "\x12"):
            if self.undo.redo(self.buffer):
                self.status = "Redo"
            else:
                self.status = "Already at newest change"
        elif key == "n":
            self._repeat_search(1)
        elif key == "N":
            self._repeat_search(-1)
        elif key in (CTRL_N, "\x0e"):
            self._goto_next_diagnostic(1)
        elif key in (CTRL_F, "\x06", "/"):
            self._search()
        elif key == "\x1d":
            self._jump_to_definition()
        elif key in (CTRL_S, "\x13", CTRL_O, "\x0f") or is_function_key(key, 2):
            if key in (CTRL_O, "\x0f"):
                self._jump_back()
            else:
                self._save()
        elif key in (CTRL_Q, "\x11") or is_function_key(key, 10):
            if self.buffer.dirty:
                self.status = "E37: No write since last change (:q! overrides)"
                return False
            return True
        return False

    def _handle_list_key(self, key) -> bool:
        if key in (ESCAPE, "\x1b", "q"):
            self.mode = "NORMAL"
            return False
        if key in ("k", curses.KEY_UP, "KEY_UP"):
            self._move_list_cursor(-1)
            return False
        if key in ("j", curses.KEY_DOWN, "KEY_DOWN"):
            self._move_list_cursor(1)
            return False
        if key in ("\n", "\r"):
            self._open_selected_list_item()
            return False
        return False

    def _move_list_cursor(self, delta: int) -> None:
        if not self.list_lines:
            self.list_cursor = 0
            return
        self.list_cursor = max(0, min(len(self.list_lines) - 1, self.list_cursor + delta))

    def _open_selected_list_item(self) -> None:
        if not self.list_actions or not (0 <= self.list_cursor < len(self.list_actions)):
            self.mode = "NORMAL"
            return
        action = self.list_actions[self.list_cursor]
        if callable(action):
            action()

    def _handle_bind_key(self, key) -> bool:
        if key in (ESCAPE, "\x1b"):
            self.mode = "NORMAL"
            self.binding_command = None
            self.status = "Bind cancelled"
            return False
        key_name = first_key_name(key)
        if not key_name:
            self.status = f"Unsupported shortcut: {key!r}"
            return False
        conflict_reason = CONFLICTING_KEYBINDING_REASONS.get(key_name)
        if conflict_reason:
            self.status = f"Shortcut conflicts: {key_name} ({conflict_reason})"
            return False
        command = self.binding_command
        if not command:
            self.mode = "NORMAL"
            self.status = "No command waiting for shortcut"
            return False
        self._set_keybinding(key_name, command)
        self.mode = "NORMAL"
        self.binding_command = None
        self.status = f"Bound {key_name} to :{command}"
        return False

    def _run_keybinding(self, key) -> bool:
        command = self._command_for_key(key)
        if not command:
            return False
        self._execute_extended_command(command)
        return True

    def _command_for_key(self, key) -> str:
        for key_name in key_to_names(key):
            command = self.config.keybindings.get(key_name)
            if command:
                return command
        return ""

    def _set_keybinding(self, key_name: str, command: str) -> None:
        bindings = dict(self.config.keybindings)
        bindings[key_name] = command
        self.config = EditorConfig(
            auto_pair=self.config.auto_pair,
            completion_key=self.config.completion_key,
            indent_width=self.config.indent_width,
            show_line_numbers=self.config.show_line_numbers,
            scan_local_headers=self.config.scan_local_headers,
            signature_help=self.config.signature_help,
            build_command=self.config.build_command,
            run_command=self.config.run_command,
            project_root_markers=self.config.project_root_markers,
            recent_files_limit=self.config.recent_files_limit,
            keybindings=bindings,
        )
        self._write_user_config()

    def _handle_tree_key(self, key) -> bool:
        if key in (ESCAPE, "\x1b", "q"):
            self._close_tree()
            return False
        if key in (CTRL_W, "\x17"):
            self._toggle_tree_focus()
            return False
        if key in ("k", curses.KEY_UP, "KEY_UP"):
            self._move_tree_cursor(-1)
            return False
        if key in ("j", curses.KEY_DOWN, "KEY_DOWN"):
            self._move_tree_cursor(1)
            return False
        if key in ("\n", "\r"):
            self._activate_tree_entry()
            return False
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
        if self._run_keybinding(key):
            return False
        if key in (CTRL_P, "\x10"):
            self._show_project_files()
            return False
        if key in (curses.KEY_LEFT, "KEY_LEFT"):
            self.buffer.move_left()
            self._drop_stale_placeholders()
        elif key in (curses.KEY_RIGHT, "KEY_RIGHT"):
            self.buffer.move_right()
            self._drop_stale_placeholders()
        elif key in (curses.KEY_UP, "KEY_UP"):
            self.buffer.move_up()
            self._drop_stale_placeholders()
        elif key in (curses.KEY_DOWN, "KEY_DOWN"):
            self.buffer.move_down()
            self._drop_stale_placeholders()
        elif key in (curses.KEY_HOME, "KEY_HOME"):
            self.buffer.move_home()
            self._drop_stale_placeholders()
        elif key in (curses.KEY_END, "KEY_END"):
            self.buffer.move_end()
            self._drop_stale_placeholders()
        elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            self._record_edit()
            if not self.buffer.backspace_smart(self.config.indent_width):
                self.buffer.backspace()
            self._clear_placeholders()
        elif key in (curses.KEY_DC, "KEY_DC"):
            self._record_edit()
            self.buffer.delete()
            self._clear_placeholders()
        elif self._is_completion_trigger(key):
            self._open_completions()
        elif key in ("\n", "\r"):
            self._record_edit()
            self.buffer.newline_with_indent(self.config.indent_width)
            self._clear_placeholders()
            self._refresh_diagnostics()
        elif self.config.auto_pair and isinstance(key, str) and key in PAIR_CLOSERS and self._jump_over_pair_placeholder(key):
            self.quit_warning = False
        elif self.config.auto_pair and isinstance(key, str) and key in PAIRS:
            self._record_edit()
            self.buffer.insert_pair(key, PAIRS[key])
            self._shift_placeholders_after_edit(2)
            self._add_pair_placeholder(PAIRS[key])
            self.quit_warning = False
        elif key in (TAB, "\t"):
            self._record_edit()
            self.buffer.indent(self.config.indent_width)
            self._clear_placeholders()
        elif isinstance(key, str) and key >= " " and key != "\x1b":
            self._record_edit()
            self.buffer.insert(key)
            self._shift_placeholders_after_edit(1)
            if key == "}":
                self.buffer.align_closing_brace(self.config.indent_width)
                self._clear_placeholders()
            self.quit_warning = False
            self._refresh_diagnostics()
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
        if self._execute_extended_command(command):
            return False
        result = self.commands.execute(command, self.buffer.dirty)
        if result.save:
            self._save()
        if result.message:
            self.status = result.message if not result.save else self.status
        if result.quit:
            return True
        return False

    def _execute_extended_command(self, command: str) -> bool:
        command = command.strip()
        if command.startswith(":"):
            command = command[1:].strip()
        if not command:
            return False
        parts = command.split()
        name = parts[0]
        args = parts[1:]
        if name in ("goto", "go") and args:
            return self._command_goto(args[0])
        if name in ("symbols", "symbol"):
            self._show_symbols()
            return True
        if name in ("diag", "diagnostics"):
            self._show_diagnostics()
            return True
        if name == "tree":
            self._toggle_project_tree(args[0] if args else "")
            return True
        if name == "open" and args:
            self._command_open(" ".join(args))
            return True
        if name in ("files", "file"):
            self._show_project_files()
            return True
        if name in ("buffers", "ls"):
            self._show_buffers()
            return True
        if name in ("bn", "bnext"):
            self._next_buffer(1)
            return True
        if name in ("bp", "bprevious", "bprev"):
            self._next_buffer(-1)
            return True
        if name in ("bd", "bdelete"):
            self._delete_current_buffer()
            return True
        if name in ("bind", "map") and args:
            self._start_bind_command(" ".join(args))
            return True
        if name == "recent":
            self._show_recent_files()
            return True
        if name == "make":
            self._run_build()
            return True
        if name == "run":
            self._run_last_command()
            return True
        if name in ("errors", "build-errors"):
            self._show_build_errors()
            return True
        if name in ("help", "h"):
            self._show_help()
            return True
        if name in ("e", "edit") and args:
            self._open_file(Path(" ".join(args)).expanduser())
            return True
        if name in ("w", "write") and args:
            self._save(Path(" ".join(args)).expanduser())
            return True
        if name == "set" and len(args) >= 2:
            self._apply_set_command(args)
            return True
        return False

    def _command_goto(self, value: str) -> bool:
        try:
            line_no = int(value)
        except ValueError:
            self.status = f"Invalid line: {value}"
            return True
        line_no = max(1, min(line_no, len(self.buffer.lines)))
        self.buffer.cursor_row = line_no - 1
        self.buffer.cursor_col = min(self.buffer.cursor_col, len(self.buffer.current_line()))
        self.status = f"Moved to line {line_no}"
        return True

    def _toggle_project_tree(self, action: str = "") -> None:
        action = action.lower()
        if action in ("close", "hide", "off"):
            self._close_tree()
            return
        if action in ("open", "show", "on"):
            self._show_project_tree()
            return
        if self.tree_visible:
            self._close_tree()
            return
        self._show_project_tree()

    def _show_symbols(self) -> None:
        index = ProjectScanner()._scan_file(self.path or Path("[buffer]"), self.buffer.lines)
        self.list_title = "Symbols"
        self.list_lines = [f"{symbol.row + 1}: {symbol.kind:<8} {symbol.name}" for symbol in index]
        if not self.list_lines:
            self.list_lines = ["No symbols"]
        self.list_actions = []
        self.list_cursor = 0
        self.list_row_offset = 0
        self.mode = "LIST"
        self.status = "Symbols: " + " | ".join(self.list_lines[:3])

    def _show_diagnostics(self) -> None:
        self._refresh_diagnostics()
        self.list_title = "Diagnostics"
        self.list_lines = [
            f"{diag.row + 1}:{diag.col + 1} {diag.severity}: {diag.message}"
            for diag in self.diagnostics
        ]
        if not self.list_lines:
            self.list_lines = ["No diagnostics"]
        self.list_actions = []
        self.list_cursor = 0
        self.list_row_offset = 0
        self.mode = "LIST"
        self.status = "Diagnostics: " + " | ".join(self.list_lines[:3])

    def _show_project_tree(self) -> None:
        self.project_files = self._ensure_project_files()
        self.tree_visible = True
        self.tree_focused = True
        self.tree_cursor = 0
        self.tree_row_offset = 0
        self.mode = "NORMAL"
        self._refresh_tree_entries()
        if not self.tree_entries:
            self.status = "Project tree: no project files"
        else:
            self.status = "Project tree: Enter open/toggle | Ctrl-W switch | q close"

    def _refresh_tree_entries(self, selected_path: str | None = None) -> None:
        if selected_path is None and self.tree_entries and 0 <= self.tree_cursor < len(self.tree_entries):
            selected_path = self.tree_entries[self.tree_cursor].relative_path
        self.tree_entries = build_project_tree_entries(self.project_files, self.tree_expanded)
        if not self.tree_entries:
            self.tree_cursor = 0
            self.tree_row_offset = 0
            return
        if selected_path:
            for index, entry in enumerate(self.tree_entries):
                if entry.relative_path == selected_path:
                    self.tree_cursor = index
                    break
            else:
                self.tree_cursor = min(self.tree_cursor, len(self.tree_entries) - 1)
        else:
            self.tree_cursor = min(self.tree_cursor, len(self.tree_entries) - 1)

    def _move_tree_cursor(self, delta: int) -> None:
        if not self.tree_entries:
            return
        self.tree_cursor = max(0, min(len(self.tree_entries) - 1, self.tree_cursor + delta))

    def _activate_tree_entry(self) -> None:
        if not self.tree_entries or not (0 <= self.tree_cursor < len(self.tree_entries)):
            self.status = "Project tree: no item selected"
            return
        entry = self.tree_entries[self.tree_cursor]
        if entry.is_dir:
            if entry.relative_path in self.tree_expanded:
                self.tree_expanded.remove(entry.relative_path)
                self.status = f"Collapsed {entry.relative_path}/"
            else:
                self.tree_expanded.add(entry.relative_path)
                self.status = f"Expanded {entry.relative_path}/"
            self._refresh_tree_entries(entry.relative_path)
            return
        if self.project_root is None:
            self.status = "No project root"
            return
        target = self.project_root / entry.relative_path
        before = self.path
        self._open_file(target)
        if self.path == target:
            self.tree_focused = False
            self.project_files = self._ensure_project_files()
            self._refresh_tree_entries(entry.relative_path)
            self.status = f"Opened {entry.relative_path} | Ctrl-W tree"
        elif before != self.path:
            self.tree_focused = False

    def _toggle_tree_focus(self) -> None:
        if not self.tree_visible:
            return
        self.tree_focused = not self.tree_focused
        self.status = "Tree focus" if self.tree_focused else "Editor focus"

    def _close_tree(self) -> None:
        self.tree_visible = False
        self.tree_focused = False
        self.status = "Project tree closed"

    def _command_open(self, query: str) -> None:
        self.project_files = self._ensure_project_files()
        if not self.project_files:
            self.status = "No project root"
            return
        matches = fuzzy_match_files(query, self.project_files.files, limit=1)
        if not matches:
            self.status = f"No file matches: {query}"
            return
        self._open_file(matches[0].path)

    def _start_bind_command(self, command: str) -> None:
        normalized = normalize_bind_command(command)
        if not normalized:
            self.status = f"Unknown bind command: {command}"
            return
        self.binding_command = normalized
        self.mode = "BIND"
        self.status = f"Press shortcut for :{normalized} (Esc cancel)"

    def _show_project_files(self) -> None:
        self.project_files = self._ensure_project_files()
        if not self.project_files:
            self.status = "No project root"
            return
        files = sorted(self.project_files.files, key=lambda item: item.relative_path)
        self.list_title = "Files"
        self.list_lines = [item.relative_path for item in files] or ["No project files"]
        self.list_actions = [lambda path=item.path: self._open_file_from_list(path) for item in files]
        self.list_cursor = 0
        self.list_row_offset = 0
        self.mode = "LIST"
        self.status = "Files: Enter open | j/k move | q close"

    def _open_file_from_list(self, path: Path) -> None:
        self._open_file(path)
        self.mode = "NORMAL"

    def _show_buffers(self) -> None:
        self._sync_current_buffer_state()
        self.list_title = "Buffers"
        self.list_lines = []
        self.list_actions = []
        for index, item in enumerate(self.buffers):
            current = "%" if index == self.current_buffer_index else " "
            dirty = "+" if item.buffer.dirty else " "
            label = self._current_buffer_label(item)
            self.list_lines.append(f"{index + 1:>2} {current}{dirty} {label}")
            self.list_actions.append(lambda buffer_index=index: self._open_buffer_from_list(buffer_index))
        if not self.list_lines:
            self.list_lines = ["No buffers"]
        self.list_cursor = self.current_buffer_index if self.list_actions else 0
        self.list_row_offset = 0
        self.mode = "LIST"
        self.status = "Buffers: Enter switch | :bn next | :bp previous | :bd delete"

    def _open_buffer_from_list(self, index: int) -> None:
        self._sync_current_buffer_state()
        self._activate_buffer(index)
        self.mode = "NORMAL"
        self.status = f"Buffer {self.current_buffer_index + 1}/{len(self.buffers)}"

    def _show_recent_files(self) -> None:
        entries = RecentFilesStore(self.recent_store_path, self.config.recent_files_limit).load()
        self.list_title = "Recent"
        self.list_lines = entries or ["No recent files"]
        self.list_actions = []
        self.list_cursor = 0
        self.list_row_offset = 0
        self.mode = "LIST"
        self.status = "Recent: " + " | ".join(self.list_lines[:3])

    def _run_build(self) -> None:
        if self.path is not None and self.buffer.dirty:
            self._save()
        project_root = self.project_root or (self.path.parent if self.path else Path.cwd())
        result = BuildCommandResolver.resolve(self.config.build_command, self.path, project_root)
        if not result.command:
            self.status = "No build command"
            return
        completed = self.build_runner(result.command, project_root)
        output = (completed.stdout or "") + (completed.stderr or "")
        self.last_build_command = result.command
        self.last_run_command = self.config.run_command.strip() or result.run_command
        self._set_build_output(output, completed.returncode)

    def _run_last_command(self) -> None:
        command = self.config.run_command.strip() or self.last_run_command
        if not command:
            project_root = self.project_root or (self.path.parent if self.path else Path.cwd())
            command = BuildCommandResolver.resolve("", self.path, project_root).run_command
        if not command:
            self.status = "No run command"
            return
        project_root = self.project_root or (self.path.parent if self.path else Path.cwd())
        completed = self.build_runner(command, project_root)
        output = (completed.stdout or "") + (completed.stderr or "")
        self.last_run_command = command
        self.status = f"Run OK: {command}" if completed.returncode == 0 else f"Run failed ({completed.returncode}): {command}"
        if output:
            self.build_output = output

    def _run_shell_command(self, command: str, cwd: Path):
        return subprocess.run(command, cwd=cwd, capture_output=True, text=True, shell=True)

    def _set_build_output(self, output: str, returncode: int) -> None:
        self.build_output = output
        self.build_diagnostics = BuildOutputParser().parse(output)
        if returncode == 0:
            self.status = f"Build OK: {len(self.build_diagnostics)} diagnostics"
        else:
            self.status = f"Build failed ({returncode}): {len(self.build_diagnostics)} diagnostics"

    def _show_build_errors(self) -> None:
        self.list_title = "Build Errors"
        self.list_lines = [
            f"{diag.path.as_posix()}:{diag.row + 1}:{diag.col + 1} {diag.severity}: {diag.message}"
            for diag in self.build_diagnostics
        ]
        if not self.list_lines:
            self.list_lines = ["No build errors"]
        self.list_actions = []
        self.list_cursor = 0
        self.list_row_offset = 0
        self.mode = "LIST"
        self.status = "Build Errors: " + " | ".join(self.list_lines[:3])

    def _show_help(self) -> None:
        self.list_title = "Help"
        self.list_lines = [
            "i/a/o/O insert | Esc normal | : command",
            ":w save | :w file save as | :q quit | :wq save quit",
            ":e file open/switch | :open fuzzy | :files quick open | Ctrl-P files",
            ":buffers list | :bn/:bp next/previous | :bd delete clean buffer",
            ":bind bn map shortcut | :tree toggle | :tree open/close | :recent",
            "Tree: Ctrl-W open/switch | j/k move | Enter open/toggle dir | q close",
            ":goto line | :symbols | :diag | :help",
            ":make build | :run last output | :errors | :set key value",
            "Tab accept completion | Ctrl-Space manual completion | Ctrl-] definition | Ctrl-O back",
        ]
        self.list_actions = []
        self.list_cursor = 0
        self.list_row_offset = 0
        self.mode = "LIST"
        self.status = "Help"

    def _open_file(self, path: Path) -> None:
        path = path.expanduser()
        existing = self._find_buffer_index(path)
        self._sync_current_buffer_state()
        if existing is not None:
            self._activate_buffer(existing)
            self.status = f"Switched to {path}"
            return
        if self.path is None and not self.buffer.dirty and self.buffer.to_text() == "":
            target = self._current_editor_buffer()
            target.path = path
            target.buffer = self._load_buffer(path)
            target.row_offset = 0
            target.col_offset = 0
            target.diagnostics = self.diagnostic_engine.analyze(target.buffer.lines)
            target.undo = UndoManager()
            target.auto_pair_placeholders = []
            self._activate_buffer(self.current_buffer_index)
        else:
            target = EditorBuffer(path=path, buffer=self._load_buffer(path))
            target.diagnostics = self.diagnostic_engine.analyze(target.buffer.lines)
            self.buffers.append(target)
            self._activate_buffer(len(self.buffers) - 1)
        self.status = f"Opened {path}"
        self._remember_recent_file(path)

    def _next_buffer(self, delta: int) -> None:
        if not self.buffers:
            self.buffers = [EditorBuffer(None, TextBuffer())]
            self.current_buffer_index = 0
        self._sync_current_buffer_state()
        next_index = (self.current_buffer_index + delta) % len(self.buffers)
        self._activate_buffer(next_index)
        self.status = f"Buffer {self.current_buffer_index + 1}/{len(self.buffers)}"

    def _delete_current_buffer(self) -> None:
        self._sync_current_buffer_state()
        current = self._current_editor_buffer()
        if current.buffer.dirty:
            self.status = "E37: No write since last change (:bd! not supported)"
            return
        del self.buffers[self.current_buffer_index]
        if not self.buffers:
            self.buffers.append(EditorBuffer(None, TextBuffer()))
            self.current_buffer_index = 0
        else:
            self.current_buffer_index = min(self.current_buffer_index, len(self.buffers) - 1)
        self._activate_buffer(self.current_buffer_index)
        self.status = f"Deleted buffer | Buf {self.current_buffer_index + 1}/{len(self.buffers)}"

    def _apply_set_command(self, args: list[str]) -> None:
        key = args[0]
        value = " ".join(args[1:])
        auto_pair = self.config.auto_pair
        completion_key = self.config.completion_key
        indent_width = self.config.indent_width
        show_line_numbers = self.config.show_line_numbers
        scan_local_headers = self.config.scan_local_headers
        signature_help = self.config.signature_help
        build_command = self.config.build_command
        run_command = self.config.run_command
        project_root_markers = self.config.project_root_markers
        recent_files_limit = self.config.recent_files_limit
        keybindings = dict(self.config.keybindings)
        if key == "auto_pair":
            auto_pair = value.lower() in ("on", "true", "1", "yes")
        elif key in ("completion_key", "complete"):
            if not normalize_key_name(value):
                self.status = f"Invalid completion key: {value}"
                return
            completion_key = value
        elif key in ("number", "show_line_numbers"):
            show_line_numbers = value.lower() in ("on", "true", "1", "yes")
        elif key == "build_command":
            build_command = value
        elif key == "run_command":
            run_command = value
        elif key == "recent_files_limit":
            try:
                recent_files_limit = int(value)
            except ValueError:
                self.status = f"Invalid recent_files_limit: {value}"
                return
            if not 1 <= recent_files_limit <= 200:
                self.status = f"Invalid recent_files_limit: {value}"
                return
        elif key == "project_root_markers":
            project_root_markers = tuple(item for item in value.split(",") if item)
            if not project_root_markers:
                self.status = "Invalid project_root_markers"
                return
        else:
            self.status = f"Unknown setting: {key}"
            return
        self.config = EditorConfig(
            auto_pair=auto_pair,
            completion_key=completion_key,
            indent_width=indent_width,
            show_line_numbers=show_line_numbers,
            scan_local_headers=scan_local_headers,
            signature_help=signature_help,
            build_command=build_command,
            run_command=run_command,
            project_root_markers=project_root_markers,
            recent_files_limit=recent_files_limit,
            keybindings=keybindings,
        )
        self._write_user_config()
        self._reload_file_context()
        self.status = f"Set {key}={value}"

    def _write_user_config(self) -> None:
        data = {
            "auto_pair": self.config.auto_pair,
            "completion_key": self.config.completion_key,
            "indent_width": self.config.indent_width,
            "show_line_numbers": self.config.show_line_numbers,
            "scan_local_headers": self.config.scan_local_headers,
            "signature_help": self.config.signature_help,
            "build_command": self.config.build_command,
            "run_command": self.config.run_command,
            "project_root_markers": list(self.config.project_root_markers),
            "recent_files_limit": self.config.recent_files_limit,
            "keybindings": dict(sorted(self.config.keybindings.items())),
        }
        self.user_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.user_config_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _remember_recent_file(self, path: Path) -> None:
        if self.project_root is None:
            return
        RecentFilesStore(self.recent_store_path, self.config.recent_files_limit).add(path, self.project_root)

    def _default_recent_store_path(self) -> Path:
        if self.project_root is None:
            return DEFAULT_CONFIG_PATH.parent / "recent" / "global.json"
        digest = hashlib.sha1(str(self.project_root.resolve()).encode("utf-8")).hexdigest()[:16]
        return DEFAULT_CONFIG_PATH.parent / "recent" / f"{digest}.json"

    def _current_word(self) -> str:
        line = self.buffer.current_line()
        if not line:
            return ""
        col = min(self.buffer.cursor_col, len(line) - 1)
        if col > 0 and not re_match_completion_char(line[col]) and re_match_completion_char(line[col - 1]):
            col -= 1
        if not re_match_completion_char(line[col]):
            return ""
        start = col
        end = col + 1
        while start > 0 and re_match_completion_char(line[start - 1]):
            start -= 1
        while end < len(line) and re_match_completion_char(line[end]):
            end += 1
        return line[start:end]

    def _jump_to_definition(self) -> bool:
        word = self._current_word()
        if not word:
            self.status = "No symbol under cursor"
            return False
        project_index = self._ensure_project_index()
        if project_index is None:
            self.status = "No project root"
            return False
        symbol = project_index.find_definition(word)
        if symbol is None:
            self.status = f"Definition not found: {word}"
            return False
        self.jump_stack.append((self.path, self.buffer.cursor_row, self.buffer.cursor_col))
        if self.path != symbol.path:
            if self.buffer.dirty:
                self.status = "Unsaved changes. Save before jumping to another file."
                return False
            self._open_file(symbol.path)
        self.buffer.cursor_row = min(symbol.row, len(self.buffer.lines) - 1)
        self.buffer.cursor_col = min(symbol.col, len(self.buffer.current_line()))
        self.status = f"Definition: {symbol.name}"
        return True

    def _jump_back(self) -> bool:
        if not self.jump_stack:
            self.status = "Jump stack empty"
            return False
        path, row, col = self.jump_stack.pop()
        if path is not None and path != self.path:
            if self.buffer.dirty:
                self.status = "Unsaved changes. Save before jumping back."
                return False
            self._open_file(path)
        self.buffer.cursor_row = min(row, len(self.buffer.lines) - 1)
        self.buffer.cursor_col = min(col, len(self.buffer.current_line()))
        self.status = "Jumped back"
        return True

    def _goto_next_diagnostic(self, direction: int) -> bool:
        self._refresh_diagnostics()
        records = self._diagnostic_records()
        if not records:
            self.status = "No diagnostics"
            return False
        current_path = self.path.resolve() if self.path else Path("[buffer]")
        current = (current_path.as_posix(), self.buffer.cursor_row, self.buffer.cursor_col)
        ordered = sorted(records, key=lambda item: (item[0].as_posix(), item[1], item[2]))
        if direction >= 0:
            target = next((item for item in ordered if (item[0].as_posix(), item[1], item[2]) > current), ordered[0])
        else:
            target = next((item for item in reversed(ordered) if (item[0].as_posix(), item[1], item[2]) < current), ordered[-1])
        path, row, col, message = target
        if path != current_path and path != Path("[buffer]"):
            if self.buffer.dirty:
                self.status = "Unsaved changes. Save before jumping to another file."
                return False
            self._open_file(path)
        self.buffer.cursor_row = min(row, len(self.buffer.lines) - 1)
        self.buffer.cursor_col = min(col, len(self.buffer.current_line()))
        self.status = message
        return True

    def _diagnostic_records(self) -> list[tuple[Path, int, int, str]]:
        current_path = self.path.resolve() if self.path else Path("[buffer]")
        records = [(current_path, item.row, item.col, item.message) for item in self.diagnostics]
        for item in self.build_diagnostics:
            records.append((self._resolve_build_path(item.path), item.row, item.col, item.message))
        return records

    def _resolve_build_path(self, path: Path) -> Path:
        if path.is_absolute():
            return path.resolve()
        root = self.project_root or (self.path.parent if self.path else Path.cwd())
        return (root / path).resolve()

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
        header_index = self._ensure_header_index()
        project_index = self._ensure_project_index()
        self.completions = self.completion.suggest(
            prefix,
            self.buffer.lines,
            self.buffer.cursor_row,
            self.buffer.cursor_col,
            header_index=header_index,
            project_index=project_index,
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
        old_prefix_len = len(self.buffer.current_prefix())
        self._record_edit()
        if item.insert_text:
            self.buffer.replace_current_prefix_with_snippet(item.insert_text)
            self._clear_placeholders()
        else:
            self.buffer.replace_current_prefix(item.text)
            self._shift_placeholders_after_edit(len(item.text) - old_prefix_len)
        self._refresh_diagnostics()
        self.status = f"Completed {item.text} ({item.kind})"
        self.completions = []

    def _record_edit(self) -> None:
        self.undo.record(self.buffer)

    def _add_pair_placeholder(self, closer: str) -> None:
        self.auto_pair_placeholders.append((self.buffer.cursor_row, self.buffer.cursor_col, closer))

    def _clear_placeholders(self) -> None:
        self.auto_pair_placeholders = []

    def _drop_stale_placeholders(self) -> None:
        self.auto_pair_placeholders = [placeholder for placeholder in self.auto_pair_placeholders if self._placeholder_matches(placeholder)]

    def _shift_placeholders_after_edit(self, delta: int) -> None:
        if delta == 0:
            return
        row = self.buffer.cursor_row
        col = self.buffer.cursor_col
        shifted: list[tuple[int, int, str]] = []
        for placeholder_row, placeholder_col, closer in self.auto_pair_placeholders:
            if placeholder_row == row and placeholder_col >= col - delta:
                shifted.append((placeholder_row, placeholder_col + delta, closer))
            else:
                shifted.append((placeholder_row, placeholder_col, closer))
        self.auto_pair_placeholders = shifted
        self._drop_stale_placeholders()

    def _placeholder_matches(self, placeholder: tuple[int, int, str]) -> bool:
        row, col, closer = placeholder
        if row < 0 or row >= len(self.buffer.lines):
            return False
        line = self.buffer.lines[row]
        return 0 <= col < len(line) and line[col] == closer

    def _placeholder_at_cursor(self, closer: str) -> tuple[int, int, str] | None:
        self._drop_stale_placeholders()
        target = (self.buffer.cursor_row, self.buffer.cursor_col, closer)
        for placeholder in self.auto_pair_placeholders:
            if placeholder == target:
                return placeholder
        return None

    def _jump_over_pair_placeholder(self, closer: str) -> bool:
        placeholder = self._placeholder_at_cursor(closer)
        if placeholder is None:
            return False
        self.buffer.move_right()
        self.auto_pair_placeholders.remove(placeholder)
        return True

    def _search(self) -> None:
        query = self._prompt("Search: ")
        if not query:
            return
        self._search_for(query)

    def _search_for(self, query: str, *, start_after_cursor: bool = False, direction: int = 1) -> bool:
        if not query:
            return False
        self.last_search_query = query
        match = self._find_match(query, start_after_cursor=start_after_cursor, direction=direction)
        if match is None:
            self.status = f"Not found: {query}"
            return False
        self.buffer.cursor_row, self.buffer.cursor_col = match
        self.status = f"Found {query!r}"
        return True

    def _repeat_search(self, direction: int) -> bool:
        if not self.last_search_query:
            self.status = "No previous search"
            return False
        return self._search_for(self.last_search_query, start_after_cursor=True, direction=direction)

    def _find_match(self, query: str, *, start_after_cursor: bool, direction: int) -> tuple[int, int] | None:
        matches = [
            (row, col)
            for row, line in enumerate(self.buffer.lines)
            for col in all_occurrences(line, query)
        ]
        if not matches:
            return None
        current = (self.buffer.cursor_row, self.buffer.cursor_col)
        if direction >= 0:
            for match in matches:
                if not start_after_cursor or match > current:
                    return match
            return matches[0]
        for match in reversed(matches):
            if not start_after_cursor or match < current:
                return match
        return matches[-1]

    def _prompt(self, label: str) -> str:
        curses.echo()
        height, width = self.stdscr.getmaxyx()
        self.stdscr.move(height - 1, 0)
        self.stdscr.clrtoeol()
        self._safe_addnstr(height - 1, 0, label, max(0, width - 1))
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
        gutter_width = self._gutter_width()
        if self.mode == "LIST":
            self._draw_list(text_height, width)
            self._draw_status(height, width)
            self.stdscr.refresh()
            return
        editor_x = self._editor_origin_x(width)
        editor_width = max(1, width - editor_x)
        if self.tree_visible:
            tree_width = max(1, editor_x - 1)
            self._draw_tree(text_height, tree_width)
            if tree_width < width:
                for row in range(text_height):
                    self._safe_addnstr(row, tree_width, "|", 1, curses.A_DIM)
        for screen_row in range(text_height):
            file_row = self.row_offset + screen_row
            if file_row >= len(self.buffer.lines):
                break
            if gutter_width:
                line_no = f"{file_row + 1:>{gutter_width - 1}} "
                self._safe_addnstr(screen_row, editor_x, line_no, gutter_width, curses.A_DIM)
            self._draw_code_line(screen_row, gutter_width, self.buffer.lines[file_row], editor_width, editor_x)
        self._draw_completions(text_height, editor_width, gutter_width, editor_x)
        self._draw_status(height, width)
        cursor_y = self.buffer.cursor_row - self.row_offset
        cursor_x = self._cursor_screen_x()
        if self.tree_visible and self.tree_focused:
            cursor_y = min(max(0, self.tree_cursor - self.tree_row_offset + 1), max(0, text_height - 1))
            cursor_x = min(2, max(0, width - 1))
        if 0 <= cursor_y < text_height and 0 <= cursor_x < width:
            self.stdscr.move(cursor_y, cursor_x)
        self.stdscr.refresh()

    def _draw_list(self, text_height: int, width: int) -> None:
        title = f" {self.list_title} "
        self._safe_addnstr(0, 0, title.ljust(width), width - 1, curses.A_REVERSE)
        visible_rows = max(0, text_height - 1)
        if self.list_cursor < self.list_row_offset:
            self.list_row_offset = self.list_cursor
        elif self.list_cursor >= self.list_row_offset + visible_rows:
            self.list_row_offset = max(0, self.list_cursor - visible_rows + 1)
        for offset, line in enumerate(self.list_lines[self.list_row_offset : self.list_row_offset + visible_rows], start=1):
            line_index = self.list_row_offset + offset - 1
            attr = curses.A_REVERSE if line_index == self.list_cursor else curses.A_NORMAL
            self._safe_addnstr(offset, 0, line.ljust(width), width - 1, attr)

    def _draw_tree(self, text_height: int, width: int) -> None:
        title = " Project "
        attr = curses.A_REVERSE if self.tree_focused else curses.A_DIM
        self._safe_addnstr(0, 0, title.ljust(width), max(0, width - 1), attr)
        visible_rows = max(0, text_height - 1)
        if self.tree_cursor < self.tree_row_offset:
            self.tree_row_offset = self.tree_cursor
        elif self.tree_cursor >= self.tree_row_offset + visible_rows:
            self.tree_row_offset = max(0, self.tree_cursor - visible_rows + 1)
        if not self.tree_entries:
            self._safe_addnstr(1, 0, " No project files", max(0, width - 1), curses.A_DIM)
            return
        for screen_offset, entry in enumerate(self.tree_entries[self.tree_row_offset : self.tree_row_offset + visible_rows], start=1):
            entry_index = self.tree_row_offset + screen_offset - 1
            if entry.is_dir:
                marker = "v" if entry.expanded else ">"
            else:
                marker = " "
            line = f" {marker} {entry.display}"
            row_attr = curses.A_REVERSE if self.tree_focused and entry_index == self.tree_cursor else curses.A_NORMAL
            self._safe_addnstr(screen_offset, 0, line.ljust(width), max(0, width - 1), row_attr)

    def _draw_status(self, height: int, width: int) -> None:
        name = str(self.path) if self.path else "[No Name]"
        dirty = " +" if self.buffer.dirty else ""
        mode = "TREE" if self.tree_visible and self.tree_focused else self.mode
        buffer_position = f"Buf {self.current_buffer_index + 1}/{len(self.buffers)}"
        left = f" {mode} | {name}{dirty} | {buffer_position} | {self.buffer.cursor_row + 1}:{self.buffer.cursor_col + 1} | sfe {read_version()} | Diagnostics: {len(self.diagnostics)} "
        self._safe_addnstr(height - 2, 0, left.ljust(width), width - 1, getattr(curses, "A_REVERSE", curses.A_NORMAL))
        if self.mode == "COMMAND":
            bottom = ":" + self.command_line
        else:
            bottom = self._signature_help_text() or self.status
        self._safe_addnstr(height - 1, 0, bottom.ljust(width), width - 1)

    def _signature_help_text(self) -> str:
        if self.mode != "INSERT" or not self.config.signature_help:
            return ""
        return self.signature_help.signature_for(self.buffer.current_line(), self.buffer.cursor_col)

    def _safe_addnstr(self, row: int, col: int, text: str, max_width: int, attr: int = 0) -> None:
        if not text or max_width <= 0:
            return
        height, width = self.stdscr.getmaxyx()
        if row < 0 or row >= height or col < 0 or col >= width:
            return
        available = max(0, width - col - 1)
        if available <= 0:
            return
        clipped = clip_display_width(text, min(max_width, available))
        if not clipped:
            return
        try:
            self.stdscr.addnstr(row, col, clipped, len(clipped), attr)
        except curses.error:
            return

    def _draw_code_line(self, screen_row: int, gutter_width: int, line: str, width: int, x_offset: int = 0) -> None:
        visible_start = self.col_offset
        visible_end = self.col_offset + max(0, width - gutter_width - 1)
        x = x_offset + gutter_width
        for token_start, token_text, attr in self._line_segments_with_attrs(screen_row, line):
            token_end = token_start + len(token_text)
            if token_end <= visible_start:
                continue
            if token_start >= visible_end:
                break
            start = max(visible_start, token_start)
            end = min(visible_end, token_end)
            text = token_text[start - token_start : end - token_start]
            self._safe_addnstr(screen_row, x, text, max(0, x_offset + width - x - 1), attr)
            x += display_width(text)

    def _line_segments_with_attrs(self, screen_row: int, line: str) -> list[tuple[int, str, int]]:
        file_row = self.row_offset + screen_row
        placeholder_cols = {
            col
            for row, col, closer in self.auto_pair_placeholders
            if row == file_row and 0 <= col < len(line) and line[col] == closer
        }
        segments: list[tuple[int, str, int]] = []
        position = 0
        for token in self.highlighter.tokenize(line):
            token_attr = self.syntax_attrs.get(token.kind, curses.A_NORMAL)
            for offset, char in enumerate(token.text):
                col = position + offset
                attr = token_attr | curses.A_DIM if col in placeholder_cols else token_attr
                if segments and segments[-1][0] + len(segments[-1][1]) == col and segments[-1][2] == attr:
                    start, text, _ = segments[-1]
                    segments[-1] = (start, text + char, attr)
                else:
                    segments.append((col, char, attr))
            position += len(token.text)
        return segments

    def _draw_completions(self, text_height: int, width: int, gutter_width: int, x_offset: int = 0) -> None:
        if not self.completions:
            return
        start_y = min(max(0, self.buffer.cursor_row - self.row_offset + 1), max(0, text_height - len(self.completions)))
        start_x = min(
            max(x_offset + gutter_width, x_offset + gutter_width + self.buffer.cursor_col - self.col_offset),
            max(x_offset + gutter_width, x_offset + width - 28),
        )
        for offset, item in enumerate(self.completions):
            attr = curses.A_REVERSE if offset == self.completion_index else curses.A_NORMAL
            source = item.source or item.kind
            label = f" {item.text:<18} {item.kind:<8} {source:<8} {item.detail:<22}"
            self._safe_addnstr(start_y + offset, start_x, label, min(len(label), x_offset + width - start_x - 1), attr)

    def _scroll_to_cursor(self) -> None:
        height, width = self.stdscr.getmaxyx()
        text_height = max(1, height - 2)
        gutter_width = self._gutter_width()
        if self.buffer.cursor_row < self.row_offset:
            self.row_offset = self.buffer.cursor_row
        elif self.buffer.cursor_row >= self.row_offset + text_height:
            self.row_offset = self.buffer.cursor_row - text_height + 1
        visible_cols = max(1, self._editor_width(width) - gutter_width - 1)
        if self.buffer.cursor_col < self.col_offset:
            self.col_offset = self.buffer.cursor_col
        elif self.buffer.cursor_col >= self.col_offset + visible_cols:
            self.col_offset = self.buffer.cursor_col - visible_cols + 1

    def _gutter_width(self) -> int:
        if not self.config.show_line_numbers:
            return 0
        return max(3, len(str(len(self.buffer.lines))) + 2)

    def _text_origin_x(self) -> int:
        return self._editor_origin_x() + self._gutter_width()

    def _cursor_screen_x(self) -> int:
        cursor_prefix = self.buffer.current_line()[: self.buffer.cursor_col]
        return self._text_origin_x() + display_width(cursor_prefix) - self.col_offset

    def _editor_origin_x(self, width: int | None = None) -> int:
        if not self.tree_visible:
            return 0
        if width is None:
            if self.stdscr is None:
                width = 80
            else:
                _, width = self.stdscr.getmaxyx()
        return min(width - 1, self._tree_panel_width(width) + 1)

    def _editor_width(self, width: int) -> int:
        return max(1, width - self._editor_origin_x(width))

    def _tree_panel_width(self, width: int) -> int:
        if width < 40:
            return max(12, width // 3)
        return min(36, max(24, width // 3))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sfe",
        description="Small SSH-friendly terminal code editor with C completions.",
    )
    parser.add_argument("--version", "-v", action="store_true", help="show version and exit")
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


def clip_display_width(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    used = 0
    chars: list[str] = []
    for char in text:
        char_width = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
        if used + char_width > max_width:
            break
        chars.append(char)
        used += char_width
    return "".join(chars)


def all_occurrences(text: str, query: str) -> list[int]:
    if not query:
        return []
    positions: list[int] = []
    start = 0
    while True:
        found = text.find(query, start)
        if found == -1:
            return positions
        positions.append(found)
        start = found + max(1, len(query))


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
    if value.startswith("control+"):
        value = "ctrl+" + value.removeprefix("control+")
    if value.removeprefix("ctrl+") in CTRL_ARROW_NAMES and value.startswith("ctrl+"):
        return value
    if len(value) == 6 and value.startswith("ctrl+") and value[-1].isalpha():
        return value
    if value.startswith("f") and value[1:].isdigit() and 1 <= int(value[1:]) <= 12:
        return value
    return ""


def normalize_bind_command(command: str) -> str:
    value = command.strip().lower()
    if value.startswith(":"):
        value = value[1:].strip()
    aliases = {
        "buffer": "buffers",
        "ls": "buffers",
        "next": "bn",
        "prev": "bp",
        "previous": "bp",
        "delete": "bd",
        "file": "files",
    }
    value = aliases.get(value, value)
    return value if value in KEYBINDABLE_COMMANDS else ""


def first_key_name(key) -> str:
    names = sorted(key_to_names(key))
    if not names:
        return ""
    preferred = [name for name in names if name not in {"ctrl+i", "ctrl+j", "ctrl+m", "ctrl+@"}]
    return preferred[0] if preferred else names[0]


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
    if isinstance(key, str) and key.startswith("KEY_C"):
        direction = key.removeprefix("KEY_C").lower()
        if direction in CTRL_ARROW_NAMES:
            return {f"ctrl+{direction}"}
    if isinstance(key, str) and key in KEYNAME_ARROW_ALIASES:
        return {KEYNAME_ARROW_ALIASES[key]}
    if isinstance(key, str) and key.startswith("ctrl+"):
        normalized = normalize_key_name(key)
        return {normalized} if normalized else set()
    if isinstance(key, str) and key.startswith("KEY_F(") and key.endswith(")"):
        number_text = key.removeprefix("KEY_F(").removesuffix(")")
        if number_text.isdigit():
            return {f"f{number_text}"}
    if isinstance(key, int):
        key_name = _curses_keyname(key)
        normalized = _normalize_modified_arrow_keyname(key_name)
        if normalized:
            return {normalized}
        if key in MODIFIED_ARROW_KEY_CODES:
            return {MODIFIED_ARROW_KEY_CODES[key]}
        if curses is not None:
            for direction in CTRL_ARROW_NAMES:
                value = getattr(curses, f"KEY_C{direction.upper()}", None)
                if value is not None and key == value:
                    return {f"ctrl+{direction}"}
            key_f0 = getattr(curses, "KEY_F0", None)
            if key_f0 is not None and key_f0 < key <= key_f0 + 12:
                return {f"f{key - key_f0}"}
    return set()


def _curses_keyname(key: int) -> str:
    if curses is None:
        return ""
    keyname = getattr(curses, "keyname", None)
    if not callable(keyname):
        return ""
    try:
        raw = keyname(key)
    except curses.error:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("ascii", errors="ignore")
    return str(raw)


def _normalize_modified_arrow_keyname(name: str) -> str:
    value = name.strip().upper()
    if not value:
        return ""
    if value in KEYNAME_ARROW_ALIASES:
        return KEYNAME_ARROW_ALIASES[value]
    if value.startswith("KEY_"):
        value = value.removeprefix("KEY_")
    if value.startswith("K"):
        value = value[1:]
    for arrow_name, direction in TERMINAL_ARROW_NAMES.items():
        if value.startswith(arrow_name):
            suffix = value[len(arrow_name) :]
            if suffix and suffix.isdigit():
                return _modified_arrow_key_name(int(suffix), direction)
    return ""


def _modified_arrow_key_name(modifier: int, direction: str) -> str:
    if direction not in CTRL_ARROW_NAMES:
        return ""
    ctrl_pressed = bool((modifier - 1) & 4)
    return f"ctrl+{direction}" if ctrl_pressed else ""


def parse_key_sequence(keys) -> int | str | None:
    if len(keys) == 1 and isinstance(keys[0], str) and len(keys[0]) > 1:
        return parse_key_sequence(list(keys[0]))
    if keys == [ESCAPE] or keys == ["\x1b"]:
        return "\x1b"
    if len(keys) < 3 or keys[0] not in (ESCAPE, "\x1b"):
        return None
    if keys[1] == "O":
        if len(keys) == 4 and isinstance(keys[2], str) and keys[2].isdigit() and keys[3] in CTRL_ARROW_SEQUENCE_SUFFIXES:
            return _modified_arrow_key_name(int(keys[2]), CTRL_ARROW_SEQUENCE_SUFFIXES[keys[3]]) or None
        return None
    if keys[1] != "[":
        return None
    if keys[-1] in CTRL_ARROW_SEQUENCE_SUFFIXES:
        body = "".join(str(part) for part in keys[2:-1])
        parts = body.split(";")
        if len(parts) == 1 and parts[0].isdigit():
            return _modified_arrow_key_name(int(parts[0]), CTRL_ARROW_SEQUENCE_SUFFIXES[keys[-1]]) or None
        if len(parts) >= 2 and parts[-1].isdigit():
            modifiers = int(parts[-1])
            return _modified_arrow_key_name(modifiers, CTRL_ARROW_SEQUENCE_SUFFIXES[keys[-1]]) or None
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
            if ctrl_pressed and 65 <= codepoint <= 90:
                return chr(codepoint - 64)
            if ctrl_pressed and 97 <= codepoint <= 122:
                return chr(codepoint - 96)
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
    if keys[1] == "O":
        if len(keys) == 2:
            return True
        if len(keys) == 3:
            return isinstance(keys[2], str) and keys[2].isdigit()
        return keys[-1] in CTRL_ARROW_SEQUENCE_SUFFIXES
    if keys[1] != "[":
        return False
    if len(keys) == 2:
        return True
    allowed = set("0123456789;")
    if keys[-1] in CTRL_ARROW_SEQUENCE_SUFFIXES:
        return True
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


def _reexec_with_bundled_python_if_available(argv: list[str]) -> bool:
    sfe_home = Path(os.environ.get("SFE_HOME", Path(__file__).resolve().parent))
    bundled_python = Path(os.environ.get("SFE_BUNDLED_PYTHON", sfe_home / "python" / "bin" / "python3"))
    if not bundled_python.exists() or sys.executable == str(bundled_python):
        return False
    env = dict(os.environ)
    native_lib = sfe_home / "python" / "lib" / "native"
    terminfo = sfe_home / "python" / "share" / "terminfo"
    if native_lib.is_dir():
        existing = env.get("LD_LIBRARY_PATH")
        env["LD_LIBRARY_PATH"] = f"{native_lib}:{existing}" if existing else str(native_lib)
    if terminfo.is_dir():
        existing = env.get("TERMINFO_DIRS")
        env["TERMINFO_DIRS"] = f"{terminfo}:{existing}" if existing else f"{terminfo}:/usr/share/terminfo:/lib/terminfo"
    env["SFE_PREFER_BUNDLED_PYTHON"] = "1"
    os.execve(str(bundled_python), [str(bundled_python), "-B", "-S", str(sfe_home / "sfe.py"), *argv], env)
    return True


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(f"sfe {read_version()}")
        return 0
    if curses is None:
        if _reexec_with_bundled_python_if_available(sys.argv[1:] if argv is None else argv):
            return 0
        print("sfe requires Python curses. It is available on the target Linux SSH server.", file=sys.stderr)
        return 1
    if not os.environ.get("TERM"):
        os.environ["TERM"] = "xterm-256color"
    curses.wrapper(lambda stdscr: EditorApp(stdscr, args.file, load_config()).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
