"""Filename-time proximity is a leakage diagnostic, not proof of independence."""

from __future__ import annotations

import bisect
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PATTERN = re.compile(r"Screenshot_(\d{8})-(\d{6})", re.IGNORECASE)


def temporal_neighbors(rows: list[dict]) -> dict:
    matched = []
    days = defaultdict(lambda: {"train": 0, "valid": 0, "test": 0})
    for row in rows:
        match = PATTERN.search(Path(row["image"]).name)
        if not match:
            continue
        try:
            nominal = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S")
        except ValueError:
            continue
        matched.append((nominal, row["image"], row["split"]))
        days[nominal.date().isoformat()][row["split"]] += 1
    training = sorted((time, image) for time, image, split in matched if split == "train")
    validation = sorted((time, image) for time, image, split in matched if split == "valid")
    train_times = [time for time, _ in training]
    pairs = []
    for time, image in validation:
        index = bisect.bisect_left(train_times, time)
        candidates = training[max(0, index - 1) : min(len(training), index + 1)]
        if not candidates:
            continue
        neighbor_time, neighbor_image = min(
            candidates,
            key=lambda item: (abs((item[0] - time).total_seconds()), item[1]),
        )
        pairs.append(
            {
                "valid": image,
                "train": neighbor_image,
                "valid_nominal_time": time.isoformat(),
                "train_nominal_time": neighbor_time.isoformat(),
                "gap_seconds": abs((neighbor_time - time).total_seconds()),
            }
        )
    pairs.sort(key=lambda pair: (pair["gap_seconds"], pair["valid"], pair["train"]))
    return {
        "method": "nearest train filename timestamp for each matched validation image",
        "clock": "nominal filename time; timezone and device identity unverified",
        "matched_images": len(matched),
        "unmatched_images": len(rows) - len(matched),
        "matched_validation_images": len(validation),
        "paired_validation_images": len(pairs),
        "within_2_seconds": sum(pair["gap_seconds"] <= 2 for pair in pairs),
        "within_10_seconds": sum(pair["gap_seconds"] <= 10 for pair in pairs),
        "days": dict(sorted(days.items())),
        "pairs": pairs,
        "interpretation": "Temporal proximity flags possible correlation; it is not an independent test.",
    }
