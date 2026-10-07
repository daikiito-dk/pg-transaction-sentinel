"""LightGBM default-risk assessment on synthetic, protected-attribute-free records.

The model is really trained and really evaluated: a LightGBM gradient-boosted
classifier (150 shallow trees, ``n_jobs=1``, deterministic) is fitted on a stratified
training split of 1,500 synthetic applications and scored on a held-out split that is
never used for fitting. A train-only standardised logistic regression is trained as a
baseline so that "fit transforms on the training split only" is verifiable, and the
scene reports both models honestly (at this sample size the linear scorecard is
competitive).

Labels are synthetic and noisy (a logistic propensity plus seeded noise), so held-out
metrics describe this simulation only - never real underwriting performance. Feature
importances are global model-level values: they are not causal and not per-applicant
explanations (no SHAP values are produced). Nothing here approves or declines credit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from lightgbm import LGBMClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from demos.ui import banner, demo_note, reset_button, table

DEFAULT_SEED = 20261007
N_RECORDS = 1500
HOLDOUT_FRACTION = 0.30
N_ESTIMATORS = 150
POLICY_THRESHOLD = 0.35

FEATURES = (
    "annual_income_million",
    "debt_to_income",
    "credit_utilization",
    "delinquencies_24m",
    "tenure_years",
)
TARGET = "default"
FEATURE_LABELS = {
    "annual_income_million": "年収（百万円） / Annual income (JPY m)",
    "debt_to_income": "返済負担率 / Debt-to-income ratio",
    "credit_utilization": "クレジット利用率 / Credit utilisation",
    "delinquencies_24m": "延滞件数24か月 / Delinquencies (24m)",
    "tenure_years": "取引年数 / Relationship years",
}
FEATURE_DOMAINS = {
    "annual_income_million": (1.0, 40.0),
    "debt_to_income": (0.0, 0.9),
    "credit_utilization": (0.0, 1.0),
    "delinquencies_24m": (0.0, 6.0),
    "tenure_years": (0.0, 30.0),
}
POLICY_BANDS = (
    (0.10, "A", "A · 低め / Low"),
    (0.25, "B", "B · 中程度 / Moderate"),
    (0.50, "C", "C · 高め / Elevated"),
    (1.01, "D", "D · 非常に高い / Very high"),
)
DEMO_REVIEW_OPTIONS = (
    ("review", "要審査 / Manual review"),
    ("accept", "条件付き可 / Conditional accept"),
    ("decline", "見送り / Decline"),
)
NO_PROTECTED_ATTRIBUTES = (
    "年齢・性別・国籍・人種などの保護属性は生成も使用もしていません。 / "
    "No protected attributes (age, gender, nationality, ethnicity) are generated or used."
)


# --------------------------------------------------------------------------- #
# Synthetic data generation
# --------------------------------------------------------------------------- #
def generate_credit_data(n_records: int = N_RECORDS, seed: int = DEFAULT_SEED) -> pd.DataFrame:
    """Generate synthetic applications with deterministic, deliberately noisy labels.

    Default propensity is a logistic function of the five financial features - including
    an interaction (utilisation x delinquency) and a debt-to-income kink, as in real
    credit risk - plus seeded noise, so the labels are reproducible but not perfectly
    predictable (the Bayes-optimal ROC AUC on this simulation is about 0.86).
    """
    if n_records < 100:
        raise ValueError("n_records must be at least 100 for a meaningful split")
    rng = np.random.default_rng(seed)
    income_million = np.clip(rng.lognormal(mean=1.0, sigma=0.5, size=n_records), 1.0, 40.0)
    debt_to_income = np.clip(rng.beta(2.2, 4.0, size=n_records) * 0.9, 0.0, 0.9)
    credit_utilization = np.clip(rng.beta(2.0, 2.5, size=n_records), 0.0, 1.0)
    delinquencies = np.clip(rng.poisson(0.35, size=n_records), 0, 6)
    tenure_years = np.clip(rng.gamma(2.0, 3.0, size=n_records), 0.0, 30.0)
    log_odds = (
        -4.10
        + 2.00 * credit_utilization
        + 0.45 * delinquencies
        + 2.00 * debt_to_income
        - 0.50 * (np.log10(income_million * 1e6) - 6.3)
        - 0.040 * tenure_years
        + 2.60 * credit_utilization * np.minimum(delinquencies, 3.0)
        + 4.00 * np.maximum(debt_to_income - 0.45, 0.0)
        + 0.70 * rng.standard_normal(n_records)
    )
    propensity = 1.0 / (1.0 + np.exp(-log_odds))
    default = (rng.random(n_records) < propensity).astype(int)
    return pd.DataFrame(
        {
            "annual_income_million": np.round(income_million, 2),
            "debt_to_income": np.round(debt_to_income, 3),
            "credit_utilization": np.round(credit_utilization, 3),
            "delinquencies_24m": delinquencies.astype(float),
            "tenure_years": np.round(tenure_years, 1),
            TARGET: default,
        }
    )


def split_train_holdout(
    data: pd.DataFrame,
    *,
    test_fraction: float = HOLDOUT_FRACTION,
    seed: int = DEFAULT_SEED,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Stratified, disjoint, reproducible train/holdout split (index preserved)."""
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be strictly between 0 and 1")
    if data.empty:
        raise ValueError("data is empty")
    if TARGET not in data.columns:
        raise ValueError(f"data must contain the {TARGET!r} column")
    if data[TARGET].nunique() < 2:
        raise ValueError("both classes are required for a stratified split")
    train, holdout = train_test_split(
        data,
        test_size=test_fraction,
        random_state=seed,
        shuffle=True,
        stratify=data[TARGET],
    )
    if len(set(train.index) & set(holdout.index)):
        raise ValueError("train and holdout overlap")
    return train.copy(), holdout.copy()


