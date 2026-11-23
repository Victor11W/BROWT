"""Describe a real exported dataset without confusing annotations and predictions."""

from __future__ import annotations

import html
import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle
from PIL import Image

from BROWT.data.audit import audit_dataset
from BROWT.data.temporal import temporal_neighbors
from BROWT.research.provenance import Experiment, write_json
from BROWT.research.temporal_plots import make_temporal_figures


def profile_images(dataset: Path, audit: dict) -> tuple[list[dict], list[dict]]:
    """Fully decode images, collect dimensions, and retain invalid files as issues."""
    rows, issues = [], []
    names = {int(key): value for key, value in audit["classes"].items()}
    for sample in audit["samples"]:
        relative = Path(sample["image"])
        image_path = dataset / relative
        try:
            with Image.open(image_path) as image:
                image.load()
                width, height = image.size
                if width < 1 or height < 1:
                    raise ValueError("Image dimensions must be positive.")
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            issues.append(
                {"path": sample["image"], "code": "image_decode_failed", "message": str(error)}
            )
            continue
        label_path = dataset / relative.parts[0] / "labels" / (relative.stem + ".txt")
        boxes = []
        if label_path.is_file() and label_path.resolve().is_relative_to(dataset.resolve()):
            try:
                lines = label_path.read_text(encoding="utf-8").splitlines()
            except UnicodeError:
                lines = []
            for line in lines:
                try:
                    fields = line.split()
                    if len(fields) != 5:
                        continue
                    cls_id = int(fields[0])
                    x, y, w, h = map(float, fields[1:])
                    if cls_id not in names or not np.isfinite([x, y, w, h]).all():
                        continue
                    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
                        continue
                    if min(x - w / 2, y - h / 2) < -1e-6 or max(x + w / 2, y + h / 2) > 1 + 1e-6:
                        continue
                except ValueError:
                    continue
                boxes.append(
                    {
                        "cls_id": cls_id,
                        "cls_name": names[cls_id],
                        "xywh": [x, y, w, h],
                        "area_fraction": w * h,
                    }
                )
        rows.append(
            {
                "image": sample["image"],
                "split": relative.parts[0],
                "width": width,
                "height": height,
                "boxes": boxes,
            }
        )
    return rows, issues


