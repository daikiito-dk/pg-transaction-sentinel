"""Unit tests for demo_tools: message parsing, guards, generator, Keycloak payloads."""

from __future__ import annotations

import io
import json
import random
import socket
from datetime import date, datetime, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from demo_tools import generate as gen
from demo_tools import is_loopback_host
from demo_tools.generate import (
    ToolDisabledError,
    generate_batch,
    generated_key,
    make_params,
    metadata,
    to_csv_bytes,
    to_json_bytes,
)
from demo_tools.kafka_io import (
    EventStore,
    KafkaUnavailableError,
    MessageFormatError,
    live_available,
    parse_bootstrap,
    parse_transfer_message,
    poll_live,
    sample_message,
    sample_tick,
    send_test_messages,
)
from demo_tools.keycloak import (
    DEMO_ROLES,
    REALM,
    TEST_USERS,
    _realm_payload,
    _user_payload,
    keycloak_settings,
    provision_test_users,
    realm_status,
)
from demo_tools.keycloak import (
    ToolDisabledError as KeycloakDisabled,
)

AS_OF = date(2026, 10, 7)
AT = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

VALID_JSON = (
    b'{"message_id":"MSG-1","account_id":"ACC-000123","counterparty_account":"EXT-001",'
    b'"amount":950000,"currency":"JPY","transaction_type":"TRANSFER_OUT",'
    b'"timestamp":"2026-10-07T09:15:00+09:00","channel":"mobile"}'
)
VALID_XML = (
    b"<transfer><message_id>MSG-2</message_id><account_id>ACC-000456</account_id>"
    b"<amount>8000</amount><currency>JPY</currency><transaction_type>DEPOSIT</transaction_type>"
    b"<timestamp>2026-10-07T00:15:00Z</timestamp><channel>branch</channel></transfer>"
)


# --------------------------------------------------------------------------- #
# Wire message parsing (JSON + defusedxml)
# --------------------------------------------------------------------------- #
def test_parses_valid_json_and_xml_transfer_messages():
    event = parse_transfer_message(VALID_JSON)
    assert (event.message_id, event.account_id, event.amount) == ("MSG-1", "ACC-000123", 950000.0)
    assert event.transaction_type == "TRANSFER_OUT" and event.fmt == "json"
    assert event.counterparty == "EXT-001" and event.timestamp.tzinfo is not None

    xml_event = parse_transfer_message(VALID_XML)
    assert xml_event.message_id == "MSG-2" and xml_event.fmt == "xml"
    assert xml_event.channel == "branch" and xml_event.counterparty is None
    assert xml_event.timestamp == datetime(2026, 10, 7, 0, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"", "empty payload"),
        (b"{not json", "not valid JSON"),
        (b"<transfer><amount>broken", "not valid XML"),
        (b"\xff\xfe\x00", "not valid UTF-8"),
        (
            b'{"message_id":"m","account_id":"ACC-1","amount":0,"currency":"JPY",'
            b'"transaction_type":"DEPOSIT","timestamp":"2026-01-01"}',
            "between 0",
        ),
        (
            b'{"message_id":"m","account_id":"ACC-1","amount":-5,"currency":"JPY",'
            b'"transaction_type":"DEPOSIT","timestamp":"2026-01-01"}',
            "between 0",
        ),
        (
            b'{"message_id":"m","account_id":"ACC-1","amount":"NaN","currency":"JPY",'
            b'"transaction_type":"DEPOSIT","timestamp":"2026-01-01"}',
            "not finite",
        ),
        (
            b'{"message_id":"m","account_id":"acc 1!","amount":10,"currency":"JPY",'
            b'"transaction_type":"DEPOSIT","timestamp":"2026-01-01"}',
            "invalid characters",
        ),
        (
            b'{"message_id":"m","account_id":"ACC-1","amount":10,"currency":"JPY",'
            b'"transaction_type":"WIRE","timestamp":"2026-01-01"}',
            "unknown transaction_type",
        ),
        (
            b'{"message_id":"m","account_id":"ACC-1","amount":10,"currency":"JPY",'
            b'"transaction_type":"DEPOSIT","timestamp":"2026-99-99"}',
            "not ISO-8601",
        ),
        (
            b'{"account_id":"ACC-1","amount":10,"currency":"JPY",'
            b'"transaction_type":"DEPOSIT","timestamp":"2026-01-01"}',
            "missing or empty",
        ),
        (b"<bad></bad>", "root element"),
        (
            b'<!DOCTYPE transfer [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b"<transfer><message_id>&xxe;</message_id><account_id>ACC-1</account_id>"
            b"<amount>10</amount><currency>JPY</currency>"
            b"<transaction_type>DEPOSIT</transaction_type><timestamp>2026-01-01</timestamp>"
            b"</transfer>",
            "not valid XML",
        ),
    ],
)
def test_malformed_messages_are_rejected_with_bounded_reasons(payload, reason):
    with pytest.raises(MessageFormatError, match=reason):
        parse_transfer_message(payload)


