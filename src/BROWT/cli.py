"""A small entry point; GPU and plotting packages are imported only when needed."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(prog="browt", description="BROWT visual research laboratory")
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser(
        "doctor", help="Inspect installed tools without changing the machine"
    )
    doctor.add_argument("--json", action="store_true", dest="as_json")
    demo = commands.add_parser(
        "demo", help="Generate a clearly labelled synthetic research demonstration"
    )
    demo.add_argument("--output", type=Path, default=Path("runs/research"))
    demo.add_argument(
        "--figures", type=Path, help="Also refresh the derived figures in this directory"
    )
    demo.add_argument("--seed", type=int, default=0)
    audit = commands.add_parser(
        "audit", help="Audit YOLO labels and exact duplicates without moving data"
    )
    audit.add_argument("--dataset", type=Path, required=True)
    audit.add_argument("--output", type=Path, default=Path("runs/dataset-audit.json"))
    audit.add_argument(
        "--splits",
        nargs="+",
        choices=["train", "valid", "test"],
        default=["train", "valid", "test"],
    )
    replay = commands.add_parser("replay", help="Normalize a logged detection sequence offline")
    replay.add_argument("--source", type=Path, required=True)
    replay.add_argument("--output", type=Path, default=Path("runs/replay"))
    acquisition = commands.add_parser(
        "import-dataset", help="Import a local public dataset ZIP without overwriting sources"
    )
    acquisition.add_argument("--archive", type=Path, required=True)
    acquisition.add_argument("--destination", type=Path, required=True)
    acquisition.add_argument(
        "--source-config", type=Path, default=Path("configs/dataset-source.yaml")
    )
    acquisition.add_argument("--expected-sha256")
    data_report = commands.add_parser(
        "dataset-report", help="Profile exported images and reference annotations"
    )
    data_report.add_argument("--dataset", type=Path, required=True)
    data_report.add_argument("--output", type=Path, default=Path("runs/dataset-study"))
    data_report.add_argument("--provenance", type=Path)
    data_report.add_argument(
        "--splits", nargs="+", choices=["train", "valid", "test"], default=["train", "valid"]
    )
    data_report.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if args.command == "doctor":
        from BROWT.research.doctor import show_doctor

        show_doctor(as_json=args.as_json)
    elif args.command == "demo":
        from BROWT.research.demo import run_demo

        print(run_demo(args.output, seed=args.seed, figures=args.figures))
    elif args.command == "replay":
        from BROWT.research.replay import run_replay

        print(run_replay(args.source, args.output))
    elif args.command == "import-dataset":
        import json

        from BROWT.data.acquire import import_dataset

        result = import_dataset(
            args.archive, args.destination, args.source_config, expected_sha256=args.expected_sha256
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] == "needs_review":
            raise SystemExit(1)
    elif args.command == "dataset-report":
        from BROWT.research.dataset_report import dataset_report

        print(
            dataset_report(
                args.dataset,
                args.output,
                splits=tuple(args.splits),
                seed=args.seed,
                provenance=args.provenance,
            )
        )
    elif args.command == "audit":
        from BROWT.data.audit import audit_dataset
        from BROWT.research.provenance import write_json

        report = audit_dataset(args.dataset, splits=tuple(args.splits))
        write_json(args.output, report)
        print(f"{len(report['issues'])} issue(s); report: {args.output}")
        if report["issues"]:
            raise SystemExit(1)
