"""Import one public export without changing its files or replacing a dataset.

Network authentication stays in the browser or a separately configured client.
This module accepts a local ZIP and never requests or stores an API key.
"""

from __future__ import annotations

import re
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import yaml

from BROWT.data.audit import audit_dataset
from BROWT.data.classes import normalize_names
from BROWT.research.provenance import canonical_hash, file_hash, write_json


def load_source_config(path: Path) -> dict:
    source = yaml.safe_load(path.read_text(encoding="utf-8"))
    required = (
        "source_id",
        "workspace",
        "project",
        "version",
        "format",
        "public_url",
        "expected_splits",
        "expected_classes",
        "license",
    )
    if not isinstance(source, dict) or source.get("schema_version") != 1:
        raise ValueError("Expected a version-1 dataset source configuration.")
    if any(key not in source for key in required):
        raise ValueError("Dataset source configuration is incomplete.")
    if (
        not isinstance(source["version"], int)
        or isinstance(source["version"], bool)
        or source["version"] < 1
    ):
        raise ValueError("The dataset version must be a positive integer.")
    splits = source["expected_splits"]
    if (
        not isinstance(splits, dict)
        or not splits
        or any(
            key not in {"train", "valid", "test"}
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            for key, count in splits.items()
        )
    ):
        raise ValueError("expected_splits must map split names to nonnegative image counts.")
    normalize_names(source["expected_classes"])
    return source


def _safe_member(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    parts = normalized.rstrip("/").split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} or ":" in part for part in parts)
        or any(ord(char) < 32 for char in normalized)
    ):
        raise ValueError(f"Unsafe archive member: {name!r}")
    return PurePosixPath(*parts)


def inspect_archive(
    archive: Path,
    *,
    max_files: int = 100_000,
    max_uncompressed_bytes: int = 4 * 1024**3,
) -> dict:
    """Validate names and bounded resource use before extracting any entry."""
    if max_files < 1 or max_uncompressed_bytes < 1:
        raise ValueError("Archive limits must be positive.")
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if not members or len(members) > max_files:
            raise ValueError("Archive is empty or contains too many entries.")
        total = 0
        seen = set()
        for member in members:
            relative = _safe_member(member.filename)
            key = relative.as_posix().casefold()
            if key in seen:
                raise ValueError(f"Duplicate/case-colliding archive member: {member.filename!r}")
            seen.add(key)
            mode = member.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or stat.S_ISLNK(mode):
                raise ValueError("Archive links and special files are not supported.")
            if member.flag_bits & 1:
                raise ValueError("Encrypted dataset exports are not supported.")
            total += member.file_size
            if total > max_uncompressed_bytes:
                raise ValueError("Archive exceeds the uncompressed-size limit.")
        return {"entries": len(members), "uncompressed_bytes": total}


def _check_identity(original: dict, source: dict) -> tuple[str, list[dict]]:
    conflicts = []
    metadata = original.get("roboflow")
    if not isinstance(metadata, dict):
        return "declared_source_only", conflicts
    complete = True
    for field in ("workspace", "project", "version"):
        value = metadata.get(field)
        if value is None:
            complete = False
        elif str(value) != str(source[field]):
            conflicts.append(
                {
                    "code": "source_identity_mismatch",
                    "field": field,
                    "expected": source[field],
                    "observed": value,
                }
            )
    return (
        "matched_export_metadata" if complete and not conflicts else "incomplete_or_conflicting"
    ), conflicts


