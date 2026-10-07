"""Tests for the synthetic portfolio health scene (real cleaning, risk maths, KMeans)."""

import numpy as np
import pandas as pd
import pytest

from demos.portfolio import (
    ASSETS,
    DEFAULT_SEED,
    TRADING_DAYS,
    annualized_volatility,
    clean_prices,
    cluster_sample_portfolios,
    concentration,
    daily_returns,
    health_score,
    historical_var,
    max_drawdown,
    normalize_weights,
    portfolio_metrics,
    sample_weight_vectors,
    score_band,
    simulate_prices,
    validate_prices,
    validate_weights,
)

EQUAL_WEIGHTS = [20.0] * 5


@pytest.fixture(scope="module")
def returns() -> pd.DataFrame:
    prices, _ = clean_prices(simulate_prices(DEFAULT_SEED))
    return daily_returns(prices)


# --------------------------------------------------------------------------- #
# Synthetic data and cleaning
# --------------------------------------------------------------------------- #
def test_simulated_prices_are_reproducible_and_correlated():
    first = simulate_prices(DEFAULT_SEED)
    second = simulate_prices(DEFAULT_SEED)
    pd.testing.assert_frame_equal(first, second)
    assert list(first.columns) == list(ASSETS)
    assert first.shape == (TRADING_DAYS + 4, len(ASSETS))
    assert np.nanmin(first.to_numpy()) > 0
    # Same correlation block drives the two equity funds.
    correlation = first.corr()
    assert correlation.loc[ASSETS[2], ASSETS[3]] > 0.4
    assert correlation.loc[ASSETS[0], ASSETS[1]] > 0.3
    assert not first.equals(simulate_prices(DEFAULT_SEED + 1))


def test_simulated_feed_contains_missing_and_duplicate_rows():
    raw = simulate_prices(DEFAULT_SEED)
    assert int(raw.index.duplicated().sum()) == 4
    assert int(raw.isna().sum().sum()) >= 20
    assert not raw.index.is_monotonic_increasing


def test_cleaning_removes_defects_and_reports_every_action():
    raw = simulate_prices(DEFAULT_SEED)
    clean, report = clean_prices(raw)
    assert clean.shape == (TRADING_DAYS, len(ASSETS))
    assert clean.index.is_monotonic_increasing
    assert not clean.index.has_duplicates
    assert not clean.isna().to_numpy().any()
    assert (clean.to_numpy() > 0).all()
    assert report["rows_received"] == raw.shape[0]
    assert report["duplicate_rows_dropped"] == 4
    assert report["missing_values_received"] > 0
    assert report["missing_values_filled"] == report["missing_values_received"]
    assert report["missing_values_unfilled"] == 0
    assert report["missing_values_remaining"] == 0
    assert report["rows_clean"] == TRADING_DAYS
    assert report["ffill_limit"] == 5
    assert report["unsorted_input"] is True
    assert report["columns_dropped_missing"] == []


def test_cleaning_never_backfills_from_future_prices():
    index = pd.bdate_range("2025-01-01", periods=5)
    frame = pd.DataFrame(
        {"a": [np.nan, np.nan, 5.0, 6.0, 7.0], "b": [1.0, 1.1, 1.2, 1.3, 1.4]}, index=index
    )
    clean, report = clean_prices(frame)
    assert clean.index[0] == index[2]
    assert clean["a"].iloc[0] == 5.0
    assert report["leading_rows_dropped"] == 2
    assert report["interior_rows_dropped"] == 0
    assert report["missing_values_unfilled"] == 2
    assert report["missing_values_filled"] == 0


def test_cleaning_respects_the_bounded_forward_fill_limit():
    index = pd.bdate_range("2025-01-01", periods=10)
    frame = pd.DataFrame(
        {"a": [1.0, 2.0] + [np.nan] * 6 + [9.0, 10.0], "b": np.arange(1.0, 11.0)}, index=index
    )
    clean, report = clean_prices(frame, ffill_limit=3)
    assert report["missing_values_filled"] == 3
    assert report["missing_values_unfilled"] == 3
    assert report["interior_rows_dropped"] == 3
    assert clean.shape[0] == 7
    # Filled from the last known past value, never from the future 9.0.
    assert clean["a"].iloc[4] == 2.0
    assert clean["a"].iloc[5] == 9.0


