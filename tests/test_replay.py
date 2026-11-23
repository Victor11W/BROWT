import json

import pytest

from BROWT.research.demo import synthetic_records
from BROWT.state.replay import iter_replay


def write_records(tmp_path, records):
    target = tmp_path / "replay.jsonl"
    target.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    return target


def test_synthetic_fixture_preserves_missing_observations(tmp_path):
    frames = list(iter_replay(write_records(tmp_path, synthetic_records(0))))
    assert len(frames) == 48
    assert sum(frame.state.player_status == "missing" for frame in frames) == 4
    assert {frame.data_kind for frame in frames} == {"synthetic"}


def test_rejects_mixed_provenance(tmp_path):
    rows = synthetic_records(0)[:2]
    rows[1]["data_kind"] = "observed"
    with pytest.raises(ValueError, match="line 2"):
        list(iter_replay(write_records(tmp_path, rows)))


def test_rejects_nonmonotonic_timestamps(tmp_path):
    rows = synthetic_records(0)[:2]
    rows[1]["timestamp_s"] = rows[0]["timestamp_s"]
    with pytest.raises(ValueError, match="strictly increasing"):
        list(iter_replay(write_records(tmp_path, rows)))


def test_rejects_fractional_class_id(tmp_path):
    rows = synthetic_records(0)[:1]
    rows[0]["detections"][0]["cls_id"] = 1.5
    with pytest.raises(ValueError):
        list(iter_replay(write_records(tmp_path, rows)))


def test_empty_replay_is_invalid(tmp_path):
    with pytest.raises(ValueError, match="empty"):
        list(iter_replay(write_records(tmp_path, [])))


def test_offline_replay_writes_a_completed_experiment(tmp_path):
    from BROWT.research.replay import run_replay

    source = write_records(tmp_path, synthetic_records(1))
    target = run_replay(source, tmp_path / "runs")
    states = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    manifest = json.loads((target.parent / "manifest.json").read_text(encoding="utf-8"))
    assert len(states) == 48
    assert manifest["status"] == "completed"
    assert manifest["data_kind"] == "synthetic"
    assert manifest["metrics"]["missing_player"] == 4
