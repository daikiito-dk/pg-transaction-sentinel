"""Bounded, loopback-only Apache Kafka monitor I/O for transfer messages.

This module powers the "transfer message monitor" demo scene:

* ``parse_transfer_message`` decodes JSON or XML (via ``defusedxml``) wire
  messages into validated :class:`TransferEvent` records, rejecting malformed
  payloads with a bounded, sanitised reason.
* :class:`EventStore` accumulates parsed events per Streamlit session with hard
  caps so the UI can never grow without bound.
* :func:`poll_live` runs one bounded, non-blocking read against a *loopback*
  broker using a ``group_id=None`` consumer (a read-only consumer that never
  commits offsets), remembering per-session offsets, and always closing the
  consumer. Connection failures raise :class:`KafkaUnavailableError` so the UI can
  surface them instead of pretending nothing happened.
* :func:`send_test_messages` is a *trusted CLI* producer used for local
  verification only. It refuses to run when ``PUBLIC_DEMO`` is enabled and it
  never accepts a non-loopback destination, so the public demo can never publish.
* Sample mode never touches Kafka: :func:`sample_tick` synthesises messages
  locally and parses them through the exact same parser, so a sample-mode stream
  is always labelled ``source="sample"`` and never shown as real Kafka traffic.

CLI::

    uv run python -m demo_tools.kafka_io produce --count 50 --format mixed --error-rate 0.1
    uv run python -m demo_tools.kafka_io consume --max-messages 100 --from beginning
"""

from __future__ import annotations

import argparse
import json
import os
import random
import xml.etree.ElementTree as ElementTree
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from defusedxml import ElementTree as DefusedElementTree

from demo_tools import env_flag, is_loopback_host, mask_hostport, require_writes_enabled

TOPIC = "banking.transfers"
DEFAULT_BOOTSTRAP = "127.0.0.1:9092"
DEFAULT_PORT = 9092

# Hard bounds so a burst of traffic can never wedge the demo UI.
MAX_STORED_EVENTS = 500
MAX_ERROR_HISTORY = 100
MAX_RAW_HISTORY = 5
MAX_MESSAGES_PER_POLL = 200
POLL_TIMEOUT_MS = 1000
MAX_CLI_MESSAGES = 5000
MAX_CLI_TIMEOUT_MS = 15000

VALID_TRANSACTION_TYPES = frozenset({"DEPOSIT", "WITHDRAWAL", "TRANSFER_IN", "TRANSFER_OUT"})
VALID_CHANNELS = ("mobile", "web", "branch", "atm")
MAX_AMOUNT = 1_000_000_000
PREVIEW_CHARS = 120
MALFORMED_KINDS = (
    "truncated",
    "invalid-json",
    "invalid-xml",
    "bad-amount",
    "bad-type",
    "bad-timestamp",
    "empty",
)


def env_var(name: str) -> str | None:
    return os.getenv(name)


_JSON_FIELDS = (
    "message_id",
    "account_id",
    "counterparty_account",
    "amount",
    "currency",
    "transaction_type",
    "timestamp",
    "channel",
)


class KafkaUnavailableError(RuntimeError):
    """Raised when the configured loopback broker/topic cannot be reached."""


