import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from contextlib import redirect_stdout

import sfe
from sfe import EditorApp, EditorConfig, display_width


class FakeCurses:
    error = RuntimeError
    A_DIM = 1
    A_NORMAL = 0
    A_REVERSE = 2
    KEY_LEFT = 260
    KEY_RIGHT = 261
    KEY_UP = 259
    KEY_DOWN = 258
    KEY_HOME = 262
    KEY_END = 360
    KEY_BACKSPACE = 263
    KEY_DC = 330
    KEY_F0 = 264
    KEY_RESIZE = 410


class FakeInputScreen:
    def __init__(self, keys):
        self.keys = list(keys)
        self.timeouts = []

    def get_wch(self):
        if not self.keys:
            raise FakeCurses.error()
        return self.keys.pop(0)

    def timeout(self, value):
        self.timeouts.append(value)


class FakeDrawScreen:
    def __init__(self):
        self.calls = []
        self.cursor = None

    def addnstr(self, row, col, text, max_width, attr=0):
        self.calls.append((row, col, text[:max_width], attr))

    def erase(self):
        self.calls.append(("erase",))

    def getmaxyx(self):
        return (20, 80)

    def move(self, row, col):
        self.cursor = (row, col)

    def refresh(self):
        self.calls.append(("refresh",))


class BoundsCheckingDrawScreen(FakeDrawScreen):
    def __init__(self, height=20, width=24):
        super().__init__()
        self.height = height
        self.width = width

    def addnstr(self, row, col, text, max_width, attr=0):
        if row < 0 or row >= self.height or col < 0 or col >= self.width:
            raise FakeCurses.error("addnwstr() returned ERR")
        if max_width <= 0:
            raise FakeCurses.error("addnwstr() returned ERR")
        if col + max_width >= self.width:
            raise FakeCurses.error("addnwstr() returned ERR")
        if display_width(text[:max_width]) > self.width - col:
            raise FakeCurses.error("addnwstr() returned ERR")
        super().addnstr(row, col, text, max_width, attr)

    def getmaxyx(self):
        return (self.height, self.width)


