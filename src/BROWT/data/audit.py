"""Read-only annotation audit and portable dataset fingerprint.

This checks labels and exact file duplicates. It does not prove independence
of nearby video frames, nor decode every image; both limitations are explicit.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from BROWT.data.classes import normalize_names
from BROWT.research.provenance import canonical_hash, file_hash

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def audit_dataset(root: Path, *, splits: tuple[str, ...] = ("train", "valid", "test")) -> dict:
    root = root.resolve()
    issues: list[dict] = []
    report = {
        "schema_version": 1,
        "root": str(root),
        "scope_splits": list(splits),
        "classes": {},
        "splits": {},
        "issues": issues,
        "samples": [],
        "limitations": [
            "Exact hashes do not detect near-duplicate frames or session leakage.",
            "Image decoding and annotation correctness require a separate visual review.",
        ],
    }

    def issue(code: str, relative: str, message: str, line: int | None = None) -> None:
        issues.append({"code": code, "path": relative, "line": line, "message": message})

    if (
        not splits
        or len(set(splits)) != len(splits)
        or any(split not in {"train", "valid", "test"} for split in splits)
    ):
        raise ValueError("splits must be unique entries from train, valid, test.")
    metadata = root / "data.yaml"
    if not metadata.is_file():
        raise FileNotFoundError(f"Missing dataset metadata: {metadata}")
    config = yaml.safe_load(metadata.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or "names" not in config:
        raise ValueError("data.yaml must contain the original class names.")
    names = normalize_names(config["names"])
    report["classes"] = names
    duplicates: dict[str, list[str]] = defaultdict(list)

    for split in splits:
        images_dir = root / split / "images"
        labels_dir = root / split / "labels"
        counts = Counter({name: 0 for name in names.values()})
        summary = {
            "images": 0,
            "annotated_images": 0,
            "background_images": 0,
            "boxes": 0,
            "class_counts": dict(counts),
        }
        report["splits"][split] = summary
        if not images_dir.is_dir() or not labels_dir.is_dir():
            issue("missing_split", split, "Expected images/ and labels/ directories.")
            continue
        images = sorted(p for p in images_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        if not images:
            issue("empty_split", split, "No supported image files.")
        stems = Counter(p.stem for p in images)
        for stem, count in sorted(stems.items()):
            if count > 1:
                issue(
                    "ambiguous_stem",
                    f"{split}/images/{stem}",
                    "Several images share one label stem.",
                )
        for image in images:
            relative = image.relative_to(root).as_posix()
            if not image.is_file() or not image.resolve().is_relative_to(root):
                issue(
                    "unsafe_image_path",
                    relative,
                    "Image must be a regular file inside the dataset.",
                )
                continue
            summary["images"] += 1
            image_digest = file_hash(image)
            duplicates[image_digest].append(relative)
            label = labels_dir / (image.stem + ".txt")
            sample = {"image": relative, "image_sha256": image_digest, "label_sha256": None}
            report["samples"].append(sample)
            if not label.is_file():
                issue(
                    "missing_label", relative, "Use an explicit empty label for a background image."
                )
                continue
            if not label.resolve().is_relative_to(root):
                issue(
                    "unsafe_label_path",
                    label.relative_to(root).as_posix(),
                    "Label is outside the dataset.",
                )
                continue
            sample["label_sha256"] = file_hash(label)
            try:
                lines = label.read_text(encoding="utf-8").splitlines()
            except UnicodeError:
                issue(
                    "invalid_encoding", label.relative_to(root).as_posix(), "Expected UTF-8 labels."
                )
                continue
            nonempty = [(number, text) for number, text in enumerate(lines, 1) if text.strip()]
            if not nonempty:
                summary["background_images"] += 1
            else:
                summary["annotated_images"] += 1
            for number, text in nonempty:
                fields = text.split()
                try:
                    if len(fields) != 5:
                        raise ValueError("Expected: class x_center y_center width height.")
                    cls_id = int(fields[0])
                    x, y, width, height = map(float, fields[1:])
                    if cls_id not in names:
                        raise ValueError("Class ID is not defined in data.yaml.")
                    if not all(math.isfinite(v) for v in (x, y, width, height)):
                        raise ValueError("Coordinates must be finite.")
                    if (
                        not 0 <= x <= 1
                        or not 0 <= y <= 1
                        or not 0 < width <= 1
                        or not 0 < height <= 1
                    ):
                        raise ValueError("Expected normalized coordinates and positive dimensions.")
                    if (
                        min(x - width / 2, y - height / 2) < -1e-6
                        or max(x + width / 2, y + height / 2) > 1 + 1e-6
                    ):
                        raise ValueError("Bounding box extends outside the image.")
                except ValueError as error:
                    issue(
                        "invalid_annotation", label.relative_to(root).as_posix(), str(error), number
                    )
                    continue
                counts[names[cls_id]] += 1
                summary["boxes"] += 1
        summary["class_counts"] = dict(counts)
        for label in sorted(labels_dir.glob("*.txt")):
            if label.stem not in stems:
                issue(
                    "orphan_label",
                    label.relative_to(root).as_posix(),
                    "Label has no matching image.",
                )

    for digest, paths in sorted(duplicates.items()):
        if len({p.split("/")[0] for p in paths}) > 1:
            issue(
                "cross_split_duplicate",
                paths[0],
                "Identical image across splits: " + ", ".join(paths),
            )
    report["fingerprint_sha256"] = canonical_hash(
        {
            "classes": names,
            "scope_splits": list(splits),
            "samples": report["samples"],
        }
    )
    report["valid"] = not issues
    return report
