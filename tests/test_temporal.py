from BROWT.data.temporal import temporal_neighbors


def row(split, stamp, suffix="a"):
    return {"image": f"{split}/images/Screenshot_{stamp}_png.rf.{suffix}.jpg", "split": split}


def test_finds_nearest_training_frame_and_retains_clock_uncertainty():
    result = temporal_neighbors(
        [
            row("train", "20240701-120000"),
            row("train", "20240701-120010"),
            row("valid", "20240701-120001"),
            row("valid", "20240701-120008"),
        ]
    )
    assert result["within_2_seconds"] == 2
    assert result["pairs"][0]["gap_seconds"] == 1
    assert "unverified" in result["clock"]
    assert result["days"]["2024-07-01"] == {"train": 2, "valid": 2, "test": 0}


def test_missing_or_invalid_filename_times_do_not_invent_proximity():
    result = temporal_neighbors(
        [
            {"image": "train/images/no-time.jpg", "split": "train"},
            row("valid", "20249999-999999"),
        ]
    )
    assert result["matched_images"] == 0
    assert result["unmatched_images"] == 2
    assert result["pairs"] == []


def test_no_train_frames_yields_no_pairs():
    result = temporal_neighbors([row("valid", "20240701-120001")])
    assert result["matched_validation_images"] == 1
    assert result["paired_validation_images"] == 0
    assert result["within_10_seconds"] == 0


def test_proximity_handles_midnight_without_assigning_a_timezone():
    result = temporal_neighbors(
        [
            row("train", "20240701-235959"),
            row("valid", "20240702-000000"),
        ]
    )
    assert result["pairs"][0]["gap_seconds"] == 1
    assert result["pairs"][0]["valid_nominal_time"] == "2024-07-02T00:00:00"
