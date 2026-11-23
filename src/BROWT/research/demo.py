"""An offline, CPU-only demonstration; it makes no Brawl Stars performance claim."""

from __future__ import annotations

import html
import json
import math
import shutil
import time
from pathlib import Path

import numpy as np
import yaml

from BROWT.detection.detector import Detection
from BROWT.research.plots import plot_overview
from BROWT.research.provenance import Experiment, file_hash, write_json
from BROWT.research.roadmap import plot_roadmap
from BROWT.state.observation import build_state
from BROWT.state.replay import iter_replay


def synthetic_records(seed: int = 0) -> list[dict]:
    rng = np.random.default_rng(seed)
    phase = float(rng.uniform(-0.1, 0.1))
    records = []
    for index in range(48):
        t = index / 10
        positions = [
            (0, "Me", 0.28 + 0.17 * math.sin(t / 2 + phase), 0.67 - 0.05 * t, 0.055),
            (1, "Enemy", 0.76 - 0.055 * t, 0.25 + 0.045 * t, 0.06),
            (2, "PP_Box", 0.68, 0.66, 0.08),
            (3, "PP", 0.48, 0.37, 0.035),
        ]
        detections = []
        for cls_id, name, x, y, half_width in positions:
            if name == "Me" and 18 <= index <= 21:
                continue
            detections.append(
                {
                    "cls_id": cls_id,
                    "cls_name": name,
                    "confidence": 0.9,
                    "xyxy": [
                        (x - half_width) * 960,
                        (y - half_width) * 540,
                        (x + half_width) * 960,
                        (y + half_width) * 540,
                    ],
                }
            )
        records.append(
            {
                "schema_version": 1,
                "data_kind": "synthetic",
                "source_id": f"geometry-v1-seed-{seed}",
                "timestamp_s": t,
                "width": 960,
                "height": 540,
                "detections": detections,
            }
        )
    return records


def run_demo(output: Path, *, seed: int = 0, figures: Path | None = None) -> Path:
    repo = Path(__file__).resolve().parents[3]
    config = {"seed": seed, "scene_version": 1, "frames": 48, "warmup": 50, "repetitions": 1200}
    plan_path = repo / "configs/research-plan.yaml"
    config["research_plan_sha256"] = file_hash(plan_path)
    run = Experiment(
        output, task="observation-demo", config=config, data_kind="synthetic", repo=repo
    )
    try:
        records = synthetic_records(seed)
        replay_path = run.folder / "detections.jsonl"
        replay_path.write_text(
            "".join(json.dumps(record, allow_nan=False) + "\n" for record in records),
            encoding="utf-8",
        )
        states = [frame.state for frame in iter_replay(replay_path)]
        states_path = run.folder / "states.jsonl"
        states_path.write_text(
            "".join(json.dumps(state.to_dict(), allow_nan=False) + "\n" for state in states),
            encoding="utf-8",
        )
        fixture = records[0]
        detections = [
            Detection(item["cls_id"], item["cls_name"], item["confidence"], tuple(item["xyxy"]))
            for item in fixture["detections"]
        ]
        durations = []
        for index in range(config["warmup"] + config["repetitions"]):
            start = time.perf_counter_ns()
            build_state(detections, width=960, height=540, timestamp_s=0)
            elapsed = (time.perf_counter_ns() - start) / 1000
            if index >= config["warmup"]:
                durations.append(elapsed)
        timing_path = run.folder / "conversion-latency-us.json"
        write_json(
            timing_path,
            {
                "unit": "microseconds",
                "scope": "build_state only",
                "warmup_excluded": config["warmup"],
                "samples": durations,
            },
        )
        p50, p95 = map(float, np.percentile(durations, [50, 95]))
        summary = {
            "data_kind": "synthetic",
            "frames": len(states),
            "player_visible_frames": sum(state.player_status == "visible" for state in states),
            "player_missing_frames": sum(state.player_status == "missing" for state in states),
            "conversion_cpu_p50_us": p50,
            "conversion_cpu_p95_us": p95,
            "input_sha256": file_hash(replay_path),
            "claim": "Geometry fixture and local conversion microbenchmark; no game, YOLO or RL measurement.",
        }
        summary_path = run.folder / "summary.json"
        write_json(summary_path, summary)
        plots = plot_overview(states, durations, run.folder)
        roadmap_plots = plot_roadmap(
            yaml.safe_load(plan_path.read_text(encoding="utf-8")), run.folder
        )
        roadmap_svg = roadmap_plots[0].read_text(encoding="utf-8")
        roadmap_svg = roadmap_svg[roadmap_svg.index("<svg") :]
        svg = plots[0].read_text(encoding="utf-8")
        svg = svg[svg.index("<svg") :]
        run_label = html.escape(run.run_id)
        report = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BROWT — Chaîne d’observation</title>