def test_rejection_reasons_never_echo_the_full_payload():
    long_evil = b'{"message_id":"' + b"A" * 500 + b'"'
    with pytest.raises(MessageFormatError) as excinfo:
        parse_transfer_message(long_evil)
    assert "A" * 40 not in str(excinfo.value)


# --------------------------------------------------------------------------- #
# EventStore bounds and error-rate accounting
# --------------------------------------------------------------------------- #
def test_event_store_is_bounded_and_counts_error_rate():
    store = EventStore(max_events=50)
    result = sample_tick(store, rng=random.Random(1), batch_size=25, error_rate=0.2, at=AT)
    assert result["received"] == 25
    assert result["parsed"] + result["malformed"] == 25
    assert len(store.events) <= 50
    assert store.error_rate == result["malformed"] / 25
    frame = store.events_frame()
    assert not frame.empty and "source" in frame.columns
    assert (frame["source"] == "sample").all()
    assert store.summary()["total"] == 25


def test_event_store_error_history_is_bounded_but_counters_are_exact():
    store = EventStore()
    for index in range(200):
        store.add_error(f"reason {index}", "preview", at=AT)
    assert len(store.errors) == 100
    assert store.total == 200 and store.parse_errors == 200


def test_store_clear_resets_accumulation_and_source():
    store = EventStore()
    sample_tick(store, rng=random.Random(2), batch_size=5, error_rate=0.0, at=AT)
    store.clear(source="kafka")
    assert store.total == 0 and store.source == "kafka" and store.error_rate == 0.0


# --------------------------------------------------------------------------- #
# Sample-mode generation (offline, never real Kafka)
# --------------------------------------------------------------------------- #
def test_sample_messages_are_deterministic_and_parse_cleanly():
    first = sample_message(0, random.Random(9), fmt="json", at=AT)
    second = sample_message(0, random.Random(9), fmt="json", at=AT)
    assert first == second
    assert first[0] == "json" and first[1].startswith(b"{")
    event = parse_transfer_message(first[1], source="sample")
    assert event.source == "sample" and event.currency == "JPY"


@pytest.mark.parametrize(
    "kind",
    [
        "truncated",
        "invalid-json",
        "invalid-xml",
        "bad-amount",
        "bad-type",
        "bad-timestamp",
        "empty",
    ],
)
def test_every_planted_malformed_kind_actually_fails_parsing(kind):
    _, payload = sample_message(0, random.Random(4), fmt="json", malformed=kind, at=AT)
    with pytest.raises(MessageFormatError):
        parse_transfer_message(payload)


