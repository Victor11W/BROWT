"""Verify the exact API bundle in a Python 3.11 Debian container without network."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def extract(archive, target):
    for name in archive.namelist():
        parts = name.replace("\\", "/").split("/")
        if name.startswith(("/", "\\")) or ".." in parts or ":" in name:
            raise ValueError("Invalid archive member")
    archive.extractall(target)


def main():
    with tempfile.TemporaryDirectory(prefix="browt-offline-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile("/bundle/session.zip") as archive:
            extract(archive, root)
        manifest = json.loads((root / "manifest.json").read_text())
        assert (
            hashlib.sha256((root / "source.zip").read_bytes()).hexdigest()
            == manifest["source_sha256"]
        )
        for name, digest in manifest["wheels"].items():
            assert hashlib.sha256((root / "wheels" / name).read_bytes()).hexdigest() == digest
        source = root / "repository"
        with zipfile.ZipFile(root / "source.zip") as archive:
            extract(archive, source)
        env = {**os.environ, "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
        pip = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(root / "wheels"),
        ]
        subprocess.run([*pip, "setuptools", "wheel"], check=True, env=env)
        subprocess.run(
            [*pip, "--no-build-isolation", "-e", ".[research,dev]"], check=True, env=env, cwd=source
        )
        for args in (
            ["ruff", "check", "."],
            ["ruff", "format", "--check", "src", "tests", ".github/autonomy"],
            ["pytest", "-q"],
        ):
            subprocess.run([sys.executable, "-m", *args], check=True, cwd=source, env=env)
        print(
            "The exact bundle installs and passes CPU checks without network on Debian/Python 3.11."
        )


if __name__ == "__main__":
    main()
