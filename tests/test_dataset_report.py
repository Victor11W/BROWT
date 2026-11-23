import json
from pathlib import Path

from BROWT.data.audit import audit_dataset
from BROWT.research.dataset_report import dataset_report, profile_images


def test_image_profile_counts_real_decoded_pixels_and_labels(dataset):
    audit = audit_dataset(dataset, splits=("train", "valid"))
    rows, issues = profile_images(dataset, audit)
    assert len(rows) == 4 and issues == []
    assert {(row["width"], row["height"]) for row in rows} == {(1, 1)}
    assert sum(len(row["boxes"]) for row in rows) == 4


def test_corrupt_images_remain_explicit_issues(dataset):
    (dataset / "valid/images/0.png").write_bytes(b"not an image")
    audit = audit_dataset(dataset, splits=("train", "valid"))
    rows, issues = profile_images(dataset, audit)
    assert len(rows) == 3
    assert issues[0]["code"] == "image_decode_failed"
    assert issues[0]["path"] == "valid/images/0.png"


def test_dataset_report_records_descriptive_scope_and_artifacts(dataset, tmp_path):
    report = dataset_report(dataset, tmp_path / "reports")
    manifest = json.loads((report.parent / "manifest.json").read_text(encoding="utf-8"))
    metrics = manifest["metrics"]
    assert manifest["status"] == "completed"
    assert metrics["decoded_images"] == 4
    assert metrics["valid_reference_boxes"] == 4
    assert metrics["class_counts"] == {"Me": 2, "Enemy": 2}
    assert metrics["independent_test_established"] is False
    assert (report.parent / "annotation-gallery.png").is_file()
    assert {Path(item["path"]).suffix for item in manifest["artifacts"]} >= {
        ".json",
        ".svg",
        ".png",
        ".html",
    }