def make_dataset_figures(
    dataset: Path, rows: list[dict], names: dict[int, str], output: Path, *, seed: int = 0
) -> tuple[list[Path], list[str]]:
    if not rows:
        raise ValueError("No decodable images to visualize.")
    counts = Counter(box["cls_name"] for row in rows for box in row["boxes"])
    order = sorted(names.values(), key=lambda name: (-counts[name], name))
    splits = list(dict.fromkeys(row["split"] for row in rows))
    palette = ["#0072B2", "#D55E00", "#009E73"]
    style = {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "svg.fonttype": "none",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "text.color": "#17263C",
        "axes.labelcolor": "#526078",
        "xtick.color": "#526078",
        "ytick.color": "#526078",
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    }
    with plt.rc_context(style):
        figure, axes = plt.subplots(2, 2, figsize=(13.8, 10), layout="constrained")
        figure.suptitle(
            "BROWT  /  Comprendre les données avant d’apprendre", fontsize=20, fontweight="bold"
        )
        axis = axes[0, 0]
        bottom = np.zeros(len(order))
        for split, color in zip(splits, palette):
            support = Counter(
                box["cls_name"] for row in rows if row["split"] == split for box in row["boxes"]
            )
            values = np.asarray([support[name] for name in order])
            axis.barh(order, values, left=bottom, color=color, label=split)
            bottom += values
        axis.invert_yaxis()
        axis.set_title("A  ·  Support des classes", loc="left", pad=14)
        axis.set_xlabel("Boîtes de référence valides (effectif)")
        axis.legend(frameon=False, loc="lower right")
        axis.grid(axis="x", alpha=0.12)
        axis.set_axisbelow(True)

        axis = axes[0, 1]
        ratios = [row["width"] / row["height"] for row in rows]
        upper_ratio = max(3.0, max(ratios) * 1.05)
        ratio_bins = np.linspace(0, upper_ratio, 31)
        for split, color in zip(splits, palette):
            values = [row["width"] / row["height"] for row in rows if row["split"] == split]
            axis.hist(
                values, bins=ratio_bins, histtype="step", linewidth=1.8, color=color, label=split
            )
        dimensions = Counter((row["width"], row["height"]) for row in rows)
        (width, height), support = dimensions.most_common(1)[0]
        axis.set_title("B  ·  Format des images", loc="left", pad=14)
        axis.set(
            xlim=(0, upper_ratio), xlabel="Rapport largeur / hauteur", ylabel="Images (effectif)"
        )
        axis.text(
            0.03,
            0.95,
            f"{support} / {len(rows)} images en {width} × {height} px",
            transform=axis.transAxes,
            va="top",
            fontsize=9,
        )
        axis.legend(frameon=False)
        axis.grid(axis="y", alpha=0.12)

        axis = axes[1, 0]
        areas = [100 * box["area_fraction"] for row in rows for box in row["boxes"]]
        if areas:
            minimum, maximum = min(areas), max(areas)
            bins = np.geomspace(max(minimum * 0.9, 1e-9), max(maximum * 1.1, minimum * 2), 35)
            axis.hist(areas, bins=bins, color=palette[0], alpha=0.85)
            axis.set_xscale("log")
        axis.set_title("C  ·  Taille des objets annotés", loc="left", pad=14)
        axis.set(
            xlabel="Surface de la boîte / surface de l’image (%)",
            ylabel="Boîtes valides (effectif)",
        )
        axis.grid(axis="y", alpha=0.12)

        axis = axes[1, 1]
        objects = [len(row["boxes"]) for row in rows]
        bins = np.arange(max(objects) + 2) - 0.5
        axis.hist(objects, bins=bins, color=palette[0], alpha=0.85)
        axis.set_title("D  ·  Densité des annotations", loc="left", pad=14)
        axis.set(xlabel="Boîtes valides par image", ylabel="Images (effectif)")
        axis.grid(axis="y", alpha=0.12)
        paths = [output / "dataset-overview.svg", output / "dataset-overview.png"]
        for target in paths:
            figure.savefig(target, dpi=160, bbox_inches="tight")
        plt.close(figure)

        pool = [row for row in rows if row["split"] == "valid"] or rows
        chosen = np.random.default_rng(seed).choice(
            len(pool), size=min(6, len(pool)), replace=False
        )
        samples = [pool[int(index)] for index in chosen]
        figure, axes = plt.subplots(2, 3, figsize=(15, 6.1), layout="constrained")
        figure.suptitle(
            "BROWT  /  Revue d’annotations de référence", fontsize=20, fontweight="bold"
        )
        for axis, row in zip(axes.flat, samples):
            with Image.open(dataset / row["image"]) as image:
                axis.imshow(image.convert("RGB"))
            for box in row["boxes"]:
                x, y, w, h = box["xywh"]
                x1, y1 = (x - w / 2) * row["width"], (y - h / 2) * row["height"]
                axis.add_patch(
                    Rectangle(
                        (x1, y1),
                        w * row["width"],
                        h * row["height"],
                        facecolor="none",
                        edgecolor="#49C5FF",
                        linewidth=1.1,
                    )
                )
                axis.text(
                    x1,
                    y1,
                    box["cls_name"],
                    fontsize=6,
                    color="white",
                    va="bottom",
                    bbox={"facecolor": "#14233B", "alpha": 0.85, "edgecolor": "none", "pad": 1},
                )
            axis.set_title(Path(row["image"]).name[:40], fontsize=8, loc="left")
            axis.set_axis_off()
        for axis in list(axes.flat)[len(samples) :]:
            axis.set_axis_off()
        gallery = output / "annotation-gallery.png"
        figure.savefig(gallery, dpi=160, bbox_inches="tight")
        plt.close(figure)
        paths.append(gallery)
    return paths, [row["image"] for row in samples]


