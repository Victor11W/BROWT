import json

import pytest

from BROWT.research.provenance import Experiment, canonical_hash, file_hash, write_json


def test_run_records_hashes_and_explicit_data_kind(tmp_path):
    run = Experiment(
        tmp_path / "runs",
        task="unit-test",
        config={"seed": 7},
        data_kind="synthetic",
        repo=tmp_path,
    )
    artifact = run.folder / "value.txt"
    artifact.write_text("measurement", encoding="utf-8")
    run.complete(metrics={"count": 1}, artifacts=[artifact])
    manifest = json.loads((run.folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["data_kind"] == "synthetic"
    assert manifest["config_sha256"] == canonical_hash({"seed": 7})
    assert manifest["artifacts"][0]["sha256"] == file_hash(artifact)
    commit = manifest["environment"]["git"]["commit"]
    assert commit is None or (len(commit) == 40 and int(commit, 16) >= 0)
    assert len(manifest["environment"]["analysis_source"]["sha256"]) == 64


def test_failed_run_retains_failure(tmp_path):
    run = Experiment(tmp_path, task="failure", config={}, data_kind="observed", repo=tmp_path)
    run.fail(RuntimeError("sensor disconnected"))
    assert run.manifest["status"] == "failed"
    assert run.manifest["error"]["type"] == "RuntimeError"


def test_json_rejects_nonfinite_values_without_corrupting_existing_file(tmp_path):
    target = tmp_path / "artifact.json"
    write_json(target, {"value": 1})
    with pytest.raises(ValueError):
        write_json(target, {"value": float("nan")})
    assert json.loads(target.read_text()) == {"value": 1}


def test_artifact_must_belong_to_its_run(tmp_path):
    run = Experiment(
        tmp_path / "runs", task="ownership", config={}, data_kind="synthetic", repo=tmp_path
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    with pytest.raises(ValueError):
        run.complete(artifacts=[outside])


def test_configuration_is_an_immutable_snapshot(tmp_path):
    settings = {"nested": {"seed": 1}}
    run = Experiment(
        tmp_path, task="snapshot", config=settings, data_kind="synthetic", repo=tmp_path
    )
    settings["nested"]["seed"] = 99
    run.complete()
    assert run.manifest["config"]["nested"]["seed"] == 1
    assert run.manifest["config_sha256"] == canonical_hash(run.manifest["config"])
