"""Tests for the LightGBM credit default-risk scene (real training, held-out evaluation)."""

import numpy as np
import pandas as pd
import pytest

from demos.credit import (
    DEFAULT_SEED,
    FEATURE_DOMAINS,
    FEATURES,
    HOLDOUT_FRACTION,
    N_RECORDS,
    TARGET,
    build_credit_bundle,
    default_applicant,
    evaluate_classifier,
    generate_credit_data,
    policy_band,
    score_applicant,
    split_train_holdout,
    validate_applicant,
)

PROTECTED_TERMS = ("gender", "sex", "ethnic", "race", "nationality", "age", "religion", "marital")


@pytest.fixture(scope="module")
def bundle() -> dict[str, object]:
    return build_credit_bundle()


# --------------------------------------------------------------------------- #
# Synthetic data and labels
# --------------------------------------------------------------------------- #
def test_synthetic_data_is_deterministic_bounded_and_free_of_protected_attributes():
    data = generate_credit_data()
    assert len(data) == N_RECORDS
    assert list(data.columns) == [*FEATURES, TARGET]
    assert data[TARGET].isin([0, 1]).all()
    assert 0.05 < float(data[TARGET].mean()) < 0.40
    for name, (low, high) in FEATURE_DOMAINS.items():
        assert data[name].between(low, high).all()
    for column in data.columns:
        assert not any(term in column.lower() for term in PROTECTED_TERMS)
    pd.testing.assert_frame_equal(data, generate_credit_data())
    assert not generate_credit_data(seed=DEFAULT_SEED + 1).equals(data)
    with pytest.raises(ValueError):
        generate_credit_data(n_records=10)


def test_labels_are_noisy_so_the_problem_is_not_separable(bundle):
    # A noisy label process cannot be memorised: train AUC must stay well below 1.
    assert float(bundle["train_auc"]) < 0.99
    assert float(bundle["lightgbm"]["roc_auc"]) > 0.65


def test_split_is_stratified_disjoint_and_reproducible():
    data = generate_credit_data()
    train, holdout = split_train_holdout(data)
    assert len(train) + len(holdout) == len(data)
    assert len(holdout) == round(len(data) * HOLDOUT_FRACTION)
    assert not set(train.index) & set(holdout.index)
    assert set(train.index) | set(holdout.index) == set(data.index)
    assert float(holdout[TARGET].mean()) == pytest.approx(float(data[TARGET].mean()), abs=0.01)
    assert float(train[TARGET].mean()) == pytest.approx(float(data[TARGET].mean()), abs=0.01)
    again_train, again_holdout = split_train_holdout(data)
    assert again_train.index.tolist() == train.index.tolist()
    assert again_holdout.index.tolist() == holdout.index.tolist()
    with pytest.raises(ValueError):
        split_train_holdout(data.assign(default=1))
    with pytest.raises(ValueError):
        split_train_holdout(pd.DataFrame())
    with pytest.raises(ValueError):
        split_train_holdout(data, test_fraction=0.0)


# --------------------------------------------------------------------------- #
# Training and held-out evaluation
# --------------------------------------------------------------------------- #
def test_metrics_come_from_the_holdout_split_and_are_self_consistent(bundle):
    metrics = bundle["lightgbm"]
    holdout = bundle["holdout"]
    assert metrics["records"] == len(holdout)
    assert len(metrics["probabilities"]) == len(holdout)
    assert all(0.0 <= float(value) <= 1.0 for value in metrics["probabilities"])
    assert 0.5 < float(metrics["roc_auc"]) < 1.0
    assert 0.0 <= float(metrics["precision"]) <= 1.0
    assert 0.0 <= float(metrics["recall"]) <= 1.0
    assert 0.0 <= float(metrics["f1"]) <= 1.0
    matrix = metrics["confusion"]
    assert sum(sum(row) for row in matrix) == len(holdout)
    assert matrix[0][0] + matrix[0][1] == metrics["support"]["non_default"]
    assert matrix[1][0] + matrix[1][1] == metrics["support"]["default"]
    true_positives = matrix[1][1]
    assert float(metrics["recall"]) == pytest.approx(true_positives / metrics["support"]["default"])
    assert float(metrics["precision"]) == pytest.approx(
        true_positives / (true_positives + matrix[0][1])
    )
    assert metrics["support"]["default"] + metrics["support"]["non_default"] == len(holdout)
    assert len(metrics["roc_curve"]["fpr"]) == len(metrics["roc_curve"]["tpr"])
    # Predictions at the reported threshold must match the confusion matrix.
    predicted = [int(value >= float(metrics["threshold"])) for value in metrics["probabilities"]]
    assert sum(predicted) == matrix[0][1] + matrix[1][1]