<style>
:root{{color-scheme:light}}*{{box-sizing:border-box}}body{{margin:0;background:#eef2f6;color:#17263c;font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1200px;margin:auto;padding:48px 28px}}.eyebrow{{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:#345982}}
h1{{font-size:clamp(30px,5vw,52px);line-height:1.1;letter-spacing:-.04em;margin:18px 0}}
.lead{{max-width:820px;color:#51617a}}.badge{{display:inline-block;background:#fff0ce;color:#654500;border-radius:6px;padding:5px 10px;font-weight:650;font-size:13px}}
.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:28px 0}}.card,figure{{margin:0;background:white;border-radius:12px;padding:22px}}
.card strong{{display:block;font-size:30px}}.card span{{font-size:13px;color:#51617a}}figure{{padding:18px}}svg{{width:100%;height:auto}}
figcaption,footer{{font-size:13px;color:#51617a}}footer{{margin-top:24px}}a{{color:#006ca7}}code{{overflow-wrap:anywhere}}
@media(max-width:650px){{main{{padding:24px 14px}}.cards{{grid-template-columns:1fr}}.card{{padding:14px}}}}
</style></head><body><main>
<div class="eyebrow">BROWT / Carnet d’expériences / Observation</div>
<h1>Voir ce que l’agent perçoit.</h1><span class="badge">Démonstration synthétique · aucune performance du jeu mesurée</span>
<p class="lead">Une séquence géométrique permet de vérifier les coordonnées, les observations manquantes et la traçabilité avant d’ajouter un modèle de vision et un émulateur.</p>
<div class="cards"><div class="card"><strong>{len(states)}</strong><span>images synthétiques relues</span></div>
<div class="card"><strong>{summary["player_missing_frames"]}</strong><span>images sans joueur, signalées explicitement</span></div>
<div class="card"><strong>{p95:.1f} µs</strong><span>p95 CPU local · conversion seule</span></div></div>
<figure><a href="observation-overview.svg" target="_blank" rel="noopener">Agrandir la planche d’observation</a>{svg}<figcaption>Figure 1. A–C : données artificielles. D : chronométrage réel du code de conversion sur ces données ; capture, inférence YOLO, commandes ADB et apprentissage exclus. Aucun score mAP, taux de victoire ou progrès RL n’est déduit de cette démonstration.</figcaption></figure>
<section><h2>Le parcours de recherche</h2><figure><a href="research-roadmap.svg" target="_blank" rel="noopener">Agrandir le parcours de recherche</a>{roadmap_svg}<figcaption>Plan indicatif, sans promesse de performance. La disponibilité du calcul et les résultats des pilotes déterminent le rythme.</figcaption></figure></section>
<footer>Expérience <code>{run_label}</code> · <a href="manifest.json">Provenance</a> · <a href="summary.json">Mesures</a> · <a href="detections.jsonl">Entrées</a> · <a href="states.jsonl">Observations</a><br>
Ces appels répétés ne sont pas des parties indépendantes. Les durées dépendent de la machine et de sa charge.</footer>
</main></body></html>"""
        report_path = run.folder / "report.html"
        report_path.write_text(report, encoding="utf-8")
        artifacts = [
            replay_path,
            states_path,
            timing_path,
            summary_path,
            report_path,
            *plots,
            *roadmap_plots,
        ]
        run.complete(metrics=summary, artifacts=artifacts)
        if figures:
            figures.mkdir(parents=True, exist_ok=True)
            for source in [*plots, *roadmap_plots]:
                shutil.copy2(source, figures / source.name)
            shutil.copy2(run.folder / "manifest.json", figures / "demo-manifest.json")
            shutil.copy2(timing_path, figures / "demo-latency-us.json")
            shutil.copy2(replay_path, figures / "demo-detections.jsonl")
            shutil.copy2(states_path, figures / "demo-states.jsonl")
            write_json(
                figures / "demo-summary.json",
                {
                    **summary,
                    "run_id": run.run_id,
                    "environment": run.manifest["environment"],
                },
            )
        return report_path
    except BaseException as error:
        run.fail(error)
        raise
