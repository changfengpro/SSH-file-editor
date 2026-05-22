import gzip
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.build_deb import _glibc_versions_from_objdump, _version_tuple, build_package


ROOT = Path(__file__).resolve().parents[1]


def make_fake_python_runtime(tmp_path):
    runtime = tmp_path / "python-runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "lib" / "python3.10").mkdir(parents=True)
    (runtime / "share" / "terminfo" / "x").mkdir(parents=True)
    (runtime / "bin" / "python3").write_bytes(b"#!/bin/sh\n")
    (runtime / "lib" / "python3.10" / "os.py").write_bytes(b"# fake stdlib\n")
    (runtime / "share" / "terminfo" / "x" / "xterm-256color").write_bytes(b"fake terminfo\n")
    return runtime


def build_test_package(tmp_path, architecture="amd64"):
    return build_package(
        ROOT,
        build_root=tmp_path / "build",
        dist_dir=tmp_path / "dist",
        python_runtime=make_fake_python_runtime(tmp_path),
        architecture=architecture,
    )


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
            deb_path = build_test_package(tmp_path)
            launcher = read_deb_member(deb_path, "data.tar.gz", "./usr/bin/sfe")

        self.assertTrue(launcher.startswith(b"#!/bin/sh\n"))
        self.assertNotIn(b"\r\n", launcher)

    def test_built_deb_declares_no_apt_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            deb_path = build_test_package(tmp_path)
            control = read_deb_member(deb_path, "control.tar.gz", "./control").decode("utf-8")

        self.assertNotRegex(control, r"(?m)^Depends:")
        self.assertNotIn("python3", control)
        self.assertIn("Architecture: amd64", control)

    def test_built_deb_name_uses_requested_architecture(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            deb_path = build_test_package(tmp_path, architecture="arm64")
            control = read_deb_member(deb_path, "control.tar.gz", "./control").decode("utf-8")

        self.assertEqual(deb_path.name, f"sfe_{(ROOT / 'VERSION').read_text(encoding='utf-8').strip()}_arm64.deb")
        self.assertIn("Architecture: arm64", control)

    def test_built_deb_contains_bundled_python_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            deb_path = build_test_package(tmp_path)
            python = read_deb_member(deb_path, "data.tar.gz", "./usr/lib/sfe/python/bin/python3")
            stdlib = read_deb_member(deb_path, "data.tar.gz", "./usr/lib/sfe/python/lib/python3.10/os.py")
            terminfo = read_deb_member(
                deb_path,
                "data.tar.gz",
                "./usr/lib/sfe/python/share/terminfo/x/xterm-256color",
            )

        self.assertTrue(python.startswith(b"#!/bin/sh\n"))
        self.assertEqual(stdlib, b"# fake stdlib\n")
        self.assertEqual(terminfo, b"fake terminfo\n")

    def test_launcher_prefers_system_python_with_bundled_fallback(self):
        launcher = (ROOT / "packaging" / "debian" / "sfe").read_text(encoding="utf-8")

        self.assertIn('SFE_SYSTEM_PYTHON="${SFE_SYSTEM_PYTHON:-/usr/bin/python3}"', launcher)
        self.assertIn('SFE_BUNDLED_PYTHON="${SFE_BUNDLED_PYTHON:-$SFE_HOME/python/bin/python3}"', launcher)
        self.assertIn('[ ! -x "$SFE_SYSTEM_PYTHON" ]', launcher)
        self.assertIn('SFE_PREFER_BUNDLED_PYTHON', launcher)
        self.assertIn("TERMINFO_DIRS", launcher)
        self.assertIn('exec "$SFE_SELECTED_PYTHON" -B -S "$SFE_HOME/sfe.py" "$@"', launcher)

    def test_launcher_only_adds_bundled_native_libs_for_bundled_python(self):
        launcher = (ROOT / "packaging" / "debian" / "sfe").read_text(encoding="utf-8")

        self.assertIn('SFE_USES_BUNDLED_PYTHON=1', launcher)
        self.assertIn('if [ "$SFE_USES_BUNDLED_PYTHON" = "1" ] && [ -d "$SFE_NATIVE_LIB" ]; then', launcher)

    def test_built_deb_contains_config_man_page_and_upgrade_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            deb_path = build_test_package(tmp_path)
            config = read_deb_member(deb_path, "data.tar.gz", "./etc/sfe/config.json")
            man_page = read_deb_member(deb_path, "data.tar.gz", "./usr/share/man/man1/sfe.1.gz")
            upgrade = read_deb_member(deb_path, "data.tar.gz", "./usr/bin/sfe-upgrade")
            version = read_deb_member(deb_path, "data.tar.gz", "./usr/lib/sfe/VERSION")

        self.assertIn(b'"indent_width": 4', config)
        self.assertIn(b'"build_command": ""', config)
        self.assertIn(b'"run_command": ""', config)
        self.assertIn(b'"project_root_markers"', config)
        self.assertIn(b'"recent_files_limit": 20', config)
        self.assertTrue(gzip.decompress(man_page).startswith(b".TH SFE 1"))
        self.assertTrue(upgrade.startswith(b"#!/bin/sh\n"))
        self.assertNotIn(b"\r\n", upgrade)
        self.assertEqual(version.decode("utf-8").strip(), (ROOT / "VERSION").read_text(encoding="utf-8").strip())

    def test_upgrade_script_sends_github_headers_and_user_agent(self):
        upgrade = (ROOT / "packaging" / "debian" / "sfe-upgrade").read_text(encoding="utf-8")

        self.assertIn("SFE_CURL_USER_AGENT", upgrade)
        self.assertIn("SFE_LATEST_DEB_URL", upgrade)
        self.assertIn("SFE_ARCH", upgrade)
        self.assertIn("releases/latest/download/sfe_latest_${SFE_ARCH}.deb", upgrade)
        self.assertIn("User-Agent: $SFE_CURL_USER_AGENT", upgrade)
        self.assertIn("Accept: application/vnd.github+json", upgrade)
        self.assertIn("X-GitHub-Api-Version: 2022-11-28", upgrade)

    def test_upgrade_script_installs_downloaded_package_without_apt_dependency_resolution(self):
        upgrade = (ROOT / "packaging" / "debian" / "sfe-upgrade").read_text(encoding="utf-8")

        self.assertIn("command -v dpkg", upgrade)
        self.assertIn("sudo dpkg -i", upgrade)
        self.assertNotIn("apt install", upgrade)

    def test_upgrade_script_uses_bundled_python_for_api_fallback(self):
        upgrade = (ROOT / "packaging" / "debian" / "sfe-upgrade").read_text(encoding="utf-8")

        self.assertIn('SFE_PYTHON="${SFE_PYTHON:-/usr/lib/sfe/python/bin/python3}"', upgrade)
        self.assertIn('"$SFE_PYTHON" - "$release_json"', upgrade)

    def test_upgrade_script_selects_supported_host_architecture(self):
        upgrade = (ROOT / "packaging" / "debian" / "sfe-upgrade").read_text(encoding="utf-8")

        self.assertIn("dpkg --print-architecture", upgrade)
        self.assertIn("amd64|arm64", upgrade)
        self.assertIn("sfe_latest_${SFE_ARCH}.deb", upgrade)
        self.assertIn('name = f"sfe_latest_{arch}.deb"', upgrade)

    def test_release_workflow_builds_and_publishes_amd64_and_arm64_packages(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

        self.assertIn("arch: amd64", workflow)
        self.assertIn("runner: ubuntu-22.04", workflow)
        self.assertIn("arch: arm64", workflow)
        self.assertIn("runner: ubuntu-22.04-arm", workflow)
        self.assertIn("--architecture \"${{ matrix.arch }}\"", workflow)
        self.assertIn("sfe_${{ steps.version.outputs.version }}_amd64.deb", workflow)
        self.assertIn("sfe_${{ steps.version.outputs.version }}_arm64.deb", workflow)
        self.assertIn("sfe_latest_arm64.deb", workflow)

    def test_release_workflow_checks_glibc_compatibility(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

        self.assertIn("--max-glibc 2.35", workflow)
        self.assertIn("Check runtime glibc compatibility", workflow)

    def test_build_script_can_check_runtime_glibc_version(self):
        build_script = (ROOT / "scripts" / "build_deb.py").read_text(encoding="utf-8")

        self.assertIn("MAX_GLIBC_SYMBOL", build_script)
        self.assertIn("check_runtime_glibc_compatibility", build_script)
        self.assertIn("--max-glibc", build_script)

    def test_glibc_symbol_parser_detects_versions_newer_than_ubuntu_2204(self):
        versions = _glibc_versions_from_objdump(
            """
            0000000000000000      DF *UND*  0000000000000000 (GLIBC_2.34) memcpy
            0000000000000000      DF *UND*  0000000000000000 (GLIBC_2.38) __isoc23_strtol
            """
        )
        too_new = [version for version in versions if _version_tuple(version) > _version_tuple("2.35")]

        self.assertEqual(too_new, ["2.38"])


if __name__ == "__main__":
    unittest.main()
