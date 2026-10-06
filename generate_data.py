"""Synthetic core-banking data generator with injected AML typologies.

Normal retail activity is generated for every account, then a configurable
share of accounts is used to inject three money-laundering typologies:

* SMURFING              - bursts of JPY 950k-990k transfers just below the
                          JPY 1M reporting / auto-alert threshold
* NIGHT_HIGH_VALUE_WITHDRAWAL - small-ticket accounts suddenly withdrawing
                          millions of yen between 02:00 and 04:00
* DORMANT_PASS_THROUGH  - a long-dormant account receives a large deposit and
                          forwards the full amount within minutes
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import text

from db import copy_dataframe

SMURFING = "SMURFING"
NIGHT_HIGH_VALUE_WITHDRAWAL = "NIGHT_HIGH_VALUE_WITHDRAWAL"
DORMANT_PASS_THROUGH = "DORMANT_PASS_THROUGH"
TYPOLOGIES = (SMURFING, NIGHT_HIGH_VALUE_WITHDRAWAL, DORMANT_PASS_THROUGH)

LAST_NAMES = [
    "佐藤",
    "鈴木",
    "高橋",
    "田中",
    "伊藤",
    "渡辺",
    "山本",
    "中村",
    "小林",
    "加藤",
    "吉田",
    "山田",
    "佐々木",
    "山口",
    "松本",
    "井上",
    "木村",
    "林",
    "斎藤",
    "清水",
]
FIRST_NAMES = [
    "太郎",
    "花子",
    "健一",
    "美咲",
    "翔",
    "陽菜",
    "大輔",
    "さくら",
    "直樹",
    "由美",
    "拓海",
    "結衣",
    "誠",
    "愛",
    "隆",
    "真由美",
    "蓮",
    "葵",
    "浩二",
    "恵",
]


@dataclass(frozen=True)
class GeneratorConfig:
    n_accounts: int = 1500
    days: int = 120
    end_date: datetime = datetime(2026, 9, 30, 23, 59, 59)
    suspicious_account_ratio: float = 0.05
    avg_monthly_txns: float = 5.0
    seed: int = 42

    @property
    def start_date(self) -> datetime:
        return self.end_date - timedelta(days=self.days)


@dataclass
class Dataset:
    accounts: pd.DataFrame
    transactions: pd.DataFrame
    ground_truth: pd.DataFrame


class _Builder:
    def __init__(self, cfg: GeneratorConfig) -> None:
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg.seed)
        self.rows: list[dict] = []

    def add(self, account_id, amount, txn_type, ts, destination=None, typology=None):
        self.rows.append(
            {
                "account_id": account_id,
                "amount": int(round(amount, -1)) if amount >= 100 else int(max(amount, 1)),
                "transaction_type": txn_type,
                "timestamp": ts.replace(microsecond=0),
                "destination_account": destination,
                "typology": typology,
            }
        )

    def random_ts(self, start: datetime, end: datetime, day_hours=(8, 22)) -> datetime:
        span_days = max((end - start).days, 1)
        day = start + timedelta(days=int(self.rng.integers(0, span_days)))
        hour = int(self.rng.integers(day_hours[0], day_hours[1]))
        return day.replace(
            hour=hour,
            minute=int(self.rng.integers(0, 60)),
            second=int(self.rng.integers(0, 60)),
        )

    def external_account(self) -> str:
        return f"EXT-{int(self.rng.integers(10_000_000, 99_999_999))}"


def _build_accounts(b: _Builder) -> pd.DataFrame:
    cfg, rng = b.cfg, b.rng
    ids = [f"ACC-{i:06d}" for i in range(1, cfg.n_accounts + 1)]
    names = [f"{rng.choice(LAST_NAMES)} {rng.choice(FIRST_NAMES)}" for _ in range(cfg.n_accounts)]
    created = [cfg.start_date - timedelta(days=int(rng.integers(30, 365 * 10))) for _ in ids]
    risk = rng.choice(["LOW", "MEDIUM", "HIGH"], size=cfg.n_accounts, p=[0.8, 0.17, 0.03])
    return pd.DataFrame(
        {
            "account_id": ids,
            "customer_name": names,
            "created_at": [c.replace(hour=9, minute=0, second=0, microsecond=0) for c in created],
            "risk_category": risk,
        }
    )


def _normal_activity(
    b: _Builder, account_id: str, start: datetime, end: datetime, account_ids: list[str]
) -> None:
    """Everyday retail activity: small-ticket payments, ATM, salary, rent."""
    rng = b.rng
    months = max((end - start).days / 30, 0.5)
    typical = float(rng.lognormal(mean=np.log(8_000), sigma=0.6))
    n = int(rng.poisson(b.cfg.avg_monthly_txns * months))
    for _ in range(n):
        ts = b.random_ts(start, end)
        amount = float(rng.lognormal(mean=np.log(typical), sigma=0.7))
        txn_type = rng.choice(
            ["WITHDRAWAL", "TRANSFER_OUT", "DEPOSIT", "TRANSFER_IN"], p=[0.45, 0.3, 0.15, 0.1]
        )
        dest = None
        if txn_type == "TRANSFER_OUT":
            dest = str(rng.choice(account_ids)) if rng.random() < 0.4 else b.external_account()
        b.add(account_id, amount, txn_type, ts, dest)

    if rng.random() < 0.6:
        salary = float(rng.normal(280_000, 60_000))
        rent = salary * float(rng.uniform(0.25, 0.35))
        landlord = b.external_account()
        month = start.replace(day=1)
        while month < end:
            pay_day = month.replace(day=25, hour=9, minute=0)
            if start <= pay_day <= end:
                b.add(account_id, max(salary, 150_000), "TRANSFER_IN", pay_day)
                rent_day = month.replace(day=27, hour=10, minute=int(rng.integers(0, 60)))
                if rent_day <= end:
                    b.add(account_id, rent, "TRANSFER_OUT", rent_day, landlord)
            month = (month + timedelta(days=32)).replace(day=1)


def _inject_smurfing(b: _Builder, account_id: str) -> None:
    rng, cfg = b.rng, b.cfg
    base = b.random_ts(
        cfg.end_date - timedelta(days=45), cfg.end_date - timedelta(days=1), day_hours=(9, 16)
    )
    ts = base
    for _ in range(int(rng.integers(4, 9))):
        amount = float(rng.integers(950_000, 990_001))
        b.add(account_id, amount, "TRANSFER_OUT", ts, b.external_account(), SMURFING)
        ts += timedelta(minutes=int(rng.integers(5, 90)))
    # Funding for the structured transfers arrives shortly before
    b.add(
        account_id,
        float(rng.integers(5_000_000, 9_000_000)),
        "DEPOSIT",
        base - timedelta(hours=int(rng.integers(1, 6))),
        None,
        SMURFING,
    )


def _inject_night_withdrawal(b: _Builder, account_id: str) -> None:
    rng, cfg = b.rng, b.cfg
    day = b.random_ts(cfg.end_date - timedelta(days=45), cfg.end_date - timedelta(days=1))
    ts = day.replace(hour=int(rng.integers(2, 4)), minute=int(rng.integers(0, 60)))
    for _ in range(int(rng.integers(2, 5))):
        amount = float(rng.integers(1_500_000, 5_000_000))
        b.add(account_id, amount, "WITHDRAWAL", ts, None, NIGHT_HIGH_VALUE_WITHDRAWAL)
        ts += timedelta(minutes=int(rng.integers(3, 25)))


def _inject_dormant_pass_through(b: _Builder, account_id: str) -> None:
    rng, cfg = b.rng, b.cfg
    ts = b.random_ts(
        cfg.end_date - timedelta(days=20), cfg.end_date - timedelta(days=1), day_hours=(0, 24)
    )
    amount = float(rng.integers(3_000_000, 20_000_000))
    b.add(account_id, amount, "TRANSFER_IN", ts, None, DORMANT_PASS_THROUGH)
    b.add(
        account_id,
        amount,
        "TRANSFER_OUT",
        ts + timedelta(minutes=int(rng.integers(2, 15))),
        b.external_account(),
        DORMANT_PASS_THROUGH,
    )


def generate_dataset(cfg: GeneratorConfig | None = None) -> Dataset:
    cfg = cfg or GeneratorConfig()
    b = _Builder(cfg)
    accounts = _build_accounts(b)
    account_ids = accounts["account_id"].tolist()

    n_suspicious = max(len(TYPOLOGIES), int(round(cfg.n_accounts * cfg.suspicious_account_ratio)))
    suspicious_ids = b.rng.choice(account_ids, size=n_suspicious, replace=False)
    assignment = {str(acc): TYPOLOGIES[i % len(TYPOLOGIES)] for i, acc in enumerate(suspicious_ids)}

    dormant_cutoff = cfg.start_date + timedelta(days=min(25, cfg.days // 4))
    for acc in account_ids:
        typology = assignment.get(acc)
        if typology == DORMANT_PASS_THROUGH:
            # Activity only at the very start of the window, then silence
            _normal_activity(b, acc, cfg.start_date, dormant_cutoff, account_ids)
            for _ in range(2):
                b.add(
                    acc,
                    float(b.rng.integers(1_000, 30_000)),
                    "WITHDRAWAL",
                    b.random_ts(cfg.start_date, dormant_cutoff),
                )
            _inject_dormant_pass_through(b, acc)
            continue
        _normal_activity(b, acc, cfg.start_date, cfg.end_date, account_ids)
        if typology == SMURFING:
            _inject_smurfing(b, acc)
        elif typology == NIGHT_HIGH_VALUE_WITHDRAWAL:
            _inject_night_withdrawal(b, acc)

    txns = pd.DataFrame(b.rows)
    txns = txns.sort_values(["timestamp", "account_id"], kind="stable").reset_index(drop=True)
    txns.insert(0, "transaction_id", np.arange(1, len(txns) + 1, dtype=np.int64))
    txns["timestamp"] = pd.to_datetime(txns["timestamp"])

    truth = txns.loc[txns["typology"].notna(), ["transaction_id", "typology"]].reset_index(
        drop=True
    )
    txns = txns.drop(columns=["typology"])
    return Dataset(accounts=accounts, transactions=txns, ground_truth=truth)


def load_to_postgres(ds: Dataset, engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE transaction_risk_scores, aml_ground_truth, transactions, accounts")
        )
        copy_dataframe(conn, ds.accounts, "accounts")
        copy_dataframe(conn, ds.transactions, "transactions")
        copy_dataframe(conn, ds.ground_truth, "aml_ground_truth")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic AML transaction data")
    parser.add_argument("--accounts", type=int, default=GeneratorConfig.n_accounts)
    parser.add_argument("--days", type=int, default=GeneratorConfig.days)
    parser.add_argument(
        "--suspicious-ratio",
        type=float,
        default=GeneratorConfig.suspicious_account_ratio,
        help="share of accounts with an injected AML typology",
    )
    parser.add_argument("--seed", type=int, default=GeneratorConfig.seed)
    args = parser.parse_args()

    cfg = GeneratorConfig(
        n_accounts=args.accounts,
        days=args.days,
        suspicious_account_ratio=args.suspicious_ratio,
        seed=args.seed,
    )
    ds = generate_dataset(cfg)

    from config import get_engine

    load_to_postgres(ds, get_engine())

    print(f"accounts           : {len(ds.accounts):>7,}")
    print(f"transactions       : {len(ds.transactions):>7,}")
    print(
        f"suspicious txns    : {len(ds.ground_truth):>7,} "
        f"({len(ds.ground_truth) / len(ds.transactions):.1%})"
    )
    for typology, count in ds.ground_truth["typology"].value_counts().items():
        print(f"  - {typology:<28}: {count:,}")


if __name__ == "__main__":
    main()