# --------------------------------------------------------------------------- #
# Loopback-only broker guards
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("127.0.0.7", True),
        ("localhost", True),
        ("::1", True),
        ("10.0.0.5", False),
        ("192.168.1.10", False),
        ("kafka.internal", False),
        ("", False),
    ],
)
def test_is_loopback_host(host, expected):
    assert is_loopback_host(host) is expected


def test_parse_bootstrap_rejects_non_loopback_and_bad_ports():
    assert parse_bootstrap("127.0.0.1:9092")[1:] == ("127.0.0.1", 9092)
    with pytest.raises(KafkaUnavailableError, match="loopback"):
        parse_bootstrap("10.0.0.5:9092")
    with pytest.raises(KafkaUnavailableError, match="port"):
        parse_bootstrap("127.0.0.1:not-a-port")


def test_live_mode_requires_admin_opt_in_and_loopback_broker(monkeypatch):
    monkeypatch.delenv("KAFKA_ENABLED", raising=False)
    available, reason = live_available()
    assert not available and "KAFKA_ENABLED" in reason

    monkeypatch.setenv("KAFKA_ENABLED", "true")
    available, reason = live_available()
    assert available and "loopback only" in reason

    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "broker.example.com:9092")
    available, reason = live_available()
    assert not available and "loopback" in reason


def test_producer_refuses_public_demo_and_missing_tool_writes(monkeypatch):
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    monkeypatch.delenv("LOCAL_TOOL_WRITES", raising=False)
    with pytest.raises(PermissionError, match="LOCAL_TOOL_WRITES"):
        send_test_messages(count=1)

    monkeypatch.setenv("LOCAL_TOOL_WRITES", "true")
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    with pytest.raises(PermissionError, match="PUBLIC_DEMO"):
        send_test_messages(count=1)

    monkeypatch.setenv("PUBLIC_DEMO", "false")
    with pytest.raises(KafkaUnavailableError, match="loopback"):
        send_test_messages(count=1, bootstrap="10.0.0.5:9092")


# --------------------------------------------------------------------------- #
# Synthetic financial data generator
# --------------------------------------------------------------------------- #
def test_generator_is_deterministic_and_bounded():
    batch = generate_batch(make_params(seed=42, days=30, label="demo", as_of=AS_OF))
    twin = generate_batch(make_params(seed=42, days=30, label="demo", as_of=AS_OF))
    pd.testing.assert_frame_equal(batch.fx_rates, twin.fx_rates)
    pd.testing.assert_frame_equal(batch.bond_quotes, twin.bond_quotes)
    assert batch.batch_id == "demo-42"
    assert batch.total_rows == (
        len(batch.fx_rates) + len(batch.term_deposits) + len(batch.bond_quotes)
    )
    assert make_params(days=9999).days == 120  # clamped to the hard bound
    assert len(make_params(fx_pairs=["USD/JPY", "BOGUS"]).fx_pairs) == 1


def test_generated_keys_are_idempotent_on_seed_and_batch():
    batch = generate_batch(make_params(seed=42, days=5, label="demo", as_of=AS_OF))
    key = batch.fx_rates["fx_key"].iat[0]
    expected_day = batch.fx_rates["day"].iat[0].isoformat()
    assert key == generated_key("fx", 42, "demo-42", "USD/JPY", expected_day)
    other_seed = generate_batch(make_params(seed=43, days=5, label="demo", as_of=AS_OF))
    assert other_seed.fx_rates["fx_key"].iat[0] != key
    other_label = generate_batch(make_params(seed=42, days=5, label="other", as_of=AS_OF))
    assert other_label.fx_rates["fx_key"].iat[0] != key


def test_bond_quotes_are_priced_by_quantlib_with_plausible_values():
    bonds = generate_batch(make_params(seed=42, days=5, as_of=AS_OF)).bond_quotes
    assert len(bonds) == len(gen.BOND_MATURITIES)
    assert (bonds["clean_price"] > 0).all() and (bonds["clean_price"] < 200).all()
    assert (bonds["yield_pct"] > 0).all()
    # Longer synthetic bonds carry longer modified durations at these yields.
    assert bonds["modified_duration"].is_monotonic_increasing