class EditorInsertModeTests(unittest.TestCase):
    def setUp(self):
        self.original_curses = sfe.curses
        sfe.curses = FakeCurses

    def tearDown(self):
        sfe.curses = self.original_curses

    def test_insert_mode_indents_when_curses_reports_tab_as_integer(self):
        app = EditorApp(stdscr=None, path=None, config=EditorConfig(indent_width=2))
        app.mode = "INSERT"

        app._handle_insert_key(9)

        self.assertEqual(app.buffer.lines, ["  "])
        self.assertEqual(app.buffer.cursor_col, 2)

    def test_insert_mode_accepts_completion_with_tab(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"
        app.buffer.lines = ["pri"]
        app.buffer.cursor_col = 3
        app._open_completions()

        app._handle_insert_key("\t")

        self.assertEqual(app.buffer.lines, ["printf"])
        self.assertEqual(app.buffer.cursor_col, 6)
        self.assertEqual(app.completions, [])

    def test_insert_mode_ctrl_p_keeps_completion_navigation_when_menu_is_open(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"
        app.buffer.lines = ["pr"]
        app.buffer.cursor_col = 2
        app._open_completions()
        app.completion_index = 0

        app._handle_insert_key("\x10")

        self.assertEqual(app.mode, "INSERT")
        self.assertEqual(app.completion_index, len(app.completions) - 1)

    def test_insert_mode_accepts_snippet_completion_with_tab(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"
        app.buffer.lines = ["ma"]
        app.buffer.cursor_col = 2
        app._open_completions()

        app._handle_insert_key("\t")

        self.assertEqual(app.buffer.lines, ["int main(void) {", "    ", "}"])
        self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (1, 4))
        self.assertEqual(app.completions, [])

    def test_insert_mode_newline_does_not_accept_completion(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"
        app.buffer.lines = ["pri"]
        app.buffer.cursor_col = 3
        app._open_completions()

        app._handle_insert_key("\n")

        self.assertEqual(app.buffer.lines, ["pri", ""])
        self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (1, 0))
        self.assertEqual(app.completions, [])

    def test_insert_mode_newline_preserves_indent_after_open_brace(self):
        app = EditorApp(stdscr=None, path=None, config=EditorConfig(indent_width=4))
        app.mode = "INSERT"
        app.buffer.lines = ["    if (ok) {"]
        app.buffer.cursor_col = len(app.buffer.current_line())

        app._handle_insert_key("\n")

        self.assertEqual(app.buffer.lines, ["    if (ok) {", "        "])
        self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (1, 8))

    def test_ctrl_space_opens_completion_from_integer_nul(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"
        app.buffer.lines = ["in"]
        app.buffer.cursor_col = 2

        app._handle_insert_key(0)

        self.assertTrue(app.completions)
        self.assertEqual(app.completions[0].text, "int")

    def test_ctrl_space_opens_completion_from_control_string(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"
        app.buffer.lines = ["in"]
        app.buffer.cursor_col = 2

        app._handle_insert_key("\x1f")

        self.assertTrue(app.completions)

    def test_ctrl_space_opens_completion_from_csi_u_sequence(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"
        app.buffer.lines = ["in"]
        app.buffer.cursor_col = 2

        app._handle_key_sequence(["\x1b", "[", "3", "2", ";", "5", "u"])

        self.assertTrue(app.completions)
        self.assertEqual(app.completions[0].text, "int")
        self.assertEqual(app.mode, "INSERT")

    def test_read_key_sequence_collects_csi_u_ctrl_space(self):
        screen = FakeInputScreen(list("\x1b[32;5u"))
        app = EditorApp(stdscr=screen, path=None)

        self.assertEqual(app._read_key_sequence(), ["\x1b", "[", "3", "2", ";", "5", "u"])
        self.assertEqual(screen.timeouts, [sfe.KEY_SEQUENCE_TIMEOUT_MS, -1])

    def test_read_key_sequence_keeps_plain_escape_on_timeout(self):
        screen = FakeInputScreen(["\x1b"])
        app = EditorApp(stdscr=screen, path=None)

        self.assertEqual(app._read_key_sequence(), ["\x1b"])
        self.assertEqual(screen.timeouts, [sfe.KEY_SEQUENCE_TIMEOUT_MS, -1])

    def test_ctrl_space_opens_completion_from_combined_csi_u_string(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"
        app.buffer.lines = ["in"]
        app.buffer.cursor_col = 2

        app._handle_key_sequence(["\x1b[32;5u"])

        self.assertTrue(app.completions)

    def test_ctrl_space_opens_completion_from_xterm_modify_other_keys_sequence(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"
        app.buffer.lines = ["in"]
        app.buffer.cursor_col = 2

        app._handle_key_sequence(list("\x1b[27;5;32~"))

        self.assertTrue(app.completions)

    def test_configured_completion_key_opens_completion(self):
        app = EditorApp(stdscr=None, path=None, config=EditorConfig(completion_key="ctrl-j"))
        app.mode = "INSERT"
        app.buffer.lines = ["in"]
        app.buffer.cursor_col = 2

        app._handle_insert_key("\n")

        self.assertTrue(app.completions)
        self.assertEqual(app.buffer.lines, ["in"])

    def test_default_ctrl_space_does_not_open_when_completion_key_is_changed(self):
        app = EditorApp(stdscr=None, path=None, config=EditorConfig(completion_key="ctrl-j"))
        app.mode = "INSERT"
        app.buffer.lines = ["in"]
        app.buffer.cursor_col = 2

        app._handle_insert_key(0)

        self.assertFalse(app.completions)

    def test_insert_mode_keeps_plain_space_as_text(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"
        app.buffer.lines = ["int"]
        app.buffer.cursor_col = 3

        app._handle_insert_key(" ")

        self.assertEqual(app.buffer.lines, ["int "])
        self.assertEqual(app.buffer.cursor_col, 4)
        self.assertEqual(app.completions, [])

    def test_insert_mode_auto_closes_braces_and_keeps_cursor_inside(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"

        app._handle_insert_key("{")

        self.assertEqual(app.buffer.lines, ["{}"])
        self.assertEqual(app.buffer.cursor_col, 1)

    def test_insert_mode_enter_between_auto_braces_aligns_closer_with_opener(self):
        app = EditorApp(stdscr=None, path=None, config=EditorConfig(indent_width=4))
        app.mode = "INSERT"
        app.buffer.lines = ["void clon()"]
        app.buffer.cursor_row = 0
        app.buffer.cursor_col = len(app.buffer.current_line())

        app._handle_insert_key("\n")
        app._handle_insert_key("{")
        app._handle_insert_key("\n")

        self.assertEqual(app.buffer.lines, ["void clon()", "{", "    ", "}"])
        self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (2, 4))

    def test_insert_mode_auto_closes_parentheses_brackets_and_quotes(self):
        cases = [("(", "()"), ("[", "[]"), ('"', '""'), ("'", "''")]
        for opener, expected in cases:
            with self.subTest(opener=opener):
                app = EditorApp(stdscr=None, path=None)
                app.mode = "INSERT"

                app._handle_insert_key(opener)

                self.assertEqual(app.buffer.lines, [expected])
                self.assertEqual(app.buffer.cursor_col, 1)

    def test_insert_mode_closing_pair_key_jumps_over_auto_placeholder(self):
        cases = [("(", ")"), ("[", "]"), ("{", "}"), ('"', '"'), ("'", "'")]
        for opener, closer in cases:
            with self.subTest(opener=opener):
                app = EditorApp(stdscr=None, path=None)
                app.mode = "INSERT"

                app._handle_insert_key(opener)
                app._handle_insert_key(closer)

                self.assertEqual(app.buffer.lines, [opener + closer])
                self.assertEqual(app.buffer.cursor_col, 2)

    def test_insert_mode_pair_placeholder_moves_after_inner_text(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"

        for key in ["(", "a", ")"]:
            app._handle_insert_key(key)

        self.assertEqual(app.buffer.lines, ["(a)"])
        self.assertEqual(app.buffer.cursor_col, 3)

    def test_insert_mode_nested_pair_placeholders_jump_in_order(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"

        for key in ["(", "(", ")", ")"]:
            app._handle_insert_key(key)

        self.assertEqual(app.buffer.lines, ["(())"])
        self.assertEqual(app.buffer.cursor_col, 4)

    def test_insert_mode_pair_placeholder_moves_after_completion_accept(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"

        for key in ["(", "p", "r", "\t", ")"]:
            app._handle_insert_key(key)

        self.assertEqual(app.buffer.lines, ["(printf)"])
        self.assertEqual(app.buffer.cursor_col, 8)

    def test_insert_mode_keeps_real_closing_char_when_no_placeholder_exists(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"
        app.buffer.lines = ["("]
        app.buffer.cursor_col = 1

        app._handle_insert_key(")")

        self.assertEqual(app.buffer.lines, ["()"])
        self.assertEqual(app.buffer.cursor_col, 2)

    def test_insert_mode_respects_disabled_auto_pair_config(self):
        app = EditorApp(stdscr=None, path=None, config=EditorConfig(auto_pair=False))
        app.mode = "INSERT"

        app._handle_insert_key("{")

        self.assertEqual(app.buffer.lines, ["{"])
        self.assertEqual(app.buffer.cursor_col, 1)

    def test_insert_mode_closing_brace_aligns_to_block_indent(self):
        app = EditorApp(stdscr=None, path=None, config=EditorConfig(indent_width=4))
        app.mode = "INSERT"
        app.buffer.lines = ["if (ok) {", "    "]
        app.buffer.cursor_row = 1
        app.buffer.cursor_col = 4

        app._handle_insert_key("}")

        self.assertEqual(app.buffer.lines, ["if (ok) {", "}"])
        self.assertEqual(app.buffer.cursor_col, 1)

    def test_insert_mode_backspace_removes_empty_pair(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"

        app._handle_insert_key("{")
        app._handle_insert_key("\b")

        self.assertEqual(app.buffer.lines, [""])
        self.assertEqual(app.buffer.cursor_col, 0)

    def test_insert_mode_backspace_on_empty_indent_removes_one_level(self):
        app = EditorApp(stdscr=None, path=None, config=EditorConfig(indent_width=4))
        app.mode = "INSERT"
        app.buffer.lines = ["        value = 1;"]
        app.buffer.cursor_col = 8

        app._handle_insert_key("\b")

        self.assertEqual(app.buffer.lines, ["    value = 1;"])
        self.assertEqual(app.buffer.cursor_col, 4)


class EditorLayoutTests(unittest.TestCase):
    def setUp(self):
        self.original_curses = sfe.curses
        sfe.curses = FakeCurses
        self.original_colors = {
            "plain": 0,
            "keyword": 10,
            "preprocessor": 20,
            "function": 30,
            "string": 40,
            "number": 50,
            "comment": 60,
            "placeholder": 70,
        }

    def tearDown(self):
        sfe.curses = self.original_curses

    def test_line_number_width_respects_config(self):
        app = EditorApp(stdscr=None, path=None, config=EditorConfig(show_line_numbers=True))
        app.buffer.lines = [""] * 120

        self.assertEqual(app._gutter_width(), 5)

        app_no_numbers = EditorApp(stdscr=None, path=None, config=EditorConfig(show_line_numbers=False))

        self.assertEqual(app_no_numbers._gutter_width(), 0)

    def test_cursor_screen_x_uses_display_width_and_gutter(self):
        app = EditorApp(stdscr=None, path=None, config=EditorConfig(show_line_numbers=True))
        app.buffer.lines = ['printf("这是");']
        app.buffer.cursor_col = len('printf("这是')

        self.assertEqual(app._cursor_screen_x(), app._gutter_width() + display_width('printf("这是'))

    def test_signature_help_text_returns_insert_mode_signature(self):
        app = EditorApp(stdscr=None, path=None, config=EditorConfig(signature_help=True))
        app.mode = "INSERT"
        app.buffer.lines = ['printf("']
        app.buffer.cursor_col = len(app.buffer.current_line())

        self.assertEqual(app._signature_help_text(), "int printf(const char *format, ...)")

    def test_startup_defers_project_and_header_scans(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            source = root / "main.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")

            with (
                mock.patch.object(sfe.HeaderScanner, "scan", side_effect=AssertionError("header scan during startup")),
                mock.patch.object(sfe.ProjectScanner, "scan", side_effect=AssertionError("project scan during startup")),
                mock.patch.object(sfe.ProjectFileScanner, "scan", side_effect=AssertionError("file scan during startup")),
            ):
                app = EditorApp(stdscr=None, path=str(source))

        self.assertEqual(app.path, source)
        self.assertEqual(app.buffer.lines[0], "int main(void) { return 0; }")

    def test_completion_loads_lazy_project_and_header_indexes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            (root / "mathx.h").write_text("#define LIMIT 16\n", encoding="utf-8")
            (root / "helper.c").write_text("int project_add(int a, int b) {\n    return a + b;\n}\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(root / "main.c"))
            app.mode = "INSERT"

            app.buffer.lines = ['#include "ma']
            app.buffer.cursor_col = len(app.buffer.current_line())
            app._open_completions()
            header_names = [item.text for item in app.completions]

            app.buffer.lines = ["project_"]
            app.buffer.cursor_col = len(app.buffer.current_line())
            app._open_completions()
            item_by_text = {item.text: item for item in app.completions}

        self.assertIn("mathx.h", header_names)
        self.assertEqual(item_by_text["project_add"].source, "project")

    def test_completion_uses_local_header_index_from_file_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mathx.h").write_text("#define LIMIT 16\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(root / "main.c"))
            app.mode = "INSERT"
            app.buffer.lines = ['#include "ma']
            app.buffer.cursor_col = len(app.buffer.current_line())

            app._open_completions()

        self.assertEqual(app.completions[0].text, "mathx.h")

    def test_completion_uses_project_symbols_from_file_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helper.c").write_text("int project_add(int a, int b) {\n    return a + b;\n}\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(root / "main.c"))
            app.mode = "INSERT"
            app.buffer.lines = ["project_"]
            app.buffer.cursor_col = len(app.buffer.current_line())

            app._open_completions()

        item_by_text = {item.text: item for item in app.completions}
        self.assertEqual(item_by_text["project_add"].source, "project")

    def test_status_line_includes_version_and_diagnostic_count(self):
        screen = FakeDrawScreen()
        app = EditorApp(stdscr=screen, path=None)
        app.diagnostics = [object(), object()]

        app._draw_status(10, 100)

        status_rows = [call[2] for call in screen.calls]
        self.assertTrue(any("sfe " in row and "Diagnostics: 2" in row for row in status_rows))

    def test_status_line_includes_buffer_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "one.c"
            second = Path(tmp) / "two.c"
            first.write_text("int one;\n", encoding="utf-8")
            second.write_text("int two;\n", encoding="utf-8")
            screen = FakeDrawScreen()
            app = EditorApp(stdscr=screen, path=str(first))
            app._execute_command(f"e {second}")

            app._draw_status(10, 120)

        status_rows = [call[2] for call in screen.calls]
        self.assertTrue(any("Buf 2/2" in row for row in status_rows))

    def test_auto_pair_placeholder_draws_dimmed_closer(self):
        screen = FakeDrawScreen()
        app = EditorApp(stdscr=screen, path=None)
        app.syntax_attrs = dict(self.original_colors)
        app.mode = "INSERT"

        app._handle_insert_key("(")
        app._draw_code_line(0, 0, app.buffer.current_line(), 20)

        self.assertIn(("(", 0), [(call[2], call[3]) for call in screen.calls])
        self.assertIn((")", FakeCurses.A_DIM), [(call[2], call[3]) for call in screen.calls])

    def test_draw_code_line_clips_at_curses_boundary(self):
        screen = BoundsCheckingDrawScreen(height=6, width=12)
        app = EditorApp(stdscr=screen, path=None, config=EditorConfig(show_line_numbers=False))
        app.syntax_attrs = dict(self.original_colors)

        app._draw_code_line(0, 0, "int value = 12345;", 12, 0)

        drawn = "".join(call[2] for call in screen.calls if len(call) == 4)
        self.assertIn("int", drawn)

    def test_safe_draw_ignores_curses_boundary_errors(self):
        screen = BoundsCheckingDrawScreen(height=4, width=10)
        app = EditorApp(stdscr=screen, path=None)

        app._safe_addnstr(0, 9, "x", 1)

        self.assertEqual(screen.calls, [])

    def test_draw_with_tree_does_not_crash_on_narrow_editor_pane(self):
        screen = BoundsCheckingDrawScreen(height=8, width=28)
        app = EditorApp(stdscr=screen, path=None)
        app.syntax_attrs = dict(self.original_colors)
        app.tree_visible = True
        app.tree_focused = False
        app.buffer.lines = ["int value = 12345678901234567890;"]

        app._draw()

        drawn = "".join(call[2] for call in screen.calls if len(call) == 4)
        self.assertIn("Project", drawn)


class EditorNormalModeTests(unittest.TestCase):
    def setUp(self):
        self.original_curses = sfe.curses
        sfe.curses = FakeCurses

    def tearDown(self):
        sfe.curses = self.original_curses

    def test_normal_mode_undo_and_redo(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"
        app._handle_insert_key("a")
        app.mode = "NORMAL"

        app._handle_normal_key("u")

        self.assertEqual(app.buffer.lines, [""])

        app._handle_normal_key("\x12")

        self.assertEqual(app.buffer.lines, ["a"])

    def test_normal_mode_repeats_search_forward_and_backward(self):
        app = EditorApp(stdscr=None, path=None)
        app.buffer.lines = ["alpha", "beta alpha", "gamma alpha"]

        self.assertTrue(app._search_for("alpha"))
        self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (0, 0))

        app._handle_normal_key("n")
        self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (1, 5))

        app._handle_normal_key("N")
        self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (0, 0))

    def test_normal_mode_repeat_search_wraps_around(self):
        app = EditorApp(stdscr=None, path=None)
        app.buffer.lines = ["alpha", "beta", "alpha"]

        self.assertTrue(app._search_for("alpha"))
        app._handle_normal_key("N")

        self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (2, 0))

    def test_normal_mode_repeat_search_without_query_updates_status(self):
        app = EditorApp(stdscr=None, path=None)

        app._handle_normal_key("n")

        self.assertEqual(app.status, "No previous search")

    def test_command_goto_moves_to_requested_line(self):
        app = EditorApp(stdscr=None, path=None)
        app.buffer.lines = ["one", "two", "three"]

        app._execute_command("goto 3")

        self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (2, 0))
        self.assertIn("line 3", app.status)

    def test_command_symbols_lists_current_file_symbols(self):
        app = EditorApp(stdscr=None, path=None)
        app.buffer.lines = ["int add(int a, int b) {", "    return a + b;", "}"]

        app._execute_command("symbols")

        self.assertEqual(app.mode, "LIST")
        self.assertIn("add", app.status)

    def test_command_diag_lists_diagnostics(self):
        app = EditorApp(stdscr=None, path=None)
        app.buffer.lines = ["int value = 1"]

        app._execute_command("diag")

        self.assertEqual(app.mode, "LIST")
        self.assertIn("missing semicolon", app.status)

    def test_command_help_opens_help_list(self):
        app = EditorApp(stdscr=None, path=None)

        app._execute_command("help")

        self.assertEqual(app.mode, "LIST")
        self.assertIn(":w", "\n".join(app.list_lines))

    def test_command_write_to_file_saves_as_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.c"
            app = EditorApp(stdscr=None, path=None)
            app.buffer.lines = ["int value;"]

            app._execute_command(f"w {target}")

            self.assertEqual(target.read_text(encoding="utf-8"), "int value;")
            self.assertEqual(app.path, target)

    def test_command_edit_loads_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "next.c"
            target.write_text("int next;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=None)

            app._execute_command(f"e {target}")

            self.assertEqual(app.path, target)
            self.assertEqual(app.buffer.lines, ["int next;", ""])

    def test_command_edit_opens_second_buffer_without_discarding_dirty_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "one.c"
            second = root / "two.c"
            first.write_text("int one;\n", encoding="utf-8")
            second.write_text("int two;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(first))
            app.mode = "INSERT"
            app._handle_insert_key("x")
            app.mode = "NORMAL"

            app._execute_command(f"e {second}")
            app._execute_command("bp")

        self.assertEqual(app.path, first)
        self.assertEqual(app.buffer.lines[0], "xint one;")
        self.assertTrue(app.buffer.dirty)
        self.assertEqual(len(app.buffers), 2)

    def test_command_edit_reuses_existing_buffer_for_same_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "one.c"
            second = root / "two.c"
            first.write_text("int one;\n", encoding="utf-8")
            second.write_text("int two;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(first))

            app._execute_command(f"e {second}")
            app.buffer.cursor_row = 0
            app.buffer.cursor_col = 3
            app._execute_command(f"e {first}")
            app._execute_command(f"e {second}")

        self.assertEqual(app.path, second)
        self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (0, 3))
        self.assertEqual(len(app.buffers), 2)

    def test_command_buffers_lists_current_and_dirty_buffers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "one.c"
            second = root / "two.c"
            first.write_text("int one;\n", encoding="utf-8")
            second.write_text("int two;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(first))
            app.mode = "INSERT"
            app._handle_insert_key("x")
            app.mode = "NORMAL"
            app._execute_command(f"e {second}")

            app._execute_command("buffers")

        self.assertEqual(app.mode, "LIST")
        joined = "\n".join(app.list_lines)
        self.assertIn("+", joined)
        self.assertIn("%", joined)
        self.assertIn("one.c", joined)
        self.assertIn("two.c", joined)

    def test_buffer_next_previous_wrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "one.c"
            second = root / "two.c"
            first.write_text("int one;\n", encoding="utf-8")
            second.write_text("int two;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(first))
            app._execute_command(f"e {second}")

            app._execute_command("bn")
            path_after_next = app.path
            app._execute_command("bp")
            path_after_previous = app.path

        self.assertEqual(path_after_next, first)
        self.assertEqual(path_after_previous, second)

    def test_buffer_delete_refuses_dirty_and_deletes_clean_buffers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "one.c"
            second = root / "two.c"
            first.write_text("int one;\n", encoding="utf-8")
            second.write_text("int two;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(first))
            app.mode = "INSERT"
            app._handle_insert_key("x")
            app.mode = "NORMAL"

            app._execute_command("bd")
            dirty_status = app.status
            dirty_count = len(app.buffers)
            app._execute_command(f"e {second}")
            app._execute_command("bd")
            clean_count = len(app.buffers)

        self.assertIn("E37", dirty_status)
        self.assertEqual(dirty_count, 1)
        self.assertEqual(clean_count, 1)
        self.assertEqual(app.path, first)

    def test_buffer_delete_last_clean_buffer_creates_no_name_buffer(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "one.c"
            target.write_text("int one;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(target))

            app._execute_command("bd")

        self.assertIsNone(app.path)
        self.assertEqual(app.buffer.lines, [""])
        self.assertFalse(app.buffer.dirty)
        self.assertEqual(len(app.buffers), 1)

    def test_configured_keybinding_runs_buffer_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "one.c"
            second = root / "two.c"
            first.write_text("int one;\n", encoding="utf-8")
            second.write_text("int two;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(first), config=EditorConfig(keybindings={"ctrl+b": "bn"}))
            app._execute_command(f"e {second}")

            app._handle_normal_key("\x02")

        self.assertEqual(app.path, first)

    def test_bind_command_prompts_for_key_and_writes_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            app = EditorApp(stdscr=None, path=None, config=EditorConfig())
            app.user_config_path = config_path

            app._execute_command("bind bn")
            app._handle_key_sequence(["\x1b", "[", "9", "8", ";", "5", "u"])

            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(app.config.keybindings, {"ctrl+b": "bn"})
        self.assertEqual(saved["keybindings"], {"ctrl+b": "bn"})
        self.assertIn("ctrl+b", app.status)
        self.assertIn(":bn", app.status)

    def test_bind_command_rejects_unknown_command(self):
        app = EditorApp(stdscr=None, path=None)

        app._execute_command("bind no_such_command")

        self.assertIn("Unknown bind command", app.status)

    def test_command_set_updates_config_and_writes_user_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            app = EditorApp(stdscr=None, path=None, config=EditorConfig())
            app.user_config_path = config_path

            app._execute_command("set auto_pair off")
            app._execute_command("set completion_key ctrl-g")
            app._execute_command("set number off")

            saved = config_path.read_text(encoding="utf-8")
            self.assertFalse(app.config.auto_pair)
            self.assertEqual(app.config.completion_key, "ctrl-g")
            self.assertFalse(app.config.show_line_numbers)
            self.assertIn('"auto_pair": false', saved)

    def test_command_set_updates_project_workflow_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            app = EditorApp(stdscr=None, path=None, config=EditorConfig())
            app.user_config_path = config_path

            app._execute_command("set build_command make debug")
            app._execute_command("set run_command ./debug")
            app._execute_command("set recent_files_limit 7")

            saved = config_path.read_text(encoding="utf-8")
            self.assertEqual(app.config.build_command, "make debug")
            self.assertEqual(app.config.run_command, "./debug")
            self.assertEqual(app.config.recent_files_limit, 7)
            self.assertIn('"build_command": "make debug"', saved)

    def test_command_tree_toggles_collapsed_project_pane(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n\tcc src/main.c\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(root / "src" / "main.c"))

            app._execute_command("tree")
            visible_after_open = app.tree_visible
            app._execute_command("tree")

        self.assertEqual(app.mode, "NORMAL")
        self.assertTrue(visible_after_open)
        self.assertFalse(app.tree_visible)
        self.assertFalse(app.tree_focused)
        self.assertEqual(app.tree_cursor, 0)
        self.assertIn("src", [entry.relative_path for entry in app.tree_entries])
        self.assertNotIn("src/main.c", [entry.relative_path for entry in app.tree_entries])

    def test_command_tree_open_and_close_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            (root / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(root / "main.c"))

            app._execute_command("tree open")
            visible_after_open = app.tree_visible
            app._execute_command("tree close")

        self.assertTrue(visible_after_open)
        self.assertFalse(app.tree_visible)

    def test_ctrl_w_opens_tree_when_tree_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            source = root / "main.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(source))

            app._handle_normal_key("\x17")

        self.assertTrue(app.tree_visible)
        self.assertTrue(app.tree_focused)
        self.assertEqual(app.mode, "NORMAL")

    def test_tree_enter_toggles_directory_and_opens_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n\tcc src/main.c\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "main.c").write_text("int target;", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(root / "Makefile"))
            app._execute_command("tree")

            app._handle_key("\n")
            expanded_paths = [entry.relative_path for entry in app.tree_entries]
            app.tree_cursor = expanded_paths.index("src/main.c")
            app._handle_key("\n")

        self.assertIn("src", app.tree_expanded)
        self.assertEqual(app.path, root / "src" / "main.c")
        self.assertEqual(app.buffer.lines, ["int target;"])
        self.assertTrue(app.tree_visible)
        self.assertFalse(app.tree_focused)

    def test_tree_enter_opens_file_in_new_buffer_and_keeps_tree_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n\tcc src/main.c\n", encoding="utf-8")
            (root / "src").mkdir()
            current = root / "current.c"
            target = root / "src" / "main.c"
            current.write_text("int current;\n", encoding="utf-8")
            target.write_text("int target;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(current))
            app._execute_command("tree")
            app._handle_key("\n")
            paths = [entry.relative_path for entry in app.tree_entries]
            app.tree_cursor = paths.index("src/main.c")
            app._handle_key("\n")

        self.assertEqual(app.path, target)
        self.assertEqual(len(app.buffers), 2)
        self.assertTrue(app.tree_visible)
        self.assertFalse(app.tree_focused)

    def test_tree_ctrl_w_toggles_focus_and_q_closes_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            (root / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(root / "main.c"))
            app._execute_command("tree")

            app._handle_key("\x17")
            focused_after_first_toggle = app.tree_focused
            app._handle_key("\x17")
            focused_after_second_toggle = app.tree_focused
            app._handle_key("q")

        self.assertFalse(focused_after_first_toggle)
        self.assertTrue(focused_after_second_toggle)
        self.assertFalse(app.tree_visible)

    def test_draw_tree_panel_keeps_editor_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            (root / "src").mkdir()
            source = root / "src" / "main.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            screen = FakeDrawScreen()
            app = EditorApp(stdscr=screen, path=str(source))
            app._execute_command("tree")

            app._draw()

        calls = [call for call in screen.calls if len(call) == 4]
        self.assertTrue(any("Project" in call[2] for call in calls))
        self.assertTrue(any("src/" in call[2] for call in calls))
        self.assertTrue(any("int" in call[2] for call in calls if isinstance(call[1], int) and call[1] > 20))

    def test_command_open_uses_fuzzy_match_to_open_best_project_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "tests").mkdir()
            current = root / "src" / "main.c"
            target = root / "tests" / "test_main.c"
            current.write_text("int current;\n", encoding="utf-8")
            target.write_text("int target;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(current))

            app._execute_command("open testmain")

            self.assertEqual(app.path, target)
            self.assertEqual(app.buffer.lines[0], "int target;")

    def test_files_command_lists_project_files_and_enter_opens_selected_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            current = root / "main.c"
            target = root / "src.c"
            current.write_text("int main;\n", encoding="utf-8")
            target.write_text("int src;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(current))

            app._execute_command("files")
            app.list_cursor = app.list_lines.index("src.c")
            app._handle_list_key("\n")

        self.assertEqual(app.path, target)
        self.assertEqual(app.buffer.lines[0], "int src;")
        self.assertEqual(app.mode, "NORMAL")

    def test_ctrl_p_opens_project_files_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            source = root / "main.c"
            source.write_text("int main;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(source))

            app._handle_normal_key("\x10")

        self.assertEqual(app.mode, "LIST")
        self.assertEqual(app.list_title, "Files")
        self.assertIn("main.c", app.list_lines)

    def test_list_cursor_moves_with_j_and_k(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "LIST"
        app.list_lines = ["one.c", "two.c", "three.c"]
        app.list_actions = [lambda: None, lambda: None, lambda: None]

        app._handle_list_key("j")
        app._handle_list_key("j")
        app._handle_list_key("k")

        self.assertEqual(app.list_cursor, 1)

    def test_command_recent_lists_recent_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            first = root / "one.c"
            second = root / "two.c"
            first.write_text("int one;\n", encoding="utf-8")
            second.write_text("int two;\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(first), config=EditorConfig(recent_files_limit=5))
            app.recent_store_path = root / ".sfe" / "recent.json"
            app._remember_recent_file(first)
            app._remember_recent_file(second)

            app._execute_command("recent")

        self.assertEqual(app.mode, "LIST")
        self.assertEqual(app.list_title, "Recent")
        self.assertEqual(app.list_lines[:2], ["two.c", "one.c"])

    def test_recent_files_default_store_stays_outside_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text("all:\n", encoding="utf-8")
            source = root / "main.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")

            app = EditorApp(stdscr=None, path=str(source))

            self.assertNotEqual(app.recent_store_path.parent, root / ".sfe")
            self.assertFalse((root / ".sfe").exists())

    def test_command_make_runs_build_and_collects_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(source), config=EditorConfig(build_command="make test"))
            calls = []

            def runner(command, cwd):
                calls.append((command, cwd))
                return subprocess.CompletedProcess(command, 2, stdout="", stderr="main.c:1:5: error: bad main\n")

            app.build_runner = runner

            app._execute_command("make")

            self.assertEqual(calls, [("make test", root)])
            self.assertEqual(len(app.build_diagnostics), 1)
            self.assertIn("Build failed", app.status)

    def test_command_run_uses_configured_run_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(source), config=EditorConfig(run_command="./demo"))
            calls = []

            def runner(command, cwd):
                calls.append((command, cwd))
                return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

            app.build_runner = runner

            app._execute_command("run")

            self.assertEqual(calls, [("./demo", root)])
            self.assertIn("Run OK", app.status)

    def test_command_errors_lists_build_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(source))
            app._set_build_output("main.c:3:2: warning: check this\n", 0)

            app._execute_command("errors")

        self.assertEqual(app.mode, "LIST")
        self.assertIn("main.c:3:2 warning: check this", "\n".join(app.list_lines))

    def test_diagnostic_navigation_includes_build_errors_and_opens_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.c"
            source.write_text("int main(void) {\nreturn 0;\n}\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(source))
            app._set_build_output("main.c:2:1: error: expected indent\n", 1)

            self.assertTrue(app._goto_next_diagnostic(1))

            self.assertEqual(app.path, source)
            self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (1, 0))
            self.assertIn("expected indent", app.status)

    def test_jump_to_definition_and_back_with_project_symbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "helper.c"
            main = root / "main.c"
            helper.write_text("int project_add(int a, int b) {\n    return a + b;\n}\n", encoding="utf-8")
            main.write_text("int main(void) {\n    return project_add(1, 2);\n}\n", encoding="utf-8")
            app = EditorApp(stdscr=None, path=str(main))
            app.buffer.cursor_row = 1
            app.buffer.cursor_col = len("    return project_add")

            self.assertTrue(app._jump_to_definition())
            self.assertEqual(app.path, helper)
            self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (0, 4))

            self.assertTrue(app._jump_back())
            self.assertEqual(app.path, main)
            self.assertEqual((app.buffer.cursor_row, app.buffer.cursor_col), (1, len("    return project_add")))

    def test_diagnostic_navigation_wraps_forward_and_backward(self):
        app = EditorApp(stdscr=None, path=None)
        app.buffer.lines = ["int a = 1", "int b = 2"]

        self.assertTrue(app._goto_next_diagnostic(1))
        self.assertEqual(app.buffer.cursor_row, 0)

        self.assertTrue(app._goto_next_diagnostic(1))
        self.assertEqual(app.buffer.cursor_row, 1)

        self.assertTrue(app._goto_next_diagnostic(-1))
        self.assertEqual(app.buffer.cursor_row, 0)


class DisplayWidthTests(unittest.TestCase):
    def test_display_width_counts_cjk_characters_as_two_columns(self):
        self.assertEqual(display_width('printf("这是一个测试C程序\\n");'), 30)

    def test_display_width_counts_ascii_as_one_column(self):
        self.assertEqual(display_width('printf("Hello, World!\\n");'), 26)


class CliTests(unittest.TestCase):
    def test_main_prints_version_with_long_flag(self):
        output = io.StringIO()

        with redirect_stdout(output):
            result = sfe.main(["--version"])

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue().strip(), f"sfe {sfe.read_version()}")

    def test_main_prints_version_with_short_flag(self):
        output = io.StringIO()

        with redirect_stdout(output):
            result = sfe.main(["-v"])

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue().strip(), f"sfe {sfe.read_version()}")

    def test_main_reexecs_bundled_python_when_curses_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "python" / "bin" / "python3"
            native = root / "python" / "lib" / "native"
            terminfo = root / "python" / "share" / "terminfo"
            bundled.parent.mkdir(parents=True)
            native.mkdir(parents=True)
            terminfo.mkdir(parents=True)
            bundled.write_text("#!/bin/sh\n", encoding="utf-8")
            original_curses = sfe.curses
            sfe.curses = None
            captured = {}

            def fake_execve(executable, argv, env):
                captured["executable"] = executable
                captured["argv"] = argv
                captured["env"] = env
                raise SystemExit(99)

            with (
                mock.patch.object(os, "execve", side_effect=fake_execve),
                mock.patch.dict(
                    os.environ,
                    {"SFE_HOME": str(root), "SFE_SYSTEM_PYTHON": "/usr/bin/python3"},
                    clear=False,
                ),
            ):
                try:
                    with self.assertRaises(SystemExit) as raised:
                        sfe.main(["hello.c"])
                    self.assertEqual(raised.exception.code, 99)
                finally:
                    sfe.curses = original_curses

        self.assertEqual(captured["executable"], str(bundled))
        self.assertEqual(captured["argv"][:3], [str(bundled), "-B", "-S"])
        self.assertEqual(captured["argv"][-1], "hello.c")
        self.assertIn(str(native), captured["env"]["LD_LIBRARY_PATH"])
        self.assertIn(str(terminfo), captured["env"]["TERMINFO_DIRS"])


if __name__ == "__main__":
    unittest.main()