def dataset_report(
    dataset: Path,
    output: Path,
    *,
    splits: tuple[str, ...] = ("train", "valid"),
    seed: int = 0,
    provenance: Path | None = None,
) -> Path:
    dataset = dataset.resolve()
    audit = audit_dataset(dataset, splits=splits)
    config = {
        "dataset_sha256": audit["fingerprint_sha256"],
        "splits": list(splits),
        "sample_seed": seed,
        "scope": "reference annotations; no detector evaluation",
    }
    if provenance:
        source_info = json.loads(provenance.read_text(encoding="utf-8"))
        config["source_id"] = source_info["source"]["source_id"]
        config["archive_sha256"] = source_info["archive"]["sha256"]
    run = Experiment(
        output,
        task="dataset-profile",
        config=config,
        data_kind="observed",
        repo=Path(__file__).resolve().parents[3],
    )
    try:
        rows, decode_issues = profile_images(dataset, audit)
        temporal = temporal_neighbors(rows)
        if not rows:
            raise ValueError("No valid image could be decoded.")
        names = {int(key): name for key, name in audit["classes"].items()}
        overview, selected = make_dataset_figures(dataset, rows, names, run.folder, seed=seed)
        temporal_figures = make_temporal_figures(dataset, temporal, run.folder)
        counts = Counter(box["cls_name"] for row in rows for box in row["boxes"])
        total = sum(counts.values())
        metrics = {
            "source_images": len(audit["samples"]),
            "decoded_images": len(rows),
            "valid_reference_boxes": total,
            "annotation_issues": len(audit["issues"]),
            "decode_issues": len(decode_issues),
            "class_counts": {name: counts[name] for name in names.values()},
            "independent_test_established": False,
            "filename_timestamp_matched_images": temporal["matched_images"],
            "validation_within_2_seconds_of_train": temporal["within_2_seconds"],
            "validation_within_10_seconds_of_train": temporal["within_10_seconds"],
            "player_annotation_counts": {
                "zero": sum(
                    not any(box["cls_name"] == "Me" for box in row["boxes"]) for row in rows
                ),
                "multiple": sum(
                    sum(box["cls_name"] == "Me" for box in row["boxes"]) > 1 for row in rows
                ),
            },
        }
        audit_path, profile_path = run.folder / "audit.json", run.folder / "image-profile.json"
        samples_path = run.folder / "gallery-samples.json"
        temporal_path = run.folder / "temporal-proximity.json"
        write_json(temporal_path, temporal)
        write_json(audit_path, audit)
        write_json(profile_path, {"images": rows, "decode_issues": decode_issues})
        write_json(
            samples_path,
            {
                "seed": seed,
                "selection": "uniform without replacement from valid, or all if absent",
                "images": selected,
            },
        )
        dominant = counts.most_common(1)
        dominant_text = (
            f"{html.escape(dominant[0][0])} représente {100 * dominant[0][1] / total:.1f} % des boîtes valides."
            if dominant and total
            else "Aucune boîte valide."
        )
        warning_count = len(audit["issues"]) + len(decode_issues)
        temporal_section = ""
        temporal_warning = ""
        if temporal["pairs"]:
            temporal_warning = f'<div class="warning"><strong>Indépendance à vérifier.</strong> {temporal["within_10_seconds"]} / {temporal["paired_validation_images"]} images de validation ont un voisin train à 10 secondes ou moins d’après les noms de fichiers. Cela signale un risque de corrélation entre les lots.</div>'
            temporal_section = """<section><h2>Un découpage à réexaminer</h2><figure><a href="temporal-gaps.svg" target="_blank" rel="noopener">Agrandir la proximité temporelle</a><img src="temporal-gaps.svg" alt="Distribution des écarts nominaux entre validation et apprentissage"><figcaption>Horodatages dérivés des noms de fichiers ; fuseau et identité d’appareil non vérifiés. Le repère de dix secondes est un diagnostic exploratoire, pas une règle suffisante pour garantir l’indépendance.</figcaption></figure><figure><a href="temporal-pairs.png" target="_blank" rel="noopener">Comparer les frames proches</a><img src="temporal-pairs.png" alt="Paires de frames train et valid proches dans le temps"><figcaption>Les deux paires aux écarts les plus courts, avec départage déterministe par nom. La proximité visuelle doit être examinée avant toute conclusion de généralisation.</figcaption></figure></section>"""
        report = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>BROWT — Profil du dataset</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#eef2f6;color:#17263c;font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1200px;margin:auto;padding:42px 24px}}h1{{font-size:clamp(30px,5vw,48px);line-height:1.1;letter-spacing:-.035em}}
