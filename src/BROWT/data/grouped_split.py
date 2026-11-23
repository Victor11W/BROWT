"""Derive a chronological train/valid candidate from filename-time proxies."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from BROWT.data.audit import audit_dataset
from BROWT.research.provenance import canonical_hash

SPLITS = ("train", "valid")
MAX_GAP_SECONDS = 10
TIMESTAMP = re.compile(r"Screenshot_([0-9]{8})-([0-9]{6})(?![0-9])", re.IGNORECASE)


def _nominal_time(image: str) -> datetime:
    matches = TIMESTAMP.findall(PurePosixPath(image).name)
    if len(matches) == 1:
        day, clock = matches[0]
        try:
            return datetime.fromisoformat(f"{day}T{clock}")
        except ValueError:
            pass
    raise ValueError(f"Expected one valid nominal timestamp in image name: {image}")


def _validation_cut(groups: list[dict], target: int) -> int:
    """Return the number of train groups, allowing only non-leaking cuts."""
    latest = {
        row["image_sha256"]: index for index, group in enumerate(groups) for row in group["samples"]
    }
    remaining = sum(len(group["samples"]) for group in groups)
    furthest = -1
    candidates = []
    for index, group in enumerate(groups[:-1]):
        remaining -= len(group["samples"])
        furthest = max(furthest, *(latest[row["image_sha256"]] for row in group["samples"]))
        if furthest == index:
            candidates.append((abs(remaining - target), remaining, index + 1))
    if not candidates:
        raise ValueError(
            "No nonempty chronological train/valid cut without exact image duplicates."
        )
    return min(candidates)[2]


def _coverage(samples: list[dict], classes: dict[int, str]) -> dict:
    boxes = Counter({name: 0 for name in classes.values()})
    images = boxes.copy()
    for row in samples:
        boxes.update(row["class_boxes"])
        images.update(row["class_boxes"].keys())
    return {
        "images": len(samples),
        "class_boxes": dict(boxes),
        "class_images": dict(images),
        "classes_without_boxes": [name for name, count in boxes.items() if not count],
    }


def grouped_manifest(root: Path, *, expected_source_sha256: str | None = None) -> dict:
    """Audit public train/valid and return a portable, unapproved candidate manifest."""
    audit = audit_dataset(root, splits=SPLITS)
    if audit["issues"]:
        first = audit["issues"][0]
        raise ValueError(f"Source audit failed: {first['code']} at {first['path']}")
    source_sha256 = audit["fingerprint_sha256"]
    if expected_source_sha256 is not None and source_sha256 != expected_source_sha256:
        raise ValueError("Source dataset fingerprint does not match the expected SHA-256.")

    classes = audit["classes"]
    timed = sorted((_nominal_time(row["image"]), row["image"], row) for row in audit["samples"])
    groups: list[dict] = []
    public: dict[str, list[dict]] = {split: [] for split in SPLITS}
    previous = None
    for nominal, image, row in timed:
        public_split = image.split("/", 1)[0]
        label = f"{public_split}/labels/{PurePosixPath(image).stem}.txt"
        raw = (root / label).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["label_sha256"]:
            raise ValueError(f"Label changed since source audit: {label}")
        counts = Counter(
            classes[int(line.split()[0])]
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        )
        stamp = nominal.isoformat()
        sample = {
            **row,
            "label": label,
            "public_split": public_split,
            "nominal_time": stamp,
            "class_boxes": dict(sorted(counts.items())),
        }
        if previous is None or nominal - previous > timedelta(seconds=MAX_GAP_SECONDS):
            groups.append({"nominal_start": stamp, "samples": []})
        groups[-1]["samples"].append(sample)
        public[public_split].append(sample)
        previous = nominal

    target = len(public["valid"])
    cut = _validation_cut(groups, target)
    grouped: dict[str, list[dict]] = {split: [] for split in SPLITS}
    for index, group in enumerate(groups):
        split = "train" if index < cut else "valid"
        group["split"] = split
        grouped[split].extend(group["samples"])

    manifest = {
        "schema_version": 1,
        "status": "candidate",
        "source_fingerprint_sha256": source_sha256,
        "scope_splits": list(SPLITS),
        "independent_test_in_scope": False,
        "rule": {
            "clock": "nominal filename time, without timezone or verified device identity",
            "max_consecutive_gap_seconds_inclusive": MAX_GAP_SECONDS,
            "partition": "chronological cut between groups with no cross-split exact image hash",
            "target_validation_images": target,
            "tie_break": "smaller validation split",
        },
        "classes": classes,
        "groups": groups,
        "coverage": {
            "public": {split: _coverage(public[split], classes) for split in SPLITS},
            "grouped": {split: _coverage(grouped[split], classes) for split in SPLITS},
        },
    }
    manifest["fingerprint_sha256"] = canonical_hash(manifest)
    return manifest


def write_grouped_manifest(
    root: Path, output: Path, *, expected_source_sha256: str | None = None
) -> None:
    """Create a derived file outside the dataset, without overwriting anything."""
    if output.resolve().is_relative_to(root.resolve()):
        raise ValueError("Output must be outside the source dataset.")
    manifest = grouped_manifest(root, expected_source_sha256=expected_source_sha256)
    content = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
