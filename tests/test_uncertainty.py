"""Tests for the bootstrap uncertainty layer.

Covers three properties an interviewer would poke at:
- reproducibility: same seed -> identical intervals;
- calibration: on synthetic data with a known ground truth, the 95% CI
  contains the truth at roughly the nominal rate;
- honesty: smaller samples produce wider intervals.
"""

from __future__ import annotations

import numpy as np

from repopulse.metrics import Analytics
from repopulse.sample_data import DEMO_REPOSITORY, load_demo_data
from repopulse.uncertainty import (
    bootstrap_interval,
    median_interval,
    proportion_interval,
    top_share_interval,
)

# --- pure-function behavior -------------------------------------------------


def test_fixed_seed_reproduces_identical_intervals() -> None:
    rng = np.random.default_rng(7)
    durations = rng.lognormal(mean=3.0, sigma=1.0, size=200).tolist()

    first = median_interval(durations, seed=42)
    second = median_interval(durations, seed=42)

    assert first == second
    assert first.as_dict() == second.as_dict()


def test_different_seeds_usually_shift_bounds() -> None:
    rng = np.random.default_rng(7)
    durations = rng.lognormal(mean=3.0, sigma=1.0, size=200).tolist()

    a = median_interval(durations, seed=1)
    b = median_interval(durations, seed=2)

    assert (a.lower, a.upper) != (b.lower, b.upper)
    assert a.point == b.point  # point estimate never depends on the seed


def test_empty_input_yields_null_interval() -> None:
    interval = median_interval([], seed=42)

    assert interval.point is None
    assert interval.lower is None
    assert interval.upper is None
    assert interval.n == 0


def test_interval_bounds_bracket_point_estimate() -> None:
    rng = np.random.default_rng(11)
    outcomes = (rng.random(150) < 0.65).tolist()

    interval = proportion_interval(outcomes, seed=42)

    assert interval.n == 150
    assert interval.lower <= interval.point <= interval.upper
    assert 0 <= interval.lower <= interval.upper <= 100


def test_generic_bootstrap_matches_median_helper() -> None:
    rng = np.random.default_rng(3)
    durations = rng.gamma(shape=2.0, scale=10.0, size=120).tolist()

    generic = bootstrap_interval(durations, np.median, seed=42)
    helper = median_interval(durations, seed=42)

    assert generic.point == helper.point
    assert generic.n == helper.n


# --- statistical properties on synthetic data --------------------------------


def test_median_ci_covers_true_value_at_roughly_nominal_rate() -> None:
    # True median of lognormal(mu, sigma) is exp(mu).
    true_median = float(np.exp(3.0))
    trials, covered = 150, 0
    for trial in range(trials):
        sample = np.random.default_rng(10_000 + trial).lognormal(3.0, 1.0, size=100)
        interval = median_interval(sample.tolist(), resamples=300, seed=trial)
        covered += interval.lower <= true_median <= interval.upper

    coverage = covered / trials
    # Percentile bootstrap on a median slightly undercovers; accept a band
    # around the nominal 95% rather than a flaky exact match.
    assert 0.82 <= coverage <= 1.0


def test_proportion_ci_covers_true_rate() -> None:
    true_rate = 60.0  # percent scale, matching metrics.py conventions
    trials, covered = 150, 0
    for trial in range(trials):
        sample = (np.random.default_rng(20_000 + trial).random(200) < 0.6).tolist()
        interval = proportion_interval(sample, resamples=300, seed=trial)
        covered += interval.lower <= true_rate <= interval.upper

    assert covered / trials >= 0.85


def test_smaller_samples_produce_wider_intervals() -> None:
    rng = np.random.default_rng(99)
    population = rng.lognormal(3.0, 1.0, size=4000)
    small = population[:30].tolist()
    large = population[:3000].tolist()

    small_ci = median_interval(small, seed=42)
    large_ci = median_interval(large, seed=42)

    small_width = small_ci.upper - small_ci.lower
    large_width = large_ci.upper - large_ci.lower
    assert small_width > 2 * large_width


def test_top_share_interval_recovers_known_concentration() -> None:
    # 40% of events from one dominant author, the rest spread thin.
    authors = ["boss"] * 40 + [f"dev{i}" for i in range(60)]

    interval = top_share_interval(authors, seed=42)

    assert interval.point == 40.0
    assert interval.lower <= 40.0 <= interval.upper
    assert interval.n == 100


def test_top_share_interval_is_reproducible() -> None:
    authors = ["a", "a", "a", "b", "b", "c"]

    assert top_share_interval(authors, seed=5) == top_share_interval(authors, seed=5)


# --- warehouse integration ---------------------------------------------------


def test_metric_uncertainty_points_match_kpi_values(tmp_path) -> None:
    db_path = tmp_path / "demo.duckdb"
    load_demo_data(db_path)

    with Analytics(db_path) as analytics:
        uncertainty = analytics.metric_uncertainty(DEMO_REPOSITORY)
        issue = analytics.issue_kpis(DEMO_REPOSITORY)
        pr = analytics.pr_kpis(DEMO_REPOSITORY)
        contributor = analytics.contributor_kpis(DEMO_REPOSITORY)

    close_rate = uncertainty["issue_close_rate"]
    assert close_rate.point == issue["close_rate"]
    assert close_rate.n == issue["total"]
    assert close_rate.lower <= close_rate.point <= close_rate.upper

    merge_rate = uncertainty["pr_merge_rate"]
    assert merge_rate.point == pr["merge_rate"]
    assert merge_rate.lower <= merge_rate.point <= merge_rate.upper

    median_close = uncertainty["issue_median_close_hours"]
    assert median_close.point == issue["median_close_hours"]
    assert median_close.lower <= median_close.point <= median_close.upper

    share = uncertainty["top_contributor_share"]
    assert share.point == contributor["top_contributor_share"]
    assert share.lower <= share.point <= share.upper


def test_metric_uncertainty_is_reproducible(tmp_path) -> None:
    db_path = tmp_path / "demo.duckdb"
    load_demo_data(db_path)

    with Analytics(db_path) as analytics:
        first = analytics.metric_uncertainty(DEMO_REPOSITORY)
        second = analytics.metric_uncertainty(DEMO_REPOSITORY)

    assert first == second
