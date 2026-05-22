#!/usr/bin/env python3
import argparse
import gzip
import io
import os
import importlib.util
import py_compile
import shutil
import stat
import subprocess
import sys
import sysconfig
import tarfile
import time
from pathlib import Path


PACKAGE = "sfe"
DEFAULT_ARCH = "amd64"
MAX_GLIBC_SYMBOL = "2.35"
SUPPORTED_ARCHITECTURES = ("amd64", "arm64")
PYTHON_RUNTIME_TARGET = "./usr/lib/sfe/python"
CORE_SYSTEM_LIB_PREFIXES = (
    "ld-linux",
    "libc.so",
    "libdl.so",
    "libm.so",
    "libpthread.so",
    "libresolv.so",
    "librt.so",
    "libutil.so",
)


def _tar_info(name, mode, size=0, kind="file", mtime=None):
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = int(time.time()) if mtime is None else mtime
    if kind == "dir":
        info.type = tarfile.DIRTYPE
        info.size = 0
    else:
        info.size = size
    return info


def _gzipped_tar(entries, mtime):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for entry in entries:
            if entry[0] == "dir":
                _, name, mode = entry
                tar.addfile(_tar_info(name, mode, kind="dir", mtime=mtime))
                continue

            _, name, source, mode = entry
            data = source.read_bytes() if isinstance(source, Path) else source
            tar.addfile(_tar_info(name, mode, len(data), mtime=mtime), io.BytesIO(data))
    return gzip.compress(raw.getvalue(), mtime=mtime)


def _ar_member(name, data, mtime, mode=0o100644):
    encoded_name = (name + "/").encode("ascii")
    if len(encoded_name) > 16:
        raise ValueError(f"ar member name is too long: {name}")

    header = b"".join(
        [
            encoded_name.ljust(16, b" "),
            str(mtime).encode("ascii").ljust(12, b" "),
            b"0".ljust(6, b" "),
            b"0".ljust(6, b" "),
            oct(mode)[2:].encode("ascii").ljust(8, b" "),
            str(len(data)).encode("ascii").ljust(10, b" "),
            b"`\n",
        ]
    )
    return header + data + (b"\n" if len(data) % 2 else b"")


def _read_text_lf(path):
    return path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _read_gzipped_text_lf(path, mtime):
    return gzip.compress(_read_text_lf(path), mtime=mtime)


def _precompile_source(source, cache_name, build_root):
    build_root = Path(build_root)
    target = build_root / cache_name
    target.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(str(source), cfile=str(target), dfile=f"/usr/lib/sfe/{Path(source).name}", doraise=True)
    return target.read_bytes()


def _copy_tree(source, destination):
    source = Path(source)
    destination = Path(destination)
    if not source.exists():
        raise FileNotFoundError(source)
    for path in source.rglob("*"):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = path.relative_to(source)
        target = destination / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target, follow_symlinks=True)


def _parse_ldd_paths(output):
    paths = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or "linux-vdso" in line or "statically linked" in line:
            continue
        if "=>" in line:
            candidate = line.split("=>", 1)[1].strip().split(" ", 1)[0]
        else:
            candidate = line.split(" ", 1)[0]
        if candidate.startswith("/") and Path(candidate).exists():
            paths.append(Path(candidate))
    return paths


def _is_core_system_library(path):
    name = path.name
    return any(name.startswith(prefix) for prefix in CORE_SYSTEM_LIB_PREFIXES)


