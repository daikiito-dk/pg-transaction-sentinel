"""Transfer-message monitor scene: live loopback Kafka, or an explicit offline sample mode.

The scene shows a bounded, auto-refreshing view of transfer wire messages on the
``banking.transfers`` topic:

* **Live mode** (admin only): requires ``KAFKA_ENABLED=true`` plus a loopback
  ``KAFKA_BOOTSTRAP_SERVERS``. It reads with a ``group_id=None`` consumer and
  surfaces broker failures explicitly — it never pretends a broken broker is
  fine.
* **Sample mode** (default, public): generates synthetic messages locally and
  parses them through the exact same parser, with a persistent amber badge so
  sample data is never presented as real Kafka traffic. It never opens a
  connection and the public demo can never publish messages.

The public demo never lets a visitor enter a broker address; the destination is
always taken from the environment and validated as loopback.
"""

from __future__ import annotations

import random
import time

import pandas as pd
import plotly.express as px
import streamlit as st

from demo_tools import kafka_io
from demo_tools.kafka_io import (
    EventStore,
    KafkaUnavailableError,
    live_available,
    poll_live,
    sample_tick,
)
from demos.ui import banner, demo_note, reset_button, table

MAX_DISPLAY_ROWS = 100
POLL_INTERVALS = (1, 2, 5)
_DEFAULT_INTERVAL = 2
_BADGE = {
    "sample": "background:#92400e;color:#fef3c7",
    "kafka": "background:#14532d;color:#dcfce7",
}
_SOURCE_LABEL = {"sample": "SAMPLE", "kafka": "KAFKA"}
TYPE_LABELS = {
    "DEPOSIT": "Deposit / 入金",
    "WITHDRAWAL": "Withdrawal / 出金",
    "TRANSFER_IN": "Transfer in / 被振込",
    "TRANSFER_OUT": "Transfer out / 振込",
}
_EVENTS_COLUMNS = {
    "received_at": "Received / 受信",
    "message_id": "Message ID / メッセージID",
    "account_id": "Account / 口座",
    "counterparty": "Counterparty / 相手",
    "transaction_type": "Type / 種別",
    "amount": "Amount (JPY) / 金額",
    "currency": "Currency",
    "channel": "Channel / 経路",
    "fmt": "Format / 形式",
    "source": "Source",
    "timestamp": "Business time / 取引時刻",
}


def _store() -> EventStore:
    return st.session_state.setdefault("events_store", EventStore(source="sample"))


def _sync_source(store: EventStore, mode: str) -> None:
    """Reset accumulated data when the user switches between sample and live."""
    assigned = st.session_state.get("events_store_mode")
    if assigned != mode:
        st.session_state["events_store_mode"] = mode
        store.clear(source="kafka" if mode == "live" else "sample")


def _tick(store: EventStore, mode: str) -> None:
    if mode == "live":
        try:
            poll_live(store)
        except KafkaUnavailableError as exc:
            store.note_failure(str(exc))
            st.session_state["events_last_error"] = str(exc)
        else:
            st.session_state.pop("events_last_error", None)
    else:
        rng = st.session_state.setdefault("events_rng", random.Random())
        sample_tick(store, rng=rng)


def _timeline_frame(store: EventStore) -> pd.DataFrame:
    """Per-minute parsed vs malformed counts for the volume chart."""
    parsed = pd.DataFrame(
        {"ts": [event.timestamp for event in store.events], "status": "parsed ok / 正常"}
    )
    malformed = pd.DataFrame(
        {"ts": [error["at"] for error in store.errors], "status": "malformed / 不正"}
    )
    frame = pd.concat((parsed, malformed), ignore_index=True)
    if frame.empty:
        return pd.DataFrame(columns=["minute", "status", "messages"])
    frame["minute"] = pd.to_datetime(frame["ts"], utc=True).dt.floor("min")
    counts = frame.groupby(["minute", "status"], as_index=False).size()
    return counts.rename(columns={"size": "messages"})


