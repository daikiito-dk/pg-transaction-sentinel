"""AML risk scoring engine.

Pipeline: PostgreSQL -> behavioural features -> Isolation Forest anomaly
score + typology rules -> 0-100 risk score -> transaction_risk_scores table.

The hybrid (rules + unsupervised ML) design mirrors how transaction
monitoring is typically run in banks: typology rules give explainable
Suspicious Transaction Alerts, while the model surfaces unusual behaviour
that no rule describes yet.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sqlalchemy import text

from config import HIGH_RISK_THRESHOLD, MEDIUM_RISK_THRESHOLD, REPORTING_THRESHOLD_JPY
from db import copy_dataframe

OUTFLOW_TYPES = ("WITHDRAWAL", "TRANSFER_OUT")
INFLOW_TYPES = ("DEPOSIT", "TRANSFER_IN")

NEAR_THRESHOLD_LOWER_JPY = 950_000
NIGHT_HOURS = range(0, 5)
DORMANCY_DAYS = 30
PASS_THROUGH_WINDOW_MIN = 60

MODEL_FEATURES = [
    "log_amount",
    "log_amount_vs_baseline",
    "is_night",
    "log_outflow_24h",
    "near_threshold_count_24h",
    "dormancy_gap_days",
    "pass_through_ratio",
]

RULE_SMURFING = "SMURFING"
RULE_NIGHT = "NIGHT_HIGH_VALUE_WITHDRAWAL"
RULE_DORMANT = "DORMANT_PASS_THROUGH"
RULE_WEIGHTS = {RULE_SMURFING: 85.0, RULE_NIGHT: 85.0, RULE_DORMANT: 90.0}
MULTI_RULE_BONUS = 5.0
ML_ONLY_REASON = "ML_ANOMALY"
REASON_SEPARATOR = " | "


@dataclass
class ScoringResult:
    scores: pd.DataFrame
    model: IsolationForest


# --------------------------------------------------------------------------- #
# Feature engineering
# --------------------------------------------------------------------------- #
def _per_account_rolling_sum(df: pd.DataFrame, column: str, window: str) -> np.ndarray:
    # df must be sorted by (account_id, timestamp) so the grouped result lines up row-for-row
    rolled = (
        df.set_index("timestamp").groupby("account_id", sort=True)[column].rolling(window).sum()
    )
    return rolled.to_numpy()


def _per_account_centered_count(df: pd.DataFrame, column: str, window: pd.Timedelta) -> np.ndarray:
    # Batch AML monitoring re-scores history, so a structured burst is judged
    # on activity both before and after each transaction (t - window, t + window].
    out = np.zeros(len(df))
    times = df["timestamp"].to_numpy()
    flags = df[column].to_numpy(dtype=float)
    for idx in df.groupby("account_id", sort=False).indices.values():
        t, cs = times[idx], np.concatenate([[0.0], np.cumsum(flags[idx])])
        left = np.searchsorted(t, t - window, side="right")
        right = np.searchsorted(t, t + window, side="right")
        out[idx] = cs[right] - cs[left]
    return out


def build_features(transactions: pd.DataFrame) -> pd.DataFrame:
    df = transactions.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["amount"] = df["amount"].astype("float64")
    df = df.sort_values(["account_id", "timestamp", "transaction_id"], kind="stable")
    df = df.reset_index(drop=True)
    grp = df.groupby("account_id", sort=True)

    is_outflow = df["transaction_type"].isin(OUTFLOW_TYPES)
    is_inflow = df["transaction_type"].isin(INFLOW_TYPES)
    df["is_outflow"] = is_outflow.astype(int)

    df["hour"] = df["timestamp"].dt.hour
    df["is_night"] = df["hour"].isin(NIGHT_HOURS).astype(int)
    df["log_amount"] = np.log1p(df["amount"])

    # Baseline = median of the account's *previous* transactions (no look-ahead)
    prior_median = grp["amount"].transform(lambda s: s.expanding().median().shift(1))
    prior_median = prior_median.fillna(df["amount"].median())
    df["amount_vs_baseline"] = df["amount"] / prior_median.clip(lower=1.0)
    df["log_amount_vs_baseline"] = np.log1p(df["amount_vs_baseline"])

    df["outflow_amount"] = df["amount"].where(is_outflow, 0.0)
    df["outflow_24h"] = _per_account_rolling_sum(df, "outflow_amount", "24h")
    df["log_outflow_24h"] = np.log1p(df["outflow_24h"])

    df["near_threshold"] = (
        is_outflow
        & (df["amount"] >= NEAR_THRESHOLD_LOWER_JPY)
        & (df["amount"] < REPORTING_THRESHOLD_JPY)
    ).astype(int)
    df["near_threshold_count_24h"] = _per_account_centered_count(
        df, "near_threshold", pd.Timedelta(hours=24)
    )

    # An account's first observed transaction is measured against the start of
    # the observation window: no activity since then is itself dormancy.
    observation_start = df["timestamp"].min()
    gap = grp["timestamp"].diff().fillna(df["timestamp"] - observation_start)
    gap_days = gap.dt.total_seconds() / 86_400
    prev_gap = gap_days.groupby(df["account_id"]).shift(1).fillna(0.0)
    # A pass-through outflow inherits the dormancy of the deposit that woke the account up
    df["dormancy_gap_days"] = np.maximum(gap_days, prev_gap)

    last_in_amount = df["amount"].where(is_inflow).groupby(df["account_id"]).ffill()
    last_in_time = df["timestamp"].where(is_inflow).groupby(df["account_id"]).ffill()
    last_in_amount = last_in_amount.groupby(df["account_id"]).shift(1)
    last_in_time = last_in_time.groupby(df["account_id"]).shift(1)
    minutes_since_in = (df["timestamp"] - last_in_time).dt.total_seconds() / 60
    within_window = is_outflow & (minutes_since_in <= PASS_THROUGH_WINDOW_MIN)
    ratio = (df["amount"] / last_in_amount).where(within_window, 0.0)
    df["pass_through_ratio"] = ratio.fillna(0.0).clip(upper=1.5)

    return df


# --------------------------------------------------------------------------- #
# Typology rules
# --------------------------------------------------------------------------- #
def apply_typology_rules(features: pd.DataFrame) -> pd.DataFrame:
    f = features
    hits = pd.DataFrame(index=f.index)
    structuring_burst = f["near_threshold_count_24h"] >= 3
    funding_deposit = (f["is_outflow"] == 0) & (f["amount"] >= REPORTING_THRESHOLD_JPY)
    hits[RULE_SMURFING] = structuring_burst & ((f["near_threshold"] == 1) | funding_deposit)
    hits[RULE_NIGHT] = (
        (f["is_night"] == 1)
        & (f["transaction_type"] == "WITHDRAWAL")
        & (f["amount"] >= REPORTING_THRESHOLD_JPY)
        & (f["amount_vs_baseline"] >= 20)
    )
    hits[RULE_DORMANT] = (f["dormancy_gap_days"] >= DORMANCY_DAYS) & (
        (f["amount"] >= REPORTING_THRESHOLD_JPY) | (f["pass_through_ratio"] >= 0.9)
    )
    return hits


def _rule_score(hits: pd.DataFrame) -> np.ndarray:
    weights = np.array([RULE_WEIGHTS[c] for c in hits.columns])
    hit_matrix = hits.to_numpy(dtype=float)
    top = (hit_matrix * weights).max(axis=1)
    extra = np.clip(hit_matrix.sum(axis=1) - 1, 0, None) * MULTI_RULE_BONUS
    return np.where(top > 0, top + extra, 0.0)


# --------------------------------------------------------------------------- #
# Explanations
# --------------------------------------------------------------------------- #
def _fmt_yen(value: float) -> str:
    return f"¥{value:,.0f}"


def _explain(row: pd.Series, hit_row: pd.Series, ml_score: float) -> str:
    reasons: list[str] = []
    if hit_row[RULE_SMURFING]:
        prefix = "スマーフィング原資の入金" if row["is_outflow"] == 0 else "スマーフィング疑い"
        reasons.append(
            f"{prefix}: 前後24時間に閾値（{_fmt_yen(REPORTING_THRESHOLD_JPY)}）直下の"
            f"送金 {int(row['near_threshold_count_24h'])} 件"
        )
    if hit_row[RULE_NIGHT]:
        reasons.append(
            f"深夜の高額出金: {int(row['hour']):02d}時台に {_fmt_yen(row['amount'])}"
            f"（平常時の約 {row['amount_vs_baseline']:,.0f} 倍）"
        )
    if hit_row[RULE_DORMANT]:
        msg = f"休眠口座の再稼働: {row['dormancy_gap_days']:.0f} 日ぶりの取引"
        if row["pass_through_ratio"] >= 0.9:
            msg += f"、入金直後に {row['pass_through_ratio']:.0%} を即時転送"
        reasons.append(msg)
    if ml_score >= MEDIUM_RISK_THRESHOLD:
        detail = []
        if row["amount_vs_baseline"] >= 10:
            detail.append(f"平常時比 {row['amount_vs_baseline']:,.0f} 倍の金額")
        if row["is_night"]:
            detail.append("深夜帯の取引")
        if row["outflow_24h"] >= REPORTING_THRESHOLD_JPY:
            detail.append(f"24時間出金累計 {_fmt_yen(row['outflow_24h'])}")
        suffix = f"（{'、'.join(detail)}）" if detail else ""
        reasons.append(f"統計的外れ値（Isolation Forest スコア {ml_score:.0f}）{suffix}")
    return REASON_SEPARATOR.join(reasons)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def risk_level(score: float) -> str:
    if score >= HIGH_RISK_THRESHOLD:
        return "HIGH"
    if score >= MEDIUM_RISK_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def _normalise_anomaly(raw: np.ndarray) -> np.ndarray:
    # Median behaviour maps to 0 and the most extreme 0.1% saturates at 100,
    # so the scale stays stable as the transaction volume grows.
    lo, hi = np.quantile(raw, [0.5, 0.999])
    return np.clip((raw - lo) / max(hi - lo, 1e-9), 0, 1) * 100


def score_transactions(
    transactions: pd.DataFrame, contamination: float = 0.01, seed: int = 42
) -> ScoringResult:
    features = build_features(transactions)
    x = features[MODEL_FEATURES].to_numpy(dtype=float)

    model = IsolationForest(
        n_estimators=300, contamination=contamination, random_state=seed, n_jobs=-1
    )
    model.fit(x)
    ml_score = _normalise_anomaly(-model.score_samples(x))

    hits = apply_typology_rules(features)
    rule_score = _rule_score(hits)
    combined = np.where(rule_score > 0, 0.8 * rule_score + 0.2 * ml_score, 0.0)
    final = np.clip(np.maximum(ml_score * 0.85, combined), 0, 100).round(1)

    rule_labels = hits.apply(lambda r: ",".join(c for c in hits.columns if r[c]), axis=1)
    rule_labels = rule_labels.where(
        (rule_labels != "") | (ml_score < MEDIUM_RISK_THRESHOLD), ML_ONLY_REASON
    )
    reasons = [
        _explain(features.iloc[i], hits.iloc[i], ml_score[i])
        if final[i] >= MEDIUM_RISK_THRESHOLD
        else ""
        for i in range(len(features))
    ]

    scores = pd.DataFrame(
        {
            "transaction_id": features["transaction_id"].astype("int64"),
            "risk_score": final,
            "risk_level": [risk_level(s) for s in final],
            "ml_score": ml_score.round(1),
            "rule_hits": rule_labels.to_numpy(),
            "reasons": reasons,
        }
    )
    return ScoringResult(scores=scores, model=model)


def evaluate(scores: pd.DataFrame, ground_truth: pd.DataFrame) -> dict[str, float]:
    merged = scores.merge(ground_truth, on="transaction_id", how="left")
    actual = merged["typology"].notna()
    predicted = merged["risk_score"] >= HIGH_RISK_THRESHOLD
    tp = int((actual & predicted).sum())
    precision = tp / max(int(predicted.sum()), 1)
    recall = tp / max(int(actual.sum()), 1)
    result = {"alerts": int(predicted.sum()), "precision": precision, "recall": recall}
    for typology, group in merged[actual].groupby("typology"):
        result[f"recall[{typology}]"] = float((group["risk_score"] >= HIGH_RISK_THRESHOLD).mean())
    return result


# --------------------------------------------------------------------------- #
# Database I/O
# --------------------------------------------------------------------------- #
def load_transactions(engine) -> pd.DataFrame:
    return pd.read_sql(
        "SELECT transaction_id, account_id, amount, transaction_type, timestamp, "
        "destination_account FROM transactions",
        engine,
    )


def save_scores(scores: pd.DataFrame, engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE transaction_risk_scores"))
        copy_dataframe(conn, scores, "transaction_risk_scores")


def run(engine) -> tuple[pd.DataFrame, dict[str, float]]:
    txns = load_transactions(engine)
    result = score_transactions(txns)
    save_scores(result.scores, engine)
    truth = pd.read_sql("SELECT transaction_id, typology FROM aml_ground_truth", engine)
    return result.scores, evaluate(result.scores, truth)


def main() -> None:
    argparse.ArgumentParser(description="Score transactions and store AML risk scores").parse_args()
    from config import get_engine

    scores, metrics = run(get_engine())
    print(f"scored transactions : {len(scores):,}")
    print(scores["risk_level"].value_counts().reindex(["HIGH", "MEDIUM", "LOW"]).to_string())
    print(f"--- evaluation against injected scenarios (score >= {HIGH_RISK_THRESHOLD}) ---")
    for key, value in metrics.items():
        print(f"{key:<40}: {value:.3f}" if isinstance(value, float) else f"{key:<40}: {value}")


if __name__ == "__main__":
    main()
