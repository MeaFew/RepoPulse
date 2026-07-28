"""Bootstrap confidence intervals for RepoPulse metrics.

A point estimate ("PR merge rate is 62.5%") answers "what", but not "how sure
are we". This module attaches 95% confidence intervals to the core aggregate
metrics by resampling the event-level records behind each metric (issues, PRs,
commit/PR author events) with replacement — the classic percentile bootstrap.

Everything here is a pure function: no database access, no global state, and
an injectable seed so results are bit-for-bit reproducible.

Honest limits: the bootstrap assumes events are exchangeable. Metrics computed
over time-ordered activity (bursts, maintainer vacations, release crunches)
have autocorrelated events, so the intervals for those metrics are narrower
than the true uncertainty.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

DEFAULT_RESAMPLES = 1000
DEFAULT_CONFIDENCE = 0.95
DEFAULT_SEED = 20260728
# Chunk cap for the (resamples x n) index matrix so vectorized resampling of
# large event lists stays memory-bounded (~32 MB per chunk).
_MAX_INDEX_BLOCK = 4_000_000


@dataclass(frozen=True)
class BootstrapInterval:
    """Point estimate plus a percentile-bootstrap confidence interval.

    ``n`` is the number of events backing the estimate, so consumers can judge
    whether a wide interval comes from a thin sample. ``point``/``lower``/
    ``upper`` are ``None`` when there is no data at all.
    """

    point: float | None
    lower: float | None
    upper: float | None
    n: int
    confidence: float = DEFAULT_CONFIDENCE
    resamples: int = DEFAULT_RESAMPLES

    def as_dict(self) -> dict[str, Any]:
        return {
            "point": self.point,
            "lower": self.lower,
            "upper": self.upper,
            "n": self.n,
            "confidence": self.confidence,
            "resamples": self.resamples,
        }

    def format(self, suffix: str = "") -> str:
        """Human-readable ``point [lower, upper]`` used in reports and the UI."""
        if self.point is None or self.lower is None or self.upper is None:
            return "—"
        return f"{self.point}{suffix} [{self.lower}, {self.upper}]"


def bootstrap_interval(
    values: Sequence[Any],
    statistic: Callable[[np.ndarray], float],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int | None = DEFAULT_SEED,
    decimals: int = 1,
) -> BootstrapInterval:
    """Generic percentile bootstrap over any event sequence.

    ``statistic`` receives one resampled array and returns a scalar. This is
    the flexible escape hatch; prefer the typed helpers below for the common
    cases (they use vectorized resampling and are much faster).
    """
    data = np.asarray(list(values))
    n = int(data.size)
    if n == 0:
        return BootstrapInterval(None, None, None, 0, confidence, resamples)
    point = float(statistic(data))
    rng = np.random.default_rng(seed)
    estimates = np.empty(resamples, dtype=float)
    for i in range(resamples):
        sample = data[rng.integers(0, n, size=n)]
        estimates[i] = statistic(sample)
    lower, upper = _percentile_bounds(estimates, confidence)
    return BootstrapInterval(
        round(point, decimals),
        round(lower, decimals),
        round(upper, decimals),
        n,
        confidence,
        resamples,
    )


def proportion_interval(
    outcomes: Sequence[bool],
    *,
    scale: float = 100.0,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int | None = DEFAULT_SEED,
    decimals: int = 1,
) -> BootstrapInterval:
    """CI for a share of events (e.g. issue close rate, PR merge rate).

    Each event contributes one boolean; the statistic is the resampled mean,
    multiplied by ``scale`` (100 keeps parity with the percent values in
    ``metrics.py``).
    """
    data = np.asarray(list(outcomes), dtype=float)
    return _vectorized_interval(
        data,
        lambda block: block.mean(axis=1),
        scale=scale,
        resamples=resamples,
        confidence=confidence,
        seed=seed,
        decimals=decimals,
    )


def median_interval(
    durations: Sequence[float],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int | None = DEFAULT_SEED,
    decimals: int = 1,
) -> BootstrapInterval:
    """CI for the median of per-event durations (e.g. hours to close)."""
    return quantile_interval(
        durations,
        0.5,
        resamples=resamples,
        confidence=confidence,
        seed=seed,
        decimals=decimals,
    )


def quantile_interval(
    durations: Sequence[float],
    q: float,
    *,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int | None = DEFAULT_SEED,
    decimals: int = 1,
) -> BootstrapInterval:
    """CI for an arbitrary quantile of per-event durations (P50/P90/...)."""
    data = np.asarray(list(durations), dtype=float)
    return _vectorized_interval(
        data,
        lambda block: np.quantile(block, q, axis=1),
        scale=1.0,
        resamples=resamples,
        confidence=confidence,
        seed=seed,
        decimals=decimals,
    )


def top_share_interval(
    authors: Sequence[str],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    confidence: float = DEFAULT_CONFIDENCE,
    seed: int | None = DEFAULT_SEED,
    decimals: int = 1,
) -> BootstrapInterval:
    """CI for the top contributor's share of all commit/PR activity.

    Events are resampled at the activity level (each commit or PR creation is
    one event), then the max author's fraction is recomputed per resample —
    this is a ratio statistic, so it uses the generic loop with bincount.
    """
    labels = [a for a in authors if a]
    n = len(labels)
    if n == 0:
        return BootstrapInterval(None, None, None, 0, confidence, resamples)
    _, codes = np.unique(np.asarray(labels), return_inverse=True)
    n_authors = int(codes.max()) + 1

    def share(code_sample: np.ndarray) -> float:
        counts = np.bincount(code_sample, minlength=n_authors)
        return 100.0 * counts.max() / counts.sum()

    point = share(codes)
    rng = np.random.default_rng(seed)
    estimates = np.empty(resamples, dtype=float)
    for i in range(resamples):
        estimates[i] = share(codes[rng.integers(0, n, size=n)])
    lower, upper = _percentile_bounds(estimates, confidence)
    return BootstrapInterval(
        round(point, decimals),
        round(lower, decimals),
        round(upper, decimals),
        n,
        confidence,
        resamples,
    )


def _percentile_bounds(estimates: np.ndarray, confidence: float) -> tuple[float, float]:
    alpha = (1.0 - confidence) / 2.0
    lower, upper = np.quantile(estimates, [alpha, 1.0 - alpha])
    return float(lower), float(upper)


def _vectorized_interval(
    data: np.ndarray,
    block_statistic: Callable[[np.ndarray], np.ndarray],
    *,
    scale: float,
    resamples: int,
    confidence: float,
    seed: int | None,
    decimals: int,
) -> BootstrapInterval:
    """Vectorized percentile bootstrap for statistics expressible on a 2-D block.

    ``block_statistic`` maps a (block, n) matrix of resampled observations to
    one estimate per row. Resamples are chunked so the index matrix stays
    memory-bounded even for large event lists.
    """
    n = int(data.size)
    if n == 0:
        return BootstrapInterval(None, None, None, 0, confidence, resamples)
    point = float(block_statistic(data.reshape(1, n))[0]) * scale
    rng = np.random.default_rng(seed)
    block_size = max(1, min(resamples, _MAX_INDEX_BLOCK // max(n, 1)))
    estimates = np.empty(resamples, dtype=float)
    done = 0
    while done < resamples:
        size = min(block_size, resamples - done)
        idx = rng.integers(0, n, size=(size, n))
        estimates[done : done + size] = block_statistic(data[idx]) * scale
        done += size
    lower, upper = _percentile_bounds(estimates, confidence)
    return BootstrapInterval(
        round(point, decimals),
        round(lower, decimals),
        round(upper, decimals),
        n,
        confidence,
        resamples,
    )
