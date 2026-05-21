import unittest

import sfe
from sfe import EditorApp, EditorConfig, display_width


class FakeCurses:
    error = RuntimeError
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

    def test_insert_mode_auto_closes_parentheses_brackets_and_quotes(self):
        cases = [("(", "()"), ("[", "[]"), ('"', '""'), ("'", "''")]
        for opener, expected in cases:
            with self.subTest(opener=opener):
                app = EditorApp(stdscr=None, path=None)
                app.mode = "INSERT"

                app._handle_insert_key(opener)

                self.assertEqual(app.buffer.lines, [expected])
                self.assertEqual(app.buffer.cursor_col, 1)

    def test_insert_mode_respects_disabled_auto_pair_config(self):
        app = EditorApp(stdscr=None, path=None, config=EditorConfig(auto_pair=False))
        app.mode = "INSERT"

        app._handle_insert_key("{")

        self.assertEqual(app.buffer.lines, ["{"])
        self.assertEqual(app.buffer.cursor_col, 1)


class EditorLayoutTests(unittest.TestCase):
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


class DisplayWidthTests(unittest.TestCase):
    def test_display_width_counts_cjk_characters_as_two_columns(self):
        self.assertEqual(display_width('printf("这是一个测试C程序\\n");'), 30)

    def test_display_width_counts_ascii_as_one_column(self):
        self.assertEqual(display_width('printf("Hello, World!\\n");'), 26)


if __name__ == "__main__":
    unittest.main()
