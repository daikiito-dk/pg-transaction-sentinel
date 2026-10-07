"""Investment portfolio health scoring on synthetic, correlated market data.

This scene performs real computation on *generated* prices: pandas cleaning without
look-ahead, NumPy return/covariance statistics, historical VaR, max drawdown,
concentration, a transparent 0-100 health score, and a scikit-learn KMeans grouping
of sampled portfolios whose labels are derived from cluster metrics (never from the
raw cluster IDs).

Everything is synthetic and reproducible from :data:`DEFAULT_SEED`; no market data is
downloaded and nothing here is investment advice.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from demos.ui import banner, demo_note, reset_button, table

TRADING_DAYS = 252
DEFAULT_SEED = 20261007
PRICE_START = 100.0
PRICE_END = pd.Timestamp("2025-12-31")
FFILL_LIMIT = 5
DIRTY_HOLES_PER_ASSET = 4
DUPLICATE_ROWS = 4
SAMPLE_PORTFOLIOS = 200
CLUSTERS = 3
NOTIONAL_JPY = 10_000_000.0
RISK_FREE_RATE = 0.005

ASSETS = (
    "Domestic Bond Fund / 国内債券",
    "Global Bond Fund / 海外債券",
    "Domestic Equity Fund / 国内株式",
    "Global Equity Fund / 海外株式",
    "REIT Fund / 不動産投信",
)
ASSET_SHORT = ("Bond JP", "Bond Global", "Equity JP", "Equity Global", "REIT")
# Annualised synthetic drift/volatility per asset (illustrative parameters, not market data).
ASSET_DRIFT = np.array([0.004, 0.012, 0.045, 0.055, 0.040])
ASSET_VOLATILITY = np.array([0.025, 0.045, 0.160, 0.200, 0.190])
CORRELATION = np.array(
    [
        [1.00, 0.62, -0.05, -0.10, 0.10],
        [0.62, 1.00, 0.12, 0.18, 0.22],
        [-0.05, 0.12, 1.00, 0.72, 0.55],
        [-0.10, 0.18, 0.72, 1.00, 0.60],
        [0.10, 0.22, 0.55, 0.60, 1.00],
    ]
)

SCORE_WEIGHTS = {
    "volatility": 0.35,
    "drawdown": 0.25,
    "concentration": 0.20,
    "risk_adjusted_return": 0.20,
}
VOLATILITY_BAND = (0.03, 0.30)
DRAWDOWN_BAND = (0.0, 0.35)
EFFECTIVE_HOLDINGS_BAND = (5.0, 1.0)
RISK_ADJUSTED_BAND = (1.0, -0.5)
SCORE_BANDS = (
    (80.0, "resilient", "Resilient", "堅牢"),
    (65.0, "balanced", "Balanced", "均衡"),
    (45.0, "watch", "Watch", "要注意"),
    (0.0, "fragile", "Fragile", "脆弱"),
)
RANK_LABELS = (
    "Conservative / 低リスク",
    "Balanced / 中庸",
    "Aggressive / 高リスク",
    "Speculative / 投機的",
)
CLUSTER_FEATURES = (
    "annual_volatility",
    "annual_return",
    "drawdown_magnitude",
    "effective_holdings",
)


# --------------------------------------------------------------------------- #
# Synthetic market data (with realistic feed defects)
# --------------------------------------------------------------------------- #
def correlation_matrix(n_assets: int) -> np.ndarray:
    """Return the positive-definite synthetic correlation block for ``n_assets``."""
    if not 1 <= n_assets <= len(ASSETS):
        raise ValueError(f"n_assets must be between 1 and {len(ASSETS)}")
    return CORRELATION[:n_assets, :n_assets]


def _dirty_feed(frame: pd.DataFrame, rng: np.random.Generator, seed: int) -> pd.DataFrame:
    """Add the defects a real price feed shows: missing days and duplicated rows."""
    dirty = frame.copy()
    row_count = int(dirty.shape[0])
    if row_count > 2 * DIRTY_HOLES_PER_ASSET:
        for column in dirty.columns:
            holes = (
                rng.choice(
                    row_count - 2 * DIRTY_HOLES_PER_ASSET,
                    size=DIRTY_HOLES_PER_ASSET,
                    replace=False,
                )
                + DIRTY_HOLES_PER_ASSET
            )
            dirty.iloc[holes, dirty.columns.get_loc(column)] = np.nan
    duplicate_at = sorted(
        {
            max(1, row_count // 20),
            max(2, row_count // 5),
            max(3, row_count // 3),
            min(row_count - 1, row_count // 2),
        }
    )
    duplicated = dirty.iloc[[row for row in duplicate_at if 0 <= row < row_count]]
    if duplicated.empty:
        return dirty
    combined = pd.concat([dirty, duplicated.iloc[:DUPLICATE_ROWS]])
    return combined.sample(frac=1.0, random_state=seed)


def simulate_prices(
    seed: int = DEFAULT_SEED,
    *,
    days: int = TRADING_DAYS,
    assets: Sequence[str] = ASSETS,
) -> pd.DataFrame:
    """Simulate correlated daily prices, then inject duplicated rows and gaps."""
    asset_count = len(assets)
    if days < 3:
        raise ValueError("days must be at least 3")
    if asset_count == 0:
        raise ValueError("at least one asset is required")
    correlation = correlation_matrix(asset_count)
    rng = np.random.default_rng(seed)
    shocks = rng.standard_normal((days, asset_count)) @ np.linalg.cholesky(correlation).T
    daily_drift = ASSET_DRIFT[:asset_count] / TRADING_DAYS
    daily_volatility = ASSET_VOLATILITY[:asset_count] / np.sqrt(TRADING_DAYS)
    log_returns = daily_drift + daily_volatility * shocks
    prices = PRICE_START * np.exp(np.cumsum(log_returns, axis=0))
    index = pd.bdate_range(end=PRICE_END, periods=days, name="date")
    clean = pd.DataFrame(prices.round(4), index=index, columns=list(assets))
    return _dirty_feed(clean, rng, seed)


def validate_prices(prices: pd.DataFrame) -> list[str]:
    """Return blocking problems with a raw price frame (missing values are not errors)."""
    errors: list[str] = []
    if prices is None or prices.shape[0] == 0 or prices.shape[1] == 0:
        return ["価格データが空です / Price data is empty."]
    non_numeric = [
        str(column)
        for column in prices.columns
        if not pd.api.types.is_numeric_dtype(prices[column])
    ]
    if non_numeric:
        return ["価格が数値ではありません / Non-numeric price column(s): " + ", ".join(non_numeric)]
    values = prices.to_numpy(dtype=float)
    if np.isinf(values).any():
        errors.append("無限大の価格があります / Infinite price values present.")
    finite = values[np.isfinite(values)]
    if finite.size and float(finite.min()) <= 0.0:
        errors.append("0以下の価格があります / Non-positive price values present.")
    return errors


def clean_prices(
    raw: pd.DataFrame, *, ffill_limit: int = FFILL_LIMIT
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Sort, de-duplicate and forward-fill a price feed using past observations only.

    The fill is bounded (``ffill_limit``) and never backward-fills, so no future price
    can leak into an earlier date. Rows that cannot be completed from past data are
    dropped and counted in the returned report.
    """
    if ffill_limit < 1:
        raise ValueError("ffill_limit must be at least 1")
    errors = validate_prices(raw)
    if errors:
        raise ValueError("; ".join(errors))
    rows_received = int(raw.shape[0])
    missing_values_received = int(raw.isna().sum().sum())
    ordered = raw.sort_index(kind="stable")
    deduplicated = ordered[~ordered.index.duplicated(keep="first")]
    dropped_columns = [
        str(column) for column in deduplicated.columns if deduplicated[column].isna().all()
    ]
    if dropped_columns:
        deduplicated = deduplicated.drop(columns=dropped_columns)
    if deduplicated.shape[1] == 0:
        raise ValueError(
            "使用可能な価格列がありません / No usable price column remains after cleaning."
        )
    filled = deduplicated.ffill(limit=ffill_limit)
    unfilled = int(filled.isna().sum().sum())
    cleaned = filled.dropna()
    if cleaned.shape[0] == 0:
        raise ValueError("クリーンな価格が残りません / No usable price rows remain after cleaning.")
    first_complete = cleaned.index.min()
    leading_rows = int((filled.index < first_complete).sum())
    rows_dropped_missing = int(filled.shape[0] - cleaned.shape[0])
    report: dict[str, object] = {
        "rows_received": rows_received,
        "duplicate_rows_dropped": rows_received - int(deduplicated.shape[0]),
        "missing_values_received": missing_values_received,
        "missing_values_filled": missing_values_received - unfilled,
        "missing_values_unfilled": unfilled,
        "rows_dropped_missing": rows_dropped_missing,
        "leading_rows_dropped": leading_rows,
        "interior_rows_dropped": rows_dropped_missing - leading_rows,
        "columns_dropped_missing": dropped_columns,
        "rows_clean": int(cleaned.shape[0]),
        "ffill_limit": int(ffill_limit),
        "missing_values_remaining": int(cleaned.isna().sum().sum()),
        "unsorted_input": not raw.index.is_monotonic_increasing,
    }
    return cleaned, report