# --------------------------------------------------------------------------- #
# Training and honest held-out evaluation
# --------------------------------------------------------------------------- #
def fit_lightgbm(train: pd.DataFrame, *, seed: int = DEFAULT_SEED) -> LGBMClassifier:
    """Fit a deterministic single-threaded LightGBM classifier on the training split.

    Deliberately shallow (4 leaves, min_child_samples 30, L2 penalty): with only ~1,000
    training rows a deep forest memorises the noisy labels (train AUC 1.00 while holdout
    AUC collapses to ~0.60), so the shipped configuration is regularised.
    """
    model = LGBMClassifier(
        n_estimators=N_ESTIMATORS,
        learning_rate=0.05,
        num_leaves=4,
        min_child_samples=30,
        subsample=0.9,
        subsample_freq=1,
        colsample_bytree=0.8,
        reg_lambda=5.0,
        random_state=seed,
        n_jobs=1,
        deterministic=True,
        force_col_wise=True,
        verbose=-1,
    )
    model.fit(train[list(FEATURES)], train[TARGET])
    return model


def evaluate_classifier(
    probabilities: Sequence[float],
    labels: Sequence[int],
    *,
    threshold: float = POLICY_THRESHOLD,
) -> dict[str, object]:
    """Held-out classification metrics for one probability vector and one threshold."""
    scores = np.asarray(probabilities, dtype=float)
    truth = np.asarray(labels, dtype=int)
    if scores.size == 0 or scores.size != truth.size:
        raise ValueError("probabilities and labels must be non-empty and equally sized")
    if not np.isfinite(scores).all() or scores.min() < 0.0 or scores.max() > 1.0:
        raise ValueError("probabilities must be finite values within [0, 1]")
    if not 0.0 < threshold < 1.0:
        raise ValueError("threshold must be strictly between 0 and 1")
    predictions = (scores >= threshold).astype(int)
    matrix = confusion_matrix(truth, predictions, labels=[0, 1])
    fpr, tpr, _ = roc_curve(truth, scores)
    return {
        "threshold": float(threshold),
        "records": int(truth.size),
        "roc_auc": float(roc_auc_score(truth, scores)),
        "precision": float(precision_score(truth, predictions, zero_division=0)),
        "recall": float(recall_score(truth, predictions, zero_division=0)),
        "f1": float(f1_score(truth, predictions, zero_division=0)),
        "accuracy": float(accuracy_score(truth, predictions)),
        "confusion": matrix.tolist(),
        "support": {"non_default": int(matrix[0].sum()), "default": int(matrix[1].sum())},
        "roc_curve": {"fpr": fpr.tolist(), "tpr": tpr.tolist()},
        "probabilities": scores.tolist(),
        "labels": truth.tolist(),
    }


