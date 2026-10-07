"""Optional, loopback-only Keycloak provisioning for fictional demo test users.

Provisions the fictional ``sentinel-demo`` realm with demo roles and test users
against a *local* Keycloak instance (default ``http://127.0.0.1:8080``).

Safety properties:

* the admin password is read from ``KEYCLOAK_ADMIN_PASSWORD`` in the environment
  or the untracked ``.env.tools`` file — never from a tracked file, and never a
  static default;
* test users are enabled but carry no static credentials: provisioning sets a
  randomly generated, temporary (must-change-at-first-login) password that is
  returned in memory only and never printed;
* the provisioning URL must be loopback, writes require ``LOCAL_TOOL_WRITES``
  (and ``PUBLIC_DEMO`` disabled), and all HTTP calls are bounded by a timeout;
* tokens and passwords never appear in error messages, logs, or CLI output.

CLI::

    uv run python -m demo_tools.keycloak status
    uv run python -m demo_tools.keycloak provision
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import requests

from demo_tools import env_flag, is_loopback_host, mask_hostport, require_writes_enabled

REALM = "sentinel-demo"
DEFAULT_URL = "http://127.0.0.1:8080"
DEMO_ROLES = ("aml-analyst", "branch-staff", "demo-observer")
ROLE_DESCRIPTIONS = {
    "aml-analyst": "Fictional AML analyst role for the demo",
    "branch-staff": "Fictional branch staff role for the demo",
    "demo-observer": "Fictional read-only observer role for the demo",
}
# Fictional personas only: .invalid e-mail addresses, no real personal data.
TEST_USERS = (
    {
        "username": "demo-analyst",
        "role": "aml-analyst",
        "email": "demo.analyst@sentinel.example.invalid",
        "firstName": "Anzu",
        "lastName": "Yamada",
    },
    {
        "username": "demo-branch",
        "role": "branch-staff",
        "email": "demo.branch@sentinel.example.invalid",
        "firstName": "Hikaru",
        "lastName": "Sato",
    },
    {
        "username": "demo-observer",
        "role": "demo-observer",
        "email": "demo.observer@sentinel.example.invalid",
        "firstName": "Rin",
        "lastName": "Kobayashi",
    },
)
_TIMEOUT = 8.0


class KeycloakUnavailableError(RuntimeError):
    """Raised when the local Keycloak admin API cannot be reached or fails."""


class ToolDisabledError(PermissionError):
    """Raised when provisioning is refused by a server-side guard."""


@dataclass
class ProvisioningResult:
    """Provisioning outcome; passwords are kept in memory and never logged."""

    realm: str
    url: str
    created_realm: bool
    roles: list[str] = field(default_factory=list)
    users: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """Credential-free summary for UI/CLI output."""
        return {
            "realm": self.realm,
            "url": self.url,
            "created_realm": self.created_realm,
            "roles": self.roles,
            "users": [
                {"username": user["username"], "role": user["role"], "enabled": True}
                for user in self.users
            ],
        }


def keycloak_settings() -> dict[str, Any]:
    url = (os.getenv("KEYCLOAK_URL") or DEFAULT_URL).strip()
    admin = (os.getenv("KEYCLOAK_ADMIN") or "demo-admin").strip() or "demo-admin"
    password = os.getenv("KEYCLOAK_ADMIN_PASSWORD") or ""
    return {
        "url": url,
        "admin": admin,
        "password": password,
        "writes_enabled": env_flag("LOCAL_TOOL_WRITES") and not env_flag("PUBLIC_DEMO"),
    }


def _url_display(url: str) -> str:
    split = urlsplit(url)
    return f"http://{mask_hostport(split.hostname or '127.0.0.1', split.port or 8080)}"


def _check_loopback(url: str) -> None:
    split = urlsplit(url)
    if split.scheme not in ("http", "https"):
        raise ToolDisabledError("KEYCLOAK_URL must be an http(s) URL")
    if not is_loopback_host(split.hostname or ""):
        raise ToolDisabledError(
            f"Keycloak URL must be loopback; refusing '{mask_hostport(split.hostname, split.port)}'"
        )


def _require_guards(url: str, password: str) -> None:
    try:
        require_writes_enabled()
    except PermissionError as exc:
        raise ToolDisabledError(str(exc)) from exc
    _check_loopback(url)
    if not password:
        raise ToolDisabledError(
            "KEYCLOAK_ADMIN_PASSWORD is not set (put it in the untracked .env.tools) / "
            "KEYCLOAK_ADMIN_PASSWORD が未設定です（追跡対象外の .env.tools に設定してください）"
        )


def _request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    data: dict[str, str] | None = None,
    operation: str = "",
) -> requests.Response:
    try:
        response = session.request(
            method, url, headers=headers, json=json_body, data=data, timeout=_TIMEOUT
        )
    except requests.RequestException as exc:
        reason = f"{type(exc).__name__}".split(" at ")[0]
        raise KeycloakUnavailableError(f"Keycloak unreachable ({reason})") from exc
    if operation and not response.ok:
        raise KeycloakUnavailableError(f"Keycloak {operation} failed (HTTP {response.status_code})")
    return response


def obtain_admin_token(session: requests.Session, base_url: str, admin: str, password: str) -> str:
    """Fetch an admin-cli token; the password is never echoed in errors."""
    response = _request(
        session,
        "POST",
        f"{base_url.rstrip('/')}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": admin,
            "password": password,
        },
        operation="admin login",
    )
    token = response.json().get("access_token")
    if not token:
        raise KeycloakUnavailableError("Keycloak admin login returned no token")
    return token


def _realm_payload() -> dict[str, Any]:
    return {
        "realm": REALM,
        "enabled": True,
        "sslRequired": "external",
        "registrationAllowed": False,
        "resetPasswordAllowed": False,
        "rememberMe": False,
        "loginWithEmailAllowed": True,
        "verifyEmail": False,
    }


def _ensure_role_mapping(
    session: requests.Session,
    admin_url: Any,
    headers: dict[str, str],
    user_id: str,
    role: str,
) -> None:
    """Map a realm role to the user, idempotently.

    Keycloak 26 rejects role-mapping POSTs that only carry the role name, so the
    full role representation (id + name) is posted. If the mapping already
    exists (e.g. the realm was imported with roles pre-assigned), nothing is
    changed.
    """
    mappings = _request(
        session, "GET", admin_url(f"/{REALM}/users/{user_id}/role-mappings/realm"), headers=headers
    )
    if mappings.ok and any(mapped.get("name") == role for mapped in mappings.json()):
        return
    representation = _request(session, "GET", admin_url(f"/{REALM}/roles/{role}"), headers=headers)
    if not representation.ok:
        raise KeycloakUnavailableError(
            f"Keycloak role lookup failed (HTTP {representation.status_code})"
        )
    _request(
        session,
        "POST",
        admin_url(f"/{REALM}/users/{user_id}/role-mappings/realm"),
        headers=headers,
        json_body=[representation.json()],
        operation="role assignment",
    )


def _user_payload(user: dict[str, str]) -> dict[str, Any]:
    """Payload for a *credential-less* user: enabled, must set password on login."""
    return {
        "username": user["username"],
        "email": user["email"],
        "enabled": True,
        "emailVerified": True,
        "firstName": user["firstName"],
        "lastName": user["lastName"],
        "requiredActions": ["UPDATE_PASSWORD"],
        "realmRoles": [user["role"]],
    }


def provision_test_users(
    *,
    base_url: str | None = None,
    admin: str | None = None,
    password: str | None = None,
    session: requests.Session | None = None,
) -> ProvisioningResult:
    """Create (idempotently) the demo realm, roles and test users with temp passwords."""
    settings = keycloak_settings()
    base_url = (base_url or settings["url"]).rstrip("/")
    admin = admin or settings["admin"]
    password = password if password is not None else settings["password"]
    _require_guards(base_url, password)

    session = session or requests.Session()
    token = obtain_admin_token(session, base_url, admin, password)
    headers = {"Authorization": f"Bearer {token}"}  # token never leaves this scope

    def admin_url(path: str) -> str:
        return f"{base_url}/admin/realms{path}"

    result = ProvisioningResult(realm=REALM, url=_url_display(base_url), created_realm=False)

    realm = _request(session, "GET", admin_url(f"/{REALM}"), headers=headers)
    if realm.status_code == 404:
        _request(
            session,
            "POST",
            admin_url(""),
            headers=headers,
            json_body=_realm_payload(),
            operation="realm creation",
        )
        result.created_realm = True
    elif not realm.ok:
        raise KeycloakUnavailableError(f"Keycloak realm lookup failed (HTTP {realm.status_code})")

    for role in DEMO_ROLES:
        existing = _request(session, "GET", admin_url(f"/{REALM}/roles/{role}"), headers=headers)
        if existing.status_code == 404:
            _request(
                session,
                "POST",
                admin_url(f"/{REALM}/roles"),
                headers=headers,
                json_body={"name": role, "description": ROLE_DESCRIPTIONS[role]},
                operation="role creation",
            )
        result.roles.append(role)

    for user in TEST_USERS:
        username = user["username"]
        lookup_url = f"{admin_url(f'/{REALM}/users')}?username={username}&exact=true"
        found = _request(session, "GET", lookup_url, headers=headers)
        if found.status_code != 200:
            status = found.status_code
            raise KeycloakUnavailableError(f"Keycloak user lookup failed (HTTP {status})")
        matches = found.json()
        if matches:
            user_id = matches[0]["id"]
            created_now = False
        else:
            _request(
                session,
                "POST",
                admin_url(f"/{REALM}/users"),
                headers=headers,
                json_body=_user_payload(user),
                operation="user creation",
            )
            lookup = _request(session, "GET", lookup_url, headers=headers)
            if lookup.status_code != 200 or not lookup.json():
                raise KeycloakUnavailableError("Keycloak user creation could not be confirmed")
            user_id = lookup.json()[0]["id"]
            created_now = True
        _ensure_role_mapping(session, admin_url, headers, user_id, user["role"])
        temporary_password = secrets.token_urlsafe(18)
        _request(
            session,
            "PUT",
            admin_url(f"/{REALM}/users/{user_id}/reset-password"),
            headers=headers,
            json_body={"type": "password", "value": temporary_password, "temporary": True},
            operation="password reset",
        )
        result.users.append(
            {
                "username": username,
                "user_id": user_id,
                "role": user["role"],
                "created": created_now,
                "temporary_password": temporary_password,
            }
        )
    return result


def realm_status(
    *,
    base_url: str | None = None,
    admin: str | None = None,
    password: str | None = None,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Read-only status of the demo realm (no credentials in the output)."""
    settings = keycloak_settings()
    base_url = (base_url or settings["url"]).rstrip("/")
    admin = admin or settings["admin"]
    password = password if password is not None else settings["password"]
    _require_guards(base_url, password)
    session = session or requests.Session()
    token = obtain_admin_token(session, base_url, admin, password)
    headers = {"Authorization": f"Bearer {token}"}
    realm = _request(session, "GET", f"{base_url}/admin/realms/{REALM}", headers=headers)
    if realm.status_code == 404:
        return {"realm": REALM, "exists": False, "url": _url_display(base_url)}
    if not realm.ok:
        raise KeycloakUnavailableError(f"Keycloak realm lookup failed (HTTP {realm.status_code})")
    users = _request(
        session, "GET", f"{base_url}/admin/realms/{REALM}/users?max=100", headers=headers
    )
    return {
        "realm": REALM,
        "exists": True,
        "enabled": bool(realm.json().get("enabled")),
        "user_count": len(users.json()) if users.ok else -1,
        "url": _url_display(base_url),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo_tools.keycloak",
        description="Local Keycloak demo-identity provisioning (loopback only)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="report whether the demo realm exists")
    subparsers.add_parser("provision", help="ensure realm, roles and test users")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "provision":
            result = provision_test_users()
            payload = result.summary()
            payload["note"] = (
                "Generated temporary passwords are kept in memory only and are not printed; "
                "users must set a new password at first login."
            )
        else:
            payload = realm_status()
    except (ToolDisabledError, PermissionError, KeycloakUnavailableError) as exc:
        print(json.dumps({"status": "unavailable", "error": str(exc)}))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
