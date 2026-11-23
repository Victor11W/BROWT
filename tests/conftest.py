from __future__ import annotations

import struct
import zlib

import pytest
import yaml


def png_bytes(red: int) -> bytes:
    def chunk(name, data):
        return (
            struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes([0, red, 32, 64])))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "data.yaml").write_text(yaml.safe_dump({"names": ["Me", "Enemy"]}), encoding="utf-8")
    for split_index, split in enumerate(("train", "valid", "test")):
        images, labels = root / split / "images", root / split / "labels"
        images.mkdir(parents=True)
        labels.mkdir(parents=True)
        for index in range(2):
            (images / f"{index}.png").write_bytes(png_bytes(10 + split_index * 20 + index))
            (labels / f"{index}.txt").write_text(f"{index} 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    return root
