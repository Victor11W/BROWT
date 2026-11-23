import shutil

import pytest
import yaml

from BROWT.data.audit import audit_dataset
from BROWT.data.classes import normalize_names
from BROWT.detection.dataset import write_data_yaml
from BROWT.research.provenance import file_hash


def test_audit_and_resolve_preserve_source(dataset, tmp_path):
    before = {p.relative_to(dataset): file_hash(p) for p in dataset.rglob("*") if p.is_file()}
    audit = audit_dataset(dataset)
    resolved = write_data_yaml(dataset, tmp_path / "derived.yaml")
    assert audit["valid"]
    assert audit["splits"]["train"]["boxes"] == 2
    assert yaml.safe_load(resolved.read_text(encoding="utf-8"))["names"] == {0: "Me", 1: "Enemy"}
    assert {
        p.relative_to(dataset): file_hash(p) for p in dataset.rglob("*") if p.is_file()
    } == before


def test_fingerprint_is_portable_and_sensitive(dataset, tmp_path):
    other = tmp_path / "copy"
    shutil.copytree(dataset, other)
    assert (
        audit_dataset(dataset)["fingerprint_sha256"] == audit_dataset(other)["fingerprint_sha256"]
    )
    (other / "train/labels/0.txt").write_text("0 0.5 0.5 0.3 0.3\n", encoding="utf-8")
    assert (
        audit_dataset(dataset)["fingerprint_sha256"] != audit_dataset(other)["fingerprint_sha256"]
    )


def test_cross_split_duplicate_is_rejected(dataset):
    (dataset / "valid/images/0.png").write_bytes((dataset / "train/images/0.png").read_bytes())
    report = audit_dataset(dataset)
    assert not report["valid"]
    assert any(issue["code"] == "cross_split_duplicate" for issue in report["issues"])


@pytest.mark.parametrize(
    "text",
    ["9 0.5 0.5 0.2 0.2", "0 nan 0.5 0.2 0.2", "0 0.1 0.1 0.8 0.8", "0 0.5 0.5 0 0.2", "0 1 2"],
)
def test_bad_annotations_have_a_location(dataset, text):
    (dataset / "train/labels/0.txt").write_text(text, encoding="utf-8")
    issues = audit_dataset(dataset)["issues"]
    assert any(i["code"] == "invalid_annotation" and i["line"] == 1 for i in issues)


def test_empty_label_is_background_but_missing_label_is_an_issue(dataset):
    label = dataset / "train/labels/0.txt"
    label.write_text("", encoding="utf-8")
    assert audit_dataset(dataset)["splits"]["train"]["background_images"] == 1
    label.unlink()
    assert any(i["code"] == "missing_label" for i in audit_dataset(dataset)["issues"])


def test_class_index_mapping_is_preserved():
    assert normalize_names({"1": "Enemy", "0": "Me"}) == {0: "Me", 1: "Enemy"}
    for invalid in ({1: "Me"}, {True: "Me"}, {0: "Same", 1: "Same"}, [], {0.5: "Me"}):
        with pytest.raises(ValueError):
            normalize_names(invalid)


def test_cannot_replace_original_yaml(dataset):
    with pytest.raises(ValueError):
        write_data_yaml(dataset, dataset / "data.yaml")
