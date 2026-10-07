"""AppTest coverage for the transfer-message monitor scene (demos/events.py)."""

from __future__ import annotations

import random

import pytest
from streamlit.testing.v1 import AppTest

SCRIPT = "from demos.events import render\nrender()"


def _pin_env(monkeypatch) -> None:
    """Make tests independent of the developer's local .env.tools settings."""
    for name in ("KAFKA_ENABLED", "KAFKA_BOOTSTRAP_SERVERS", "PUBLIC_DEMO", "LOCAL_TOOL_WRITES"):
        monkeypatch.delenv(name, raising=False)


def _render() -> AppTest:
    return AppTest.from_string(SCRIPT).run()


def test_sample_mode_is_default_and_never_claimed_to_be_real_kafka(monkeypatch):
    _pin_env(monkeypatch)
    at = _render()
    assert not at.exception
    # Live mode is not offered unless KAFKA_ENABLED is set by the local admin.
    assert list(at.radio[0].options) == ["Sample mode / サンプルモード"]
    markdown = " ".join(element.value for element in at.markdown)
    assert "SAMPLE MODE" in markdown
    assert "not real Kafka" in markdown


def test_sample_monitoring_accumulates_bounded_events_and_shows_error_rate(monkeypatch):
    _pin_env(monkeypatch)
    at = _render()
    at.toggle[0].set_value(True).run()
    assert not at.exception
    store = at.session_state["events_store"]
    assert store.total >= 1
    assert store.source == "sample"
    metrics = {metric.label: metric.value for metric in at.metric}
    assert metrics["Messages / 受信メッセージ"] != "0"

    before = at.session_state["events_store"].total
    at.button(key="events_poll_now").click().run()
    assert at.session_state["events_store"].total >= before + 1


def test_malformed_samples_surface_in_the_error_table(monkeypatch):
    _pin_env(monkeypatch)
    at = _render()
    at.toggle[0].set_value(True).run()
    # Seed the session generator deterministically: seed 7 plants a malformed
    # message in the very first tick, so the error path is exercised reliably.
    at.session_state["events_rng"] = random.Random(7)
    at.button(key="events_poll_now").click().run()
    store = at.session_state["events_store"]
    assert store.parse_errors >= 1
    markdown = " ".join(element.value for element in at.markdown)
    assert "Malformed messages" in markdown


def test_reset_clears_session_accumulation(monkeypatch):
    _pin_env(monkeypatch)
    at = _render()
    at.toggle[0].set_value(True).run()
    assert at.session_state["events_store"].total >= 1
    at.button(key="events_reset").click().run()
    assert at.session_state["events_store"].total == 0
    assert at.session_state["events_store"].parse_errors == 0


def test_live_mode_is_offered_only_with_admin_opt_in(monkeypatch):
    _pin_env(monkeypatch)
    monkeypatch.setenv("KAFKA_ENABLED", "true")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")
    at = _render()
    labels = list(at.radio[0].options)
    assert "Live Kafka / ライブ Kafka" in labels


def test_live_mode_surfaces_broker_failure_instead_of_silent_success(monkeypatch):
    # A loopback address with nothing listening: failure must be explicit.
    _pin_env(monkeypatch)
    monkeypatch.setenv("KAFKA_ENABLED", "true")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9099")
    at = _render()
    at.radio[0].set_value("live").run()
    at.toggle[0].set_value(True).run(timeout=15)
    store = at.session_state["events_store"]
    assert store.connected is False and store.source == "kafka"
    # The next render must show the failure, never a silent success.
    at.button(key="events_poll_now").click().run(timeout=15)
    errors = [element.value for element in at.error]
    assert any("Kafka" in message for message in errors)
    assert at.session_state["events_store"].live_failures >= 1


def test_switching_modes_resets_the_store(monkeypatch):
    _pin_env(monkeypatch)
    monkeypatch.setenv("KAFKA_ENABLED", "true")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9099")
    at = _render()
    at.toggle[0].set_value(True).run()
    assert at.session_state["events_store"].total >= 1
    at.radio[0].set_value("live").run()
    store = at.session_state["events_store"]
    assert store.total == 0 and store.source == "kafka"  # sample data never mixed in


def test_sample_payload_previews_are_bounded_and_shown_as_samples(monkeypatch):
    _pin_env(monkeypatch)
    at = _render()
    at.toggle[0].set_value(True).run()
    store = at.session_state["events_store"]
    assert 0 < len(store.raw_payloads) <= 5
    assert all(len(item["preview"]) <= 120 for item in store.raw_payloads)


@pytest.mark.parametrize("interval", [1, 5])
def test_interval_selection_is_accepted(interval, monkeypatch):
    _pin_env(monkeypatch)
    at = _render()
    at.toggle[0].set_value(True).run()
    at.select_slider(key="events_interval").set_value(interval).run()
    assert not at.exception
    assert at.session_state["events_interval"] == interval