def test_exports_round_trip_csv_and_json():
    batch = generate_batch(make_params(seed=42, days=7, as_of=AS_OF))
    csv_frame = pd.read_csv(io.BytesIO(to_csv_bytes(batch.term_deposits)))
    assert len(csv_frame) == len(batch.term_deposits)
    assert list(csv_frame.columns) == list(batch.term_deposits.columns)
    json_records = json.loads(to_json_bytes(batch.bond_quotes))
    assert len(json_records) == len(batch.bond_quotes)
    assert set(json_records[0]) == set(batch.bond_quotes.columns)


def test_sandbox_ddl_never_contains_drop_or_truncate_and_is_schema_qualified():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    joined = " ".join(
        str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for table in metadata.sorted_tables
    )
    assert "DROP" not in joined.upper()
    assert "TRUNCATE" not in joined.upper()
    assert joined.count("demo_sandbox.") >= len(metadata.sorted_tables)


def test_insert_guards_refuse_public_demo_missing_optin_and_remote_hosts(monkeypatch):
    batch = generate_batch(make_params(seed=42, days=3, as_of=AS_OF))
    remote = create_engine("postgresql+psycopg2://user:pw@db.internal.example:5432/aml_db")

    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    monkeypatch.delenv("LOCAL_TOOL_WRITES", raising=False)
    with pytest.raises(ToolDisabledError, match="LOCAL_TOOL_WRITES"):
        gen.insert_batch(batch, remote, confirmed=True)

    monkeypatch.setenv("LOCAL_TOOL_WRITES", "true")
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    with pytest.raises(ToolDisabledError, match="PUBLIC_DEMO"):
        gen.insert_batch(batch, remote, confirmed=True)

    monkeypatch.setenv("PUBLIC_DEMO", "false")
    with pytest.raises(ToolDisabledError, match="explicit confirmation"):
        gen.insert_batch(batch, remote, confirmed=False)

    with pytest.raises(ToolDisabledError, match="loopback"):
        gen.insert_batch(batch, remote, confirmed=True)


# --------------------------------------------------------------------------- #
# Keycloak provisioning (guards + payload shape, HTTP mocked)
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload


class FakeKeycloakSession:
    """Records every call (method + URL only) and replays canned responses."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.lookup_counts: dict[str, int] = {}
        self.reset_payloads: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        if url.endswith("/realms/master/protocol/openid-connect/token"):
            return FakeResponse(200, {"access_token": "token-in-memory"})
        if url.endswith("/admin/realms/sentinel-demo") and method == "GET":
            return FakeResponse(404)
        if url.endswith("/admin/realms/sentinel-demo") and method == "POST":
            return FakeResponse(201)
        if url.endswith("/admin/realms") and method == "POST":
            return FakeResponse(201)
        if "/roles/" in url:  # role lookup: existing role representation (id + name)
            role_name = url.rsplit("/", 1)[1]
            return FakeResponse(200, {"id": f"role-{role_name}", "name": role_name})
        if url.endswith("/roles"):
            return FakeResponse(201)
        if "exact=true" in url:  # user lookup: absent first, present after creation
            username = url.split("username=")[1].split("&")[0]
            self.lookup_counts[username] = self.lookup_counts.get(username, 0) + 1
            if self.lookup_counts[username] == 1:
                return FakeResponse(200, [])
            return FakeResponse(200, [{"id": f"id-{username}", "username": username}])
        if url.endswith("/users"):
            return FakeResponse(201)
        if "/role-mappings/realm" in url and method == "GET":
            return FakeResponse(200, [])  # not yet mapped in this fake
        if "/role-mappings/realm" in url:
            return FakeResponse(204)
        if "/reset-password" in url:
            self.reset_payloads.append(kwargs.get("json"))
            return FakeResponse(204)
        raise AssertionError(f"unexpected call {method} {url}")


def test_keycloak_guards_refuse_public_demo_remote_urls_and_missing_password(monkeypatch):
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    monkeypatch.delenv("LOCAL_TOOL_WRITES", raising=False)
    with pytest.raises(KeycloakDisabled, match="LOCAL_TOOL_WRITES"):
        provision_test_users(password="local-only")

    monkeypatch.setenv("LOCAL_TOOL_WRITES", "true")
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    with pytest.raises(KeycloakDisabled, match="PUBLIC_DEMO"):
        provision_test_users(password="local-only")

    monkeypatch.setenv("PUBLIC_DEMO", "false")
    with pytest.raises(KeycloakDisabled, match="KEYCLOAK_ADMIN_PASSWORD"):
        provision_test_users(password="")

    with pytest.raises(KeycloakDisabled, match="loopback"):
        provision_test_users(base_url="http://keycloak.example.com:8080", password="local-only")


def test_keycloak_realm_and_user_payloads_contain_no_static_credentials():
    realm = _realm_payload()
    assert realm["realm"] == REALM and realm["enabled"] is True
    assert "credentials" not in realm

    for user in TEST_USERS:
        payload = _user_payload(user)
        assert payload["enabled"] is True
        assert "credentials" not in payload
        assert "UPDATE_PASSWORD" in payload["requiredActions"]
        assert payload["realmRoles"] == [user["role"]]
        assert payload["email"].endswith(".invalid")
    assert set(DEMO_ROLES) == {"aml-analyst", "branch-staff", "demo-observer"}


def test_provisioning_creates_realm_roles_and_users_with_temporary_passwords(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "false")
    monkeypatch.setenv("LOCAL_TOOL_WRITES", "true")

    session = FakeKeycloakSession()
    result = provision_test_users(password="generated-locally", session=session)

    assert result.realm == REALM and result.created_realm is True
    assert result.roles == list(DEMO_ROLES)
    assert len(result.users) == len(TEST_USERS)

    # Temporary passwords: present, unique, and never leaked via the summary.
    passwords = [user["temporary_password"] for user in result.users]
    assert all(passwords) and len(set(passwords)) == len(passwords)
    summary = json.dumps(result.summary())
    assert "temporary_password" not in summary and "token" not in summary
    assert "generated-locally" not in summary

    user_posts = [
        url for method, url in session.calls if method == "POST" and url.endswith("/users")
    ]
    assert len(user_posts) == len(TEST_USERS)
    assert len(session.reset_payloads) == len(TEST_USERS)
    assert all(payload["temporary"] is True for payload in session.reset_payloads)


def test_realm_status_reports_missing_realm_without_credentials(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "false")
    monkeypatch.setenv("LOCAL_TOOL_WRITES", "true")

    class StatusSession(FakeKeycloakSession):
        def request(self, method, url, **kwargs):
            self.calls.append((method, url))
            if url.endswith("/realms/master/protocol/openid-connect/token"):
                return FakeResponse(200, {"access_token": "token-in-memory"})
            if url.endswith("/admin/realms/sentinel-demo"):
                return FakeResponse(404)
            raise AssertionError(url)

    status = realm_status(session=StatusSession(), password="local-only")
    assert status == {"realm": REALM, "exists": False, "url": "http://127.0.0.1:8080"}


def test_keycloak_settings_read_password_from_environment(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ADMIN_PASSWORD", "")
    assert keycloak_settings()["password"] == ""
    monkeypatch.setenv("KEYCLOAK_ADMIN_PASSWORD", "x")
    assert keycloak_settings()["password"] == "x"


# --------------------------------------------------------------------------- #
# Integration (skipped unless the local services are actually running)
# --------------------------------------------------------------------------- #
def _tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _local_postgres() -> bool:
    import config

    try:
        engine = create_engine(config.get_database_url(), connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any failure means the DB is not testable here
        return False
    return True


requires_local_db = pytest.mark.skipif(not _local_postgres(), reason="local aml_db not reachable")
requires_kafka = pytest.mark.skipif(
    not _tcp_open("127.0.0.1", 9092), reason="local Kafka not running"
)


@requires_local_db
def test_sandbox_insert_is_idempotent_and_touches_only_demo_sandbox(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "false")
    monkeypatch.setenv("LOCAL_TOOL_WRITES", "true")
    engine = gen.sandbox_engine()
    batch = generate_batch(make_params(seed=90210, days=10, label="pytest", as_of=AS_OF))

    with engine.connect() as conn:
        before = {
            table: conn.execute(text(f"SELECT COUNT(*) FROM public.{table}")).scalar()
            for table in ("accounts", "transactions", "transaction_risk_scores")
        }

    first = gen.insert_batch(batch, engine, confirmed=True)
    assert first.created is True
    assert first.inserted == batch.total_rows and first.skipped == 0
    assert "demo_sandbox" in first.target

    second = gen.insert_batch(batch, engine, confirmed=True)  # same keys: all skipped
    assert second.created is False
    assert second.inserted == 0 and second.skipped == batch.total_rows

    with engine.connect() as conn:
        stored = conn.execute(
            text("SELECT COUNT(*) FROM demo_sandbox.fx_rates WHERE batch_id = 'pytest-90210'")
        ).scalar()
        schema_exists = conn.execute(
            text("SELECT nspname FROM pg_namespace WHERE nspname = 'demo_sandbox'")
        ).scalar()
        after = {
            table: conn.execute(text(f"SELECT COUNT(*) FROM public.{table}")).scalar()
            for table in ("accounts", "transactions", "transaction_risk_scores")
        }
    assert schema_exists == "demo_sandbox"
    assert stored == len(batch.fx_rates)
    assert before == after  # non-destructive: the AML tables are untouched

    # Clean up only this test's own rows (precise WHERE, no DROP/TRUNCATE).
    with engine.begin() as conn:
        for table in ("fx_rates", "term_deposits", "bond_quotes", "batches"):
            conn.execute(text(f"DELETE FROM demo_sandbox.{table} WHERE batch_id = 'pytest-90210'"))


@requires_kafka
def test_real_broker_round_trip_produce_and_bounded_consume(monkeypatch):
    import uuid

    from kafka import KafkaAdminClient

    # A unique ephemeral topic keeps the test hermetic: nothing from earlier
    # runs (or the demo topic itself) is ever read or asserted against.
    topic = f"banking.transfers.it-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("PUBLIC_DEMO", "false")
    monkeypatch.setenv("LOCAL_TOOL_WRITES", "true")
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092")

    try:
        produced = send_test_messages(
            topic=topic, count=25, seed=90211, fmt="mixed", error_rate=0.2
        )
        assert produced["sent"] == 25 and produced["malformed_planted"] > 0

        store = EventStore(source="kafka")
        first = poll_live(store, topic=topic, timeout_ms=4000, max_messages=500, start="earliest")
        assert first["status"] == "ok" and first["received"] == 25
        assert first["parsed"] + first["malformed"] == 25
        assert first["malformed"] == produced["malformed_planted"]
        assert all(event.source == "kafka" for event in store.events)
        assert all(event.fmt in ("json", "xml") for event in store.events)

        second = poll_live(store, topic=topic, timeout_ms=1000, max_messages=500)
        assert second["received"] == 0  # per-session offsets: no duplicates on re-poll
    finally:
        # Clean up only the ephemeral topic this test created.
        try:
            admin = KafkaAdminClient(bootstrap_servers="127.0.0.1:9092")
            admin.delete_topics([topic])
            admin.close()
        except Exception:  # noqa: BLE001 - cleanup is best-effort on a local broker
            pass