.kicker,footer{{color:#526078;font-size:13px}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:26px 0}}
.warning{{background:#fff0d7;color:#624400;border-radius:12px;padding:18px;margin:20px 0}}.card,figure,.note{{background:white;border-radius:12px;padding:20px}}.card strong{{font-size:30px;display:block}}
figure{{margin:22px 0}}img{{width:100%;height:auto}}figcaption{{font-size:13px;color:#526078}}a{{color:#006ca7}}
@media(max-width:650px){{main{{padding:24px 14px}}.cards{{grid-template-columns:1fr}}}}
</style></head><body><main>
<div class="kicker">BROWT / Données réelles / Annotations de référence</div>
<h1>Comprendre les données<br>avant d’apprendre.</h1>
<p>Aucune prédiction de modèle n’est utilisée dans ce rapport. Les figures décrivent les fichiers exportés et leurs annotations.</p>
<div class="cards"><div class="card"><strong>{len(rows):,}</strong>images décodées</div>
<div class="card"><strong>{total:,}</strong>boîtes valides</div>
<div class="card"><strong>{warning_count:,}</strong>anomalies structurelles</div></div>
{temporal_warning}
<div class="note">{dominant_text} Les frames ne sont pas des sessions indépendantes. Aucun test indépendant ni score de modèle n’est établi ici.</div>
<figure><a href="dataset-overview.svg" target="_blank" rel="noopener">Agrandir les distributions</a>
<img src="dataset-overview.svg" alt="Support des classes, géométrie des images et des boîtes, densité des annotations">
<figcaption>Figure 1. Comptages des annotations valides et dimensions des images décodées. Les lignes invalides restent dans l’audit et sont exclues des statistiques de boîtes ; les images indécodables restent signalées dans le profil.</figcaption></figure>
<figure><a href="annotation-gallery.png" target="_blank" rel="noopener">Agrandir les exemples annotés</a>
<img src="annotation-gallery.png" alt="Six images du dataset avec leurs boîtes de référence">
<figcaption>Figure 2. Échantillon déterministe tiré sans remise dans le lot valid, ou dans l’ensemble des images si valid est absent. Il s’agit d’annotations fournies, pas de détections du modèle.</figcaption></figure>
{temporal_section}
<footer>Run {html.escape(run.run_id)} · <a href="manifest.json">Provenance</a> · <a href="audit.json">Audit</a> ·
<a href="image-profile.json">Mesures par image</a> · <a href="gallery-samples.json">Échantillonnage</a></footer>
</main></body></html>"""
        report_path = run.folder / "report.html"
        report_path.write_text(report, encoding="utf-8")
        run.complete(
            metrics=metrics,
            artifacts=[
                audit_path,
                profile_path,
                samples_path,
                temporal_path,
                report_path,
                *overview,
                *temporal_figures,
            ],
        )
        return report_path
    except BaseException as error:
        run.fail(error)
        raise