def test_training_is_reproducible_and_holdout_rows_are_never_used_for_fitting(bundle):
    second = build_credit_bundle()
    assert second["lightgbm"]["roc_auc"] == bundle["lightgbm"]["roc_auc"]
    assert second["lightgbm"]["probabilities"] == bundle["lightgbm"]["probabilities"]
    pd.testing.assert_frame_equal(second["importance"], bundle["importance"])
    assert second["train"].index.tolist() == bundle["train"].index.tolist()
    assert not set(bundle["train"].index) & set(bundle["holdout"].index)
    assert set(bundle["train"].index) | set(bundle["holdout"].index) == set(bundle["data"].index)
    # The baseline sees the training split only; its scaler stats prove it.
    scaler = bundle["scaler"]
    train_means = bundle["train"][list(FEATURES)].mean().to_numpy()
    all_means = bundle["data"][list(FEATURES)].mean().to_numpy()
    assert np.allclose(scaler.mean_, train_means)
    assert not np.allclose(scaler.mean_, all_means)
    assert np.allclose(scaler.scale_, bundle["train"][list(FEATURES)].std(ddof=0).to_numpy())
    assert bundle["baseline"].coef_.shape == (1, len(FEATURES))


def test_feature_importance_is_global_and_covers_every_feature(bundle):
    importance = bundle["importance"]
    assert len(importance) == len(FEATURES)
    assert set(importance["feature"]) == set(FEATURES)
    assert importance["gain_share"].sum() == pytest.approx(1.0)
    assert (importance["gain"] > 0).all()
    assert importance["gain"].is_monotonic_decreasing
    assert int(importance["splits"].sum()) > 0
    labels = dict(zip(importance["feature"], importance["label"], strict=True))
    assert all(labels[name] for name in FEATURES)


def test_evaluate_classifier_guards_and_perfect_case():
    perfect = evaluate_classifier([0.1, 0.9, 0.2, 0.8], [0, 1, 0, 1], threshold=0.5)
    assert perfect["roc_auc"] == pytest.approx(1.0)
    assert perfect["precision"] == pytest.approx(1.0)
    assert perfect["recall"] == pytest.approx(1.0)
    assert perfect["confusion"] == [[2, 0], [0, 2]]
    assert perfect["support"] == {"non_default": 2, "default": 2}
    assert perfect["records"] == 4
    with pytest.raises(ValueError):
        evaluate_classifier([], [])
    with pytest.raises(ValueError):
        evaluate_classifier([0.5, 0.5], [1])
    with pytest.raises(ValueError):
        evaluate_classifier([1.5], [1])
    with pytest.raises(ValueError):
        evaluate_classifier([np.nan], [1])
    with pytest.raises(ValueError):
        evaluate_classifier([0.5], [1], threshold=1.0)


