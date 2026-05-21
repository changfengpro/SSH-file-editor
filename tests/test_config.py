import json
import tempfile
import unittest
from pathlib import Path

from sfe import EditorApp, EditorConfig, load_config


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
                json.dumps({"completion_key": "ctrl-g", "show_line_numbers": True}),
                encoding="utf-8",
            )

            config = load_config(user_path=user_path, system_path=system_path)

        self.assertEqual(config.indent_width, 2)
        self.assertTrue(config.show_line_numbers)
        self.assertEqual(config.completion_key, "ctrl-g")

    def test_load_config_ignores_invalid_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"auto_pair": "no", "completion_key": ""}),
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
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(user_path=path, system_path=Path("missing-system-config.json"))

        self.assertEqual(config.indent_width, 4)
        self.assertTrue(config.show_line_numbers)
        self.assertTrue(config.scan_local_headers)
        self.assertTrue(config.signature_help)


if __name__ == "__main__":
    unittest.main()