class MessageFormatError(ValueError):
    """Raised for malformed transfer messages; the reason never echoes full payloads."""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _preview(payload: str | bytes) -> str:
    """Bounded, control-character-safe preview of a raw payload."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    return " ".join(payload.split())[:PREVIEW_CHARS]


@dataclass(frozen=True)
class TransferEvent:
    """One validated transfer wire message (synthetic accounts, JPY amounts)."""

    message_id: str
    account_id: str
    amount: float
    currency: str
    transaction_type: str
    timestamp: datetime
    counterparty: str | None = None
    channel: str = "unknown"
    fmt: str = "unknown"
    source: str = "sample"
    received_at: datetime = field(default_factory=_now_utc)

    def row(self) -> dict[str, Any]:
        return {
            "received_at": self.received_at,
            "message_id": self.message_id,
            "account_id": self.account_id,
            "counterparty": self.counterparty,
            "transaction_type": self.transaction_type,
            "amount": self.amount,
            "currency": self.currency,
            "channel": self.channel,
            "fmt": self.fmt,
            "source": self.source,
            "timestamp": self.timestamp,
        }


def _require_text(value: Any, name: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MessageFormatError(f"missing or empty {name}")
    text = value.strip()
    if len(text) > max_length:
        raise MessageFormatError(f"{name} longer than {max_length} characters")
    return text


def _parse_amount(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise MessageFormatError("amount is not a number")
    try:
        amount = float(value)
    except ValueError:
        raise MessageFormatError("amount is not numeric") from None
    if amount != amount or amount in (float("inf"), float("-inf")):
        raise MessageFormatError("amount is not finite")
    if not 0 < amount <= MAX_AMOUNT:
        raise MessageFormatError(f"amount must be between 0 and {MAX_AMOUNT:,}")
    return amount


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MessageFormatError("missing or empty timestamp")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        raise MessageFormatError("timestamp is not ISO-8601") from None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    if not datetime(2000, 1, 1, tzinfo=timezone.utc) <= stamp <= _now_utc() + timedelta(days=1):
        raise MessageFormatError("timestamp is outside the plausible range")
    return stamp.astimezone(timezone.utc)


def _validate_fields(values: dict[str, Any]) -> TransferEvent:
    message_id = _require_text(values.get("message_id"), "message_id", max_length=64)
    account_id = _require_text(values.get("account_id"), "account_id", max_length=32)
    if not all(char.isascii() and (char.isalnum() or char == "-") for char in account_id):
        raise MessageFormatError("account_id contains invalid characters")
    amount = _parse_amount(values.get("amount"))
    currency = _require_text(values.get("currency"), "currency", max_length=3).upper()
    if len(currency) != 3 or not currency.isalpha():
        raise MessageFormatError("currency must be a 3-letter code")
    transaction_type = _require_text(
        values.get("transaction_type"), "transaction_type", max_length=20
    ).upper()
    if transaction_type not in VALID_TRANSACTION_TYPES:
        raise MessageFormatError(f"unknown transaction_type '{transaction_type}'")
    counterparty = values.get("counterparty_account")
    if counterparty is not None:
        counterparty = _require_text(counterparty, "counterparty_account", max_length=32) or None
        if counterparty and not all(
            char.isascii() and (char.isalnum() or char == "-") for char in counterparty
        ):
            raise MessageFormatError("counterparty_account contains invalid characters")
    channel = values.get("channel") or "unknown"
    channel = _require_text(channel, "channel", max_length=32).lower()
    timestamp = _parse_timestamp(values.get("timestamp"))
    return TransferEvent(
        message_id=message_id,
        account_id=account_id,
        amount=amount,
        currency=currency,
        transaction_type=transaction_type,
        timestamp=timestamp,
        counterparty=counterparty,
        channel=channel,
        fmt=str(values.get("_fmt", "unknown")),
        source=str(values.get("_source", "kafka")),
    )


def parse_transfer_message(payload: bytes | str, *, source: str = "kafka") -> TransferEvent:
    """Decode one JSON or XML transfer message, raising ``MessageFormatError`` if malformed.

    XML is parsed with ``defusedxml`` so entity expansions, external entities and
    DTDs are rejected before any data is read.
    """
    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            raise MessageFormatError("payload is not valid UTF-8") from None
    else:
        text = payload
    stripped = text.lstrip()
    if not stripped:
        raise MessageFormatError("empty payload")
    if stripped.startswith("{"):
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            raise MessageFormatError("payload is not valid JSON") from None
        if not isinstance(document, dict):
            raise MessageFormatError("JSON payload is not an object")
        document["_fmt"], document["_source"] = "json", source
        return _validate_fields(document)
    if stripped.startswith("<"):
        try:
            root = DefusedElementTree.fromstring(text)
        except Exception:  # noqa: BLE001 - any defusedxml rejection is a malformed message
            raise MessageFormatError("payload is not valid XML") from None
        if root.tag != "transfer":
            raise MessageFormatError("XML root element must be <transfer>")
        document = {child.tag: (child.text or "") for child in root}
        document["_fmt"], document["_source"] = "xml", source
        return _validate_fields(document)
    raise MessageFormatError("payload is neither JSON nor XML")


def serialize_transfer_event(event: TransferEvent, *, fmt: str = "json") -> bytes:
    """Serialise an event for the trusted CLI producer (fictional values only)."""
    payload = {
        "message_id": event.message_id,
        "account_id": event.account_id,
        "counterparty_account": event.counterparty,
        "amount": event.amount,
        "currency": event.currency,
        "transaction_type": event.transaction_type,
        "timestamp": event.timestamp.isoformat(),
        "channel": event.channel,
    }
    if fmt == "xml":
        root = ElementTree.Element("transfer")
        for name, value in payload.items():
            if value is None:
                continue
            child = ElementTree.SubElement(root, name)
            child.text = (
                event.timestamp.isoformat()
                if name == "timestamp" and isinstance(value, datetime)
                else str(value)
            )
        return ElementTree.tostring(root, encoding="unicode").encode()
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


# --------------------------------------------------------------------------- #
# Session-scoped, bounded accumulation
# --------------------------------------------------------------------------- #
@dataclass
class EventStore:
    """Per-session event accumulation with hard bounds on memory use."""

    source: str = "sample"
    max_events: int = MAX_STORED_EVENTS
    events: deque[TransferEvent] = field(default_factory=lambda: deque(maxlen=MAX_STORED_EVENTS))
    errors: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_ERROR_HISTORY))
    raw_payloads: deque[dict[str, Any]] = field(
        default_factory=lambda: deque(maxlen=MAX_RAW_HISTORY)
    )
    total: int = 0
    parsed_ok: int = 0
    parse_errors: int = 0
    live_failures: int = 0
    last_error: str | None = None
    last_event_at: datetime | None = None
    started_at: datetime = field(default_factory=_now_utc)
    connected: bool = False
    # topic-partition -> next offset to read (per-session, never committed)
    offsets: dict[int, int] = field(default_factory=dict)

    def add(self, event: TransferEvent) -> None:
        self.events.append(event)
        self.total += 1
        self.parsed_ok += 1
        self.last_event_at = event.received_at

    def add_error(self, reason: str, preview: str, *, at: datetime | None = None) -> None:
        self.errors.append(
            {"at": at or _now_utc(), "reason": reason[:PREVIEW_CHARS], "preview": preview}
        )
        self.total += 1
        self.parse_errors += 1
        self.last_error = reason

    def add_raw(self, fmt: str, payload: bytes | str) -> None:
        self.raw_payloads.append({"fmt": fmt, "preview": _preview(payload)})

    def note_failure(self, reason: str) -> None:
        self.live_failures += 1
        self.connected = False
        self.last_error = reason

    @property
    def error_rate(self) -> float:
        return self.parse_errors / self.total if self.total else 0.0

    def clear(self, *, source: str | None = None) -> None:
        self.events.clear()
        self.errors.clear()
        self.raw_payloads.clear()
        self.total = self.parsed_ok = self.parse_errors = self.live_failures = 0
        self.last_error = None
        self.last_event_at = None
        self.started_at = _now_utc()
        self.connected = False
        self.offsets.clear()
        if source:
            self.source = source

    def events_frame(self) -> pd.DataFrame:
        if not self.events:
            return pd.DataFrame(
                columns=[
                    "received_at",
                    "message_id",
                    "account_id",
                    "counterparty",
                    "transaction_type",
                    "amount",
                    "currency",
                    "channel",
                    "fmt",
                    "source",
                    "timestamp",
                ]
            )
        return pd.DataFrame([event.row() for event in self.events])

    def errors_frame(self) -> pd.DataFrame:
        if not self.errors:
            return pd.DataFrame(columns=["at", "reason", "preview"])
        return pd.DataFrame(list(self.errors))

    def summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "total": self.total,
            "parsed_ok": self.parsed_ok,
            "parse_errors": self.parse_errors,
            "error_rate": self.error_rate,
            "live_failures": self.live_failures,
            "connected": self.connected,
            "last_error": self.last_error,
            "last_event_at": self.last_event_at,
            "stored_events": len(self.events),
        }


# --------------------------------------------------------------------------- #
# Live mode guards and configuration
# --------------------------------------------------------------------------- #
def live_settings() -> dict[str, Any]:
    bootstrap = (env_var("KAFKA_BOOTSTRAP_SERVERS") or DEFAULT_BOOTSTRAP).strip()
    topic = (env_var("KAFKA_TOPIC") or TOPIC).strip() or TOPIC
    return {
        "enabled": env_flag("KAFKA_ENABLED"),
        "bootstrap": bootstrap,
        "topic": topic,
    }


def parse_bootstrap(bootstrap: str) -> tuple[list[str], str, int]:
    """Validate a comma-separated bootstrap list, allowing loopback hosts only."""
    entries = [entry.strip() for entry in bootstrap.split(",") if entry.strip()]
    if not entries:
        raise KafkaUnavailableError("KAFKA_BOOTSTRAP_SERVERS is empty")
    hosts: list[str] = []
    port = DEFAULT_PORT
    for entry in entries:
        host, sep, port_text = entry.rpartition(":")
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        if not sep:
            host, port_text = entry, str(DEFAULT_PORT)
        if not is_loopback_host(host):
            raise KafkaUnavailableError(
                "broker address must be a loopback address (127.0.0.1 / localhost); "
                f"refusing '{mask_hostport(host, port_text)}'"
            )
        try:
            port = int(port_text)
        except ValueError:
            raise KafkaUnavailableError("broker port is not numeric") from None
        if not 1 <= port <= 65535:
            raise KafkaUnavailableError("broker port is out of range")
        hosts.append(host)
    return hosts, hosts[0], port


def live_available() -> tuple[bool, str]:
    """Return ``(available, reason)`` for live mode; the reason is bilingual."""
    settings = live_settings()
    if not settings["enabled"]:
        return (
            False,
            "Live mode is disabled: set KAFKA_ENABLED=true locally to allow a real Kafka "
            "connection. / ライブモードは無効です。KAFKA_ENABLED=true を"
            "ローカルで設定してください。",
        )
    try:
        _, host, port = parse_bootstrap(settings["bootstrap"])
    except KafkaUnavailableError as exc:
        return False, f"Live mode is disabled: {exc}"
    return True, f"broker {mask_hostport(host, port)} · topic {settings['topic']} (loopback only)"


def poll_live(
    store: EventStore,
    *,
    bootstrap: str | None = None,
    topic: str | None = None,
    max_messages: int = MAX_MESSAGES_PER_POLL,
    timeout_ms: int = POLL_TIMEOUT_MS,
    start: str = "latest",
) -> dict[str, Any]:
    """Run a single bounded, non-blocking read against the local broker.

    The consumer is created with ``group_id=None`` (a read-only public consumer
    that never joins a group or commits offsets), offsets are tracked in the
    caller's session store, and the consumer is always closed before returning.
    """
    settings = live_settings()
    bootstrap = bootstrap or settings["bootstrap"]
    topic = topic or settings["topic"]
    max_messages = max(1, min(int(max_messages), MAX_CLI_MESSAGES))
    timeout_ms = max(0, min(int(timeout_ms), MAX_CLI_TIMEOUT_MS))
    hosts, host, port = parse_bootstrap(bootstrap)

    try:
        from kafka import KafkaConsumer, TopicPartition
        from kafka.errors import OffsetOutOfRangeError
    except ImportError as exc:  # pragma: no cover - dependency is pinned in pyproject.toml
        raise KafkaUnavailableError("kafka-python is not installed") from exc

    consumer: KafkaConsumer | None = None
    try:
        consumer = KafkaConsumer(
            bootstrap_servers=[f"{h}:{port}" for h in hosts],
            group_id=None,
            enable_auto_commit=False,
            auto_offset_reset="latest",
            max_poll_records=max_messages,
            # Bound the eager API-version bootstrap so a dead broker fails fast
            # instead of blocking the UI for the 30s client default.
            bootstrap_timeout_ms=min(2000 + timeout_ms, 5000),
        )
        partitions = consumer.partitions_for_topic(topic)
        if not partitions:
            destination = mask_hostport(host, port)
            raise KafkaUnavailableError(f"topic '{topic}' does not exist on {destination}")
        tps = [TopicPartition(topic, p) for p in sorted(partitions)]
        consumer.assign(tps)
        ends = consumer.end_offsets(tps)
        for tp in tps:
            position = store.offsets.get(tp.partition)
            if position is None:
                position = consumer.beginning_offsets([tp])[tp] if start == "earliest" else ends[tp]
                store.offsets[tp.partition] = position
            consumer.seek(tp, position)
        try:
            batches = consumer.poll(timeout_ms=timeout_ms, max_records=max_messages)
        except OffsetOutOfRangeError:
            # Retention deleted our position; resynchronise to the live edge.
            for tp in tps:
                store.offsets[tp.partition] = ends[tp]
                consumer.seek(tp, ends[tp])
            batches = consumer.poll(timeout_ms=timeout_ms, max_records=max_messages)
        received = parsed = malformed = 0
        for tp in tps:
            for record in batches.get(tp, []):
                received += 1
                store.add_raw("wire", record.value)
                try:
                    event = parse_transfer_message(record.value, source="kafka")
                except MessageFormatError as exc:
                    store.add_error(str(exc), _preview(record.value))
                    malformed += 1
                else:
                    store.add(event)
                    parsed += 1
                store.offsets[tp.partition] = record.offset + 1
        store.connected = True
        store.source = "kafka"
        return {
            "status": "ok",
            "broker": mask_hostport(host, port),
            "topic": topic,
            "received": received,
            "parsed": parsed,
            "malformed": malformed,
            "partitions": len(tps),
        }
    except KafkaUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface any broker failure as unavailable
        detail = str(exc) or type(exc).__name__
        if not detail.startswith(type(exc).__name__):
            detail = f"{type(exc).__name__}: {detail}"
        reason = detail.split(" at ")[0][:PREVIEW_CHARS]
        raise KafkaUnavailableError(f"Kafka unavailable ({reason})") from exc
    finally:
        if consumer is not None:
            consumer.close()


def send_test_messages(
    *,
    bootstrap: str | None = None,
    topic: str | None = None,
    count: int = 25,
    seed: int = 7,
    fmt: str = "mixed",
    error_rate: float = 0.0,
) -> dict[str, Any]:
    """Trusted CLI producer for local verification; disabled in PUBLIC_DEMO mode.

    Never accepts a non-loopback destination, and is intentionally not reachable
    from the demo UI: the public demo must not publish messages.
    """
    require_writes_enabled()
    settings = live_settings()
    bootstrap = bootstrap or settings["bootstrap"]
    topic = topic or settings["topic"]
    count = max(1, min(int(count), MAX_CLI_MESSAGES))
    error_rate = min(max(float(error_rate), 0.0), 0.5)
    _, host, port = parse_bootstrap(bootstrap)
    rng = random.Random(seed)
    at = _now_utc()

    try:
        from kafka import KafkaProducer
    except ImportError as exc:  # pragma: no cover
        raise KafkaUnavailableError("kafka-python is not installed") from exc

    producer: KafkaProducer | None = None
    sent = malformed_planted = 0
    try:
        producer = KafkaProducer(bootstrap_servers=[f"{host}:{port}"], linger_ms=50)
        for index in range(count):
            planted = rng.random() < error_rate
            _, payload = sample_message(
                index,
                rng,
                fmt="xml" if (fmt == "xml" or (fmt == "mixed" and index % 2)) else "json",
                malformed=random.choice(MALFORMED_KINDS) if planted else None,
                at=at,
            )
            # Values are pre-encoded JSON/XML bytes; keys keep per-account order.
            future = producer.send(topic, key=_sample_account(rng).encode(), value=payload)
            future.get(timeout=10)
            sent += 1
            malformed_planted += int(planted)
        producer.flush(timeout=10)
    except Exception as exc:  # noqa: BLE001 - surface any broker failure as unavailable
        detail = str(exc) or type(exc).__name__
        if not detail.startswith(type(exc).__name__):
            detail = f"{type(exc).__name__}: {detail}"
        reason = detail.split(" at ")[0][:PREVIEW_CHARS]
        raise KafkaUnavailableError(f"Kafka unavailable ({reason})") from exc
    finally:
        if producer is not None:
            producer.close()
    return {
        "status": "ok",
        "broker": mask_hostport(host, port),
        "topic": topic,
        "sent": sent,
        "malformed_planted": malformed_planted,
        "format": fmt,
    }


# --------------------------------------------------------------------------- #
# Sample mode: local synthetic stream, never presented as real Kafka
# --------------------------------------------------------------------------- #
def _sample_account(rng: random.Random) -> str:
    return f"ACC-{rng.randint(0, 999_999):06d}"


def _sample_amount(rng: random.Random) -> int:
    if rng.random() < 0.05:  # near the JPY 1M reporting threshold, for AML flavour
        return 950_000 + rng.randint(0, 40_000)
    return max(1, round(min(rng.lognormvariate(8.8, 0.7), 800_000)))


def sample_message(
    index: int,
    rng: random.Random,
    *,
    fmt: str = "json",
    malformed: str | None = None,
    at: datetime | None = None,
) -> tuple[str, bytes]:
    """Build one deterministic, clearly-fictional transfer payload.

    ``malformed`` plants a broken payload so error-rate handling can be
    demonstrated without any real system being involved.
    """
    at = at or _now_utc()
    base = TransferEvent(
        message_id=f"MSG-{int(at.timestamp())}-{index:06d}",
        account_id=_sample_account(rng),
        amount=_sample_amount(rng),
        currency="JPY",
        transaction_type=rng.choices(
            sorted(VALID_TRANSACTION_TYPES), weights=[0.25, 0.25, 0.3, 0.2], k=1
        )[0],
        timestamp=at,
        counterparty=_sample_account(rng) if rng.random() < 0.55 else None,
        channel=rng.choice(VALID_CHANNELS),
        fmt=fmt,
        source="sample",
    )
    payload = serialize_transfer_event(base, fmt=fmt)
    if malformed == "truncated":
        return fmt, payload[: max(1, len(payload) // 2)]
    if malformed == "invalid-json":
        return "json", b"{not-json"
    if malformed == "invalid-xml":
        return "xml", b"<transfer><amount>broken"
    if malformed == "bad-amount":
        values = {"amount": -5000, "fmt": fmt, "source": "sample"}
        return _apply_malformed(base, values, fmt)
    if malformed == "bad-type":
        return _apply_malformed(base, {"transaction_type": "TRANSFER_SIDEWAYS"}, fmt)
    if malformed == "bad-timestamp":
        return _apply_malformed(base, {"timestamp": "2026-13-45T99:99:99+09:00"}, fmt)
    if malformed == "empty":
        return fmt, b""
    return fmt, payload


def _apply_malformed(base: TransferEvent, overrides: dict[str, Any], fmt: str) -> tuple[str, bytes]:
    document = {
        "message_id": base.message_id,
        "account_id": base.account_id,
        "counterparty_account": base.counterparty,
        "amount": base.amount,
        "currency": base.currency,
        "transaction_type": base.transaction_type,
        "timestamp": base.timestamp.isoformat(),
        "channel": base.channel,
    }
    document.update(overrides)
    if fmt == "xml":
        root = ElementTree.Element("transfer")
        for name, value in document.items():
            if value is None:
                continue
            child = ElementTree.SubElement(root, name)
            child.text = str(value)
        return "xml", ElementTree.tostring(root, encoding="unicode").encode()
    return "json", json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode()


def sample_tick(
    store: EventStore,
    *,
    rng: random.Random | None = None,
    batch_size: int | None = None,
    error_rate: float = 0.08,
    at: datetime | None = None,
) -> dict[str, Any]:
    """Generate and parse one local batch of sample messages (never real Kafka)."""
    rng = rng or random.Random()
    at = at or _now_utc()
    count = batch_size if batch_size is not None else rng.randint(1, 5)
    count = max(1, min(count, 25))
    received = parsed = malformed = 0
    for index in range(count):
        wire_fmt = "xml" if rng.random() < 0.35 else "json"
        plant_error = rng.random() < error_rate
        declared, payload = sample_message(
            index,
            rng,
            fmt=wire_fmt,
            malformed=rng.choice(MALFORMED_KINDS) if plant_error else None,
            at=at,
        )
        received += 1
        store.add_raw(declared, payload)
        try:
            event = parse_transfer_message(payload, source="sample")
        except MessageFormatError as exc:
            store.add_error(str(exc), _preview(payload), at=at)
            malformed += 1
        else:
            store.add(event)
            parsed += 1
    store.source = "sample"
    return {
        "status": "ok",
        "source": "sample",
        "received": received,
        "parsed": parsed,
        "malformed": malformed,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bootstrap", default=None, help="host:port list (loopback only; default from env)"
    )
    parser.add_argument("--topic", default=None, help=f"topic (default {TOPIC})")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo_tools.kafka_io",
        description="Local Kafka transfer-message tooling (loopback brokers only)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    produce = subparsers.add_parser("produce", help="publish synthetic transfer messages (local)")
    _add_common_arguments(produce)
    produce.add_argument("--count", type=int, default=25, help=f"messages (1-{MAX_CLI_MESSAGES})")
    produce.add_argument("--seed", type=int, default=7)
    produce.add_argument("--format", choices=("json", "xml", "mixed"), default="mixed")
    produce.add_argument(
        "--error-rate", type=float, default=0.0, help="share of deliberately malformed messages"
    )

    consume = subparsers.add_parser("consume", help="run one bounded read and print a summary")
    _add_common_arguments(consume)
    consume.add_argument("--max-messages", type=int, default=100)
    consume.add_argument("--timeout-ms", type=int, default=2000)
    consume.add_argument("--from", dest="start", choices=("latest", "earliest"), default="earliest")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "produce":
            result = send_test_messages(
                bootstrap=args.bootstrap,
                topic=args.topic,
                count=args.count,
                seed=args.seed,
                fmt=args.format,
                error_rate=args.error_rate,
            )
        else:
            store = EventStore(source="kafka")
            result = poll_live(
                store,
                bootstrap=args.bootstrap,
                topic=args.topic,
                max_messages=args.max_messages,
                timeout_ms=args.timeout_ms,
                start=args.start,
            )
            result["summary"] = store.summary()
            result["recent_events"] = [
                {key: str(value) for key, value in event.row().items()}
                for event in list(store.events)[-5:]
            ]
    except (KafkaUnavailableError, PermissionError) as exc:
        print(json.dumps({"status": "unavailable", "error": str(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
