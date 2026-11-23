"""Visualize filename-time proximity with the diagnostic's uncertainty intact."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image


def make_temporal_figures(dataset: Path, summary: dict, output: Path) -> list[Path]:
    pairs = summary["pairs"]
    if not pairs:
        return []
    style = {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "svg.fonttype": "none",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "text.color": "#17263C",
        "axes.labelcolor": "#526078",
        "figure.facecolor": "white",
        "axes.facecolor": "white",
    }
    paths = []
    with plt.rc_context(style):
        figure, axis = plt.subplots(figsize=(11, 4.8), layout="constrained")
        gaps = np.sort([pair["gap_seconds"] for pair in pairs])
        cumulative = np.arange(1, len(gaps) + 1) / len(gaps) * 100
        axis.step(np.r_[0, gaps], np.r_[0, cumulative], where="post", color="#0072B2", linewidth=2)
        axis.axvline(10, color="#D55E00", linestyle="--", label="Repère diagnostique : 10 s")
        axis.set_xscale("symlog", linthresh=1)
        axis.set(
            xlim=(0, max(20, gaps[-1] * 1.1)),
            ylim=(0, 103),
            xlabel="Écart nominal au train le plus proche (secondes)",
            ylabel="Images de validation cumulées (%)",
        )
        axis.set_title(
            "BROWT  /  Proximité temporelle entre les lots", loc="left", fontsize=15, pad=18
        )
        axis.text(
            0.02,
            0.95,
            f"{summary['within_10_seconds']} / {len(pairs)} images à 10 s ou moins",
            transform=axis.transAxes,
            va="top",
            fontweight="bold",
        )
        axis.legend(frameon=False, loc="lower right")
        axis.grid(alpha=0.15)
        axis.text(
            0,
            -0.25,
            "Horodatages issus des noms de fichiers ; fuseau et appareil non vérifiés.",
            transform=axis.transAxes,
            fontsize=9,
            color="#526078",
        )
        for suffix in ("svg", "png"):
            target = output / ("temporal-gaps." + suffix)
            figure.savefig(target, dpi=160, bbox_inches="tight")
            paths.append(target)
        plt.close(figure)

        selected = pairs[:2]
        figure, axes = plt.subplots(
            len(selected),
            2,
            figsize=(14, 3.25 * len(selected) + 0.7),
            squeeze=False,
            layout="constrained",
        )
        figure.suptitle(
            "BROWT  /  Deux lots, des frames très proches", fontsize=19, fontweight="bold"
        )
        for row_index, pair in enumerate(selected):
            for column, split in enumerate(("train", "valid")):
                axis = axes[row_index, column]
                with Image.open(dataset / pair[split]) as image:
                    axis.imshow(image.convert("RGB"))
                nominal = pair[split + "_nominal_time"].replace("T", " ")
                axis.set_title(
                    f"{split} · {nominal} · écart {pair['gap_seconds']:g} s",
                    loc="left",
                    fontsize=10,
                    color="#0072B2" if split == "train" else "#D55E00",
                )
                axis.set_axis_off()
        target = output / "temporal-pairs.png"
        figure.savefig(target, dpi=160, bbox_inches="tight")
        plt.close(figure)
        paths.append(target)
    return paths