def _amounts_frame(store: EventStore) -> pd.DataFrame:
    frame = store.events_frame()
    if frame.empty:
        return frame
    return frame.assign(
        transaction_type=frame["transaction_type"].map(lambda t: TYPE_LABELS.get(t, t))
    )


def _dark_layout(figure, height: int = 300):
    figure.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        legend={"orientation": "h", "y": -0.2},
    )
    return figure


def _source_badge(store: EventStore) -> str:
    label = _SOURCE_LABEL.get(store.source, store.source.upper())
    style = _BADGE.get(store.source, "background:#1f2937;color:#e5e7eb")
    return (
        f'<span style="{style};padding:2px 12px;border-radius:999px;font-weight:700">{label}</span>'
    )


def _render_kpis(store: EventStore, mode: str) -> None:
    last_event = store.last_event_at.strftime("%H:%M:%S") if store.last_event_at else "—"
    connection = "sample" if mode != "live" else ("online" if store.connected else "offline")
    st.markdown(
        f"{_source_badge(store)}"
        f'<span style="margin-left:10px;color:#9ca3af;font-size:0.85rem">'
        f"last event: {last_event}</span>",
        unsafe_allow_html=True,
    )
    columns = st.columns(5)
    columns[0].metric("Messages / 受信メッセージ", f"{store.total:,}")
    columns[1].metric("Parsed OK / 正常", f"{store.parsed_ok:,}")
    columns[2].metric("Malformed / 不正", f"{store.parse_errors:,}")
    columns[3].metric("Error rate / エラー率", f"{store.error_rate:.1%}")
    columns[4].metric("Connection / 接続", connection)


def _monitor_body() -> None:
    store = _store()
    mode = st.session_state.get("events_mode", "sample")
    running = bool(st.session_state.get("events_running", False))
    interval = int(st.session_state.get("events_interval", _DEFAULT_INTERVAL))
    forced = st.session_state.pop("events_force_tick", False)
    _sync_source(store, mode)

    if mode == "live":
        last_error = st.session_state.get("events_last_error")
        if last_error:
            st.error(
                f"Kafka is unavailable — retrying automatically: {last_error} / "
                f"Kafka に接続できません。自動再試行中です。"
            )

    last_poll = float(st.session_state.get("events_last_poll", 0.0))
    elapsed = time.monotonic() - last_poll
    if forced or (running and elapsed >= interval):
        st.session_state["events_last_poll"] = time.monotonic()
        _tick(store, mode)

    if store.total == 0:
        st.info(
            "No messages yet. Start monitoring or press “Poll once now”. / "
            "まだメッセージがありません。「監視」を開始するか「今すぐ1回取得」を押してください。"
        )
    _render_kpis(store, mode)

    timeline = _timeline_frame(store)
    if not timeline.empty:
        volume = px.bar(
            timeline,
            x="minute",
            y="messages",
            color="status",
            title="Message volume per minute / 分あたりのメッセージ数",
            labels={"minute": "", "messages": "messages / 件"},
            color_discrete_map={
                "parsed ok / 正常": "#38bdf8",
                "malformed / 不正": "#ef4444",
            },
        )
        st.plotly_chart(_dark_layout(volume), width="stretch", theme="streamlit")

    amounts = _amounts_frame(store)
    if not amounts.empty:
        scatter = px.scatter(
            amounts,
            x="timestamp",
            y="amount",
            color="transaction_type",
            log_y=True,
            title="Transfer amounts over time (log scale) / 時間経過と金額（対数軸）",
            labels={"timestamp": "", "amount": "JPY / 円", "transaction_type": "Type / 種別"},
        )
        st.plotly_chart(_dark_layout(scatter, 320), width="stretch", theme="streamlit")

    latest = store.events_frame().head(MAX_DISPLAY_ROWS)
    if not latest.empty:
        st.markdown(
            f"#### Recent messages / 最新メッセージ　"
            f'<span style="color:#9ca3af;font-size:0.85rem">showing {len(latest):,}</span>',
            unsafe_allow_html=True,
        )
        st.dataframe(
            latest.rename(columns=_EVENTS_COLUMNS),
            width="stretch",
            hide_index=True,
            height=300,
            column_config={
                "Amount (JPY) / 金額": st.column_config.NumberColumn(format="¥%,d"),
            },
        )
    if store.errors:
        recent_errors = store.errors_frame().tail(10)
        st.markdown("#### Malformed messages / 不正なメッセージ")
        table(
            recent_errors.rename(
                columns={
                    "at": "Time / 時刻",
                    "reason": "Reason / 理由",
                    "preview": "Payload preview / ペイロード抜粋",
                }
            ),
            label="Malformed transfer messages",
        )
    if store.raw_payloads:
        with st.expander("Last raw payloads / 最後の生ペイロード"):
            for item in store.raw_payloads:
                st.markdown(
                    f"`{item['fmt']}` · `{item['preview']}`",
                )


