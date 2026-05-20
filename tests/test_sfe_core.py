import unittest

from sfe_core import CompletionEngine, TextBuffer, VimCommandProcessor


class TextBufferTests(unittest.TestCase):
    def test_insert_newline_and_backspace_merge_lines(self):
        buf = TextBuffer(["int main() {", "return 0;"])
        buf.cursor_row = 0
        buf.cursor_col = 3

        buf.newline()

        self.assertEqual(buf.lines, ["int", " main() {", "return 0;"])
        self.assertEqual((buf.cursor_row, buf.cursor_col), (1, 0))

        buf.backspace()

        self.assertEqual(buf.lines, ["int main() {", "return 0;"])
        self.assertEqual((buf.cursor_row, buf.cursor_col), (0, 3))

    def test_movement_clamps_to_line_lengths(self):
        buf = TextBuffer(["abcdef", "xy"])
        buf.cursor_row = 0
        buf.cursor_col = 6

        buf.move_down()

        self.assertEqual((buf.cursor_row, buf.cursor_col), (1, 2))

    def test_current_prefix_and_replacement(self):
        buf = TextBuffer(["printf(pr)"])
        buf.cursor_row = 0
        buf.cursor_col = 9

        self.assertEqual(buf.current_prefix(), "pr")

        buf.replace_current_prefix("print_total")

        self.assertEqual(buf.lines, ["printf(print_total)"])
        self.assertEqual(buf.cursor_col, len("printf(print_total"))


class CompletionEngineTests(unittest.TestCase):
    def test_c_completion_uses_keywords_stdlib_and_buffer_identifiers(self):
        engine = CompletionEngine()
        lines = ["int print_total = 0;", "void prepare_value(void);"]

        names = [item.text for item in engine.suggest("pr", lines, 0, 2)]

        self.assertIn("printf", names)
        self.assertIn("prepare_value", names)
        self.assertIn("print_total", names)

    def test_include_completion_suggests_headers(self):
        engine = CompletionEngine()
        lines = ["#include <st"]

        names = [item.text for item in engine.suggest("st", lines, 0, len(lines[0]))]

        self.assertIn("stdio.h", names)
        self.assertIn("stdlib.h", names)

    def test_empty_prefix_returns_no_suggestions(self):
        engine = CompletionEngine()

        self.assertEqual(engine.suggest("", ["int main(void) {"], 0, 0), [])

    def test_vscode_style_completion_ranks_buffer_then_fuzzy_matches(self):
        engine = CompletionEngine()
        lines = ["int print_total = 0;", "void prepare_total(void);"]

        items = engine.suggest("pt", lines, 0, 2)
        names = [item.text for item in items]

        self.assertEqual(names[0], "print_total")
        self.assertIn("prepare_total", names)
        self.assertIn("printf", names)
        self.assertEqual(items[0].kind, "buffer")


class VimCommandProcessorTests(unittest.TestCase):
    def test_write_command_requests_save(self):
        result = VimCommandProcessor().execute("w", dirty=True)

        self.assertTrue(result.save)
        self.assertFalse(result.quit)
        self.assertEqual(result.message, "written")

    def test_quit_blocks_when_buffer_is_dirty(self):
        result = VimCommandProcessor().execute("q", dirty=True)

        self.assertFalse(result.quit)
        self.assertFalse(result.save)
        self.assertIn("No write since last change", result.message)

    def test_force_quit_allows_dirty_buffer_to_close(self):
        result = VimCommandProcessor().execute("q!", dirty=True)

        self.assertTrue(result.quit)
        self.assertFalse(result.save)

    def test_write_quit_saves_and_exits(self):
        result = VimCommandProcessor().execute("wq", dirty=True)

        self.assertTrue(result.save)
        self.assertTrue(result.quit)

    def test_x_matches_write_quit(self):
        result = VimCommandProcessor().execute("x", dirty=False)

        self.assertTrue(result.save)
        self.assertTrue(result.quit)


if __name__ == "__main__":
    unittest.main()