def import_dataset(
    archive: Path,
    destination: Path,
    source_path: Path,
    *,
    expected_sha256: str | None = None,
    max_uncompressed_bytes: int = 4 * 1024**3,
) -> dict:
    """Archive the ZIP, extract into a new folder, audit and record provenance.

    An import with annotation or identity issues is preserved for review, never
    silently repaired. Existing destinations, including empty folders, are refused.
    """
    archive = archive.resolve()
    destination = destination.resolve()
    if not archive.is_file():
        raise FileNotFoundError(archive)
    if destination.exists():
        raise FileExistsError(f"Refusing to replace a dataset: {destination}")
    if expected_sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("expected_sha256 must contain exactly 64 hexadecimal characters.")
    source = load_source_config(source_path)
    archive_digest = file_hash(archive)
    if expected_sha256 is not None and archive_digest != expected_sha256.lower():
        raise ValueError("Archive SHA-256 differs from the expected value.")
    inspection = inspect_archive(archive, max_uncompressed_bytes=max_uncompressed_bytes)
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(parent).free < inspection["uncompressed_bytes"] + archive.stat().st_size:
        raise OSError("Not enough free disk space for the original ZIP and extracted export.")

    staging = Path(tempfile.mkdtemp(prefix=".browt-import-", dir=parent)).resolve()
    if staging.parent != parent:
        raise RuntimeError("Unexpected staging directory.")
    try:
        payload = staging / "payload"
        export = payload / "export"
        export.mkdir(parents=True)
        archived = payload / "source.zip"
        with archive.open("rb") as original, archived.open("xb") as copy:
            shutil.copyfileobj(original, copy, length=1024 * 1024)
        if file_hash(archived) != archive_digest:
            raise OSError("The archive changed while it was being copied.")
        with zipfile.ZipFile(archived) as bundle:
            for member in bundle.infolist():
                relative = _safe_member(member.filename)
                target = export.joinpath(*relative.parts)
                if not target.resolve().is_relative_to(export.resolve()):
                    raise ValueError("Archive member would leave the export directory.")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as original, target.open("xb") as copy:
                        shutil.copyfileobj(original, copy, length=1024 * 1024)

        candidates = list(export.rglob("data.yaml"))
        if len(candidates) != 1:
            raise ValueError("Expected exactly one data.yaml in the exported archive.")
        dataset = candidates[0].parent
        relative_dataset = dataset.relative_to(payload)
        original_yaml = yaml.safe_load(candidates[0].read_text(encoding="utf-8"))
        if not isinstance(original_yaml, dict):
            raise ValueError("data.yaml must be a mapping.")
        identity, findings = _check_identity(original_yaml, source)
        names = normalize_names(original_yaml.get("names"))
        if set(names.values()) != set(source["expected_classes"]):
            findings.append(
                {
                    "code": "unexpected_classes",
                    "expected": source["expected_classes"],
                    "observed": list(names.values()),
                }
            )
        splits = tuple(name for name, count in source["expected_splits"].items() if count > 0)
        audit = audit_dataset(dataset, splits=splits)
        audit["root"] = str(destination / relative_dataset)
        for split, expected in source["expected_splits"].items():
            actual = audit["splits"].get(split, {}).get("images", 0)
            if actual != expected:
                findings.append(
                    {
                        "code": "unexpected_image_count",
                        "split": split,
                        "expected": expected,
                        "observed": actual,
                    }
                )
        provenance = {
            "schema_version": 1,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "data_kind": "observed",
            "source": source,
            "source_config_sha256": canonical_hash(source),
            "archive": {
                "file": "source.zip",
                "sha256": archive_digest,
                "bytes": archived.stat().st_size,
                **inspection,
            },
            "dataset_relative_path": relative_dataset.as_posix(),
            "dataset_fingerprint_sha256": audit["fingerprint_sha256"],
            "class_index_mapping": names,
            "source_identity": identity,
            "review_findings": findings,
            "annotation_issue_count": len(audit["issues"]),
            "status": "needs_review" if findings or audit["issues"] else "imported",
            "independent_test_established": False,
        }
        write_json(payload / "provenance.json", provenance)
        write_json(payload / "audit.json", audit)
        # Verify both targets before the directory move. No existing dataset is removed.
        if payload.resolve().parent != staging or destination.parent.resolve() != parent:
            raise RuntimeError("Unexpected import move target.")
        if destination.exists():
            raise FileExistsError(f"Destination appeared during import: {destination}")
        payload.rename(destination)
        return {
            "destination": str(destination),
            "dataset_path": str(destination / relative_dataset),
            "provenance_path": str(destination / "provenance.json"),
            "audit_path": str(destination / "audit.json"),
            "status": provenance["status"],
            "annotation_issues": len(audit["issues"]),
            "review_findings": len(findings),
            "archive_sha256": archive_digest,
        }
    finally:
        # Delete only the task-created staging directory after checking its resolved parent.
        if staging.exists():
            if staging.resolve().parent != parent or not staging.name.startswith(".browt-import-"):
                raise RuntimeError("Unsafe staging cleanup target.")
            shutil.rmtree(staging)
