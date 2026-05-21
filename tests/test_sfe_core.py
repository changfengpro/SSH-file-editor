import tempfile
import unittest
from pathlib import Path

from sfe_core import (
    BuildCommandResolver,
    BuildOutputParser,
    CDiagnosticEngine,
    CompletionEngine,
    CProjectSymbol,
    HeaderIndex,
    HeaderScanner,
    ProjectFileScanner,
    ProjectScanner,
    RecentFilesStore,
    SignatureHelpEngine,
    SyntaxHighlighter,
    TextBuffer,
    UndoManager,
    VimCommandProcessor,
    find_project_root,
    fuzzy_match_files,
)


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

    def test_indent_inserts_four_spaces_at_cursor(self):
        buf = TextBuffer(["int main(void) {"])
        buf.cursor_row = 0
        buf.cursor_col = 0

        buf.indent()

        self.assertEqual(buf.lines, ["    int main(void) {"])
        self.assertEqual(buf.cursor_col, 4)

    def test_newline_preserves_indent_and_adds_one_level_after_open_brace(self):
        buf = TextBuffer(["    if (ok) {"])
        buf.cursor_col = len(buf.current_line())

        buf.newline_with_indent(indent_width=4)

        self.assertEqual(buf.lines, ["    if (ok) {", "        "])
        self.assertEqual((buf.cursor_row, buf.cursor_col), (1, 8))

    def test_newline_between_brace_pair_aligns_closing_brace_with_opener(self):
        buf = TextBuffer(["    if (ok) {}"])
        buf.cursor_col = len("    if (ok) {")

        buf.newline_with_indent(indent_width=4)

        self.assertEqual(buf.lines, ["    if (ok) {", "        ", "    }"])
        self.assertEqual((buf.cursor_row, buf.cursor_col), (1, 8))

    def test_newline_between_parentheses_pair_aligns_closer_with_opener(self):
        buf = TextBuffer(["    call()"])
        buf.cursor_col = len("    call(")

        buf.newline_with_indent(indent_width=4)

        self.assertEqual(buf.lines, ["    call(", "        ", "    )"])
        self.assertEqual((buf.cursor_row, buf.cursor_col), (1, 8))

    def test_newline_between_brackets_pair_aligns_closer_with_opener(self):
        buf = TextBuffer(["    items[]"])
        buf.cursor_col = len("    items[")

        buf.newline_with_indent(indent_width=2)

        self.assertEqual(buf.lines, ["    items[", "      ", "    ]"])
        self.assertEqual((buf.cursor_row, buf.cursor_col), (1, 6))

    def test_newline_preserves_indent_without_open_brace(self):
        buf = TextBuffer(["    return value;"])
        buf.cursor_col = len(buf.current_line())

        buf.newline_with_indent(indent_width=2)

        self.assertEqual(buf.lines, ["    return value;", "    "])
        self.assertEqual((buf.cursor_row, buf.cursor_col), (1, 4))

    def test_smart_backspace_on_empty_indent_removes_one_indent_level(self):
        buf = TextBuffer(["        value = 1;"])
        buf.cursor_col = 8

        self.assertTrue(buf.backspace_smart(indent_width=4))

        self.assertEqual(buf.lines, ["    value = 1;"])
        self.assertEqual(buf.cursor_col, 4)

    def test_smart_backspace_between_empty_pair_removes_pair(self):
        for line in ["{}", "()", "[]", '""', "''"]:
            with self.subTest(line=line):
                buf = TextBuffer([line])
                buf.cursor_col = 1

                self.assertTrue(buf.backspace_smart(indent_width=4))

                self.assertEqual(buf.lines, [""])
                self.assertEqual(buf.cursor_col, 0)

    def test_smart_backspace_returns_false_when_no_smart_case_matches(self):
        buf = TextBuffer(["abc"])
        buf.cursor_col = 3

        self.assertFalse(buf.backspace_smart(indent_width=4))
        self.assertEqual(buf.lines, ["abc"])
        self.assertEqual(buf.cursor_col, 3)

    def test_align_closing_brace_dedents_current_indent(self):
        buf = TextBuffer(["    }"])
        buf.cursor_col = 5

        self.assertTrue(buf.align_closing_brace(indent_width=4))

        self.assertEqual(buf.lines, ["}"])
        self.assertEqual(buf.cursor_col, 1)

    def test_replace_current_prefix_with_multiline_snippet_places_cursor(self):
        buf = TextBuffer(["ma"])
        buf.cursor_col = 2

        buf.replace_current_prefix_with_snippet("int main(void) {\n    $0\n}")

        self.assertEqual(buf.lines, ["int main(void) {", "    ", "}"])
        self.assertEqual((buf.cursor_row, buf.cursor_col), (1, 4))

    def test_replace_current_prefix_with_multiline_snippet_preserves_current_indent(self):
        buf = TextBuffer(["    if"])
        buf.cursor_col = len("    if")

        buf.replace_current_prefix_with_snippet("if ($0) {\n    \n}")

        self.assertEqual(buf.lines, ["    if () {", "        ", "    }"])
        self.assertEqual((buf.cursor_row, buf.cursor_col), (0, len("    if (")))


