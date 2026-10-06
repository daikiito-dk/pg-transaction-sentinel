"""Seed and score the database once, so a fresh deployment comes up with data.

Safe to run on every start: it does nothing when risk scores already exist.
"""

from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

import ml_engine
from config import get_engine
from generate_data import generate_dataset, load_to_postgres


def wait_for_database(engine, attempts: int = 30, delay: float = 2.0) -> None:
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            if attempt == attempts:
                raise
            time.sleep(delay)


def has_scores(engine) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(text("SELECT EXISTS (SELECT 1 FROM transaction_risk_scores)")).scalar()
        )


def main() -> None:
    engine = get_engine()
    wait_for_database(engine)
    if has_scores(engine):
        print("bootstrap: risk scores already present, skipping")
        return
    print("bootstrap: generating synthetic data ...")
    load_to_postgres(generate_dataset(), engine)
    print("bootstrap: scoring transactions ...")
    _, metrics = ml_engine.run(engine)
    print(f"bootstrap: done ({metrics['alerts']:,} high-risk alerts)")


if __name__ == "__main__":
    main()
