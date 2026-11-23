"""Read the versioned detection-log format without an emulator or a model."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from BROWT.detection.detector import Detection
from BROWT.state.observation import FrameState, build_state


@dataclass(frozen=True)
class ReplayFrame:
    state: FrameState
    data_kind: str
    source_id: str


def iter_replay(source: Path) -> Iterator[ReplayFrame]:
    previous = -1.0
    identity = None
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if record.get("schema_version") != 1:
                    raise ValueError("Unsupported schema_version.")
                kind = record["data_kind"]
                source_id = record["source_id"]
                if (
                    kind not in {"observed", "synthetic"}
                    or not isinstance(source_id, str)
                    or not source_id
                ):
                    raise ValueError("Specify data_kind and a nonempty source_id.")
                if identity is not None and identity != (kind, source_id):
                    raise ValueError(
                        "A replay cannot mix source identities or real and synthetic inputs."
                    )
                identity = (kind, source_id)
                detections = [
                    Detection(
                        item["cls_id"],
                        item["cls_name"],
                        float(item["confidence"]),
                        tuple(item["xyxy"]),
                    )
                    for item in record["detections"]
                ]
                state = build_state(
                    detections,
                    width=record["width"],
                    height=record["height"],
                    timestamp_s=record["timestamp_s"],
                )
                if state.timestamp_s <= previous:
                    raise ValueError("Timestamps must be strictly increasing within a replay.")
                previous = state.timestamp_s
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                raise ValueError(f"{source.name}, line {line_number}: {error}") from error
            yield ReplayFrame(state, kind, source_id)
    if identity is None:
        raise ValueError("Replay is empty.")
