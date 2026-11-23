import json
import os
import subprocess
import sys
from pathlib import Path


def test_doctor_runs_without_the_vision_stack():
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=str(root / "src"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "BROWT", "doctor", "--json"],
        cwd=root,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    report = json.loads(result.stdout)
    assert report["python_supported"]
    assert "ultralytics" in report["packages"]
    assert "nvidia-smi" in report["tools"]


def test_doctor_follows_the_configured_dataset_path(tmp_path):
    from BROWT.research.doctor import diagnose

    config = tmp_path / "configs/detection.yaml"
    config.parent.mkdir()
    config.write_text("data:\n  root: imported/export\n", encoding="utf-8")
    dataset = tmp_path / "imported/export"
    dataset.mkdir(parents=True)
    (dataset / "data.yaml").write_text("names: [Me]\n", encoding="utf-8")
    report = diagnose(tmp_path)
    assert report["local_assets"]["dataset_yaml"] is True
    assert Path(report["local_assets"]["dataset_root"]) == dataset