def feature_importance(
    model: LGBMClassifier, *, features: Sequence[str] = FEATURES
) -> pd.DataFrame:
    """Global gain and split-count importance - model level, not per-applicant, not causal."""
    gain = model.booster_.feature_importance(importance_type="gain")
    splits = model.booster_.feature_importance(importance_type="split")
    names = list(model.booster_.feature_name())
    if len(names) != len(features):
        raise ValueError("feature name mismatch between model and specification")
    total_gain = float(np.sum(gain)) or 1.0
    frame = pd.DataFrame(
        {
            "feature": list(features),
            "label": [FEATURE_LABELS.get(name, name) for name in features],
            "gain": gain.astype(float),
            "gain_share": gain.astype(float) / total_gain,
            "splits": splits.astype(int),
        }
    )
    return frame.sort_values("gain", ascending=False).reset_index(drop=True)


def build_credit_bundle(seed: int = DEFAULT_SEED, n_records: int = N_RECORDS) -> dict[str, object]:
    """Train both models and compute every held-out number the scene reports."""
    data = generate_credit_data(n_records, seed)
    train, holdout = split_train_holdout(data, seed=seed)
    model = fit_lightgbm(train, seed=seed)
    # The baseline's scaler is fitted on the training split only: no holdout leakage.
    scaler = StandardScaler().fit(train[list(FEATURES)])
    baseline = LogisticRegression(max_iter=1000, random_state=seed).fit(
        scaler.transform(train[list(FEATURES)]), train[TARGET]
    )
    lightgbm_metrics = evaluate_classifier(
        model.predict_proba(holdout[list(FEATURES)])[:, 1],
        holdout[TARGET],
        threshold=POLICY_THRESHOLD,
    )
    baseline_metrics = evaluate_classifier(
        baseline.predict_proba(scaler.transform(holdout[list(FEATURES)]))[:, 1],
        holdout[TARGET],
        threshold=POLICY_THRESHOLD,
    )
    return {
        "seed": int(seed),
        "n_records": int(n_records),
        "estimators": N_ESTIMATORS,
        "data": data,
        "train": train,
        "holdout": holdout,
        "model": model,
        "baseline": baseline,
        "scaler": scaler,
        "lightgbm": lightgbm_metrics,
        "baseline_metrics": baseline_metrics,
        "importance": feature_importance(model),
        "threshold": POLICY_THRESHOLD,
        "train_auc": float(
            roc_auc_score(train[TARGET], model.predict_proba(train[list(FEATURES)])[:, 1])
        ),
        "default_rate": float(data[TARGET].mean()),
        "train_default_rate": float(train[TARGET].mean()),
        "holdout_default_rate": float(holdout[TARGET].mean()),
        "holdout_probabilities": lightgbm_metrics["probabilities"],
    }


# --------------------------------------------------------------------------- #
# Applicant scoring (demo only)
# --------------------------------------------------------------------------- #
def default_applicant() -> dict[str, float]:
    """A fresh, fictional applicant used as the form default."""
    return {
        "annual_income_million": 4.5,
        "debt_to_income": 0.32,
        "credit_utilization": 0.45,
        "delinquencies_24m": 1.0,
        "tenure_years": 5.0,
    }


def validate_applicant(applicant: Mapping[str, float]) -> list[str]:
    """Return input problems for one hypothetical applicant."""
    missing = [name for name in FEATURES if name not in applicant]
    if missing:
        return ["未入力の項目があります / Missing fields: " + ", ".join(missing)]
    errors: list[str] = []
    for name in FEATURES:
        try:
            value = float(applicant[name])
        except (TypeError, ValueError):
            errors.append(f"{FEATURE_LABELS[name]} は数値で入力してください / must be numeric.")
            continue
        if not np.isfinite(value):
            errors.append(f"{FEATURE_LABELS[name]} に欠損値があります / must be finite.")
            continue
        low, high = FEATURE_DOMAINS[name]
        if not low <= value <= high:
            errors.append(
                f"{FEATURE_LABELS[name]} は {low:g}〜{high:g} の範囲で入力してください / "
                f"must be between {low:g} and {high:g}."
            )
    return errors


