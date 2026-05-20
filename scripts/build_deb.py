#!/usr/bin/env python3
import argparse
import gzip
import io
import shutil
import tarfile
import time
from pathlib import Path


PACKAGE = "sfe"


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


def build_package(root, build_root=None, dist_dir=None):
    root = Path(root)
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    build_root = Path(build_root) if build_root else root / "build" / "deb"
    dist_dir = Path(dist_dir) if dist_dir else root / "dist"
    pkg_root = build_root / f"{PACKAGE}_{version}_all"
    deb_path = dist_dir / f"{PACKAGE}_{version}_all.deb"
    mtime = int(time.time())

    if build_root.exists():
        shutil.rmtree(build_root)
    dist_dir.mkdir(parents=True, exist_ok=True)
    pkg_root.mkdir(parents=True, exist_ok=True)

    control_text = (
        (root / "packaging" / "debian" / "control")
        .read_text(encoding="utf-8")
        .replace("@VERSION@", version)
    )
    control_tar = _gzipped_tar(
        [("file", "./control", control_text.encode("utf-8"), 0o644)],
        mtime,
    )

    data_tar = _gzipped_tar(
        [
            ("dir", "./usr", 0o755),
            ("dir", "./usr/bin", 0o755),
            ("dir", "./usr/lib", 0o755),
            ("dir", "./usr/lib/sfe", 0o755),
            ("dir", "./usr/share", 0o755),
            ("dir", "./usr/share/doc", 0o755),
            ("dir", "./usr/share/doc/sfe", 0o755),
            ("file", "./usr/bin/sfe", _read_text_lf(root / "packaging" / "debian" / "sfe"), 0o755),
            ("file", "./usr/lib/sfe/sfe.py", root / "sfe.py", 0o644),
            ("file", "./usr/lib/sfe/sfe_core.py", root / "sfe_core.py", 0o644),
            ("file", "./usr/share/doc/sfe/README.md", root / "README.md", 0o644),
        ],
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
    args = parser.parse_args()

    print(build_package(args.root))


if __name__ == "__main__":
    main()
