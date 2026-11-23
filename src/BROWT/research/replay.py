"""Normalize a recorded detection sequence into a traceable offline experiment."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from BROWT.research.provenance import Experiment, file_hash
from BROWT.state.replay import iter_replay


def run_replay(source: Path, output: Path) -> Path:
    iterator = iter_replay(source)
    first = next(iterator)
    repo = Path(__file__).resolve().parents[3]
    run = Experiment(
        output,
        task="detection-replay",
        data_kind=first.data_kind,
        repo=repo,
        config={"source_id": first.source_id, "source_sha256": file_hash(source)},
    )
    try:
        target = run.folder / "states.jsonl"
        count = missing = ambiguous = 0
        with target.open("w", encoding="utf-8", newline="\n") as stream:
            for frame in itertools.chain([first], iterator):
                stream.write(json.dumps(frame.state.to_dict(), allow_nan=False) + "\n")
                count += 1
                missing += frame.state.player_status == "missing"
                ambiguous += frame.state.player_status == "ambiguous"
        run.complete(
            metrics={"frames": count, "missing_player": missing, "ambiguous_player": ambiguous},
            artifacts=[target],
        )
        return target
    except BaseException as error:
        run.fail(error)
        raise
