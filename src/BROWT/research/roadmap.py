"""Render planned work as planned work, independently from experimental results."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


def plot_roadmap(plan: dict, output: Path) -> list[Path]:
    phases = plan["phases"]
    horizon = int(plan["horizon_weeks"])
    for phase in phases:
        if not 1 <= phase["start_week"] <= phase["end_week"] <= horizon:
            raise ValueError(f"Invalid week range: {phase['id']}")
    output.mkdir(parents=True, exist_ok=True)
    with plt.rc_context(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "svg.fonttype": "none",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.spines.left": False,
            "axes.edgecolor": "#A5AEBC",
            "text.color": "#17263C",
            "axes.labelcolor": "#526078",
            "xtick.color": "#526078",
            "ytick.color": "#17263C",
            "figure.facecolor": "white",
            "axes.facecolor": "white",
        }
    ):
        figure, axis = plt.subplots(figsize=(13.5, 6.2), layout="constrained")
        figure.suptitle("BROWT  /  Une progression par preuves", fontsize=22, fontweight="bold")
        labels = []
        for index, phase in enumerate(phases):
            active = phase["status"] == "in_progress"
            color = "#0072B2" if active else "#CBD5E1"
            left = phase["start_week"] - 0.5
            width = phase["end_week"] - phase["start_week"] + 1
            axis.barh(index, width, left=left, height=0.58, color=color)
            axis.text(
                left + width / 2,
                index,
                phase["id"],
                va="center",
                ha="center",
                fontsize=10,
                fontweight="bold",
                color="white" if active else "#27364B",
            )
            labels.append(phase["name"])
            if phase["hardware"] == "cuda_target":
                axis.text(
                    left + width + 0.18, index, "CUDA", va="center", fontsize=8, color="#9A5800"
                )
        axis.set(
            yticks=range(len(phases)),
            yticklabels=labels,
            xlim=(0.5, horizon + 1.5),
            xticks=range(1, horizon + 1),
            xlabel="Semaines indicatives · à réviser après les pilotes",
        )
        axis.invert_yaxis()
        axis.tick_params(axis="y", length=0, pad=12)
        axis.grid(axis="x", alpha=0.12)
        axis.set_axisbelow(True)
        axis.set_title(
            "Priorité : recherche et visualisations scientifiques · démarrage sur CPU",
            loc="left",
            fontsize=11,
            color="#526078",
            pad=18,
        )
        axis.legend(
            handles=[
                Patch(color="#0072B2", label="Jalon en cours"),
                Patch(color="#CBD5E1", label="Jalon prévu"),
            ],
            frameon=False,
            loc="upper center",
            bbox_to_anchor=(0.5, -0.13),
            ncol=2,
        )
        paths = [output / "research-roadmap.svg", output / "research-roadmap.png"]
        for target in paths:
            figure.savefig(
                target,
                dpi=160,
                bbox_inches="tight",
                metadata={"Creator": "BROWT research plan; not experimental results"},
            )
        plt.close(figure)
    return paths
