"""AML Transaction Monitoring dashboard (Streamlit)."""

from __future__ import annotations

import html

import pandas as pd
import plotly.express as px
import streamlit as st

import ml_engine
from banking_scenes import render_branch_scene, render_customer_scene, render_demo_header
from config import HIGH_RISK_THRESHOLD, MEDIUM_RISK_THRESHOLD, PUBLIC_DEMO, get_engine
from demos.catalog import render_extra_scene

LEVEL_COLORS = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#22c55e"}
TYPOLOGY_LABELS = {
    "SMURFING": "Smurfing（閾値回避）",
    "NIGHT_HIGH_VALUE_WITHDRAWAL": "Night High-Value Withdrawal（深夜高額出金）",
    "DORMANT_PASS_THROUGH": "Dormant Pass-Through（休眠口座の即時転送）",
    "ML_ANOMALY": "ML Anomaly（統計的外れ値）",
}
TXN_TYPE_LABELS = {
    "DEPOSIT": "入金",
    "WITHDRAWAL": "出金",
    "TRANSFER_IN": "被振込",
    "TRANSFER_OUT": "振込",
}

st.set_page_config(
    page_title="Banking Scenario Lab | PG Transaction Sentinel",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container { padding-top: 1.6rem; }
    .badge { display:inline-block; padding:2px 10px; border-radius:999px; font-weight:700;
             font-size:0.78rem; letter-spacing:0.04em; color:#0b0f17; }
    .badge-HIGH { background:#ef4444; } .badge-MEDIUM { background:#f59e0b; }
    .badge-LOW { background:#22c55e; }
    .kpi { background:#141a26; border:1px solid #1f2937; border-radius:10px; padding:14px 18px; }
    .kpi .label { color:#9ca3af; font-size:0.78rem; text-transform:uppercase;
                  letter-spacing:0.08em; }
    .kpi .value { font-size:1.7rem; font-weight:700; margin-top:2px; }
    .reason { background:#1a1f2b; border-left:4px solid #ef4444; padding:8px 12px;
              border-radius:4px; margin:6px 0; font-size:0.92rem; }
    .acct-card { background:#141a26; border:1px solid #1f2937; border-radius:10px;
                 padding:14px 18px; }
    .muted { color:#9ca3af; font-size:0.85rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #
@st.cache_resource
def engine():
    return get_engine()


@st.cache_data(ttl=60, show_spinner="Loading transactions from PostgreSQL ...")
def load_monitoring_data() -> pd.DataFrame:
    query = """
        SELECT t.transaction_id, t.account_id, a.customer_name, a.risk_category,
               t.amount, t.transaction_type, t.timestamp, t.destination_account,
               s.risk_score, s.risk_level, s.ml_score, s.rule_hits, s.reasons
        FROM transactions t
        JOIN accounts a USING (account_id)
        LEFT JOIN transaction_risk_scores s USING (transaction_id)
    """
    df = pd.read_sql(query, engine(), parse_dates=["timestamp"])
    df["amount"] = df["amount"].astype(float)
    df["risk_score"] = df["risk_score"].astype(float)
    df["ml_score"] = df["ml_score"].astype(float)
    return df


def badge(level: str) -> str:
    return f'<span class="badge badge-{level}">{level}</span>'


def kpi(label: str, value: str, color: str = "#e5e7eb") -> str:
    return (
        f'<div class="kpi"><div class="label">{label}</div>'
        f'<div class="value" style="color:{color}">{value}</div></div>'
    )


def typology_names(rule_hits: str) -> list[str]:
    return [TYPOLOGY_LABELS.get(code, code) for code in rule_hits.split(",") if code]


def style_levels(df: pd.DataFrame):
    def color(value: str) -> str:
        c = LEVEL_COLORS.get(value)
        return f"background-color:{c}; color:#0b0f17; font-weight:700" if c else ""

    return df.style.map(color, subset=["Risk Level"])


def apply_dark_layout(fig, height: int = 360):
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        legend={"orientation": "h", "y": -0.2},
    )
    return fig


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
scene = render_demo_header()

if render_extra_scene(scene):
    st.stop()

try:
    data = load_monitoring_data()
except Exception as exc:  # noqa: BLE001 - surface any DB connectivity issue to the analyst
    st.error(
        "Cannot connect to PostgreSQL. Run `make up` and then `make data score`.\n\n"
        "PostgreSQL に接続できません。`make up` のあと `make data score` を実行してください。"
        f"\n\n`{exc}`"
    )
    st.stop()

if data.empty:
    st.warning("No transaction data found. / 取引データがありません。")
    st.stop()

if scene == "customer":
    render_customer_scene(data)
    st.stop()

if scene == "branch":
    render_branch_scene(data)
    st.stop()

if data["risk_score"].isna().all():
    st.warning(
        "No risk scores found. Run `make data score`.\n\n"
        "スコアがありません。`make data score` を実行してください。"
    )
    st.stop()

with st.sidebar:
    st.markdown("### Alert Filters")
    threshold = st.slider("Risk score threshold", 0, 100, HIGH_RISK_THRESHOLD, step=5)
    all_typologies = sorted({c for v in data["rule_hits"].dropna() for c in v.split(",") if c})
    selected_typologies = st.multiselect(
        "Typology",
        all_typologies,
        default=all_typologies,
        format_func=lambda c: TYPOLOGY_LABELS.get(c, c),
    )
    min_day, max_day = data["timestamp"].min().date(), data["timestamp"].max().date()
    date_range = st.date_input("Period", (min_day, max_day), min_value=min_day, max_value=max_day)

    st.divider()
    st.markdown("### Model Operations")
    if (
        st.button(
            "Re-score transactions",
            width="stretch",
            disabled=PUBLIC_DEMO,
            help="Disabled in the public demo / 公開デモでは無効です" if PUBLIC_DEMO else None,
        )
        and not PUBLIC_DEMO
    ):
        with st.spinner("Running Isolation Forest + typology rules ..."):
            _, metrics = ml_engine.run(engine())
        load_monitoring_data.clear()
        st.success(f"Re-scored. Alerts: {metrics['alerts']:,}")
        st.rerun()
    st.caption(
        f"HIGH ≥ {HIGH_RISK_THRESHOLD} / MEDIUM ≥ {MEDIUM_RISK_THRESHOLD} / LOW < "
        f"{MEDIUM_RISK_THRESHOLD}"
    )

st.markdown("## PG Transaction Sentinel")
st.markdown(
    '<div class="muted">AML Transaction Monitoring · Suspicious Transaction Alerts · '
    "PostgreSQL × Isolation Forest × Typology Rules</div>",
    unsafe_allow_html=True,
)
if PUBLIC_DEMO:
    st.caption(
        "Public demo with synthetic data. No real customers or accounts. / "
        "合成データによる公開デモです。実在の顧客・口座とは関係ありません。"
    )
st.write("")

in_period = data["timestamp"].dt.date.between(*date_range) if len(date_range) == 2 else True
period = data[in_period]
alerts = period[period["risk_score"] >= threshold]
if selected_typologies:
    alerts = alerts[
        alerts["rule_hits"]
        .fillna("")
        .apply(lambda v: any(code in v.split(",") for code in selected_typologies))
    ]
else:
    alerts = alerts.iloc[0:0]

level_counts = period["risk_level"].value_counts()
cols = st.columns(5)
cols[0].markdown(kpi("Transactions monitored", f"{len(period):,}"), unsafe_allow_html=True)
cols[1].markdown(
    kpi("High risk alerts", f"{level_counts.get('HIGH', 0):,}", LEVEL_COLORS["HIGH"]),
    unsafe_allow_html=True,
)
cols[2].markdown(
    kpi("Medium risk", f"{level_counts.get('MEDIUM', 0):,}", LEVEL_COLORS["MEDIUM"]),
    unsafe_allow_html=True,
)
cols[3].markdown(
    kpi("Accounts under watch", f"{alerts['account_id'].nunique():,}"), unsafe_allow_html=True
)
cols[4].markdown(
    kpi("Alerted amount", f"¥{alerts['amount'].sum() / 1e6:,.1f}M"), unsafe_allow_html=True
)
st.write("")

tab_alerts, tab_overview = st.tabs(["Suspicious Transaction Alerts", "Risk Overview"])

with tab_overview:
    left, right = st.columns(2)
    hist = px.histogram(
        period,
        x="risk_score",
        color="risk_level",
        nbins=50,
        log_y=True,
        color_discrete_map=LEVEL_COLORS,
        title="Risk score distribution (log scale)",
    )
    left.plotly_chart(apply_dark_layout(hist), width="stretch")

    typ = period[period["risk_level"] != "LOW"]["rule_hits"].fillna("").str.split(",").explode()
    typ = typ[typ != ""].map(lambda c: TYPOLOGY_LABELS.get(c, c)).value_counts().reset_index()
    typ.columns = ["Typology", "Alerts"]
    bar = px.bar(
        typ,
        x="Alerts",
        y="Typology",
        orientation="h",
        title="Alerts by typology",
        color_discrete_sequence=["#ef4444"],
    )
    right.plotly_chart(apply_dark_layout(bar), width="stretch")

    daily = (
        period[period["risk_level"] != "LOW"]
        .assign(day=lambda d: d["timestamp"].dt.date)
        .groupby(["day", "risk_level"])
        .size()
        .reset_index(name="alerts")
    )
    trend = px.bar(
        daily,
        x="day",
        y="alerts",
        color="risk_level",
        color_discrete_map=LEVEL_COLORS,
        title="Daily alert volume",
    )
    st.plotly_chart(apply_dark_layout(trend, 300), width="stretch")

with tab_alerts:
    st.markdown(
        f"#### Alert queue — score ≥ {threshold}　<span class='muted'>"
        f"{len(alerts):,} alerts</span>",
        unsafe_allow_html=True,
    )
    table = (
        alerts.sort_values(["risk_score", "timestamp"], ascending=[False, False])
        .assign(
            typology=lambda d: (
                d["rule_hits"].fillna("").map(lambda v: " / ".join(typology_names(v)))
            )
        )[
            [
                "transaction_id",
                "timestamp",
                "account_id",
                "customer_name",
                "transaction_type",
                "amount",
                "risk_score",
                "risk_level",
                "typology",
                "reasons",
            ]
        ]
        .rename(
            columns={
                "transaction_id": "Txn ID",
                "timestamp": "Timestamp",
                "account_id": "Account",
                "customer_name": "Customer",
                "transaction_type": "Type",
                "amount": "Amount (JPY)",
                "risk_score": "Risk Score",
                "risk_level": "Risk Level",
                "typology": "Typology",
                "reasons": "Detection Reasons",
            }
        )
        .reset_index(drop=True)
    )
    event = st.dataframe(
        style_levels(table),
        width="stretch",
        hide_index=True,
        height=380,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Timestamp": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
            "Amount (JPY)": st.column_config.NumberColumn(format="¥%,d"),
            "Risk Score": st.column_config.ProgressColumn(
                min_value=0, max_value=100, format="%.1f"
            ),
            "Detection Reasons": st.column_config.TextColumn(width="large"),
        },
    )

    watchlist = (
        alerts.groupby("account_id")["risk_score"].max().sort_values(ascending=False).index.tolist()
    )
    selected_rows = event.selection.rows if event is not None else []
    st.divider()
    if not watchlist:
        st.info("No alerts match the filters. / 条件に一致するアラートはありません。")
        st.stop()

    default_account = table.loc[selected_rows[0], "Account"] if selected_rows else watchlist[0]
    account_id = st.selectbox(
        "Account investigation (or select a row in the table) / "
        "口座調査（表の行を選択しても切り替わります）",
        watchlist,
        index=watchlist.index(default_account),
        format_func=lambda a: f"{a} · {data.loc[data['account_id'] == a, 'customer_name'].iat[0]}",
    )

    history = data[data["account_id"] == account_id].sort_values("timestamp")
    acct_alerts = history[history["risk_score"] >= MEDIUM_RISK_THRESHOLD]
    top = history.loc[history["risk_score"].idxmax()]

    info, chart = st.columns([1, 2.2])
    with info:
        st.markdown(
            f"""
            <div class="acct-card">
              <div class="muted">ACCOUNT</div>
              <div style="font-size:1.3rem;font-weight:700">{html.escape(account_id)}</div>
              <div>{html.escape(top["customer_name"])}</div>
              <div style="margin-top:10px">{badge(top["risk_level"])}
                <span style="margin-left:8px;font-size:1.2rem;font-weight:700">
                {top["risk_score"]:.1f}</span><span class="muted"> / 100</span></div>
              <div class="muted" style="margin-top:10px">
                KYC risk category: {html.escape(top["risk_category"])}<br>
                Transactions: {len(history):,} · Alerts: {len(acct_alerts):,}<br>
                Outflow total: ¥{
                history.loc[
                    history["transaction_type"].isin(ml_engine.OUTFLOW_TYPES), "amount"
                ].sum():,.0f}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("##### Detection reasons")
        parts = [
            part
            for reason in acct_alerts.sort_values("risk_score", ascending=False)["reasons"].dropna()
            for part in reason.split(ml_engine.REASON_SEPARATOR)
        ]
        for part in list(dict.fromkeys(parts))[:8]:
            st.markdown(f'<div class="reason">{html.escape(part)}</div>', unsafe_allow_html=True)

    with chart:
        plot_df = history.assign(
            type_label=history["transaction_type"].map(TXN_TYPE_LABELS),
            marker=history["risk_score"].clip(lower=8),
        )
        fig = px.scatter(
            plot_df,
            x="timestamp",
            y="amount",
            color="risk_level",
            symbol="type_label",
            size="marker",
            size_max=18,
            log_y=True,
            color_discrete_map=LEVEL_COLORS,
            hover_data={
                "transaction_id": True,
                "risk_score": True,
                "reasons": True,
                "marker": False,
                "type_label": True,
            },
            title="Transaction timeline (amount, log scale)",
            labels={"amount": "Amount (JPY)", "timestamp": "", "type_label": "Type"},
        )
        st.plotly_chart(apply_dark_layout(fig, 420), width="stretch")

    st.markdown("##### Transaction history")
    st.dataframe(
        style_levels(
            history.sort_values("timestamp", ascending=False)[
                [
                    "transaction_id",
                    "timestamp",
                    "transaction_type",
                    "amount",
                    "destination_account",
                    "risk_score",
                    "risk_level",
                ]
            ].rename(
                columns={
                    "transaction_id": "Txn ID",
                    "timestamp": "Timestamp",
                    "transaction_type": "Type",
                    "amount": "Amount (JPY)",
                    "destination_account": "Counterparty",
                    "risk_score": "Risk Score",
                    "risk_level": "Risk Level",
                }
            )
        ),
        width="stretch",
        hide_index=True,
        height=300,
        column_config={
            "Timestamp": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
            "Amount (JPY)": st.column_config.NumberColumn(format="¥%,d"),
            "Risk Score": st.column_config.ProgressColumn(
                min_value=0, max_value=100, format="%.1f"
            ),
        },
    )
