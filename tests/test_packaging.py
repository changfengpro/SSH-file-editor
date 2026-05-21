import gzip
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.build_deb import build_package


ROOT = Path(__file__).resolve().parents[1]


def read_deb_member(path, archive_name, member_name):
    data = Path(path).read_bytes()
    if not data.startswith(b"!<arch>\n"):
        raise AssertionError("not a Debian ar archive")

    pos = 8
    members = {}
    while pos < len(data):
        header = data[pos : pos + 60]
        name = header[:16].decode("ascii").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        pos += 60
        members[name] = data[pos : pos + size]
        pos += size + (size % 2)

    archive = gzip.decompress(members[archive_name])
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        return tar.extractfile(member_name).read()


class PackagingTests(unittest.TestCase):
    def test_launcher_template_uses_unix_line_endings(self):
        launcher = (ROOT / "packaging" / "debian" / "sfe").read_bytes()

        self.assertTrue(launcher.startswith(b"#!/bin/sh\n"))
        self.assertNotIn(b"\r\n", launcher)

    def test_built_deb_launcher_uses_unix_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            deb_path = build_package(
                ROOT,
                build_root=tmp_path / "build",
                dist_dir=tmp_path / "dist",
            )
            launcher = read_deb_member(deb_path, "data.tar.gz", "./usr/bin/sfe")

        self.assertTrue(launcher.startswith(b"#!/bin/sh\n"))
        self.assertNotIn(b"\r\n", launcher)

    def test_built_deb_contains_config_man_page_and_upgrade_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            deb_path = build_package(
                ROOT,
                build_root=tmp_path / "build",
                dist_dir=tmp_path / "dist",
            )
            config = read_deb_member(deb_path, "data.tar.gz", "./etc/sfe/config.json")
            man_page = read_deb_member(deb_path, "data.tar.gz", "./usr/share/man/man1/sfe.1.gz")
            upgrade = read_deb_member(deb_path, "data.tar.gz", "./usr/bin/sfe-upgrade")

        self.assertIn(b'"indent_width": 4', config)
        self.assertTrue(gzip.decompress(man_page).startswith(b".TH SFE 1"))
        self.assertTrue(upgrade.startswith(b"#!/bin/sh\n"))
        self.assertNotIn(b"\r\n", upgrade)


if __name__ == "__main__":
    unittest.main()
