from pathlib import Path
from types import SimpleNamespace

import pytest

from BROWT.detection.report import build_markdown, extract_metrics, patch_readme


@pytest.fixture
def metrics():
    return SimpleNamespace(
        box=SimpleNamespace(map50=0.8, map=0.6, mp=0.75, mr=0.7, ap_class_index=[0], ap50=[0.9]),
        speed={"inference": 2.5},
    )


def test_report_uses_real_backend_size_and_missing_class_marker(metrics):
    text = build_markdown(
        Path("best.pt"),
        metrics,
        {0: "Me", 1: "Enemy"},
        10,
        "n",
        device="cpu",
        imgsz=320,
        split="val",
    )
    assert "MPS" not in text
    assert "image size 320" in text
    assert "Inference latency (cpu)" in text
    assert "Enemy | n/a (not evaluated)" in text
    assert extract_metrics(metrics, {0: "Me", 1: "Enemy"})["per_class_ap50"]["Enemy"] is None


@pytest.mark.parametrize(
    "text",
    [
        "missing",
        "<!-- RESULTS:END --><!-- RESULTS:START -->",
        "<!-- RESULTS:START --><!-- RESULTS:START --><!-- RESULTS:END -->",
    ],
)
def test_patch_rejects_ambiguous_marker_layout(tmp_path, text):
    target = tmp_path / "README.md"
    target.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        patch_readme(target, "results")
    assert target.read_text(encoding="utf-8") == text