def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Simple daily returns per asset, with non-finite observations removed."""
    if prices.shape[0] < 2:
        raise ValueError("価格が2点未満です / Need at least two price observations.")
    returns = prices.pct_change().replace([np.inf, -np.inf], np.nan).dropna(how="any")
    if returns.empty:
        raise ValueError("リターンを算出できません / No usable return observations.")
    return returns


# --------------------------------------------------------------------------- #
# Risk metrics
# --------------------------------------------------------------------------- #
def _series(values: pd.Series | Sequence[float]) -> pd.Series:
    series = pd.Series(values, dtype=float).dropna()
    if series.empty:
        raise ValueError("リターン系列が空です / Return series is empty.")
    return series


def annualized_volatility(
    returns: pd.Series | Sequence[float], *, periods: int = TRADING_DAYS
) -> float:
    """Sample standard deviation of daily returns scaled by ``sqrt(periods)``."""
    series = _series(returns)
    if series.size < 2:
        raise ValueError("ボラティリティには2点以上必要です / Need two observations.")
    return float(series.std(ddof=1) * np.sqrt(periods))


def annualized_return(
    returns: pd.Series | Sequence[float], *, periods: int = TRADING_DAYS
) -> float:
    """Arithmetic annualisation of the mean daily return (documented in MODELS.md)."""
    return float(_series(returns).mean() * periods)


def historical_var(
    returns: pd.Series | Sequence[float],
    *,
    confidence: float = 0.95,
    notional: float = NOTIONAL_JPY,
) -> float:
    """Historical VaR: the loss at the ``1 - confidence`` return quantile."""
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be strictly between 0 and 1")
    if notional <= 0:
        raise ValueError("notional must be positive")
    quantile = float(_series(returns).quantile(1.0 - confidence))
    return float(max(-quantile, 0.0) * notional)


def max_drawdown(returns: pd.Series | Sequence[float]) -> float:
    """Worst peak-to-trough decline of the compounded growth path (<= 0)."""
    series = _series(returns)
    growth = (1.0 + series).cumprod()
    peak = growth.cummax()
    return float((growth / peak - 1.0).min())


def concentration(
    weights: Sequence[float], *, assets: Sequence[str] | None = None
) -> dict[str, object]:
    """Herfindahl-Hirschman index, effective number of holdings and top weight."""
    vector = normalize_weights(weights)
    hhi = float(np.square(vector).sum())
    top = int(np.argmax(vector))
    top_asset = (
        str(assets[top])
        if assets is not None and len(assets) == vector.size
        else f"Asset {top + 1}"
    )
    return {
        "hhi": hhi,
        "effective_holdings": float(1.0 / hhi),
        "top_weight": float(vector[top]),
        "top_asset": top_asset,
    }


def portfolio_returns(returns: pd.DataFrame, weights: Sequence[float]) -> pd.Series:
    """Weighted daily portfolio return series."""
    vector = normalize_weights(weights, expected=returns.shape[1])
    values = returns.to_numpy(dtype=float) @ vector
    return pd.Series(values, index=returns.index, name="portfolio")


def portfolio_metrics(
    returns: pd.DataFrame,
    weights: Sequence[float],
    *,
    assets: Sequence[str] | None = None,
    periods: int = TRADING_DAYS,
    notional: float = NOTIONAL_JPY,
    risk_free_rate: float = RISK_FREE_RATE,
) -> dict[str, float | str]:
    """Compute every risk statistic the scene displays for one weight vector."""
    vector = normalize_weights(weights, expected=returns.shape[1])
    series = portfolio_returns(returns, vector)
    variance_series = annualized_volatility(series, periods=periods)
    annual_return = annualized_return(series, periods=periods)
    concentration_metrics = concentration(vector, assets=assets)
    risk_adjusted = (annual_return - risk_free_rate) / variance_series if variance_series else 0.0
    return {
        "observations": float(series.size),
        "annual_return": annual_return,
        "annual_volatility": variance_series,
        "var_95_amount": historical_var(series, confidence=0.95, notional=notional),
        "var_99_amount": historical_var(series, confidence=0.99, notional=notional),
        "var_95_ratio": historical_var(series, confidence=0.95, notional=1.0),
        "max_drawdown": max_drawdown(series),
        "risk_adjusted_return": float(risk_adjusted),
        "worst_day": float(series.min()),
        "cumulative_growth": float((1.0 + series).prod()),
        "notional": float(notional),
        **concentration_metrics,
    }


# --------------------------------------------------------------------------- #
# Transparent 0-100 health score
# --------------------------------------------------------------------------- #
def _scale(value: float, *, best: float, worst: float) -> float:
    """Linear 0-100 scale where ``best`` maps to 100 and ``worst`` maps to 0."""
    if best == worst:
        return 0.0
    ratio = (float(value) - worst) / (best - worst)
    return round(100.0 * float(np.clip(ratio, 0.0, 1.0)), 2)


def score_band(score: float) -> dict[str, str]:
    """Map a 0-100 score to a labelled band."""
    value = float(np.clip(float(score), 0.0, 100.0))
    for floor, code, english, japanese in SCORE_BANDS:
        if value >= floor:
            return {"code": code, "en": english, "jp": japanese}
    return {"code": "fragile", "en": "Fragile", "jp": "脆弱"}


def health_score(metrics: Mapping[str, float]) -> dict[str, object]:
    """Score a portfolio 0-100 with the documented, inspectable component formula."""
    required = ("annual_volatility", "max_drawdown", "effective_holdings", "risk_adjusted_return")
    missing = [name for name in required if name not in metrics]
    if missing:
        raise ValueError("metrics missing: " + ", ".join(missing))
    components = {
        "volatility": {
            "label": "年率ボラティリティ / Annual volatility",
            "value": float(metrics["annual_volatility"]),
            "score": _scale(
                float(metrics["annual_volatility"]),
                best=VOLATILITY_BAND[0],
                worst=VOLATILITY_BAND[1],
            ),
            "weight": SCORE_WEIGHTS["volatility"],
        },
        "drawdown": {
            "label": "最大下落率 / Max drawdown",
            "value": float(metrics["max_drawdown"]),
            "score": _scale(
                abs(float(metrics["max_drawdown"])),
                best=DRAWDOWN_BAND[0],
                worst=DRAWDOWN_BAND[1],
            ),
            "weight": SCORE_WEIGHTS["drawdown"],
        },
        "concentration": {
            "label": "集中度 / Concentration",
            "value": float(metrics["effective_holdings"]),
            "score": _scale(
                float(metrics["effective_holdings"]),
                best=EFFECTIVE_HOLDINGS_BAND[0],
                worst=EFFECTIVE_HOLDINGS_BAND[1],
            ),
            "weight": SCORE_WEIGHTS["concentration"],
        },
        "risk_adjusted_return": {
            "label": "リスク調整後リターン / Excess return per unit risk",
            "value": float(metrics["risk_adjusted_return"]),
            "score": _scale(
                float(metrics["risk_adjusted_return"]),
                best=RISK_ADJUSTED_BAND[0],
                worst=RISK_ADJUSTED_BAND[1],
            ),
            "weight": SCORE_WEIGHTS["risk_adjusted_return"],
        },
    }
    for component in components.values():
        component["contribution"] = round(component["score"] * component["weight"], 2)
    total = round(sum(component["contribution"] for component in components.values()), 1)
    return {"score": total, "band": score_band(total), "components": components}


# --------------------------------------------------------------------------- #
# Weight input guards
# --------------------------------------------------------------------------- #
def validate_weights(
    weights: Sequence[float],
    *,
    expected: int = len(ASSETS),
    tolerance: float | None = 0.5,
) -> list[str]:
    """Report invalid user weights. Pass ``tolerance=None`` to skip the 100% rule."""
    try:
        values = np.asarray(list(weights), dtype=float)
    except (TypeError, ValueError):
        return ["配分は数値で入力してください / Weights must be numeric."]
    if values.ndim != 1 or values.size == 0:
        return ["配分が空です / Weights must be a non-empty list."]
    if values.size != expected:
        return [f"配分は{expected}件必要です / Expected {expected} weights, got {values.size}."]
    errors: list[str] = []
    if not np.isfinite(values).all():
        errors.append("配分に欠損値があります / Weights contain NaN or infinity.")
    if (values < 0).any():
        errors.append("配分は0以上にしてください / Weights must not be negative.")
    if float(values.sum()) <= 0.0:
        errors.append("配分の合計が0です / Weights must not sum to zero.")
    elif tolerance is not None and abs(float(values.sum()) - 100.0) > tolerance:
        errors.append(
            f"配分の合計を100%にしてください（現在 {float(values.sum()):.1f}%）/ "
            "Weights must sum to 100%."
        )
    return errors


def normalize_weights(weights: Sequence[float], *, expected: int | None = None) -> np.ndarray:
    """Validate weights and scale them to a positive vector that sums to one."""
    try:
        values = np.asarray(list(weights), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("配分は数値で入力してください / Weights must be numeric.") from exc
    if values.ndim != 1 or values.size == 0:
        raise ValueError("配分が空です / Weights must be a non-empty sequence.")
    if expected is not None and values.size != expected:
        raise ValueError(
            f"配分は{expected}件必要です / Expected {expected} weights, got {values.size}."
        )
    if not np.isfinite(values).all():
        raise ValueError("配分に欠損値があります / Weights must be finite.")
    if (values < 0.0).any():
        raise ValueError("配分は0以上にしてください / Weights must not be negative.")
    total = float(values.sum())
    if total <= 0.0:
        raise ValueError("配分の合計が0です / Weights must not sum to zero.")
    return values / total


# --------------------------------------------------------------------------- #
# Peer grouping with KMeans
# --------------------------------------------------------------------------- #
def sample_weight_vectors(n_samples: int, *, n_assets: int, seed: int) -> np.ndarray:
    """Dirichlet(1) draws: a uniform sample of long-only weight vectors on the simplex."""
    if n_samples < 1:
        raise ValueError("n_samples must be at least 1")
    if n_assets < 1:
        raise ValueError("n_assets must be at least 1")
    rng = np.random.default_rng(seed)
    return rng.dirichlet(np.ones(n_assets), size=n_samples)


def cluster_sample_portfolios(
    returns: pd.DataFrame,
    current_weights: Sequence[float],
    *,
    assets: Sequence[str] | None = None,
    n_samples: int = SAMPLE_PORTFOLIOS,
    n_clusters: int = CLUSTERS,
    seed: int = DEFAULT_SEED,
) -> dict[str, object]:
    """KMeans over sampled portfolios; risk labels come from cluster metrics, not IDs."""
    if not 2 <= n_clusters <= len(RANK_LABELS):
        raise ValueError(f"n_clusters must be between 2 and {len(RANK_LABELS)}")
    asset_count = returns.shape[1]
    current = normalize_weights(current_weights, expected=asset_count)
    vectors = np.vstack(
        [current, sample_weight_vectors(n_samples, n_assets=asset_count, seed=seed)]
    )
    rows = []
    for position, vector in enumerate(vectors):
        metrics = portfolio_metrics(returns, vector, assets=assets)
        rows.append(
            {
                "annual_volatility": float(metrics["annual_volatility"]),
                "annual_return": float(metrics["annual_return"]),
                "drawdown_magnitude": abs(float(metrics["max_drawdown"])),
                "effective_holdings": float(metrics["effective_holdings"]),
                "is_current": position == 0,
            }
        )
    frame = pd.DataFrame(rows)
    scaled = StandardScaler().fit_transform(frame[list(CLUSTER_FEATURES)].to_numpy(dtype=float))
    model = KMeans(n_clusters=n_clusters, n_init=10, random_state=seed, max_iter=300)
    labels = model.fit_predict(scaled)
    frame["cluster"] = labels
    # Rank clusters by their measured volatility + drawdown profile, then attach labels.
    risk_profile = pd.Series(scaled[:, 0] + scaled[:, 2], index=frame.index, name="risk_profile")
    ranking = risk_profile.groupby(frame["cluster"]).mean().sort_values()
    label_by_cluster = {
        int(cluster): RANK_LABELS[rank] for rank, cluster in enumerate(ranking.index)
    }
    frame["risk_label"] = frame["cluster"].map(label_by_cluster)
    label_order = [label_by_cluster[int(cluster)] for cluster in ranking.index]
    summary = (
        frame.groupby("risk_label")
        .agg(
            portfolios=("risk_label", "size"),
            mean_annual_volatility=("annual_volatility", "mean"),
            mean_annual_return=("annual_return", "mean"),
            mean_drawdown_magnitude=("drawdown_magnitude", "mean"),
            mean_effective_holdings=("effective_holdings", "mean"),
        )
        .reindex(label_order)
        .reset_index()
    )
    current_row = frame[frame["is_current"]].iloc[0]
    return {
        "sample": frame,
        "summary": summary,
        "labels": label_order,
        "silhouette": float(silhouette_score(scaled, labels)) if n_clusters < len(frame) else 0.0,
        "current_label": str(current_row["risk_label"]),
        "current_volatility_percentile": float(
            (
                frame.loc[~frame["is_current"], "annual_volatility"]
                < current_row["annual_volatility"]
            ).mean()
            * 100.0
        ),
        "inertia": float(model.inertia_),
    }


# --------------------------------------------------------------------------- #
# Cached deterministic artifacts
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def market_data(seed: int = DEFAULT_SEED) -> dict[str, object]:
    """Cached deterministic market artifacts: raw feed, cleaned prices, returns, report."""
    raw = simulate_prices(seed)
    prices, report = clean_prices(raw)
    return {
        "prices": prices,
        "returns": daily_returns(prices),
        "report": report,
        "raw_rows": int(raw.shape[0]),
    }


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


def _component_table(score: dict[str, object]) -> pd.DataFrame:
    components: dict[str, dict[str, float]] = score["components"]  # type: ignore[assignment]
    return pd.DataFrame(
        [
            {
                "項目 / Component": component["label"],
                "値 / Value": component["value"],
                "スコア / Score (0-100)": component["score"],
                "重み / Weight": component["weight"],
                "寄与 / Contribution": component["contribution"],
            }
            for component in components.values()
        ]
    )


def _cleaning_table(report: Mapping[str, object], raw_rows: int) -> pd.DataFrame:
    labels = {
        "rows_received": "受信行数 / Rows received",
        "duplicate_rows_dropped": "重複行の削除 / Duplicate rows dropped",
        "missing_values_received": "受信時の欠損値 / Missing values received",
        "missing_values_filled": "過去値で補完 / Filled from past prices only",
        "missing_values_unfilled": "補完できず / Not fillable within the bound",
        "leading_rows_dropped": "先頭（起算前）の除外 / Warm-up rows dropped",
        "interior_rows_dropped": "途中欠損による除外 / Interior rows dropped",
        "rows_clean": "クリーン後の行数 / Rows after cleaning",
        "ffill_limit": "前方補完の上限 / Bounded ffill limit",
        "missing_values_remaining": "残存欠損値 / Missing values remaining",
    }
    rows = [{"検査項目 / Check": labels[key], "結果 / Result": report[key]} for key in labels]
    rows.insert(0, {"検査項目 / Check": "生データ行数 / Raw feed rows", "結果": raw_rows})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Streamlit scene
# --------------------------------------------------------------------------- #
def render() -> None:
    """Render the portfolio health scene (no arguments; the app registers the route)."""
    banner(
        "SENTINEL · ポートフォリオ健全性診断 / Portfolio health",
        "Synthetic prices · DEMO",
    )
    st.caption(
        "5資産・252営業日の合成価格（固定シード）を実際に計算しています。"
        "市場データの取得や投資助言は行いません。 / Real maths on simulated prices, "
        "fixed seed. No market data download, no investment advice."
    )
    data = market_data(DEFAULT_SEED)
    returns: pd.DataFrame = data["returns"]  # type: ignore[assignment]
    report: dict[str, object] = data["report"]  # type: ignore[assignment]
    raw_rows = int(data["raw_rows"])  # type: ignore[arg-type]

    st.markdown("### 1. 資産配分 / Allocation")
    weight_columns = st.columns(len(ASSETS))
    raw_weights: list[float] = []
    for column, asset, short in zip(weight_columns, ASSETS, ASSET_SHORT, strict=True):
        with column:
            raw_weights.append(
                st.number_input(
                    short,
                    min_value=0.0,
                    max_value=100.0,
                    value=20.0,
                    step=5.0,
                    key=f"portfolio_weight_{short}",
                    help=asset,
                )
            )
    entered_total = float(sum(raw_weights))
    weight_errors = validate_weights(raw_weights, tolerance=None)
    if weight_errors:
        for message in weight_errors:
            st.error(message)
        weights = [20.0] * len(ASSETS)
        st.info(
            "入力を確認できないため均等配分 / Showing equal weights because the input is invalid."
        )
    else:
        weights = raw_weights
        if abs(entered_total - 100.0) > 0.5:
            st.info(
                f"合計 {entered_total:.0f}% → Σw = 1 に正規化します / "
                f"Entered {entered_total:.0f}%: normalised to Σw = 1."
            )

    assets_in_use = list(returns.columns)
    if len(assets_in_use) != len(ASSETS):
        st.warning(
            "欠損が続く資産を除外しました / Assets without usable prices were excluded: "
            + ", ".join(asset for asset in ASSETS if asset not in assets_in_use)
        )
    selected = [
        (weight, asset)
        for weight, asset in zip(weights, ASSETS, strict=True)
        if asset in assets_in_use
    ]
    try:
        vector = normalize_weights([weight for weight, _ in selected], expected=len(assets_in_use))
    except ValueError as exc:
        st.error(f"{exc} 均等配分で表示します / Falling back to equal weights.")
        vector = normalize_weights([1.0] * len(assets_in_use))
    assets_used = [asset for _, asset in selected]

    try:
        metrics = portfolio_metrics(returns, vector, assets=assets_used)
    except ValueError as exc:  # pragma: no cover - defensive guard
        st.error(str(exc))
        st.stop()
        return
    score = health_score(metrics)
    band: dict[str, str] = score["band"]  # type: ignore[assignment]

    st.markdown("### 2. 健全性スコア / Health score")
    score_column, detail_column = st.columns([1, 2.4])
    with score_column:
        st.metric("0-100 健全性 / Health score", f"{score['score']:.1f}")
        st.markdown(
            f'<div class="bank-notice"><strong>{band["en"]} / {band["jp"]}</strong><br>'
            "合成データに対する透明なデモ指標です / Transparent demo metric on simulated data."
            "</div>",
            unsafe_allow_html=True,
        )
    with detail_column:
        table(_component_table(score), label="Health score components")
        st.caption(
            "スコア = Σ (成分スコア × 重み)。各成分はしきい値バンド内の線形クリップです。"
            " / Score = Σ (component × weight); each component is a linear clip inside a band."
        )

    st.markdown("### 3. 主要リスク指標 / Risk metrics")
    risk_columns = st.columns(5)
    risk_columns[0].metric("年率ボラティリティ / Annual vol", f"{metrics['annual_volatility']:.2%}")
    risk_columns[1].metric("年率リターン(算術) / Annual return", f"{metrics['annual_return']:.2%}")
    risk_columns[2].metric("日次VaR 95% / VaR 95 (1d)", f"¥{metrics['var_95_amount']:,.0f}")
    risk_columns[3].metric("最大下落率 / Max drawdown", f"{metrics['max_drawdown']:.2%}")
    risk_columns[4].metric(
        "実効保有数 / Effective holdings", f"{metrics['effective_holdings']:.2f}"
    )
    st.caption(
        f"VaRは想定元本 ¥{metrics['notional']:,.0f} の1日損失（履歴シミュレーション）。"
        "上限は252営業日 / Historical 1-day VaR on the stated notional, 252 trading days."
    )

    series = portfolio_returns(returns, vector)
    growth = (1.0 + series).cumprod()
    drawdown = growth / growth.cummax() - 1.0

    price_chart, growth_chart = st.columns(2)
    with price_chart:
        figure = go.Figure()
        prices: pd.DataFrame = data["prices"]  # type: ignore[assignment]
        for column in prices.columns:
            figure.add_trace(
                go.Scatter(x=prices.index, y=prices[column], mode="lines", name=str(column))
            )
        figure.update_layout(title="合成価格 / Simulated prices (start = 100)")
        st.plotly_chart(_light_layout(figure), width="stretch")
    with growth_chart:
        figure = go.Figure()
        figure.add_trace(
            go.Scatter(
                x=growth.index,
                y=growth,
                mode="lines",
                name="累積成長 / Growth of 1.0",
                line={"color": "#1769aa"},
            )
        )
        figure.update_layout(title="ポートフォリオ累積成長 / Portfolio growth")
        st.plotly_chart(_light_layout(figure), width="stretch")
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=drawdown.index,
            y=drawdown * 100.0,
            mode="lines",
            fill="tozeroy",
            name="下落率 / Drawdown (%)",
            line={"color": "#b45309"},
        )
    )
    figure.update_layout(title="ドローダウン推移 / Drawdown path (%)")
    st.plotly_chart(_light_layout(figure, 240), width="stretch")

    st.markdown("### 4. 類似ポートフォリオの分類 / Peer grouping (KMeans)")
    grouping = cluster_sample_portfolios(returns, vector, assets=assets_used)
    summary: pd.DataFrame = grouping["summary"]  # type: ignore[assignment]
    sample: pd.DataFrame = grouping["sample"]  # type: ignore[assignment]
    figure = go.Figure()
    for label, group in sample.groupby("risk_label", sort=False):
        figure.add_trace(
            go.Scatter(
                x=group["annual_volatility"] * 100.0,
                y=group["annual_return"] * 100.0,
                mode="markers",
                name=str(label),
                marker={"size": 7, "opacity": 0.7},
            )
        )
    current_row = sample[sample["is_current"]]
    figure.add_trace(
        go.Scatter(
            x=current_row["annual_volatility"] * 100.0,
            y=current_row["annual_return"] * 100.0,
            mode="markers+text",
            name="現在の配分 / Current",
            text=["Current"],
            textposition="top center",
            marker={"symbol": "star", "size": 18, "color": "#b91c1c"},
        )
    )
    figure.update_layout(
        title=f"{len(sample)} サンプル配分の群 / Groups of {len(sample)} sampled allocations",
        xaxis_title="年率ボラティリティ / Annual volatility (%)",
        yaxis_title="年率リターン / Annual return (%)",
    )
    st.plotly_chart(_light_layout(figure, 380), width="stretch")
    table(
        summary.rename(
            columns={
                "risk_label": "グループ / Group",
                "portfolios": "件数 / n",
                "mean_annual_volatility": "平均ボラ / Mean vol",
                "mean_annual_return": "平均リターン / Mean return",
                "mean_drawdown_magnitude": "平均下落率 / Mean drawdown",
                "mean_effective_holdings": "実効保有数 / Effective holdings",
            }
        ),
        label="Cluster summary",
    )
    st.caption(
        f"現在の配分: {grouping['current_label']} · ボラティリティ順位 "
        f"{grouping['current_volatility_percentile']:.0f}パーセンタイル · "
        f"シルエット係数 {grouping['silhouette']:.3f} / "
        f"Current allocation in that group; silhouette {grouping['silhouette']:.3f}."
    )
    st.caption(
        "グループ名はクラスタIDではなく、各クラスタの実測ボラティリティと下落率の順位から"
        "付けています（説明用の記述であり、格付けや推奨ではありません） / Labels come from "
        "measured cluster metrics, not cluster IDs; descriptive only, no rating or recommendation."
    )

    with st.expander("スコアの計算式 / Score formula"):
        st.markdown(
            "各成分は `100 × clip((値 − worst) / (best − worst), 0, 1)` で0〜100に変換し、"
            "重み付き合計をスコアとします。 / Each component is scaled to 0-100 with that clip."
        )
        table(
            pd.DataFrame(
                [
                    {
                        "成分 / Component": "年率ボラティリティ / Annual volatility",
                        "best": VOLATILITY_BAND[0],
                        "worst": VOLATILITY_BAND[1],
                        "重み / Weight": SCORE_WEIGHTS["volatility"],
                    },
                    {
                        "成分 / Component": "最大下落率(絶対値) / |Max drawdown|",
                        "best": DRAWDOWN_BAND[0],
                        "worst": DRAWDOWN_BAND[1],
                        "重み / Weight": SCORE_WEIGHTS["drawdown"],
                    },
                    {
                        "成分 / Component": "実効保有数 / Effective holdings",
                        "best": EFFECTIVE_HOLDINGS_BAND[0],
                        "worst": EFFECTIVE_HOLDINGS_BAND[1],
                        "重み / Weight": SCORE_WEIGHTS["concentration"],
                    },
                    {
                        "成分 / Component": "年率超過リターン ÷ ボラ / Excess return per unit risk",
                        "best": RISK_ADJUSTED_BAND[0],
                        "worst": RISK_ADJUSTED_BAND[1],
                        "重み / Weight": SCORE_WEIGHTS["risk_adjusted_return"],
                    },
                ]
            ),
            label="Score bands",
        )
        table(
            pd.DataFrame(
                [
                    {"区分 / Band": f"{english} / {japanese}", "下限 / Floor": floor}
                    for floor, _code, english, japanese in SCORE_BANDS
                ]
            ),
            label="Band floors",
        )
        st.caption(
            "下限未満は Fragile。スコアは合成データ用に設計したデモ指標で、"
            "規制上の格付けや投資助言ではありません。 / Below the last floor is Fragile."
        )

    with st.expander("データ生成とクリーニング / Data generation & cleaning"):
        st.write(
            f"{len(ASSETS)}資産の相関付き幾何ブラウン運動（ドリフト・ボラ・相関行列はコード内の"
            f"合成パラメータ、シード {DEFAULT_SEED}）で{TRADING_DAYS}営業日を生成し、"
            "実データ同様に欠損値と重複行を混入させています。 / Correlated geometric random "
            "walk with synthetic parameters, contaminated with gaps and duplicate rows."
        )
        table(_cleaning_table(report, raw_rows), label="Cleaning report")
        st.write(
            "クリーニングは日付でソート→重複行を先頭優先で削除→上限"
            f"{FFILL_LIMIT}営業日の前方補完のみ（後方補完なし）→補完できない行を除外、"
            "の順です。将来の価格が過去の日付に入ることはありません。 / No look-ahead: "
            "sort, de-duplicate, bounded past-only forward fill, then drop the rest."
        )

    with st.expander("前提と限界 / Assumptions & limits"):
        st.write(
            "・価格は合成であり、実在の市場・商品ではありません。 / Prices are synthetic.  \n"
            "・VaRは履歴分位点で、正規分布やファットテールを仮定していません。 / "
            "VaR is a historical quantile.  \n"
            "・指標は252営業日・5資産・200サンプル配分に限定した計算です。 / "
            "Bounded to 252 days, 5 assets, 200 sampled allocations.  \n"
            "・KMeansのグループは記述的な分類で、将来のリターンや安全性を保証しません。 / "
            "Clusters are descriptive only.  \n"
            "・投資助言・勧誘ではありません。 / Not investment advice."
        )
    demo_note()
    reset_button("portfolio", [f"portfolio_weight_{short}" for short in ASSET_SHORT])