def policy_band(probability: float) -> dict[str, str]:
    """Map a demo probability to an illustrative policy band (not a credit decision)."""
    value = float(np.clip(float(probability), 0.0, 1.0))
    for upper, code, label in POLICY_BANDS:
        if value < upper:
            return {"code": code, "band": label}
    return {"code": "D", "band": "D · 非常に高い / Very high"}


def score_applicant(
    bundle: Mapping[str, object], applicant: Mapping[str, float]
) -> dict[str, object]:
    """Score one hypothetical applicant with the trained model (single-row inference)."""
    errors = validate_applicant(applicant)
    if errors:
        raise ValueError("; ".join(errors))
    row = pd.DataFrame([{name: float(applicant[name]) for name in FEATURES}])
    model: LGBMClassifier = bundle["model"]  # type: ignore[assignment]
    probability = float(model.predict_proba(row[list(FEATURES)])[:, 1][0])
    holdout_probabilities = np.asarray(bundle["holdout_probabilities"], dtype=float)
    return {
        "probability": probability,
        "band": policy_band(probability),
        "percentile": float((holdout_probabilities < probability).mean() * 100.0),
        "threshold": float(bundle["threshold"]),  # type: ignore[arg-type]
        "holdout_mean": float(holdout_probabilities.mean()),
        "applicant": {name: float(applicant[name]) for name in FEATURES},
    }


@st.cache_resource(show_spinner="合成データでLightGBMを学習中 / Training LightGBM ...")
def credit_model_bundle(seed: int = DEFAULT_SEED, n_records: int = N_RECORDS) -> dict[str, object]:
    """Cached immutable training bundle (deterministic for a given seed/size)."""
    return build_credit_bundle(seed, n_records)


# --------------------------------------------------------------------------- #
# Presentation helpers
# --------------------------------------------------------------------------- #
def _light_layout(figure: go.Figure, height: int = 320) -> go.Figure:
    figure.update_layout(
        template="plotly_white",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        legend={"orientation": "h", "y": -0.25},
    )
    return figure


def _metric_table(
    lightgbm_metrics: Mapping[str, object], baseline_metrics: Mapping[str, object]
) -> pd.DataFrame:
    rows = []
    for name, key in (
        ("ROC AUC", "roc_auc"),
        ("適合率 / Precision", "precision"),
        ("再現率 / Recall", "recall"),
        ("F1", "f1"),
        ("正解率 / Accuracy", "accuracy"),
    ):
        rows.append(
            {
                "指標 / Metric": name,
                f"LightGBM（しきい値 {lightgbm_metrics['threshold']:.2f}）": (
                    f"{float(lightgbm_metrics[key]):.3f}"
                ),
                "ロジスティック回帰 / Logistic baseline": (f"{float(baseline_metrics[key]):.3f}"),
            }
        )
    return pd.DataFrame(rows)


def _confusion_table(metrics: Mapping[str, object]) -> pd.DataFrame:
    matrix = metrics["confusion"]  # type: ignore[assignment]
    return pd.DataFrame(
        {
            "実際 / Actual": ["デフォルトなし / No default", "デフォルト / Default"],
            "予測: 低リスク / Predicted low": [matrix[0][0], matrix[1][0]],
            "予測: 高リスク / Predicted high": [matrix[0][1], matrix[1][1]],
            "標本数 / Support": [metrics["support"]["non_default"], metrics["support"]["default"]],  # type: ignore[index]
        }
    )


def _applicant_table(applicant: Mapping[str, float]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "項目 / Field": FEATURE_LABELS[name],
                "入力値 / Value": f"{float(applicant[name]):g}",
                "許容範囲 / Domain": (f"{FEATURE_DOMAINS[name][0]:g}–{FEATURE_DOMAINS[name][1]:g}"),
            }
            for name in FEATURES
        ]
    )


