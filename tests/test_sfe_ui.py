import io
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout

import sfe
from sfe import EditorApp, EditorConfig, display_width


class FakeCurses:
    error = RuntimeError
    A_DIM = 1
    A_NORMAL = 0
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

    def addnstr(self, row, col, text, max_width, attr=0):
        self.calls.append((row, col, text[:max_width], attr))


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

    def test_auto_pair_placeholder_draws_dimmed_closer(self):
        screen = FakeDrawScreen()
        app = EditorApp(stdscr=screen, path=None)
        app.syntax_attrs = dict(self.original_colors)
        app.mode = "INSERT"

        app._handle_insert_key("(")
        app._draw_code_line(0, 0, app.buffer.current_line(), 20)

        self.assertIn(("(", 0), [(call[2], call[3]) for call in screen.calls])
        self.assertIn((")", FakeCurses.A_DIM), [(call[2], call[3]) for call in screen.calls])


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


if __name__ == "__main__":
    unittest.main()
