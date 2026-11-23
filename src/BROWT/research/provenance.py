"""Small, inspectable experiment records; no external tracking service."""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    """Replace one derived JSON artifact atomically, rejecting NaN/Infinity."""
    content = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def source_snapshot(repo: Path) -> dict:
    candidates = [
        *repo.glob("src/**/*.py"),
        *repo.glob("configs/**/*.yaml"),
        *repo.glob("configs/**/*.json"),
    ]
    candidates += [p for p in (repo / "pyproject.toml", repo / "requirements.txt") if p.is_file()]
    records = [
        {"path": p.relative_to(repo).as_posix(), "sha256": file_hash(p)}
        for p in sorted(set(candidates))
        if p.is_file()
    ]
    return {"sha256": canonical_hash(records), "files": records}


def environment_snapshot(repo: Path) -> dict:
    packages = {}
    for name in (
        "BROWT",
        "numpy",
        "PyYAML",
        "matplotlib",
        "Pillow",
        "torch",
        "ultralytics",
        "gymnasium",
        "stable-baselines3",
        "pytest",
        "ruff",
    ):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None

    def git(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
                **(
                    {"creationflags": subprocess.CREATE_NO_WINDOW}
                    if sys.platform == "win32"
                    else {}
                ),
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    revision = git("rev-parse", "HEAD")
    status = git("status", "--porcelain")
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "analysis_source": source_snapshot(repo),
        "git": {"commit": revision, "dirty": None if status is None else bool(status)},
    }


class Experiment:
    """One unique run directory, an explicit status, and hashes of its artifacts."""

    def __init__(self, output: Path, *, task: str, config: dict, data_kind: str, repo: Path):
        if data_kind not in {"observed", "synthetic"}:
            raise ValueError("data_kind must be observed or synthetic.")
        config = copy.deepcopy(config)
        config_digest = canonical_hash(config)
        now = datetime.now(timezone.utc)
        self.run_id = now.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.folder = output / self.run_id
        self.folder.mkdir(parents=True, exist_ok=False)
        self.manifest = {
            "schema_version": 1,
            "run_id": self.run_id,
            "task": task,
            "data_kind": data_kind,
            "status": "running",
            "created_at": now.isoformat(),
            "config": config,
            "config_sha256": config_digest,
            "environment": environment_snapshot(repo),
            "artifacts": [],
            "metrics": {},
        }
        self._write()

    def _write(self) -> None:
        write_json(self.folder / "manifest.json", self.manifest)

    def complete(self, *, metrics: dict | None = None, artifacts: list[Path] | None = None) -> None:
        records = []
        for artifact in artifacts or []:
            relative = artifact.resolve().relative_to(self.folder.resolve())
            if relative.as_posix() == "manifest.json":
                raise ValueError("The manifest cannot hash itself.")
            records.append(
                {
                    "path": relative.as_posix(),
                    "sha256": file_hash(artifact),
                    "bytes": artifact.stat().st_size,
                }
            )
        self.manifest.update(
            status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            metrics=metrics or {},
            artifacts=records,
        )
        self._write()

    def fail(self, error: BaseException) -> None:
        self.manifest.update(
            status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            error={"type": type(error).__name__, "message": str(error)},
        )
        self._write()