class UndoManagerTests(unittest.TestCase):
    def test_undo_and_redo_restore_buffer_snapshot(self):
        buf = TextBuffer(["int"])
        undo = UndoManager()

        undo.record(buf)
        buf.insert(" main")

        self.assertTrue(undo.undo(buf))
        self.assertEqual(buf.lines, ["int"])
        self.assertEqual(buf.cursor_col, 0)

        self.assertTrue(undo.redo(buf))
        self.assertEqual(buf.lines, [" mainint"])
        self.assertEqual(buf.cursor_col, 5)

    def test_new_edit_after_undo_clears_redo_stack(self):
        buf = TextBuffer([""])
        undo = UndoManager()
        undo.record(buf)
        buf.insert("a")
        undo.undo(buf)

        undo.record(buf)
        buf.insert("b")

        self.assertFalse(undo.redo(buf))
        self.assertEqual(buf.lines, ["b"])


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

    def test_exact_prefix_keyword_ranks_before_fuzzy_matches(self):
        engine = CompletionEngine()
        lines = ["int main(void);"]

        items = engine.suggest("in", lines, 0, 2)

        self.assertEqual(items[0].text, "int")
        self.assertEqual(items[0].kind, "keyword")

    def test_completion_ranking_prefers_prefix_keywords_for_common_c_prefixes(self):
        engine = CompletionEngine()
        scenarios = [
            ("ch", "char"),
            ("str", "struct"),
            ("ret", "return"),
            ("uns", "unsigned"),
            ("vo", "void"),
        ]

        for prefix, expected in scenarios:
            with self.subTest(prefix=prefix):
                lines = [
                    "int main(void);",
                    "char string_value[16];",
                    "int return_code;",
                    "void retry(void);",
                    "unsigned value;",
                ]

                items = engine.suggest(prefix, lines, 0, len(prefix))

                self.assertEqual(items[0].text, expected)
                self.assertEqual(items[0].kind, "keyword")

    def test_completion_ranking_keeps_buffer_symbols_before_stdlib_for_fuzzy_matches(self):
        engine = CompletionEngine()
        lines = [
            "int print_total = 0;",
            "void prepare_total(void);",
            "int malloc_count = 0;",
        ]

        pt_names = [item.text for item in engine.suggest("pt", lines, 0, 2)]
        mc_names = [item.text for item in engine.suggest("mc", lines, 0, 2)]

        self.assertLess(pt_names.index("print_total"), pt_names.index("printf"))
        self.assertLess(mc_names.index("malloc_count"), mc_names.index("malloc"))

    def test_completion_ranking_keeps_prefix_buffer_symbols_before_stdlib(self):
        engine = CompletionEngine()
        lines = ["int main(void);", "int malloc_count = 0;"]

        names = [item.text for item in engine.suggest("ma", lines, 0, 2)]

        self.assertEqual(names[:3], ["main", "malloc_count", "malloc"])

    def test_include_completion_ranks_prefix_headers_before_fuzzy_headers(self):
        engine = CompletionEngine()
        lines = ["#include <st"]

        names = [item.text for item in engine.suggest("st", lines, 0, len(lines[0]))]

        self.assertLess(names.index("stdio.h"), names.index("assert.h"))
        self.assertTrue(all(name.endswith(".h") for name in names))

    def test_local_header_symbols_rank_before_stdlib(self):
        engine = CompletionEngine()
        index = HeaderIndex(
            headers={"mathx.h"},
            symbols=[CProjectSymbol("malloc_count", "macro", "mathx.h")],
        )

        items = engine.suggest("ma", ["int main(void);"], 0, 2, header_index=index)

        self.assertEqual(items[0].text, "main")
        self.assertEqual(items[1].text, "malloc_count")
        self.assertLess([item.text for item in items].index("malloc_count"), [item.text for item in items].index("malloc"))

    def test_include_completion_suggests_local_headers_for_quotes(self):
        engine = CompletionEngine()
        index = HeaderIndex(headers={"mathx.h", "model.h"}, symbols=[])
        lines = ['#include "ma']

        names = [item.text for item in engine.suggest("ma", lines, 0, len(lines[0]), header_index=index)]

        self.assertEqual(names[0], "mathx.h")
        self.assertNotIn("stdio.h", names)

    def test_completion_includes_c_snippets_with_insert_text(self):
        engine = CompletionEngine()

        items = engine.suggest("ma", ["ma"], 0, 2)
        item_by_text = {item.text: item for item in items}

        self.assertIn("main", item_by_text)
        self.assertEqual(item_by_text["main"].kind, "snippet")
        self.assertEqual(item_by_text["main"].source, "snippet")
        self.assertIn("$0", item_by_text["main"].insert_text)

    def test_completion_keeps_control_flow_snippets_when_name_matches_keyword(self):
        items = CompletionEngine().suggest("if", ["if"], 0, 2)

        self.assertEqual(items[0].text, "if")
        self.assertEqual(items[0].kind, "snippet")
        self.assertIn("$0", items[0].insert_text)

    def test_completion_uses_project_symbols_with_source_and_location_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.c").write_text("int project_total;\nint project_add(int a, int b) {\n    return a + b;\n}\n", encoding="utf-8")
            project_index = ProjectScanner().scan(root)

        items = CompletionEngine().suggest("project_", ["project_"], 0, 8, project_index=project_index)
        item_by_text = {item.text: item for item in items}

        self.assertEqual(item_by_text["project_add"].kind, "function")
        self.assertEqual(item_by_text["project_add"].source, "project")
        self.assertIn("main.c:2", item_by_text["project_add"].detail)
        self.assertEqual(item_by_text["project_total"].kind, "global")