# --------------------------------------------------------------------------- #
# Applicant scoring (demo only)
# --------------------------------------------------------------------------- #
def test_policy_bands_and_applicant_validation():
    assert policy_band(-0.2)["code"] == "A"
    assert policy_band(0.0)["code"] == "A"
    assert policy_band(0.099)["code"] == "A"
    assert policy_band(0.10)["code"] == "B"
    assert policy_band(0.25)["code"] == "C"
    assert policy_band(0.50)["code"] == "D"
    assert policy_band(1.5)["code"] == "D"
    applicant = default_applicant()
    assert validate_applicant(applicant) == []
    assert validate_applicant({**applicant, "annual_income_million": 40.0}) == []
    assert validate_applicant({**applicant, "annual_income_million": 0.5}) != []
    assert validate_applicant({**applicant, "debt_to_income": 0.95}) != []
    assert validate_applicant({**applicant, "credit_utilization": 1.5}) != []
    assert validate_applicant({**applicant, "delinquencies_24m": -1}) != []
    assert validate_applicant({**applicant, "tenure_years": 31.0}) != []
    assert validate_applicant({**applicant, "debt_to_income": "abc"}) != []
    assert validate_applicant({name: applicant[name] for name in FEATURES[:-1]}) != []


def test_applicant_scoring_uses_the_trained_model_and_stays_in_range(bundle):
    low_risk = {
        "annual_income_million": 20.0,
        "debt_to_income": 0.05,
        "credit_utilization": 0.05,
        "delinquencies_24m": 0.0,
        "tenure_years": 20.0,
    }
    high_risk = {
        "annual_income_million": 1.5,
        "debt_to_income": 0.88,
        "credit_utilization": 0.98,
        "delinquencies_24m": 5.0,
        "tenure_years": 0.5,
    }
    low = score_applicant(bundle, low_risk)
    high = score_applicant(bundle, high_risk)
    assert 0.0 <= float(low["probability"]) <= 1.0
    assert float(low["probability"]) < float(high["probability"])
    assert float(low["probability"]) < float(bundle["threshold"]) < float(high["probability"])
    assert low["band"]["code"] == "A"
    assert high["band"]["code"] == "D"
    assert 0.0 <= float(low["percentile"]) <= 100.0
    assert float(high["percentile"]) > float(low["percentile"])
    # The reported probability must be the model's own output, not a canned value.
    row = pd.DataFrame([{name: float(low_risk[name]) for name in FEATURES}])
    expected = float(bundle["model"].predict_proba(row[list(FEATURES)])[:, 1][0])
    assert float(low["probability"]) == pytest.approx(expected)
    with pytest.raises(ValueError):
        score_applicant(bundle, {**low_risk, "credit_utilization": 2.0})


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def test_scene_renders_records_demo_review_and_isolates_sessions():
    from streamlit.testing.v1 import AppTest

    from demos.credit import credit_model_bundle

    script = "from demos.credit import render\nrender()"
    snapshot = list(credit_model_bundle(DEFAULT_SEED, N_RECORDS)["lightgbm"]["probabilities"])
    at = AppTest.from_string(script, default_timeout=60).run()
    assert not at.exception
    assert len(at.number_input) == len(FEATURES)
    assert len(at.tabs) == 3
    assert any("ROC AUC" in item.label for item in at.metric)
    probability = next(item.value for item in at.metric if "default probability" in item.label)

    at.number_input(key="credit_applicant_credit_utilization").set_value(0.99).run()
    assert not at.exception
    changed = next(item.value for item in at.metric if "default probability" in item.label)
    assert changed != probability

    at.button(key="credit_review_accept").click().run()
    assert not at.exception
    assert at.session_state["credit_review"] == "accept"
    assert any("Demo record only" in item.value for item in at.info)

    # Cached training artifacts must not be mutated by rendering.
    assert (
        list(credit_model_bundle(DEFAULT_SEED, N_RECORDS)["lightgbm"]["probabilities"]) == snapshot
    )

    # A second session starts clean and keeps its own widgets.
    other = AppTest.from_string(script, default_timeout=60).run()
    assert "credit_review" not in other.session_state
    assert not other.info
    assert other.session_state["credit_applicant_credit_utilization"] == pytest.approx(0.45)
    assert at.session_state["credit_applicant_credit_utilization"] == pytest.approx(0.99)

    at.button(key="credit_reset").click().run()
    assert not at.exception
    assert "credit_review" not in at.session_state
    assert at.number_input(key="credit_applicant_credit_utilization").value == pytest.approx(0.45)
