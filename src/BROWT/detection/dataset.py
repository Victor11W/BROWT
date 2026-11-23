"""Resolve a pre-partitioned dataset without moving files or editing its metadata.

Train/validation/test membership belongs to the research protocol. Splitting
adjacent video frames automatically is deliberately not supported.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from BROWT.data.classes import normalize_names


def write_data_yaml(root: Path, destination: Path | None = None) -> Path:
    """Create a derived absolute-path YAML; leave the Roboflow original intact."""
    root = root.resolve()
    source = root / "data.yaml"
    if not source.is_file():
        raise FileNotFoundError(f"Missing dataset metadata: {source}")
    original = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(original, dict) or "names" not in original:
        raise ValueError("data.yaml must define the original class-index mapping.")
    names = normalize_names(original["names"])
    destination = destination or (root / "data.resolved.yaml")
    if destination.resolve() == source:
        raise ValueError("The resolved YAML cannot replace the source data.yaml.")
    config = {
        "path": str(root),
        "train": "train/images",
        "val": "valid/images",
        "nc": len(names),
        "names": names,
    }
    if (root / "test/images").is_dir():
        config["test"] = "test/images"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return destination