# --------------------------------------------------------------------------- #
# Streamlit scene
# --------------------------------------------------------------------------- #
def render() -> None:
    """Render the credit default-risk scene (no arguments; the app registers the route)."""
    banner(
        "SENTINEL · 与信デフォルトリスク評価 / Credit default-risk assessment",
        "LightGBM · Synthetic data · DEMO",
    )
    st.caption(
        "合成1,500件で実際に学習し、ホールドアウト標本で評価したデモモデルです。"
        "実在の審査実績ではなく、融資の承認・謝絶は行いません。 / Really trained and "
        "held-out evaluated on synthetic records. Not real underwriting; no credit decisions."
    )
    bundle = credit_model_bundle(DEFAULT_SEED, N_RECORDS)
    metrics: dict[str, object] = bundle["lightgbm"]  # type: ignore[assignment]
    baseline_metrics: dict[str, object] = bundle["baseline_metrics"]  # type: ignore[assignment]
    importance: pd.DataFrame = bundle["importance"]  # type: ignore[assignment]
    train: pd.DataFrame = bundle["train"]  # type: ignore[assignment]
    holdout: pd.DataFrame = bundle["holdout"]  # type: ignore[assignment]

    st.markdown("### 1. ホールドアウト性能 / Held-out performance")
    headline = st.columns(5)
    headline[0].metric("ROC AUC（ホールドアウト）", f"{metrics['roc_auc']:.3f}")
    headline[1].metric(f"適合率 @ {metrics['threshold']:.2f}", f"{metrics['precision']:.3f}")
    headline[2].metric(f"再現率 @ {metrics['threshold']:.2f}", f"{metrics['recall']:.3f}")
    headline[3].metric("デフォルト率（ホールドアウト）", f"{bundle['holdout_default_rate']:.1%}")
    headline[4].metric("学習 / ホールドアウト", f"{len(train)} / {len(holdout)}")
    st.caption(
        "合成データでの性能です。実在の審査データでの性能を示すものではなく、"
        "分布が変われば再学習が必要です。 / Synthetic performance only; not evidence of real "
        "underwriting performance."
    )

    evaluation_tab, importance_tab, applicant_tab = st.tabs(
        ["モデル評価 / Evaluation", "要因の重要度 / Importance", "仮想申込 / Applicant"]
    )

    with evaluation_tab:
        table(_metric_table(metrics, baseline_metrics), label="Model comparison")
        left, right = st.columns(2)
        with left:
            figure = go.Figure()
            figure.add_trace(
                go.Scatter(
                    x=metrics["roc_curve"]["fpr"],  # type: ignore[index]
                    y=metrics["roc_curve"]["tpr"],  # type: ignore[index]
                    mode="lines",
                    name=f"LightGBM (AUC {metrics['roc_auc']:.3f})",
                    line={"color": "#1769aa"},
                )
            )
            figure.add_trace(
                go.Scatter(
                    x=[0.0, 1.0],
                    y=[0.0, 1.0],
                    mode="lines",
                    name="ランダム / Random",
                    line={"dash": "dash", "color": "#94a3b8"},
                )
            )
            figure.update_layout(
                title="ROC曲線（ホールドアウト）/ ROC curve",
                xaxis_title="偽陽性率 / FPR",
                yaxis_title="真陽性率 / TPR",
            )
            st.plotly_chart(_light_layout(figure), width="stretch")
        with right:
            probabilities = np.asarray(metrics["probabilities"], dtype=float)
            labels = np.asarray(metrics["labels"], dtype=int)
            figure = go.Figure()
            figure.add_trace(
                go.Histogram(
                    x=probabilities[labels == 0],
                    name="デフォルトなし / No default",
                    opacity=0.7,
                    marker={"color": "#1769aa"},
                )
            )
            figure.add_trace(
                go.Histogram(
                    x=probabilities[labels == 1],
                    name="デフォルト / Default",
                    opacity=0.7,
                    marker={"color": "#b91c1c"},
                )
            )
            figure.add_vline(x=metrics["threshold"], line_dash="dash", line_color="#334155")
            figure.update_layout(
                title="予測確率の分布 / Predicted probabilities",
                xaxis_title="推定デフォルト確率 / Estimated probability",
                yaxis_title="件数 / Records",
                barmode="overlay",
            )
            st.plotly_chart(_light_layout(figure), width="stretch")
        table(_confusion_table(metrics), label="Confusion matrix")
        st.caption(
            "混同行列と適合率・再現率は上記しきい値での値です。しきい値はデモ用に固定しており、"
            "業務の与信方針ではありません。 / The threshold is a fixed demo value, not a policy."
        )

    with importance_tab:
        figure = go.Figure(
            go.Bar(
                x=importance["gain_share"] * 100.0,
                y=importance["label"],
                orientation="h",
                marker={"color": "#1769aa"},
            )
        )
        figure.update_layout(
            title="ゲイン重要度（モデル全体）/ Global gain importance (%)",
            xaxis_title="ゲイン寄与 / Gain share (%)",
            yaxis_title="",
        )
        st.plotly_chart(_light_layout(figure, 300), width="stretch")
        table(
            importance.rename(
                columns={
                    "label": "特徴量 / Feature",
                    "gain": "ゲイン / Gain",
                    "gain_share": "ゲイン比率 / Gain share",
                    "splits": "分割回数 / Splits",
                }
            )[
                [
                    "特徴量 / Feature",
                    "ゲイン / Gain",
                    "ゲイン比率 / Gain share",
                    "分割回数 / Splits",
                ]
            ],
            label="Feature importance",
        )
        st.caption(
            "重要度はモデル全体の統計量です。因果関係や個人の理由ではなく、"
            "個別説明（SHAP等）は提供していません。 / Global model-level importance: not causal "
            "and not a per-applicant explanation; no individual SHAP values."
        )

    with applicant_tab:
        st.markdown("##### 仮想申込者の入力 / Hypothetical applicant")
        st.caption(
            "架空の数値を入力してモデル出力を確認できます。実際の申込情報は"
            "入力しないでください。 / Fictional values only; do not enter real data."
        )
        input_columns = st.columns(3)
        applicant: dict[str, float] = {}
        defaults = default_applicant()
        for position, name in enumerate(FEATURES):
            low, high = FEATURE_DOMAINS[name]
            with input_columns[position % 3]:
                applicant[name] = st.number_input(
                    FEATURE_LABELS[name],
                    min_value=float(low),
                    max_value=float(high),
                    value=float(defaults[name]),
                    step=0.05 if high <= 1.0 else 0.5,
                    key=f"credit_applicant_{name}",
                )
        input_errors = validate_applicant(applicant)
        if input_errors:
            for message in input_errors:
                st.error(message)
        else:
            result = score_applicant(bundle, applicant)
            band: dict[str, str] = result["band"]  # type: ignore[assignment]
            left, right = st.columns([1, 1.6])
            with left:
                st.metric(
                    "推定デフォルト確率 / Estimated default probability",
                    f"{result['probability']:.1%}",
                )
                st.markdown(
                    f'<div class="bank-notice"><strong>{band["band"]}</strong><br>'
                    f"デモ方針バンド（しきい値 {result['threshold']:.2f}）/ "
                    "Illustrative demo band</div>",
                    unsafe_allow_html=True,
                )
            with right:
                table(_applicant_table(applicant), label="Applicant inputs")
                st.caption(
                    f"ホールドアウト標本の {result['percentile']:.0f}% より高い確率です"
                    f"（ホールドアウト平均 {result['holdout_mean']:.1%}）。 / Higher than "
                    f"{result['percentile']:.0f}% of held-out records."
                )
            st.caption(
                "これは架空の申込に対するデモ出力です。実際の与信判断・融資承認ではありません。 / "
                "Demo output for a fictional applicant; not a credit decision or approval."
            )
            review_columns = st.columns(len(DEMO_REVIEW_OPTIONS))
            for column, (code, label) in zip(review_columns, DEMO_REVIEW_OPTIONS, strict=True):
                with column:
                    if st.button(label, key=f"credit_review_{code}", width="stretch"):
                        st.session_state["credit_review"] = code
                        st.rerun()
            recorded = st.session_state.get("credit_review")
            if recorded:
                label = dict(DEMO_REVIEW_OPTIONS).get(recorded, recorded)
                st.info(
                    f"デモ記録: {label}。この端末のセッション内だけの記録で、"
                    "実際の審査結果や申込状態は変更しません。 / Demo record only: "
                    "nothing outside this browser session changes."
                )

    with st.expander("合成データとラベルの作り方 / Data generation & labels"):
        st.write(
            f"{N_RECORDS}件を固定シード {DEFAULT_SEED} で生成。既定確率は `logit = -4.10 + "
            "2.00×利用率 + 0.45×延滞 + 2.00×返済負担率 − 0.50×(log10(年収) − 6.3) − "
            "0.040×取引年数 + 2.60×利用率×min(延滞,3) + 4.00×max(返済負担率 − 0.45, 0) + "
            "0.70×ノイズ` のロジスティック関数です。交互作用としきい値効果を含み、"
            "ノイズがあるため完全には予測できません（この模擬の理論上限AUCは約0.86）。 / "
            "Logistic propensity with an interaction and a debt-to-income kink plus seeded "
            "noise, so labels are reproducible but not perfectly predictable."
        )
        table(
            pd.DataFrame(
                [
                    {
                        "特徴量 / Feature": FEATURE_LABELS[name],
                        "範囲 / Range": (
                            f"{FEATURE_DOMAINS[name][0]:g}–{FEATURE_DOMAINS[name][1]:g}"
                        ),
                        "生成方法 / Generator": generator,
                    }
                    for name, generator in zip(
                        FEATURES,
                        (
                            "log-normal（1〜40百万円）",
                            "beta(2.2, 4.0) × 0.9",
                            "beta(2.0, 2.5)",
                            "Poisson(0.35)、0〜6に制限",
                            "gamma(2.0, 3.0)、0〜30年に制限",
                        ),
                        strict=True,
                    )
                ]
            ),
            label="Feature generators",
        )
        st.caption(NO_PROTECTED_ATTRIBUTES)

    with st.expander("学習・評価の手順 / Training & evaluation protocol"):
        st.write(
            f"・層化分割（デフォルト率を保った {HOLDOUT_FRACTION:.0%} ホールドアウト、"
            f"シード {DEFAULT_SEED}）。学習とホールドアウトの行インデックスは重複しません。  \n"
            f"・LightGBM: {N_ESTIMATORS}本、learning_rate 0.05、num_leaves 4、"
            "`min_child_samples=30`、`reg_lambda=5.0`、`n_jobs=1`・`deterministic=True`"
            "（同一シードで同一結果）。学習1,000行では深い木がノイズを丸暗記するため、"
            "浅く正則化した構成にしています。  \n"
            "・比較用のロジスティック回帰は標準化を含め学習データのみでfitしています"
            "（ホールドアウトは変換のみ）。  \n"
            "・指標はすべてホールドアウト側で算出しています。 / All metrics are held-out."
        )
        st.caption(
            f"行数: 学習 {len(train)} / ホールドアウト {len(holdout)}、デフォルト率 "
            f"{bundle['train_default_rate']:.1%} / {bundle['holdout_default_rate']:.1%}。"
            f"LightGBMのAUCは学習 {bundle['train_auc']:.3f} / ホールドアウト "
            f"{metrics['roc_auc']:.3f}（学習を丸暗記していないことを確認）。"
            f" ロジスティック回帰 {baseline_metrics['roc_auc']:.3f} とほぼ同等で、"
            "1,500件・単調なリスク関数では線形スコアカードも競争力があります。 / "
            "Train vs holdout AUC shows no memorisation; the linear scorecard is competitive "
            "at this sample size."
        )

    with st.expander("限界と注意 / Limitations"):
        st.write(
            "・合成データの性能は実在の審査性能を意味しません。 / Synthetic performance "
            "does not transfer to real underwriting.  \n"
            "・確率の較正（calibration）、分布シフト、時系列ドリフトは検証していません。 / "
            "Calibration, distribution shift and drift are not evaluated.  \n"
            "・しきい値と方針バンドは説明用の固定値です。 / Thresholds and bands are "
            "illustrative constants.  \n"
            "・重要度はモデル全体の指標で、因果や個人の理由ではありません。 / Importance is "
            "global and non-causal.  \n"
            "・この画面は融資の承認・謝絶を行う権限を持ちません。 / This screen has no "
            "lending authority."
        )
    demo_note()
    reset_button(
        "credit",
        ["credit_review", *[f"credit_applicant_{name}" for name in FEATURES]],
    )