def test_cleaning_drops_a_fully_missing_asset_column():
    index = pd.bdate_range("2025-01-01", periods=3)
    frame = pd.DataFrame({"a": [1.0, 2.0, 3.0], "b": [np.nan] * 3}, index=index)
    clean, report = clean_prices(frame)
    assert list(clean.columns) == ["a"]
    assert report["columns_dropped_missing"] == ["b"]
    assert report["rows_clean"] == 3


def test_price_and_clean_guards():
    assert validate_prices(pd.DataFrame()) != []
    assert validate_prices(pd.DataFrame({"a": ["text"]})) != []
    assert validate_prices(pd.DataFrame({"a": [1.0, 0.0]})) != []
    assert validate_prices(pd.DataFrame({"a": [1.0, np.inf]})) != []
    assert validate_prices(pd.DataFrame({"a": [1.0, np.nan]})) == []
    with pytest.raises(ValueError):
        clean_prices(pd.DataFrame())
    with pytest.raises(ValueError):
        clean_prices(pd.DataFrame({"a": [1.0, -1.0]}))
    with pytest.raises(ValueError):
        clean_prices(pd.DataFrame({"a": [np.nan, np.nan]}))
    with pytest.raises(ValueError):
        clean_prices(pd.DataFrame({"a": [1.0, 2.0]}), ffill_limit=0)


# --------------------------------------------------------------------------- #
# Weight input guards
# --------------------------------------------------------------------------- #
def test_weight_validation_covers_structural_rules_and_the_100_percent_rule():
    assert validate_weights(EQUAL_WEIGHTS) == []
    assert validate_weights([50.0, 50.0, 0.0, 0.0, 0.0], tolerance=None) == []
    assert validate_weights([40.0, 40.0, 0.0, 0.0, 0.0]) != []
    assert validate_weights([20.0, 20.0, 20.0, 20.0]) != []
    assert validate_weights([0.0] * 5, tolerance=None) != []
    assert validate_weights([20.0, 20.0, 20.0, 20.0, np.nan], tolerance=None) != []
    assert validate_weights([40.0, 40.0, 40.0, 0.0, -20.0], tolerance=None) != []
    assert validate_weights(["a", 20.0, 20.0, 20.0, 20.0], tolerance=None) != []
    assert validate_weights([]) != []
    assert validate_weights([100.0]) != []


def test_normalize_weights_scales_and_rejects_invalid_input():
    assert normalize_weights(EQUAL_WEIGHTS) == pytest.approx([0.2] * 5)
    assert normalize_weights([50.0, 50.0, 0.0, 0.0, 0.0]) == pytest.approx(
        [0.5, 0.5, 0.0, 0.0, 0.0]
    )
    assert normalize_weights([2.0, 2.0]).sum() == pytest.approx(1.0)
    for invalid in ([0.0] * 5, [-1.0, 2.0], [np.nan, 1.0], []):
        with pytest.raises(ValueError):
            normalize_weights(invalid)
    with pytest.raises(ValueError):
        normalize_weights([1.0, 1.0], expected=5)


def test_sample_weight_vectors_lie_on_the_simplex_and_are_reproducible():
    vectors = sample_weight_vectors(50, n_assets=5, seed=7)
    assert vectors.shape == (50, 5)
    assert (vectors >= 0).all()
    assert np.allclose(vectors.sum(axis=1), 1.0)
    assert np.allclose(vectors, sample_weight_vectors(50, n_assets=5, seed=7))
    with pytest.raises(ValueError):
        sample_weight_vectors(0, n_assets=5, seed=7)
    with pytest.raises(ValueError):
        sample_weight_vectors(5, n_assets=0, seed=7)


