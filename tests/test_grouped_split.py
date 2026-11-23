import json
import shutil
from pathlib import Path

import pytest
from conftest import png_bytes

from BROWT.cli import main
from BROWT.data.grouped_split import grouped_manifest, write_grouped_manifest
from BROWT.research.provenance import canonical_hash


@pytest.fixture
def timed_export(tmp_path):
    root = tmp_path / "source"
    rows = [
        ("train", "20240506-235955", [0, 0]),
        ("valid", "20240507-000005", [1]),
        ("train", "20240507-000005", [1]),
        ("train", "20240507-000015", [0]),
        ("train", "20240507-000026", [1]),
        ("valid", "20240508-000000", []),
    ]
    for split in ("train", "valid"):
        for kind in ("images", "labels"):
            (root / split / kind).mkdir(parents=True)
    (root / "data.yaml").write_text("names: [Ball, Enemy, Rare]\n", encoding="utf-8")
    paths = []
    for index, (split, stamp, annotations) in enumerate(rows):
        stem = f"Screenshot_{stamp}_png.rf.{index}"
        image = root / split / "images" / f"{stem}.png"
        image.write_bytes(png_bytes(index))
        (root / split / "labels" / f"{stem}.txt").write_text(
            "".join(f"{class_id} 0.5 0.5 0.2 0.2\n" for class_id in annotations),
            encoding="utf-8",
        )
        paths.append(image)
    return root, paths


def test_midnight_ties_chaining_and_coverage(timed_export):
    root, paths = timed_export
    result = grouped_manifest(root)
    first, second, third = result["groups"]
    assert [group["split"] for group in (first, second, third)] == ["train", "valid", "valid"]
    assert [row["image"] for row in first["samples"]] == [
        paths[i].relative_to(root).as_posix() for i in (0, 2, 1, 3)
    ]
    assert second["nominal_start"] == "2024-05-07T00:00:26"  # 11 s starts a new group
    assert third["nominal_start"] == "2024-05-08T00:00:00"
    public = result["coverage"]["public"]
    grouped = result["coverage"]["grouped"]
    assert public["train"]["class_boxes"] == {"Ball": 3, "Enemy": 2, "Rare": 0}
    assert grouped["train"]["class_images"] == {"Ball": 2, "Enemy": 2, "Rare": 0}
    assert grouped["valid"] == {
        "images": 2,
        "class_boxes": {"Ball": 0, "Enemy": 1, "Rare": 0},
        "class_images": {"Ball": 0, "Enemy": 1, "Rare": 0},
        "classes_without_boxes": ["Ball", "Rare"],
    }
    assert result["status"] == "candidate"
    assert result["independent_test_in_scope"] is False


def test_exact_duplicate_moves_cut(timed_export):
    root, paths = timed_export
    paths[4].write_bytes(paths[0].read_bytes())  # same original split, distinct time groups
    result = grouped_manifest(root)
    assert [group["split"] for group in result["groups"]] == ["train", "train", "valid"]
    for group in ("train", "valid"):
        assert result["coverage"]["grouped"][group]["images"] > 0


def test_duplicate_spanning_all_cuts_is_rejected(timed_export):
    root, paths = timed_export
    paths[5].write_bytes(paths[1].read_bytes())  # two public-valid images
    with pytest.raises(ValueError, match="No nonempty chronological"):
        grouped_manifest(root)


def test_size_tie_uses_smaller_validation(timed_export):
    root, paths = timed_export
    image = paths[3]
    label = image.parent.parent / "labels" / f"{image.stem}.txt"
    # Original group sizes 4/1/1 become 3/2/1; target 2 ties between 1 and 3.
    image.rename(image.with_name(image.name.replace("000015", "000024")))
    label.rename(label.with_name(label.name.replace("000015", "000024")))
    result = grouped_manifest(root)
    assert [group["split"] for group in result["groups"]] == ["train", "train", "valid"]


def test_one_group_rejected(timed_export):
    root, paths = timed_export
    for path in paths:
        label = path.parent.parent / "labels" / f"{path.stem}.txt"
        path.rename(path.with_name(f"Screenshot_20240507-000005{path.name[26:]}"))
        label.rename(label.with_name(f"Screenshot_20240507-000005{label.name[26:]}"))
    with pytest.raises(ValueError, match="No nonempty chronological"):
        grouped_manifest(root)


@pytest.mark.parametrize(
    "name",
    [
        "no-time",
        "Screenshot_20240507-246000",
        "Screenshot_20240230-000000",
        "Screenshot_20240507-000005_Screenshot_20240507-000006",
    ],
)
def test_bad_timestamp_fails_without_silent_exclusion(timed_export, name):
    root, paths = timed_export
    image = paths[0]
    label = root / "train/labels" / f"{image.stem}.txt"
    image.rename(image.with_name(name + ".png"))
    label.rename(label.with_name(name + ".txt"))
    with pytest.raises(ValueError, match="one valid nominal timestamp"):
        grouped_manifest(root)


def test_source_audit_and_expected_hash_are_enforced(timed_export):
    root, paths = timed_export
    with pytest.raises(ValueError, match="does not match"):
        grouped_manifest(root, expected_source_sha256="0" * 64)
    (root / "train/labels" / f"{paths[0].stem}.txt").unlink()
    with pytest.raises(ValueError, match="missing_label"):
        grouped_manifest(root)


def _source_bytes(root: Path) -> dict:
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_cli_manifest_is_portable_and_preserves_inputs(timed_export, tmp_path, monkeypatch):
    root, paths = timed_export
    before = _source_bytes(root)
    relocated = shutil.copytree(root, tmp_path / "relocated")
    output = tmp_path / "run/manifest.json"
    expected = grouped_manifest(root)["source_fingerprint_sha256"]
    monkeypatch.setattr(
        "sys.argv",
        [
            "browt",
            "grouped-split",
            "--dataset",
            str(root),
            "--output",
            str(output),
            "--expected-source-sha256",
            expected,
        ],
    )
    main()
    other_output = tmp_path / "second.json"
    write_grouped_manifest(relocated, other_output)
    assert output.read_bytes() == other_output.read_bytes()
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest.pop("fingerprint_sha256") == canonical_hash(manifest)
    with pytest.raises(FileExistsError):
        write_grouped_manifest(root, output)
    with pytest.raises(ValueError, match="outside the source dataset"):
        write_grouped_manifest(root, root / "derived.json")
    assert _source_bytes(root) == before
    paths[0].write_bytes(png_bytes(30))
    assert grouped_manifest(root)["fingerprint_sha256"] != canonical_hash(manifest)
