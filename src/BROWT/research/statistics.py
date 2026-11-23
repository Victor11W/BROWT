"""Statistics for independent experimental units, not correlated video frames."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class MeanInterval:
    mean: float
    lower: float
    upper: float
    n: int
    confidence: float


def bootstrap_mean_ci(
    values,
    *,
    confidence: float = 0.95,
    n_resamples: int = 2000,
    seed: int = 0,
) -> MeanInterval:
    """Percentile bootstrap of the mean over independent sessions/episodes/seeds.

    The caller must choose independent units. This function cannot repair
    temporal leakage or pseudoreplication. It deliberately rejects n < 2.
    """
    data = np.asarray(values, dtype=float)
    if data.ndim != 1 or data.size < 2 or not np.isfinite(data).all():
        raise ValueError("Provide at least two finite, independent scalar observations.")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1.")
    if not isinstance(n_resamples, int) or isinstance(n_resamples, bool) or n_resamples < 100:
        raise ValueError("n_resamples must be an integer of at least 100.")
    rng = np.random.default_rng(seed)
    # Bound memory for large studies.
    means = np.empty(n_resamples)
    batch = max(1, min(256, 1_000_000 // data.size))
    for start in range(0, n_resamples, batch):
        size = min(batch, n_resamples - start)
        means[start : start + size] = rng.choice(data, size=(size, data.size), replace=True).mean(1)
    tail = (1 - confidence) / 2
    low, high = np.quantile(means, [tail, 1 - tail])
    return MeanInterval(float(data.mean()), float(low), float(high), int(data.size), confidence)
