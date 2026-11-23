import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from BROWT.detection import train
from BROWT.research.provenance import file_hash


@pytest.fixture
def training_setup(dataset, tmp_path, monkeypatch):
    config = {
        "model": {"size": "n", "pretrained": ""},
        "data": {"root": str(dataset)},
        "train": {
            "epochs": 3,
            "imgsz": 320,
            "batch": 2,
            "patience": 2,
            "device": "cpu",
            "workers": 0,
            "seed": 7,
        },
        "output": {"project": "runs", "name": "test", "weights_dir": "models"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    calls = {"training": [], "evaluation": [], "models": []}

    class FakeYOLO:
        def __init__(self, weights):
            calls["models"].append(str(weights))

        def train(self, **kwargs):
            calls["training"].append(kwargs)
            best = Path(kwargs["project"]) / kwargs["name"] / "weights/best.pt"
            best.parent.mkdir(parents=True)
            best.write_bytes(b"test-double-checkpoint")
            self.trainer = SimpleNamespace(best=best)

        def val(self, **kwargs):
            calls["evaluation"].append(kwargs)
            return SimpleNamespace(box=SimpleNamespace(map50=0.1, map=0.05))

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))
    monkeypatch.setattr(train, "repo_root", lambda: tmp_path)

    def invoke(*flags):
        monkeypatch.setattr(sys, "argv", ["train", "--config", str(config_path), *flags])
        train.main()

    return invoke, calls, tmp_path, dataset


def test_smoke_never_publishes_or_reads_locked_test(training_setup):
    invoke, calls, root, dataset = training_setup
    (dataset / "test/labels/0.txt").write_text(
        "intentionally invalid locked test", encoding="utf-8"
    )
    before = {p: file_hash(p) for p in dataset.rglob("*") if p.is_file()}
    invoke("--smoke")
    assert calls["training"][0]["epochs"] == 1
    assert calls["training"][0]["fraction"] == 0.05
    assert calls["evaluation"] == []
    assert not (root / "models").exists()
    assert {p: file_hash(p) for p in dataset.rglob("*") if p.is_file()} == before
    manifest = json.loads(next((root / "runs").glob("*/manifest.json")).read_text())
    assert manifest["status"] == "completed"
    assert manifest["metrics"]["test_evaluated"] is False


@pytest.mark.parametrize("flag", ["--publish", "--evaluate-test"])
def test_smoke_rejects_publication_and_test_evaluation(training_setup, flag):
    invoke, calls, _, _ = training_setup
    with pytest.raises(SystemExit) as error:
        invoke("--smoke", flag)
    assert error.value.code == 2
    assert calls["training"] == []


def test_full_training_does_not_evaluate_test_implicitly(training_setup):
    invoke, calls, _, _ = training_setup
    invoke()
    assert calls["evaluation"] == []
    assert calls["training"][0]["exist_ok"] is False


def test_final_evaluation_loads_best_checkpoint(training_setup):
    invoke, calls, _, _ = training_setup
    invoke("--evaluate-test")
    assert calls["evaluation"][0]["split"] == "test"
    assert calls["evaluation"][0]["imgsz"] == 320
    assert Path(calls["models"][-1]).parts[-2:] == ("weights", "best.pt")


def test_publication_cannot_overwrite_a_model(training_setup):
    invoke, calls, root, _ = training_setup
    target = root / "models/brawl_yolo11n.pt"
    target.parent.mkdir()
    target.write_bytes(b"existing-model")
    with pytest.raises(FileExistsError):
        invoke("--publish")
    assert target.read_bytes() == b"existing-model"
    assert calls["training"] == []


def test_failed_training_records_failure(training_setup, monkeypatch):
    invoke, _, root, _ = training_setup

    class FailingYOLO:
        def __init__(self, weights):
            pass

        def train(self, **kwargs):
            raise RuntimeError("intentional test failure")

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FailingYOLO))
    with pytest.raises(RuntimeError, match="intentional"):
        invoke("--smoke")
    manifest = json.loads(next((root / "runs").glob("*/manifest.json")).read_text())
    assert manifest["status"] == "failed"


def test_publish_checkpoint_uses_exclusive_creation(tmp_path):
    source, target = tmp_path / "source.pt", tmp_path / "target.pt"
    source.write_bytes(b"new")
    target.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        train.publish_checkpoint(source, target)
    assert target.read_bytes() == b"old"


def test_pretrained_path_is_resolved_and_hashed(training_setup):
    invoke, calls, root, _ = training_setup
    checkpoint = root / "initial.pt"
    checkpoint.write_bytes(b"original-base-weights")
    config_path = root / "config.yaml"
    settings = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    settings["model"]["pretrained"] = "initial.pt"
    config_path.write_text(yaml.safe_dump(settings), encoding="utf-8")
    invoke("--smoke")
    assert Path(calls["models"][0]) == checkpoint
    manifest = json.loads(next((root / "runs").glob("*/manifest.json")).read_text())
    assert manifest["input_weights_sha256"] == file_hash(checkpoint)
