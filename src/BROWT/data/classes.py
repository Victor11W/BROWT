"""Preserve the YOLO label-index mapping across list and dictionary exports."""

from __future__ import annotations


def normalize_names(names: list | dict) -> dict[int, str]:
    if isinstance(names, list):
        result = dict(enumerate(names))
    elif isinstance(names, dict):
        result = {}
        for key, value in names.items():
            if isinstance(key, bool):
                raise ValueError("Boolean class IDs are invalid.")
            index = int(key)
            if str(index) != str(key) or index in result:
                raise ValueError("Class IDs must be unique nonnegative integers.")
            result[index] = value
    else:
        raise ValueError("data.yaml names must be a list or dictionary.")
    if not result or sorted(result) != list(range(len(result))):
        raise ValueError("Class IDs must be contiguous, starting at zero.")
    if any(not isinstance(name, str) or not name.strip() for name in result.values()):
        raise ValueError("Every class must have a nonempty string name.")
    if len(set(result.values())) != len(result):
        raise ValueError("Class names must be unique.")
    return dict(sorted(result.items()))
