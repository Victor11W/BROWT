"""Train a reproducible YOLO11 experiment without modifying the source dataset.

Normal runs use train/validation only. The locked test set is evaluated only
with --evaluate-test. Smoke runs never evaluate test or publish checkpoints.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import yaml

from BROWT.data.audit import audit_dataset
from BROWT.detection.dataset import write_data_yaml
from BROWT.research.provenance import Experiment, file_hash, write_json


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch

    if torch.cuda.is_available():
        return "0"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_config(path: Path) -> dict:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("The configuration must be a YAML mapping.")
    return config


def publish_checkpoint(best: Path, destination: Path) -> None:
    """Explicitly publish once. Never replace an existing checkpoint."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with destination.open("xb") as target:
            created = True
            with best.open("rb") as source:
                shutil.copyfileobj(source, target)
        if file_hash(best) != file_hash(destination):
            raise OSError("Published checkpoint hash mismatch.")
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a traceable Brawl Stars YOLO11 detector.")
    parser.add_argument("--config", type=Path, default=repo_root() / "configs/detection.yaml")
    parser.add_argument("--smoke", action="store_true", help="One epoch on 5% of training data.")
    parser.add_argument(
        "--evaluate-test", action="store_true", help="Final locked-test evaluation."
    )
    parser.add_argument(
        "--publish", action="store_true", help="Copy best weights once to data/models."
    )
    args = parser.parse_args()
    if args.smoke and (args.publish or args.evaluate_test):
        parser.error("--smoke cannot be combined with --publish or --evaluate-test.")

    root = repo_root()
    config = load_config(args.config)
    dataset_root = (root / config["data"]["root"]).resolve()
    splits = ("train", "valid", "test") if args.evaluate_test else ("train", "valid")
    audit = audit_dataset(dataset_root, splits=splits)
    if not audit["valid"]:
        codes = sorted({issue["code"] for issue in audit["issues"]})
        raise ValueError("Dataset audit failed: " + ", ".join(codes) + ". Run browt audit.")
    size = config["model"]["size"]
    published = root / config["output"]["weights_dir"] / f"brawl_yolo11{size}.pt"
    if args.publish and published.exists():
        raise FileExistsError(f"Refusing to replace an existing model: {published}")
    device = resolve_device(config["train"]["device"])
    experiment_config = {
        "settings": config,
        "smoke": args.smoke,
        "evaluate_test": args.evaluate_test,
        "publish": args.publish,
        "device": device,
        "dataset_sha256": audit["fingerprint_sha256"],
    }
    run = Experiment(
        root / config["output"]["project"],
        task="detection-smoke" if args.smoke else "detection-train",
        config=experiment_config,
        data_kind="observed",
        repo=root,
    )
    try:
        from ultralytics import YOLO

        audit_path = run.folder / "dataset-audit.json"
        write_json(audit_path, audit)
        data_yaml = write_data_yaml(dataset_root, destination=run.folder / "data.resolved.yaml")
        train_cfg = config["train"]
        pretrained = config["model"]["pretrained"]
        weights = str((root / pretrained).resolve()) if pretrained else f"yolo11{size}.pt"
        model = YOLO(weights)
        checkpoint = Path(str(getattr(model, "ckpt_path", None) or weights))
        run.manifest["input_weights_sha256"] = (
            file_hash(checkpoint) if checkpoint.is_file() else None
        )
        run._write()
        kwargs = {
            "data": str(data_yaml),
            "epochs": 1 if args.smoke else train_cfg["epochs"],
            "imgsz": train_cfg["imgsz"],
            "batch": train_cfg["batch"],
            "patience": train_cfg["patience"],
            "device": device,
            "workers": train_cfg["workers"],
            "seed": train_cfg["seed"],
            "deterministic": True,
            "project": str(run.folder),
            "name": "model",
            "exist_ok": False,
        }
        if args.smoke:
            kwargs["fraction"] = 0.05
        model.train(**kwargs)
        best = Path(model.trainer.best)
        if not best.is_file():
            raise FileNotFoundError("Training did not produce a best checkpoint.")
        artifacts = [audit_path, data_yaml, best]
        metrics = {"test_evaluated": False}
        if args.evaluate_test:
            result = YOLO(str(best)).val(
                data=str(data_yaml),
                split="test",
                device=device,
                imgsz=train_cfg["imgsz"],
            )
            metrics.update(
                test_evaluated=True,
                test_map50=float(result.box.map50),
                test_map50_95=float(result.box.map),
            )
        if args.publish:
            publish_checkpoint(best, published)
            run.manifest["published_checkpoint"] = {
                "path": str(published.relative_to(root)),
                "sha256": file_hash(published),
            }
        run.complete(metrics=metrics, artifacts=artifacts)
        print(f"Experiment: {run.folder}\nBest weights: {best}")
    except BaseException as error:
        run.fail(error)
        raise


if __name__ == "__main__":
    main()
