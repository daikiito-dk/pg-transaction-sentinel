from datetime import datetime, timedelta

import pandas as pd
import pytest

import ml_engine
from generate_data import GeneratorConfig, generate_dataset


def _txn(i, account, amount, txn_type, ts):
    return {
        "transaction_id": i,
        "account_id": account,
        "amount": amount,
        "transaction_type": txn_type,
        "timestamp": ts,
        "destination_account": None,
    }


@pytest.fixture
def handcrafted():
    t0 = datetime(2026, 6, 1, 10, 0)
    rows = [_txn(1, "A", 5_000, "WITHDRAWAL", t0)]
    # Smurfing burst on account A
    for k in range(4):
        rows.append(
            _txn(2 + k, "A", 960_000, "TRANSFER_OUT", t0 + timedelta(days=60, minutes=30 * k))
        )
    # Dormant account B: one old txn, then pass-through 90 days later
    rows.append(_txn(10, "B", 3_000, "WITHDRAWAL", t0))
    rows.append(_txn(11, "B", 8_000_000, "TRANSFER_IN", t0 + timedelta(days=90)))
    rows.append(_txn(12, "B", 8_000_000, "TRANSFER_OUT", t0 + timedelta(days=90, minutes=5)))
    # Night withdrawal on account C
    for d in range(10):
        rows.append(_txn(20 + d, "C", 4_000, "WITHDRAWAL", t0 + timedelta(days=d)))
    rows.append(_txn(40, "C", 3_000_000, "WITHDRAWAL", datetime(2026, 6, 20, 2, 30)))
    return pd.DataFrame(rows)


def test_features_capture_typology_signals(handcrafted):
    f = ml_engine.build_features(handcrafted).set_index("transaction_id")
    assert f.loc[2, "near_threshold_count_24h"] == 4  # look-around window covers whole burst
    assert f.loc[40, "is_night"] == 1
    assert f.loc[40, "amount_vs_baseline"] > 100
    assert f.loc[11, "dormancy_gap_days"] >= 89
    assert f.loc[12, "dormancy_gap_days"] >= 89  # inherited from the waking deposit
    assert f.loc[12, "pass_through_ratio"] == pytest.approx(1.0)
    assert f.loc[1, "pass_through_ratio"] == 0


def test_rules_fire_on_handcrafted_patterns(handcrafted):
    f = ml_engine.build_features(handcrafted)
    hits = ml_engine.apply_typology_rules(f).set_index(f["transaction_id"])
    assert hits.loc[[2, 3, 4, 5], ml_engine.RULE_SMURFING].all()
    assert hits.loc[40, ml_engine.RULE_NIGHT]
    assert hits.loc[[11, 12], ml_engine.RULE_DORMANT].all()
    assert not hits.loc[[1, 10, 20]].any(axis=None)


@pytest.mark.parametrize(
    ("score", "level"), [(95, "HIGH"), (80, "HIGH"), (79.9, "MEDIUM"), (50, "MEDIUM"), (10, "LOW")]
)
def test_risk_level_bands(score, level):
    assert ml_engine.risk_level(score) == level


def test_end_to_end_detection_on_synthetic_data():
    ds = generate_dataset(GeneratorConfig(n_accounts=400, seed=11))
    result = ml_engine.score_transactions(ds.transactions)
    scores = result.scores

    assert len(scores) == len(ds.transactions)
    assert scores["risk_score"].between(0, 100).all()
    assert set(scores["risk_level"]) <= {"HIGH", "MEDIUM", "LOW"}

    metrics = ml_engine.evaluate(scores, ds.ground_truth)
    assert metrics["recall"] >= 0.9
    assert metrics["precision"] >= 0.9

    high = scores[scores["risk_level"] == "HIGH"]
    assert (high["reasons"] != "").all()