# --------------------------------------------------------------------------- #
# Risk metrics and the health score
# --------------------------------------------------------------------------- #
def test_risk_metrics_are_finite_ordered_and_guarded(returns):
    metrics = portfolio_metrics(returns, EQUAL_WEIGHTS, assets=ASSETS)
    assert metrics["observations"] == returns.shape[0]
    assert metrics["annual_volatility"] > 0
    assert metrics["var_99_amount"] >= metrics["var_95_amount"] > 0
    assert metrics["var_95_ratio"] == pytest.approx(metrics["var_95_amount"] / 10_000_000)
    assert metrics["max_drawdown"] <= 0
    assert metrics["cumulative_growth"] > 0
    assert metrics["hhi"] == pytest.approx(0.2)
    assert metrics["effective_holdings"] == pytest.approx(5.0)
    assert metrics["top_weight"] == pytest.approx(0.2)
    assert metrics["top_asset"] in ASSETS
    # Equity risk must dominate a bond-only portfolio on the same simulated prices.
    equity = portfolio_metrics(returns, [0.0, 0.0, 0.0, 100.0, 0.0], assets=ASSETS)
    bonds = portfolio_metrics(returns, [100.0, 0.0, 0.0, 0.0, 0.0], assets=ASSETS)
    assert equity["annual_volatility"] > bonds["annual_volatility"]
    assert equity["var_95_amount"] > bonds["var_95_amount"]

    with pytest.raises(ValueError):
        annualized_volatility(pd.Series([], dtype=float))
    with pytest.raises(ValueError):
        annualized_volatility(pd.Series([0.01]))
    with pytest.raises(ValueError):
        historical_var(pd.Series([0.01, -0.02]), confidence=1.5)
    with pytest.raises(ValueError):
        historical_var(pd.Series([0.01, -0.02]), notional=0.0)
    with pytest.raises(ValueError):
        max_drawdown(pd.Series([np.nan]))
    with pytest.raises(ValueError):
        portfolio_metrics(returns, [1.0, 2.0])


def test_concentration_reports_hhi_effective_holdings_and_top_weight():
    single = concentration([100.0, 0.0, 0.0, 0.0, 0.0], assets=ASSETS)
    assert single["hhi"] == pytest.approx(1.0)
    assert single["effective_holdings"] == pytest.approx(1.0)
    assert single["top_weight"] == pytest.approx(1.0)
    assert single["top_asset"] == ASSETS[0]
    even = concentration(EQUAL_WEIGHTS, assets=ASSETS)
    assert even["effective_holdings"] == pytest.approx(5.0)


def test_health_score_formula_is_bounded_transparent_and_monotone():
    best = {
        "annual_volatility": 0.03,
        "max_drawdown": 0.0,
        "effective_holdings": 5.0,
        "risk_adjusted_return": 1.0,
    }
    worst = {
        "annual_volatility": 0.30,
        "max_drawdown": -0.35,
        "effective_holdings": 1.0,
        "risk_adjusted_return": -0.5,
    }
    best_score = health_score(best)
    worst_score = health_score(worst)
    assert best_score["score"] == pytest.approx(100.0)
    assert worst_score["score"] == pytest.approx(0.0)
    assert worst_score["score"] < best_score["score"] <= 100.0
    assert sum(
        component["weight"] for component in best_score["components"].values()
    ) == pytest.approx(1.0)
    assert best_score["score"] == pytest.approx(
        sum(component["contribution"] for component in best_score["components"].values()), abs=0.05
    )
    assert all(
        0.0 <= component["score"] <= 100.0 for component in best_score["components"].values()
    )
    # The documented linear clip: mid-band volatility maps to exactly 50.
    midpoint = health_score({**best, "annual_volatility": 0.165})
    assert midpoint["components"]["volatility"]["score"] == pytest.approx(50.0)
    assert health_score({**best, "annual_volatility": 0.20})["score"] < best_score["score"]
    assert health_score({**best, "max_drawdown": -0.25})["score"] < best_score["score"]
    assert health_score({**best, "effective_holdings": 1.0})["score"] < best_score["score"]
    with pytest.raises(ValueError):
        health_score({"annual_volatility": 0.1})


def test_score_bands_cover_every_range():
    assert score_band(100.0)["code"] == "resilient"
    assert score_band(80.0)["code"] == "resilient"
    assert score_band(79.9)["code"] == "balanced"
    assert score_band(65.0)["code"] == "balanced"
    assert score_band(64.9)["code"] == "watch"
    assert score_band(45.0)["code"] == "watch"
    assert score_band(44.9)["code"] == "fragile"
    assert score_band(-5.0)["code"] == "fragile"
    assert score_band(150.0)["code"] == "resilient"
    assert all(score_band(value)["jp"] for value in (0, 50, 70, 90))


