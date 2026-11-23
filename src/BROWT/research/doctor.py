"""Read-only diagnosis. Missing optional tools are not failed scientific results."""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import sys
from pathlib import Path

import yaml

from BROWT.research.provenance import environment_snapshot


def diagnose(repo: Path | None = None) -> dict:
    repo = (repo or Path(__file__).resolve().parents[3]).resolve()
    dataset_root = repo / "data/dataset"
    configuration_note = None
    try:
        configuration = yaml.safe_load(
            (repo / "configs/detection.yaml").read_text(encoding="utf-8")
        )
        configured = configuration["data"]["root"]
        if not isinstance(configured, str):
            raise ValueError("data.root must be a path string")
        dataset_root = (repo / configured).resolve()
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        configuration_note = str(error)
    packages = {}
    for name in (
        "numpy",
        "PyYAML",
        "matplotlib",
        "ultralytics",
        "mss",
        "torch",
        "gymnasium",
        "stable-baselines3",
        "pytest",
        "ruff",
    ):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "schema_version": 1,
        "python_supported": (3, 11) <= sys.version_info[:2] < (3, 13),
        "packages": packages,
        "tools": {name: shutil.which(name) for name in ("git", "quarto", "adb", "nvidia-smi")},
        "local_assets": {
            "dataset_root": str(dataset_root),
            "dataset_yaml": (dataset_root / "data.yaml").is_file(),
            "published_weights": sorted(p.name for p in (repo / "data/models").glob("*.pt")),
        },
        "environment": environment_snapshot(repo),
        "configuration_note": configuration_note,
        "note": "CPU research is supported. NVIDIA tooling is optional; availability is not inferred.",
    }


def show_doctor(*, as_json: bool = False) -> None:
    report = diagnose()
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("BROWT — environment diagnosis")
    print(f"Python supported: {report['python_supported']}")
    for group in ("packages", "tools", "local_assets"):
        print(f"\n{group}:")
        for key, value in report[group].items():
            print(f"  {key}: {value if value is not None else 'not installed / not on PATH'}")
    print("\n" + report["note"])
