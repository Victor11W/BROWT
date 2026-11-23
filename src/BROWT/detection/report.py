"""Evaluate a checkpoint into a traceable report. Validation is the default split."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from BROWT.data.audit import audit_dataset
from BROWT.data.classes import normalize_names
from BROWT.detection.dataset import write_data_yaml
from BROWT.detection.detector import Detector
from BROWT.detection.train import load_config, repo_root, resolve_device
from BROWT.research.provenance import Experiment, file_hash, write_json

START = "<!-- RESULTS:START -->"
END = "<!-- RESULTS:END -->"


def _fmt(value: float) -> str:
    return f"{value:.3f}" if math.isfinite(value) else "n/a"


def extract_metrics(metrics, names: dict[int, str]) -> dict:
    box = metrics.box

    def finite(value) -> float | None:
        value = float(value)
        return value if math.isfinite(value) else None

    evaluated = {int(c): finite(ap) for c, ap in zip(box.ap_class_index, box.ap50)}
    return {
        "map50": finite(box.map50),
        "map50_95": finite(box.map),
        "precision": finite(box.mp),
        "recall": finite(box.mr),
        "latency_ms_per_image": {key: finite(value) for key, value in metrics.speed.items()},
        "per_class_ap50": {name: evaluated.get(index) for index, name in names.items()},
    }


def build_markdown(
    weights: Path,
    metrics,
    names: dict[int, str],
    epochs: int,
    size: str,
    *,
    split: str = "val",
    imgsz: int = 640,
    device: str = "unknown",
) -> str:
    box = metrics.box
    infer_ms = float(metrics.speed.get("inference", float("nan")))
    lines = [
        "# Detector evaluation",
        "",
        f"Checkpoint: `{weights.as_posix()}`",
        f"Configuration: YOLO11{size}, image size {imgsz}, {epochs} epochs configured.",
        f"Evaluation split: **{split}**. Backend: **{device}**.",
        "",
        "Configured epochs do not establish how many epochs the checkpoint actually completed.",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| mAP@50 | {_fmt(box.map50)} |",
        f"| mAP@50–95 | {_fmt(box.map)} |",
        f"| Precision | {_fmt(box.mp)} |",
        f"| Recall | {_fmt(box.mr)} |",
        f"| Inference latency ({device}) | {_fmt(infer_ms)} ms/image |",
        "",
        "This latency covers model inference only, not capture, input injection or the full loop.",
        "",
        "| Class | AP@50 |",
        "|-------|-------|",
    ]
    per_class = {int(c): float(ap) for c, ap in zip(box.ap_class_index, box.ap50)}
    for index, name in sorted(names.items()):
        value = _fmt(per_class[index]) if index in per_class else "n/a (not evaluated)"
        lines.append(f"| {name} | {value} |")
    lines += ["", "No confidence interval is inferred from a single run."]
    return "\n".join(lines)


def patch_readme(readme: Path, block: str) -> None:
    """Explicit optional publication, with exactly one ordered marker pair."""
    text = readme.read_text(encoding="utf-8")
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) >= text.index(END):
        raise ValueError(f"Expected one ordered result-marker pair in {readme}")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    readme.write_text(f"{before}{START}\n{block}\n{END}{after}", encoding="utf-8")


def main() -> None:
    root = repo_root()
    parser = argparse.ArgumentParser(description="Evaluate a checkpoint with provenance.")
    parser.add_argument("--config", type=Path, default=root / "configs/detection.yaml")
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--split", choices=["val", "test"], default="val")
    parser.add_argument("--update-readme", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    names_root = (root / config["data"]["root"]).resolve()
    audit = audit_dataset(names_root, splits=("valid" if args.split == "val" else "test",))
    if not audit["valid"]:
        raise ValueError("Dataset audit failed. Run browt audit before evaluation.")
    if not args.weights.is_file():
        raise FileNotFoundError(args.weights)
    names = normalize_names(audit["classes"])
    device = resolve_device(config["train"]["device"])
    run = Experiment(
        root / "runs/evaluation",
        task="detection-evaluation",
        data_kind="observed",
        repo=root,
        config={
            "settings": config,
            "split": args.split,
            "device": device,
            "weights_sha256": file_hash(args.weights),
            "dataset_sha256": audit["fingerprint_sha256"],
        },
    )
    try:
        data_yaml = write_data_yaml(names_root, run.folder / "data.resolved.yaml")
        detector = Detector(args.weights, device=device, imgsz=config["train"]["imgsz"])
        metrics = detector._model.val(
            data=str(data_yaml),
            split=args.split,
            device=device,
            imgsz=config["train"]["imgsz"],
            project=str(run.folder),
            name="evaluation",
            exist_ok=False,
            verbose=False,
        )
        block = build_markdown(
            args.weights,
            metrics,
            names,
            config["train"]["epochs"],
            config["model"]["size"],
            split=args.split,
            imgsz=config["train"]["imgsz"],
            device=device,
        )
        markdown_path = run.folder / "report.md"
        markdown_path.write_text(block + "\n", encoding="utf-8")
        json_path = run.folder / "metrics.json"
        values = extract_metrics(metrics, names)
        write_json(json_path, values)
        run.complete(metrics=values, artifacts=[data_yaml, markdown_path, json_path])
        if args.update_readme:
            patch_readme(root / "src/BROWT/detection/README.md", block)
        print(f"Evaluation: {run.folder}")
    except BaseException as error:
        run.fail(error)
        raise


if __name__ == "__main__":
    main()
