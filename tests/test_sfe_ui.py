import unittest

import sfe
from sfe import EditorApp


class FakeCurses:
    KEY_LEFT = 260
    KEY_RIGHT = 261
    KEY_UP = 259
    KEY_DOWN = 258
    KEY_HOME = 262
    KEY_END = 360
    KEY_BACKSPACE = 263
    KEY_DC = 330
    KEY_F0 = 264


class EditorInsertModeTests(unittest.TestCase):
    def setUp(self):
        self.original_curses = sfe.curses
        sfe.curses = FakeCurses

    def tearDown(self):
        sfe.curses = self.original_curses

    def test_insert_mode_indents_when_curses_reports_tab_as_integer(self):
        app = EditorApp(stdscr=None, path=None)
        app.mode = "INSERT"

        app._handle_insert_key(9)

        self.assertEqual(app.buffer.lines, ["    "])
        self.assertEqual(app.buffer.cursor_col, 4)


if __name__ == "__main__":
    unittest.main()
