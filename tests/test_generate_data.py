import pytest

from generate_data import (
    DORMANT_PASS_THROUGH,
    NIGHT_HIGH_VALUE_WITHDRAWAL,
    SMURFING,
    TYPOLOGIES,
    GeneratorConfig,
    generate_dataset,
)


@pytest.fixture(scope="module")
def dataset():
    return generate_dataset(GeneratorConfig(n_accounts=300, seed=7))


def _scenario(ds, typology):
    ids = ds.ground_truth.loc[ds.ground_truth["typology"] == typology, "transaction_id"]
    return ds.transactions[ds.transactions["transaction_id"].isin(ids)]


def test_schema_and_keys(dataset):
    txns = dataset.transactions
    assert txns["transaction_id"].is_unique
    assert set(txns["account_id"]) <= set(dataset.accounts["account_id"])
    assert (txns["amount"] > 0).all()
    assert set(txns["transaction_type"]) <= {"DEPOSIT", "WITHDRAWAL", "TRANSFER_IN", "TRANSFER_OUT"}
    assert set(dataset.accounts["risk_category"]) <= {"LOW", "MEDIUM", "HIGH"}


def test_all_typologies_injected(dataset):
    assert set(dataset.ground_truth["typology"]) == set(TYPOLOGIES)


def test_smurfing_stays_just_below_threshold(dataset):
    transfers = _scenario(dataset, SMURFING).query("transaction_type == 'TRANSFER_OUT'")
    assert transfers["amount"].between(950_000, 999_999).all()


def test_night_withdrawals_are_between_2_and_4am(dataset):
    night = _scenario(dataset, NIGHT_HIGH_VALUE_WITHDRAWAL)
    assert night["timestamp"].dt.hour.between(2, 4).all()
    assert (night["amount"] >= 1_000_000).all()


def test_dormant_pass_through_forwards_full_amount(dataset):
    for _, group in _scenario(dataset, DORMANT_PASS_THROUGH).groupby("account_id"):
        inflow = group[group["transaction_type"] == "TRANSFER_IN"].iloc[0]
        outflow = group[group["transaction_type"] == "TRANSFER_OUT"].iloc[0]
        assert outflow["amount"] == inflow["amount"]
        assert 0 < (outflow["timestamp"] - inflow["timestamp"]).total_seconds() <= 15 * 60


def test_generation_is_deterministic():
    cfg = GeneratorConfig(n_accounts=50, seed=1)
    a, b = generate_dataset(cfg), generate_dataset(cfg)
    assert a.transactions.equals(b.transactions)
