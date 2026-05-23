import json
import tempfile
import unittest
from pathlib import Path

from sfe import EditorApp, EditorConfig, load_config, normalize_key_name, parse_key_sequence


class EditorConfigTests(unittest.TestCase):
    def test_editor_uses_default_config_when_not_provided(self):
        app = EditorApp(stdscr=None, path=None)

        self.assertEqual(app.config, EditorConfig())

    def test_load_config_uses_defaults_when_file_is_missing(self):
        config = load_config(Path("missing-config.json"))

        self.assertEqual(config, EditorConfig())

    def test_load_config_reads_auto_pair_and_completion_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "auto_pair": False,
                        "completion_key": "ctrl-j",
                        "indent_width": 2,
                        "show_line_numbers": False,
                        "scan_local_headers": False,
                        "signature_help": False,
                        "build_command": "make debug",
                        "run_command": "./debug",
                        "project_root_markers": ["Makefile", ".git"],
                        "recent_files_limit": 12,
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(user_path=path, system_path=Path("missing-system-config.json"))

        self.assertFalse(config.auto_pair)
        self.assertEqual(config.completion_key, "ctrl-j")
        self.assertEqual(config.indent_width, 2)
        self.assertFalse(config.show_line_numbers)
        self.assertFalse(config.scan_local_headers)
        self.assertFalse(config.signature_help)
        self.assertEqual(config.build_command, "make debug")
        self.assertEqual(config.run_command, "./debug")
        self.assertEqual(config.project_root_markers, ("Makefile", ".git"))
        self.assertEqual(config.recent_files_limit, 12)

    def test_load_config_reads_command_keybindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "keybindings": {
                            "f3": "bn",
                            "ctrl-right": "tree",
                            "ctrl-q": "tree",
                            "ctrl-x": "not-real",
                            "bad-key": "bp",
                        }
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(user_path=path, system_path=Path("missing-system-config.json"))

        self.assertEqual(config.keybindings, {"f3": "bn", "ctrl+right": "tree"})

    def test_normalize_key_name_accepts_ctrl_arrow_aliases(self):
        self.assertEqual(normalize_key_name("ctrl-right"), "ctrl+right")
        self.assertEqual(normalize_key_name("control+left"), "ctrl+left")
        self.assertEqual(normalize_key_name("ctrl-up"), "ctrl+up")
        self.assertEqual(normalize_key_name("control-down"), "ctrl+down")
        self.assertEqual(normalize_key_name("f3"), "f3")

    def test_parse_key_sequence_accepts_ctrl_arrow_modifier_matrix(self):
        cases = [
            ("\x1b[1;5A", "ctrl+up"),
            ("\x1b[1;6B", "ctrl+down"),
            ("\x1b[1;7C", "ctrl+right"),
            ("\x1b[1;8D", "ctrl+left"),
            ("\x1b[5A", "ctrl+up"),
            ("\x1b[6B", "ctrl+down"),
            ("\x1b[7C", "ctrl+right"),
            ("\x1b[8D", "ctrl+left"),
            ("\x1bO5A", "ctrl+up"),
            ("\x1bO6B", "ctrl+down"),
            ("\x1bO7C", "ctrl+right"),
            ("\x1bO8D", "ctrl+left"),
        ]
        for sequence, expected in cases:
            with self.subTest(sequence=repr(sequence)):
                self.assertEqual(parse_key_sequence(list(sequence)), expected)

    def test_parse_key_sequence_ignores_non_ctrl_arrow_modifiers(self):
        for sequence in ["\x1b[1;2A", "\x1b[1;3B", "\x1b[1;4C", "\x1b[2D", "\x1b[3A", "\x1b[4B"]:
            with self.subTest(sequence=repr(sequence)):
                self.assertIsNone(parse_key_sequence(list(sequence)))

    def test_load_config_merges_system_then_user_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            system_path = root / "etc" / "config.json"
            user_path = root / "user" / "config.json"
            system_path.parent.mkdir(parents=True)
            user_path.parent.mkdir(parents=True)
            system_path.write_text(
                json.dumps({"indent_width": 2, "show_line_numbers": False}),
                encoding="utf-8",
            )
            user_path.write_text(
                json.dumps({"completion_key": "ctrl-g", "show_line_numbers": True, "build_command": "make local"}),
                encoding="utf-8",
            )

            config = load_config(user_path=user_path, system_path=system_path)

        self.assertEqual(config.indent_width, 2)
        self.assertTrue(config.show_line_numbers)
        self.assertEqual(config.completion_key, "ctrl-g")
        self.assertEqual(config.build_command, "make local")

    def test_load_config_ignores_invalid_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"auto_pair": "no", "completion_key": "", "keybindings": []}),
                encoding="utf-8",
            )

            config = load_config(user_path=path, system_path=Path("missing-system-config.json"))

        self.assertEqual(config, EditorConfig())

    def test_load_config_ignores_invalid_new_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "indent_width": 99,
                        "show_line_numbers": "yes",
                        "scan_local_headers": "no",
                        "signature_help": 1,
                        "build_command": 42,
                        "run_command": False,
                        "project_root_markers": ["", 1],
                        "recent_files_limit": 0,
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(user_path=path, system_path=Path("missing-system-config.json"))

        self.assertEqual(config.indent_width, 4)
        self.assertTrue(config.show_line_numbers)
        self.assertTrue(config.scan_local_headers)
        self.assertTrue(config.signature_help)
        self.assertEqual(config.build_command, "")
        self.assertEqual(config.run_command, "")
        self.assertEqual(config.project_root_markers, EditorConfig().project_root_markers)
        self.assertEqual(config.recent_files_limit, 20)


if __name__ == "__main__":
    unittest.main()