@st.fragment(run_every="1s")
def _monitor_auto() -> None:
    _monitor_body()


@st.fragment()
def _monitor_manual() -> None:
    _monitor_body()


def render() -> None:
    """Render the transfer-message monitor scene (registered by the app)."""
    available, reason = live_available()
    settings = kafka_io.live_settings()
    banner(
        "SENTINEL · Transfer Message Monitor / 振込メッセージ監視",
        "Apache Kafka consumer demo · bounded, loopback-only",
        accent="#22d3ee",
    )
    demo_note()

    options = {"sample": "Sample mode / サンプルモード"}
    if available:
        options["live"] = "Live Kafka / ライブ Kafka"
    mode = st.radio(
        "Mode / モード",
        list(options),
        format_func=options.get,
        horizontal=True,
        key="events_mode",
    )
    if mode == "live":
        st.markdown(
            f'<div style="border-left:4px solid #22c55e;padding:8px 12px;margin-bottom:8px">'
            f"<strong>LIVE</strong> — consuming from broker <code>{settings['bootstrap']}</code>, "
            f"topic <code>{settings['topic']}</code> with a read-only consumer "
            "(group_id=None, offsets kept per browser session, loopback only). / "
            "読み取り専用コンシューマーで消費しています（ループバックのみ）。</div>",
            unsafe_allow_html=True,
        )
    else:
        if available:
            st.caption(
                "Live mode is available (KAFKA_ENABLED=true). / ライブモードは利用可能です。"
            )
        else:
            st.caption(f"Live mode is disabled — {reason}")
        st.markdown(
            '<div style="border-left:4px solid #f59e0b;padding:8px 12px;margin-bottom:8px">'
            "<strong>SAMPLE MODE</strong> — messages are generated locally in this browser "
            "session and parsed by the same code path. This is not real Kafka traffic, and "
            "nothing is sent anywhere. / サンプルモードはこのブラウザ内で生成した架空"
            "メッセージです。実際の Kafka 通信ではありません。</div>",
            unsafe_allow_html=True,
        )

    running = st.toggle(
        "Monitoring / 監視",
        value=False,
        key="events_running",
        help="Auto-refreshes the panel while enabled. / 有効化すると自動更新します。",
    )
    st.select_slider(
        "Refresh interval (s) / 更新間隔（秒）",
        options=POLL_INTERVALS,
        value=_DEFAULT_INTERVAL,
        key="events_interval",
    )
    if st.button("Poll once now / 今すぐ1回取得", key="events_poll_now"):
        st.session_state["events_force_tick"] = True

    if running:
        _monitor_auto()
    else:
        _monitor_manual()

    reset_button(
        "events",
        [
            "events_store",
            "events_store_mode",
            "events_running",
            "events_last_poll",
            "events_last_error",
            "events_force_tick",
        ],
    )
