import math

import pytest

from BROWT.detection import Detection
from BROWT.state import build_state


def detection(name="Me", box=(100, 50, 300, 150), confidence=0.8, cls_id=0):
    return Detection(cls_id, name, confidence, box)


def test_normalizes_without_mutating_detection():
    original = detection()
    state = build_state([original], width=1000, height=500, timestamp_s=1.5)
    assert state.player.center == pytest.approx((0.2, 0.2))
    assert state.objects[0].xyxy == pytest.approx((0.1, 0.1, 0.3, 0.3))
    assert original.xyxy == (100, 50, 300, 150)


def test_missing_and_ambiguous_player_are_explicit():
    missing = build_state([], width=100, height=100, timestamp_s=0)
    ambiguous = build_state([detection(), detection()], width=1000, height=500, timestamp_s=0)
    assert missing.player is None and missing.player_status == "missing"
    assert ambiguous.player is None and ambiguous.player_status == "ambiguous"
    assert missing.to_dict()["objects"] == ()


@pytest.mark.parametrize(
    "box", [(0, 0, 0, 2), (3, 0, 2, 5), (0, 0, math.nan, 3), (200, 200, 220, 220)]
)
def test_invalid_boxes_are_rejected(box):
    with pytest.raises(ValueError):
        build_state([detection(box=box)], width=100, height=100, timestamp_s=0)


@pytest.mark.parametrize("confidence", [-0.1, 1.1, math.inf, math.nan])
def test_invalid_confidences_are_rejected(confidence):
    with pytest.raises(ValueError):
        build_state([detection(confidence=confidence)], width=1000, height=500, timestamp_s=0)


def test_clips_partial_boxes():
    state = build_state([detection(box=(-5, -10, 50, 50))], width=100, height=100, timestamp_s=0)
    assert state.objects[0].xyxy == (0, 0, 0.5, 0.5)


def test_order_does_not_depend_on_detector_output_order():
    a, b = detection(), detection("Enemy", cls_id=1)
    assert build_state([a, b], width=1000, height=500, timestamp_s=0) == build_state(
        [b, a],
        width=1000,
        height=500,
        timestamp_s=0,
    )


@pytest.mark.parametrize(
    "width,height,timestamp", [(0, 10, 0), (True, 10, 0), (10, 2.5, 0), (10, 10, -1)]
)
def test_invalid_frame_metadata(width, height, timestamp):
    with pytest.raises(ValueError):
        build_state([], width=width, height=height, timestamp_s=timestamp)
