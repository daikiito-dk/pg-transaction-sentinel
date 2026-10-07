"""AppTest coverage for the synthetic data generator scene (demos/generator.py)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from streamlit.testing.v1 import AppTest

SCRIPT = "from demos.generator import render\nrender()"


def _render() -> AppTest:
    return AppTest.from_string(SCRIPT).run()


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


def _clear_environment(monkeypatch) -> None:
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    monkeypatch.delenv("LOCAL_TOOL_WRITES", raising=False)
    monkeypatch.delenv("KEYCLOAK_ADMIN_PASSWORD", raising=False)


def test_preview_defaults_to_a_bounded_deterministic_batch(monkeypatch):
    _clear_environment(monkeypatch)
    at = _render()
    assert not at.exception
    batch = at.session_state["gen_batch"]
    assert batch.batch_id == "demo-42"
    assert batch.params.days == 30
    assert 0 < batch.total_rows <= 600
    assert len(at.dataframe) == 3  # one preview table per dataset tab


def test_regenerating_with_new_seed_produces_a_new_batch(monkeypatch):
    _clear_environment(monkeypatch)
    at = _render()
    at.number_input(key="gen_seed").set_value(99).run()
    at.button(key="gen_refresh").click().run()
    batch = at.session_state["gen_batch"]
    assert batch.params.seed == 99 and batch.batch_id == "demo-99"


def test_sandbox_write_section_is_disabled_without_local_opt_in(monkeypatch):
    _clear_environment(monkeypatch)
    at = _render()
    infos = [element.value for element in at.info]
    assert any("Inserts are disabled here" in message for message in infos)
    assert "gen_result" not in at.session_state


def test_public_demo_disables_the_sandbox_section(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "true")
    monkeypatch.setenv("LOCAL_TOOL_WRITES", "true")  # even with the opt-in, public demo wins
    at = _render()
    assert not at.exception
    infos = [element.value for element in at.info]
    assert any("Inserts are disabled here" in message for message in infos)


@requires_local_db
def test_confirmed_sandbox_insert_is_idempotent_and_reported_honestly(monkeypatch):
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    monkeypatch.setenv("LOCAL_TOOL_WRITES", "true")

    import config

    engine = create_engine(config.get_database_url(), connect_args={"connect_timeout": 2})
    with engine.connect() as conn:
        before_accounts = conn.execute(text("SELECT COUNT(*) FROM accounts")).scalar()

    at = _render()
    at.checkbox(key="gen_confirm").set_value(True).run()
    at.button(key="gen_insert").click().run()
    first = at.session_state["gen_result"]
    assert first["attempted"] == at.session_state["gen_batch"].total_rows
    assert first["inserted"] + first["skipped"] == first["attempted"]

    at.button(key="gen_insert").click().run()  # same batch, generated keys: everything skipped
    second = at.session_state["gen_result"]
    assert second["inserted"] == 0 and second["skipped"] == first["attempted"]

    success_text = " ".join(element.value for element in at.success)
    assert "demo_sandbox" in success_text and "idempotent" in success_text

    with engine.connect() as conn:
        after_accounts = conn.execute(text("SELECT COUNT(*) FROM accounts")).scalar()
        stored = conn.execute(
            text("SELECT COUNT(*) FROM demo_sandbox.batches WHERE batch_id = 'demo-42'")
        ).scalar()
    assert before_accounts == after_accounts  # AML tables untouched
    assert stored == 1


def test_keycloak_section_disabled_without_writes_or_password(monkeypatch):
    _clear_environment(monkeypatch)
    at = _render()
    infos = [element.value for element in at.info]
    assert any("Provisioning is disabled" in message for message in infos)


def test_keycloak_provisioning_reports_users_but_never_passwords(monkeypatch):
    from demo_tools.keycloak import ProvisioningResult

    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    monkeypatch.setenv("LOCAL_TOOL_WRITES", "true")
    monkeypatch.setenv("KEYCLOAK_ADMIN_PASSWORD", "generated-in-memory")

    def fake_provision(**kwargs):
        return ProvisioningResult(
            realm="sentinel-demo",
            url="http://127.0.0.1:8080",
            created_realm=True,
            roles=["aml-analyst"],
            users=[{"username": "demo-analyst", "role": "aml-analyst", "enabled": True}],
        )

    monkeypatch.setattr("demo_tools.keycloak.provision_test_users", fake_provision)
    at = _render()
    at.button(key="gen_keycloak").click().run()
    assert not at.exception
    summary = at.session_state["gen_keycloak_result"]
    assert summary["realm"] == "sentinel-demo"
    success_text = " ".join(element.value for element in at.success)
    assert "demo-analyst" in success_text
    assert "generated-in-memory" not in success_text
    markdown = " ".join(element.value for element in at.markdown)
    captions = " ".join(element.value for element in at.caption)
    assert "generated-in-memory" not in markdown + captions
    assert "not displayed or logged" in captions
    assert "一時パスワード" in captions


def test_reset_clears_generated_batch_and_results(monkeypatch):
    _clear_environment(monkeypatch)
    at = _render()
    assert "gen_batch" in at.session_state
    at.button(key="generator_reset").click().run()
    # Session-owned state is cleared; a fresh default preview regenerates on
    # the next visit because preview-without-writes is the safe default.
    assert "gen_result" not in at.session_state
    assert "gen_confirm" not in at.session_state
    assert "gen_keycloak_result" not in at.session_state
    assert at.session_state["gen_batch"].total_rows > 0
