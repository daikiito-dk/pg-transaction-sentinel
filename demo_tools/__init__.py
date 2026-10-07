"""Local-only demo tooling: Kafka monitor I/O, synthetic data generator, Keycloak provisioning.

Every helper in this package is deliberately conservative:

* network destinations are restricted to loopback addresses,
* writes require the explicit ``LOCAL_TOOL_WRITES=true`` opt-in in addition to
  ``PUBLIC_DEMO`` being disabled, and
* credentials are read from the environment (optionally ``.env.tools``, which is
  never committed) and are never logged or embedded in error messages.

Environment variables understood by this package (see ``.env.tools.example``):

``PUBLIC_DEMO``          when true, every tool write is refused.
``LOCAL_TOOL_WRITES``    additional explicit opt-in required for local writes.
``KAFKA_ENABLED``       admin opt-in that enables the live Kafka monitor.
``KAFKA_BOOTSTRAP_SERVERS`` / ``KAFKA_TOPIC`` live monitor destination (loopback only).
``KEYCLOAK_URL`` / ``KEYCLOAK_ADMIN`` / ``KEYCLOAK_ADMIN_PASSWORD``
                         optional local Keycloak provisioning target and admin login.
"""

from __future__ import annotations

import os
from ipaddress import ip_address
from pathlib import Path

from dotenv import load_dotenv

# Local secrets file for tool credentials only. It is intentionally not tracked by
# git (see .gitignore) and never ships with the repository; the tracked
# ``.env.tools.example`` contains empty credential entries.
_TOOLS_ENV_PATH = Path(__file__).resolve().parent.parent / ".env.tools"

load_dotenv(_TOOLS_ENV_PATH)  # never overrides variables already present in the process
load_dotenv(_TOOLS_ENV_PATH.parent / ".env")  # same file config.py loads, for shared settings

_TRUTHY = {"1", "true", "yes", "on"}


def env_flag(name: str, *, default: bool = False) -> bool:
    """Read a boolean environment flag, mirroring config.PUBLIC_DEMO semantics."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUTHY


def is_loopback_host(host: str) -> bool:
    """True only for IPv4/IPv6 loopback addresses or the literal ``localhost``.

    Hostnames other than ``localhost`` (including internal Docker DNS names such
    as ``kafka``) are rejected so the demo UI can never be pointed at an
    arbitrary or remote destination.
    """
    candidate = (host or "").strip().strip("[]").lower()
    if candidate == "localhost":
        return True
    if not candidate:
        return False
    try:
        address = ip_address(candidate)
    except ValueError:
        return False
    return address.is_loopback


def mask_hostport(host: str, port: str | int | None = None) -> str:
    """Human-readable ``host:port`` display for UI text; credentials never appear."""
    return f"{host}:{port}" if port else str(host)


def require_writes_enabled() -> None:
    """Refuse tool writes in public demo mode or without the local opt-in.

    Raises ``PermissionError`` describing exactly which guard failed, without
    ever echoing configuration values.
    """
    if env_flag("PUBLIC_DEMO"):
        raise PermissionError(
            "Tool writes are disabled: PUBLIC_DEMO=true. / "
            "PUBLIC_DEMO=true のためツールの書き込みは無効です。"
        )
    if not env_flag("LOCAL_TOOL_WRITES"):
        raise PermissionError(
            "Tool writes require LOCAL_TOOL_WRITES=true in the local environment. / "
            "ツールの書き込みには LOCAL_TOOL_WRITES=true が必要です。"
        )
