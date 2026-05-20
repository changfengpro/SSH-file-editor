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
                json.dumps({"auto_pair": False, "completion_key": "ctrl-j"}),
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertFalse(config.auto_pair)
        self.assertEqual(config.completion_key, "ctrl-j")

    def test_load_config_ignores_invalid_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"auto_pair": "no", "completion_key": ""}),
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertEqual(config, EditorConfig())


if __name__ == "__main__":
    unittest.main()