# --------------------------------------------------------------------------- #
# KMeans peer grouping
# --------------------------------------------------------------------------- #
def test_kmeans_groups_are_labelled_from_cluster_metrics_not_cluster_ids(returns):
    result = cluster_sample_portfolios(returns, EQUAL_WEIGHTS, assets=ASSETS, seed=DEFAULT_SEED)
    sample = result["sample"]
    summary = result["summary"]
    assert len(sample) == 201
    assert int(sample["is_current"].sum()) == 1
    assert set(sample["cluster"]) == {0, 1, 2}
    assert list(summary["risk_label"]) == result["labels"]
    assert set(sample["risk_label"]) == set(summary["risk_label"])
    assert result["current_label"] in set(summary["risk_label"])
    assert -1.0 <= result["silhouette"] <= 1.0
    assert result["inertia"] > 0
    # Risk ordering is derived from measured cluster metrics, so it must increase.
    assert summary["mean_drawdown_magnitude"].is_monotonic_increasing
    assert summary["mean_annual_volatility"].is_monotonic_increasing
    ranked = (
        sample.assign(
            risk_score=sample["annual_volatility"].rank(pct=True)
            + sample["drawdown_magnitude"].rank(pct=True)
        )
        .groupby("risk_label")["risk_score"]
        .mean()
        .reindex(result["labels"])
    )
    assert ranked.is_monotonic_increasing
    assert 0.0 <= result["current_volatility_percentile"] <= 100.0


def test_kmeans_grouping_is_reproducible_and_guarded(returns):
    first = cluster_sample_portfolios(returns, EQUAL_WEIGHTS, assets=ASSETS, seed=DEFAULT_SEED)
    second = cluster_sample_portfolios(returns, EQUAL_WEIGHTS, assets=ASSETS, seed=DEFAULT_SEED)
    pd.testing.assert_frame_equal(first["sample"], second["sample"])
    pd.testing.assert_frame_equal(first["summary"], second["summary"])
    assert first["silhouette"] == second["silhouette"]
    other_seed = cluster_sample_portfolios(returns, EQUAL_WEIGHTS, assets=ASSETS, seed=1)
    assert other_seed["sample"].shape == first["sample"].shape
    with pytest.raises(ValueError):
        cluster_sample_portfolios(returns, EQUAL_WEIGHTS, n_clusters=9)
    with pytest.raises(ValueError):
        cluster_sample_portfolios(returns, EQUAL_WEIGHTS, n_clusters=1)
    with pytest.raises(ValueError):
        cluster_sample_portfolios(returns, [1.0, 1.0])


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_scene_renders_validates_weights_and_keeps_session_state_isolated():
    from streamlit.testing.v1 import AppTest

    from demos.portfolio import market_data

    script = "from demos.portfolio import render\nrender()"
    snapshot = market_data(DEFAULT_SEED)["returns"].copy()
    at = AppTest.from_string(script, default_timeout=60).run()
    assert not at.exception
    assert len(at.number_input) == len(ASSETS)
    assert any("Health score" in item.label for item in at.metric)
    assert not any("正規化" in item.value for item in at.info)

    before = next(item.value for item in at.metric if "Health score" in item.label)
    at.number_input(key="portfolio_weight_Equity Global").set_value(50.0).run()
    assert not at.exception
    assert any("正規化" in item.value for item in at.info)
    after = next(item.value for item in at.metric if "Health score" in item.label)
    assert before != after

    # Cached market artifacts must not be mutated by rendering.
    pd.testing.assert_frame_equal(market_data(DEFAULT_SEED)["returns"], snapshot)

    # A second session starts from the widget defaults, not the first session's input.
    other = AppTest.from_string(script, default_timeout=60).run()
    assert at.session_state["portfolio_weight_Equity Global"] == pytest.approx(50.0)
    assert other.session_state["portfolio_weight_Equity Global"] == pytest.approx(20.0)

    at.button(key="portfolio_reset").click().run()
    assert not at.exception
    assert at.number_input(key="portfolio_weight_Equity Global").value == pytest.approx(20.0)