class ProjectScannerTests(unittest.TestCase):
    def test_scans_c_project_symbols_recursively(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "include").mkdir()
            (root / "include" / "model.h").write_text(
                "\n".join(
                    [
                        "#define LIMIT 8",
                        "typedef unsigned long count_t;",
                        "struct model;",
                        "int model_size(struct model *m);",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "main.c").write_text(
                "\n".join(
                    [
                        '#include "model.h"',
                        "static int total_count;",
                        "int add(int left, int right) {",
                        "    return left + right;",
                        "}",
                    ]
                ),
                encoding="utf-8",
            )

            index = ProjectScanner().scan(root)

        symbols = {symbol.name: symbol for symbol in index.symbols}
        self.assertEqual(symbols["LIMIT"].kind, "macro")
        self.assertEqual(symbols["count_t"].kind, "typedef")
        self.assertEqual(symbols["model"].kind, "struct")
        self.assertEqual(symbols["model_size"].kind, "function")
        self.assertEqual(symbols["add"].row, 2)
        self.assertEqual(symbols["add"].path.name, "main.c")
        self.assertEqual(symbols["total_count"].kind, "global")

    def test_find_definition_prefers_exact_symbol_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.c").write_text("int target_value;\n", encoding="utf-8")
            index = ProjectScanner().scan(root)

        symbol = index.find_definition("target_value")

        self.assertIsNotNone(symbol)
        self.assertEqual(symbol.name, "target_value")


class ProjectFileWorkflowTests(unittest.TestCase):
    def test_find_project_root_walks_up_to_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "src" / "module"
            nested.mkdir(parents=True)
            (root / "Makefile").write_text("all:\n\tcc main.c\n", encoding="utf-8")

            found = find_project_root(nested, ("Makefile", ".git", "compile_commands.json"))

        self.assertEqual(found, root)

    def test_project_file_scanner_ignores_generated_and_vcs_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for directory in ("src", ".git", ".worktrees", ".sfe", "build", "dist", "__pycache__"):
                (root / directory).mkdir()
            (root / "src" / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (root / "README.md").write_text("# demo\n", encoding="utf-8")
            (root / ".sfe" / "recent.json").write_text('["src/main.c"]\n', encoding="utf-8")
            (root / "build" / "main.c").write_text("ignored\n", encoding="utf-8")
            (root / ".git" / "config").write_text("ignored\n", encoding="utf-8")

            files = ProjectFileScanner().scan(root)

        paths = [item.relative_path for item in files.files]
        self.assertEqual(paths, ["README.md", "src/main.c"])

    def test_project_scanners_do_not_ignore_project_inside_worktree_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / ".worktrees"
            root = parent / "feature"
            root.mkdir(parents=True)
            (root / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("ignored\n", encoding="utf-8")

            symbols = ProjectScanner().scan(root).symbols
            files = ProjectFileScanner().scan(root).files

        self.assertEqual([symbol.name for symbol in symbols], ["main"])
        self.assertEqual([file.relative_path for file in files], ["main.c"])

    def test_fuzzy_match_files_prefers_filename_and_contiguous_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative in ("src/main.c", "tests/test_main.c", "src/memory/index.c"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            files = ProjectFileScanner().scan(root)

            matches = fuzzy_match_files("main", files.files)

        self.assertEqual(matches[0].relative_path, "src/main.c")
        self.assertEqual(matches[1].relative_path, "tests/test_main.c")

    def test_recent_files_store_deduplicates_limits_and_persists_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store_path = root / ".sfe" / "recent.json"
            store = RecentFilesStore(store_path, limit=2)

            store.add(root / "src" / "main.c", root)
            store.add(root / "include" / "app.h", root)
            store.add(root / "src" / "main.c", root)

            reloaded = RecentFilesStore(store_path, limit=2)
            entries = reloaded.load()

        self.assertEqual(entries, ["src/main.c", "include/app.h"])

    def test_build_output_parser_extracts_gcc_style_errors_and_warnings(self):
        output = "\n".join(
            [
                "src/main.c:4:12: error: expected ';' before '}' token",
                "include/app.h:2:1: warning: unused declaration",
                "make: *** [Makefile:2: all] Error 1",
            ]
        )

        diagnostics = BuildOutputParser().parse(output)

        self.assertEqual(len(diagnostics), 2)
        self.assertEqual(diagnostics[0].path, Path("src/main.c"))
        self.assertEqual((diagnostics[0].row, diagnostics[0].col), (3, 11))
        self.assertEqual(diagnostics[0].severity, "error")
        self.assertIn("expected", diagnostics[0].message)
        self.assertEqual(diagnostics[1].severity, "warning")

    def test_build_command_resolver_prefers_config_makefile_then_gcc_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src" / "hello.c"
            source.parent.mkdir()
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")

            configured = BuildCommandResolver.resolve("cc custom.c -o custom", source, root)
            self.assertEqual(configured.command, "cc custom.c -o custom")
            self.assertEqual(configured.run_command, "./custom")

            (root / "Makefile").write_text("all:\n\tcc src/hello.c -o hello\n", encoding="utf-8")
            make = BuildCommandResolver.resolve("", source, root)
            self.assertEqual(make.command, "make")
            self.assertEqual(make.run_command, "./hello")

            (root / "Makefile").unlink()
            fallback = BuildCommandResolver.resolve("", source, root)
            self.assertEqual(fallback.command, "gcc src/hello.c -o hello")
            self.assertEqual(fallback.run_command, "./hello")


class CDiagnosticEngineTests(unittest.TestCase):
    def test_reports_unmatched_brackets_unterminated_string_duplicate_include_and_missing_semicolon(self):
        diagnostics = CDiagnosticEngine().analyze(
            [
                "#include <stdio.h>",
                "#include <stdio.h>",
                "int value = 1",
                'printf("oops);',
                "if (value > 0) {",
            ]
        )

        messages = [diagnostic.message for diagnostic in diagnostics]

        self.assertIn("duplicate include: stdio.h", messages)
        self.assertIn("missing semicolon", messages)
        self.assertIn("unterminated string literal", messages)
        self.assertIn("unmatched {", messages)


class HeaderScannerTests(unittest.TestCase):
    def test_scans_current_directory_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mathx.h").write_text(
                "\n".join(
                    [
                        "#define LIMIT 16",
                        "typedef unsigned long size_alias;",
                        "struct item;",
                        "int add(int left, int right);",
                    ]
                ),
                encoding="utf-8",
            )

            index = HeaderScanner().scan(root)

        self.assertIn("mathx.h", index.headers)
        symbols = {symbol.name: symbol for symbol in index.symbols}
        self.assertEqual(symbols["LIMIT"].kind, "macro")
        self.assertEqual(symbols["size_alias"].kind, "typedef")
        self.assertEqual(symbols["item"].kind, "struct")
        self.assertEqual(symbols["add"].kind, "function")
        self.assertEqual(symbols["add"].signature, "int add(int left, int right)")


class SignatureHelpEngineTests(unittest.TestCase):
    def test_returns_builtin_function_signature(self):
        engine = SignatureHelpEngine()
        line = 'printf("'

        self.assertEqual(
            engine.signature_for(line, len(line)),
            "int printf(const char *format, ...)",
        )

    def test_returns_header_function_signature(self):
        index = HeaderIndex(
            headers={"mathx.h"},
            symbols=[CProjectSymbol("add", "function", "mathx.h", "int add(int left, int right)")],
        )
        engine = SignatureHelpEngine.from_header_index(index)
        line = "total = add("

        self.assertEqual(engine.signature_for(line, len(line)), "int add(int left, int right)")


class SyntaxHighlighterTests(unittest.TestCase):
    def test_highlights_c_keywords_and_preprocessor_directives(self):
        highlighter = SyntaxHighlighter()

        tokens = highlighter.tokenize("#define ITEM struct item { int count; }")
        pairs = [(token.text, token.kind) for token in tokens]

        self.assertIn(("#define", "preprocessor"), pairs)
        self.assertIn(("struct", "keyword"), pairs)
        self.assertIn(("int", "keyword"), pairs)

    def test_highlights_strings_numbers_and_comments(self):
        highlighter = SyntaxHighlighter()

        tokens = highlighter.tokenize('printf("value=%d", 42); // show value')
        pairs = [(token.text, token.kind) for token in tokens]

        self.assertIn(('"value=%d"', "string"), pairs)
        self.assertIn(("42", "number"), pairs)
        self.assertIn(("// show value", "comment"), pairs)

    def test_highlights_c_function_names(self):
        highlighter = SyntaxHighlighter()

        tokens = highlighter.tokenize("int main() { return printf(\"ok\"); }")
        pairs = [(token.text, token.kind) for token in tokens]

        self.assertIn(("main", "function"), pairs)
        self.assertIn(("printf", "function"), pairs)


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
