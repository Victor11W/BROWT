from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import pytest
import yaml

from BROWT.data.acquire import import_dataset, inspect_archive, load_source_config
from BROWT.research.provenance import file_hash


@pytest.fixture
def public_export(dataset, tmp_path):
    metadata = yaml.safe_load((dataset / "data.yaml").read_text(encoding="utf-8"))
    metadata["roboflow"] = {"workspace": "test-workspace", "project": "test-project", "version": 21}
    (dataset / "data.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    archive = tmp_path / "export.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for item in dataset.rglob("*"):
            if item.is_file():
                bundle.write(item, item.relative_to(dataset).as_posix())
    source = {
        "schema_version": 1,
        "source_id": "test-source",
        "workspace": "test-workspace",
        "project": "test-project",
        "version": 21,
        "format": "yolov11",
        "public_url": "https://universe.roboflow.com/test-workspace/test-project/dataset/21",
        "license": "MIT",
        "expected_splits": {"train": 2, "valid": 2, "test": 2},
        "expected_classes": ["Me", "Enemy"],
    }
    config = tmp_path / "source.yaml"
    config.write_text(yaml.safe_dump(source), encoding="utf-8")
    return archive, config, source


def test_import_keeps_archive_and_original_export_unchanged(public_export, tmp_path):
    archive, config, _ = public_export
    original_hash = file_hash(archive)
    result = import_dataset(archive, tmp_path / "imported", config)
    destination = Path(result["destination"])
    assert result["status"] == "imported"
    assert file_hash(destination / "source.zip") == original_hash == file_hash(archive)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            assert (destination / "export" / member.filename).read_bytes() == bundle.read(member)
    provenance = json.loads((destination / "provenance.json").read_text(encoding="utf-8"))
    audit = json.loads((destination / "audit.json").read_text(encoding="utf-8"))
    assert provenance["source_identity"] == "matched_export_metadata"
    assert provenance["class_index_mapping"] == {"0": "Me", "1": "Enemy"}
    assert not provenance["independent_test_established"]
    assert audit["root"] == str(destination / "export")
    assert not list(destination.parent.glob(".browt-import-*"))


def test_existing_dataset_is_never_replaced(public_export, tmp_path):
    archive, config, _ = public_export
    target = tmp_path / "existing"
    target.mkdir()
    marker = target / "keep.txt"
    marker.write_text("existing research data", encoding="utf-8")
    with pytest.raises(FileExistsError):
        import_dataset(archive, target, config)
    assert marker.read_text(encoding="utf-8") == "existing research data"


def test_wrong_archive_hash_stops_before_creating_destination(public_export, tmp_path):
    archive, config, _ = public_export
    target = tmp_path / "not-created"
    with pytest.raises(ValueError, match="SHA-256"):
        import_dataset(archive, target, config, expected_sha256="0" * 64)
    assert not target.exists()


@pytest.mark.parametrize(
    "member",
    [
        "../outside.txt",
        "/absolute.txt",
        "C:/outside.txt",
        "train/../../outside.txt",
        "train/image.jpg:stream",
        "..\\outside.txt",
        "train/./image.jpg",
    ],
)
def test_unsafe_archive_paths_are_rejected_before_extraction(tmp_path, member):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(member, "payload")
    with pytest.raises(ValueError, match="Unsafe"):
        inspect_archive(archive)


def test_archive_links_are_rejected(tmp_path):
    archive = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(link, "../outside")
    with pytest.raises(ValueError, match="links"):
        inspect_archive(archive)


def test_case_colliding_members_are_rejected(tmp_path):
    archive = tmp_path / "collision.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Train/images/a.jpg", "one")
        bundle.writestr("train/images/A.jpg", "two")
    with pytest.raises(ValueError, match="colliding"):
        inspect_archive(archive)


def test_size_limit_is_checked_before_extracting(public_export):
    archive, _, _ = public_export
    with pytest.raises(ValueError, match="uncompressed"):
        inspect_archive(archive, max_uncompressed_bytes=1)


def test_metadata_conflicts_are_preserved_for_review(public_export, tmp_path):
    archive, config, source = public_export
    source["version"] = 99
    config.write_text(yaml.safe_dump(source), encoding="utf-8")
    result = import_dataset(archive, tmp_path / "review", config)
    assert result["status"] == "needs_review"
    manifest = json.loads(Path(result["provenance_path"]).read_text(encoding="utf-8"))
    assert any(item["code"] == "source_identity_mismatch" for item in manifest["review_findings"])


def test_nested_export_root_is_supported(public_export, tmp_path):
    archive, config, _ = public_export
    nested = tmp_path / "nested.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(nested, "w") as bundle:
        for member in original.infolist():
            bundle.writestr("dataset-v21/" + member.filename, original.read(member))
    result = import_dataset(nested, tmp_path / "nested-import", config)
    assert Path(result["dataset_path"]).name == "dataset-v21"
    assert result["status"] == "imported"


def test_incomplete_archive_is_not_published(public_export, tmp_path):
    _, config, _ = public_export
    archive = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("README.txt", "no metadata")
    target = tmp_path / "incomplete-import"
    with pytest.raises(ValueError, match="data.yaml"):
        import_dataset(archive, target, config)
    assert not target.exists()
    assert not list(tmp_path.glob(".browt-import-*"))


def test_public_source_configuration_is_versioned():
    root = Path(__file__).resolve().parents[1]
    source = load_source_config(root / "configs/dataset-source.yaml")
    assert source["version"] == 21
    assert source["expected_splits"] == {"train": 2297, "valid": 254}
    assert source["preprocessing"]["resize"] == "none"