def _ldd_dependencies(path):
    result = subprocess.run(["ldd", str(path)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    return _parse_ldd_paths(result.stdout)


def _version_tuple(version):
    return tuple(int(part) for part in version.split("."))


def _glibc_versions_from_objdump(output):
    versions = set()
    for line in output.splitlines():
        marker = "GLIBC_"
        if marker not in line:
            continue
        for part in line.replace("(", " ").replace(")", " ").split():
            if part.startswith(marker):
                versions.add(part[len(marker) :])
    return versions


def _glibc_versions_for_binary(path):
    result = subprocess.run(["objdump", "-T", str(path)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return set()
    return _glibc_versions_from_objdump(result.stdout)


def check_runtime_glibc_compatibility(runtime_root, max_glibc=MAX_GLIBC_SYMBOL):
    runtime_root = Path(runtime_root)
    max_version = _version_tuple(max_glibc)
    violations = []

    for path in sorted(runtime_root.rglob("*")):
        if not path.is_file() or not (os.access(path, os.X_OK) or ".so" in path.name):
            continue
        too_new = [
            version
            for version in _glibc_versions_for_binary(path)
            if _version_tuple(version) > max_version
        ]
        if too_new:
            rel = path.relative_to(runtime_root).as_posix()
            violations.append(f"{rel}: {', '.join(sorted(too_new, key=_version_tuple))}")

    if violations:
        details = "\n".join(f"  {item}" for item in violations)
        raise RuntimeError(f"bundled runtime requires glibc newer than {max_glibc}:\n{details}")


def _copy_native_dependencies(runtime_root):
    native_dir = runtime_root / "lib" / "native"
    seen = set()
    pending = []

    for path in runtime_root.rglob("*"):
        if path.is_file() and (os.access(path, os.X_OK) or ".so" in path.name):
            pending.extend(_ldd_dependencies(path))

    while pending:
        dep = pending.pop().resolve()
        if dep in seen or _is_core_system_library(dep):
            continue
        seen.add(dep)
        target = native_dir / dep.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(dep, target, follow_symlinks=True)
        pending.extend(_ldd_dependencies(target))


def _copy_terminfo(destination):
    target = destination / "share" / "terminfo"
    for source in (Path("/usr/share/terminfo"), Path("/lib/terminfo")):
        if source.exists():
            _copy_tree(source, target)
            return


def collect_python_runtime(destination):
    if os.name != "posix":
        raise RuntimeError("bundled Python runtime collection must run on Linux; pass --python-runtime for tests")

    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)

    version_dir = f"python{sys.version_info.major}.{sys.version_info.minor}"
    stdlib = Path(sysconfig.get_paths()["stdlib"]).resolve()
    platstdlib = Path(sysconfig.get_paths().get("platstdlib", stdlib)).resolve()
    python_exe = Path(sys.executable).resolve()

    (destination / "bin").mkdir(parents=True)
    (destination / "lib").mkdir(parents=True)
    shutil.copy2(python_exe, destination / "bin" / "python3", follow_symlinks=True)
    os.chmod(destination / "bin" / "python3", 0o755)
    _copy_tree(stdlib, destination / "lib" / version_dir)
    if platstdlib != stdlib and platstdlib.exists():
        _copy_tree(platstdlib, destination / "lib" / version_dir)
    _copy_native_dependencies(destination)
    _copy_terminfo(destination)
    return destination


def _runtime_entries(runtime_root):
    runtime_root = Path(runtime_root)
    if not (runtime_root / "bin" / "python3").exists():
        raise FileNotFoundError(f"missing bundled Python executable: {runtime_root / 'bin' / 'python3'}")

    entries = []
    directories = set()
    for path in sorted(runtime_root.rglob("*")):
        rel = path.relative_to(runtime_root).as_posix()
        target = f"{PYTHON_RUNTIME_TARGET}/{rel}"
        if path.is_dir():
            directories.add(target)
            continue
        if path.is_file():
            parent = Path(target).parent.as_posix()
            while parent and parent not in (".", "/"):
                directories.add(parent)
                if parent == PYTHON_RUNTIME_TARGET:
                    break
                parent = Path(parent).parent.as_posix()
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode == 0:
                mode = 0o755 if os.access(path, os.X_OK) else 0o644
            entries.append(("file", target, path, mode))

    dir_entries = [("dir", directory, 0o755) for directory in sorted(directories)]
    return dir_entries + entries


def build_package(root, build_root=None, dist_dir=None, python_runtime=None, architecture=DEFAULT_ARCH):
    if architecture not in SUPPORTED_ARCHITECTURES:
        supported = ", ".join(SUPPORTED_ARCHITECTURES)
        raise ValueError(f"unsupported architecture: {architecture}; expected one of: {supported}")

    root = Path(root)
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    build_root = Path(build_root) if build_root else root / "build" / "deb"
    dist_dir = Path(dist_dir) if dist_dir else root / "dist"
    pkg_root = build_root / f"{PACKAGE}_{version}_{architecture}"
    deb_path = dist_dir / f"{PACKAGE}_{version}_{architecture}.deb"
    mtime = int(time.time())

    if build_root.exists():
        shutil.rmtree(build_root)
    dist_dir.mkdir(parents=True, exist_ok=True)
    pkg_root.mkdir(parents=True, exist_ok=True)
    if python_runtime is None:
        python_runtime = collect_python_runtime(build_root / "python-runtime")

    control_text = (
        (root / "packaging" / "debian" / "control")
        .read_text(encoding="utf-8")
        .replace("@VERSION@", version)
        .replace("@ARCHITECTURE@", architecture)
    )
    control_tar = _gzipped_tar(
        [("file", "./control", control_text.encode("utf-8"), 0o644)],
        mtime,
    )

    data_tar = _gzipped_tar(
        [
            ("dir", "./etc", 0o755),
            ("dir", "./etc/sfe", 0o755),
            ("dir", "./usr", 0o755),
            ("dir", "./usr/bin", 0o755),
            ("dir", "./usr/lib", 0o755),
            ("dir", "./usr/lib/sfe", 0o755),
            ("dir", PYTHON_RUNTIME_TARGET, 0o755),
            ("dir", "./usr/share", 0o755),
            ("dir", "./usr/share/doc", 0o755),
            ("dir", "./usr/share/doc/sfe", 0o755),
            ("dir", "./usr/share/man", 0o755),
            ("dir", "./usr/share/man/man1", 0o755),
            ("file", "./etc/sfe/config.json", _read_text_lf(root / "packaging" / "debian" / "config.json"), 0o644),
            ("file", "./usr/bin/sfe", _read_text_lf(root / "packaging" / "debian" / "sfe"), 0o755),
            ("file", "./usr/bin/sfe-upgrade", _read_text_lf(root / "packaging" / "debian" / "sfe-upgrade"), 0o755),
            ("file", "./usr/lib/sfe/VERSION", _read_text_lf(root / "VERSION"), 0o644),
            ("file", "./usr/lib/sfe/sfe.py", root / "sfe.py", 0o644),
            ("file", "./usr/lib/sfe/sfe_core.py", root / "sfe_core.py", 0o644),
            (
                "file",
                f"./usr/lib/sfe/__pycache__/{Path(importlib.util.cache_from_source('sfe.py')).name}",
                _precompile_source(root / "sfe.py", Path(importlib.util.cache_from_source("sfe.py")).name, build_root / "pycache"),
                0o644,
            ),
            (
                "file",
                f"./usr/lib/sfe/__pycache__/{Path(importlib.util.cache_from_source('sfe_core.py')).name}",
                _precompile_source(
                    root / "sfe_core.py",
                    Path(importlib.util.cache_from_source("sfe_core.py")).name,
                    build_root / "pycache",
                ),
                0o644,
            ),
            ("file", "./usr/share/doc/sfe/README.md", root / "README.md", 0o644),
            (
                "file",
                "./usr/share/man/man1/sfe.1.gz",
                _read_gzipped_text_lf(root / "packaging" / "debian" / "sfe.1", mtime),
                0o644,
            ),
        ]
        + _runtime_entries(python_runtime),
        mtime,
    )

    deb = (
        b"!<arch>\n"
        + _ar_member("debian-binary", b"2.0\n", mtime)
        + _ar_member("control.tar.gz", control_tar, mtime)
        + _ar_member("data.tar.gz", data_tar, mtime)
    )
    deb_path.write_bytes(deb)
    return deb_path


def main():
    parser = argparse.ArgumentParser(description="Build the sfe Debian package.")
    parser.add_argument("--root", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument(
        "--python-runtime",
        type=Path,
        help="Use an existing bundled Python runtime directory instead of collecting one from this Linux host.",
    )
    parser.add_argument(
        "--architecture",
        choices=SUPPORTED_ARCHITECTURES,
        default=DEFAULT_ARCH,
        help="Debian architecture to write into the package metadata and file name.",
    )
    parser.add_argument(
        "--check-glibc",
        action="store_true",
        help="Check the bundled Python runtime for glibc symbol compatibility after building.",
    )
    parser.add_argument(
        "--max-glibc",
        default=MAX_GLIBC_SYMBOL,
        help="Maximum allowed GLIBC symbol version when --check-glibc is used.",
    )
    args = parser.parse_args()

    deb_path = build_package(args.root, python_runtime=args.python_runtime, architecture=args.architecture)
    if args.check_glibc:
        runtime_root = args.python_runtime or Path(args.root) / "build" / "deb" / "python-runtime"
        check_runtime_glibc_compatibility(runtime_root, args.max_glibc)
    print(deb_path)


if __name__ == "__main__":
    main()
