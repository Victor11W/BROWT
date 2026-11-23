"""Convert pixel detections into explicit, normalized observations.

Coordinates use the screen convention: x grows right, y grows down.
Missing or ambiguous player detections remain missing; no position is invented.
This is an observation of one frame, not the game's hidden state.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Iterable

from BROWT.detection.detector import Detection


@dataclass(frozen=True)
class ObservedObject:
    cls_id: int
    cls_name: str
    confidence: float
    xyxy: tuple[float, float, float, float]

    @property
    def center(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.xyxy
        return (x1 + x2) / 2, (y1 + y2) / 2


@dataclass(frozen=True)
class FrameState:
    timestamp_s: float
    width: int
    height: int
    objects: tuple[ObservedObject, ...]

    @property
    def player_candidates(self) -> tuple[ObservedObject, ...]:
        return tuple(obj for obj in self.objects if obj.cls_name == "Me")

    @property
    def player(self) -> ObservedObject | None:
        candidates = self.player_candidates
        return candidates[0] if len(candidates) == 1 else None

    @property
    def player_status(self) -> str:
        count = len(self.player_candidates)
        return "missing" if count == 0 else ("visible" if count == 1 else "ambiguous")

    def to_dict(self) -> dict:
        result = asdict(self)
        result["player_status"] = self.player_status
        return result


def build_state(
    detections: Iterable[Detection],
    *,
    width: int,
    height: int,
    timestamp_s: float,
) -> FrameState:
    """Validate geometry and normalize into [0, 1] without changing the input.

    Boxes crossing the frame boundary are clipped. Empty boxes, nonfinite
    values, negative class IDs and invalid confidences are rejected.
    """
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise ValueError("Frame width and height must be positive integers.")
    if not math.isfinite(timestamp_s) or timestamp_s < 0:
        raise ValueError("timestamp_s must be finite and nonnegative.")

    objects = []
    for det in detections:
        if not isinstance(det.cls_id, int) or isinstance(det.cls_id, bool) or det.cls_id < 0:
            raise ValueError("Class IDs must be nonnegative integers.")
        if not det.cls_name or not math.isfinite(det.confidence) or not 0 <= det.confidence <= 1:
            raise ValueError("Each detection needs a class name and confidence in [0, 1].")
        if len(det.xyxy) != 4 or not all(math.isfinite(v) for v in det.xyxy):
            raise ValueError("Each bounding box needs four finite coordinates.")
        x1, y1, x2, y2 = det.xyxy
        if x2 <= x1 or y2 <= y1:
            raise ValueError("Bounding boxes must have positive area.")
        clipped = (
            min(1.0, max(0.0, x1 / width)),
            min(1.0, max(0.0, y1 / height)),
            min(1.0, max(0.0, x2 / width)),
            min(1.0, max(0.0, y2 / height)),
        )
        if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
            raise ValueError("Bounding box lies outside the frame.")
        objects.append(ObservedObject(det.cls_id, det.cls_name, det.confidence, clipped))
    objects.sort(key=lambda obj: (obj.cls_id, -obj.confidence, obj.xyxy, obj.cls_name))
    return FrameState(float(timestamp_s), width, height, tuple(objects))
