"""Publication-oriented geometry figures; no invented detector or RL scores."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Rectangle

from BROWT.state.observation import FrameState

COLORS = {"Me": "#0072B2", "Enemy": "#D55E00", "PP": "#009E73", "PP_Box": "#666666"}
NEUTRAL = "#637083"


def plot_overview(states: list[FrameState], latency_us: list[float], output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    style = {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.edgecolor": "#A5AEBC",
        "axes.labelcolor": "#24324A",
        "text.color": "#14233B",
        "xtick.color": "#526078",
        "ytick.color": "#526078",
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    }
    with plt.rc_context(style):
        figure, axes = plt.subplots(2, 2, figsize=(13.5, 9.8), layout="constrained")
        figure.suptitle(
            "BROWT  /  Vérifier la chaîne d’observation", fontsize=21, fontweight="bold"
        )
        snapshot = states[0]
        for index, normalized in enumerate((False, True)):
            axis = axes[0, index]
            width, height = (1, 1) if normalized else (snapshot.width, snapshot.height)
            for obj in snapshot.objects:
                x1, y1, x2, y2 = obj.xyxy
                color = COLORS.get(obj.cls_name, NEUTRAL)
                axis.add_patch(
                    Rectangle(
                        (x1 * width, y1 * height),
                        (x2 - x1) * width,
                        (y2 - y1) * height,
                        facecolor=color,
                        edgecolor=color,
                        alpha=0.15,
                        linewidth=1.5,
                    )
                )
                x, y = obj.center
                axis.scatter(x * width, y * height, color=color, s=28, zorder=3)
                axis.annotate(
                    obj.cls_name,
                    (x1 * width, y1 * height),
                    xytext=(0, 5),
                    textcoords="offset points",
                    fontsize=9,
                    color=color,
                )
            axis.set(
                xlim=(0, width),
                ylim=(height, 0),
                aspect="equal",
                xlabel="x / largeur" if normalized else "x (pixels)",
                ylabel="y / hauteur" if normalized else "y (pixels)",
            )
            axis.set_title(
                ("A  ·  Détections simulées" if not normalized else "B  ·  Observation normalisée"),
                loc="left",
                pad=18,
            )
            axis.grid(alpha=0.12)
            axis.text(
                0, 1.015, "SCÈNE SYNTHÉTIQUE", transform=axis.transAxes, fontsize=8, color=NEUTRAL
            )

        axis = axes[1, 0]
        times = np.asarray([state.timestamp_s for state in states])
        for cls_name, marker in (("Me", "o"), ("Enemy", "s")):
            centers = []
            for state in states:
                objects = [obj for obj in state.objects if obj.cls_name == cls_name]
                centers.append(objects[0].center if len(objects) == 1 else (np.nan, np.nan))
            centers = np.asarray(centers)
            axis.plot(
                centers[:, 0],
                centers[:, 1],
                color=COLORS[cls_name],
                marker=marker,
                markevery=6,
                markersize=4,
                label=cls_name,
            )
        axis.set(
            xlim=(0, 1), ylim=(1, 0), aspect="equal", xlabel="x / largeur", ylabel="y / hauteur"
        )
        axis.set_title("C  ·  Trajectoires et observations manquantes", loc="left", pad=18)
        axis.text(
            0,
            1.015,
            f"SCÈNE SYNTHÉTIQUE · {len(times)} images · {times[-1]:.1f} s",
            transform=axis.transAxes,
            fontsize=8,
            color=NEUTRAL,
        )
        axis.text(
            0.02,
            0.03,
            "Les ruptures signalent une absence de détection.",
            transform=axis.transAxes,
            fontsize=8,
            color=NEUTRAL,
        )
        axis.legend(loc="upper right", frameon=False)
        axis.grid(alpha=0.12)

        axis = axes[1, 1]
        p50, p95 = np.percentile(latency_us, [50, 95])
        axis.hist(latency_us, bins=35, color=COLORS["Me"], alpha=0.85)
        axis.axvline(p50, color="#14233B", linestyle="-", label=f"p50 = {p50:.1f} µs")
        axis.axvline(p95, color=COLORS["Enemy"], linestyle="--", label=f"p95 = {p95:.1f} µs")
        axis.set(xlabel="Durée de conversion d’une image (µs)", ylabel="Nombre d’appels")
        axis.set_title("D  ·  Coût local de la conversion", loc="left", pad=18)
        axis.text(
            0,
            1.015,
            f"MESURE CPU LOCALE · {len(latency_us)} appels · entrée synthétique",
            transform=axis.transAxes,
            fontsize=8,
            color=NEUTRAL,
        )
        axis.text(
            0.98,
            0.55,
            "Capture, YOLO et ADB exclus",
            ha="right",
            transform=axis.transAxes,
            fontsize=9,
            color=NEUTRAL,
        )
        axis.legend(loc="upper right", frameon=False)
        axis.grid(axis="y", alpha=0.12)
        paths = [output / "observation-overview.svg", output / "observation-overview.png"]
        for target in paths:
            figure.savefig(
                target,
                dpi=160,
                bbox_inches="tight",
                metadata={"Creator": "BROWT reproducible research demo"},
            )
        plt.close(figure)
    return paths
