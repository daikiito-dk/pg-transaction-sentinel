import pytest
from sqlalchemy import text

from config import get_engine


def _scores_available() -> bool:
    try:
        with get_engine().connect() as conn:
            return conn.execute(text("SELECT count(*) FROM transaction_risk_scores")).scalar() > 0
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _scores_available(), reason="requires PostgreSQL with scored data"
)


def _rescore_button(at):
    return next(b for b in at.sidebar.button if b.label == "Re-score transactions")


@requires_db
def test_dashboard_renders_without_errors():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("../app.py", default_timeout=60).run()
    assert not at.exception
    assert any("Alert queue" in m.value for m in at.markdown)
    assert any("Detection reasons" in m.value for m in at.markdown)
    assert not _rescore_button(at).disabled


@requires_db
def test_public_demo_disables_rescoring(monkeypatch):
    from streamlit.testing.v1 import AppTest

    import config

    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    at = AppTest.from_file("../app.py", default_timeout=60).run()
    assert not at.exception
    assert _rescore_button(at).disabled
    assert any("Public demo" in c.value for c in at.caption)


@requires_db
def test_customer_scene_and_return_to_aml():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("../app.py", default_timeout=60).run()
    at.button(key="scene_customer").click().run()
    assert not at.exception
    assert any("Account overview" in m.value for m in at.markdown)
    account_picker = at.selectbox(key="customer_account")
    expected_account = (
        "ACC-000002"
        if any(option.startswith("ACC-000002 · ") for option in account_picker.options)
        else account_picker.options[0].split(" · ", 1)[0]
    )
    assert account_picker.value == expected_account
    assert not any(b.label == "Re-score transactions" for b in at.sidebar.button)
    assert any("Illustrative balance" in m.value for m in at.markdown)
    at.button(key="customer_nav_statement").click().run()
    assert len(at.metric) == 3
    statement = next(m.value for m in at.markdown if 'aria-label="Transaction statement' in m.value)
    assert "risk_score" not in statement
    assert "Detection Reasons" not in statement
    accounts = at.selectbox(key="customer_account").options
    assert accounts
    selected_account = accounts[-1].split(" · ", 1)[0]
    at.selectbox(key="customer_account").set_value(selected_account).run()
    assert not at.exception
    at.button(key="scene_aml").click().run()
    assert not at.exception
    assert any("Alert queue" in m.value for m in at.markdown)
    assert _rescore_button(at)
    assert not any("color-scheme: light" in item.value for item in at.markdown)


@requires_db
def test_branch_customer_search_and_empty_results():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("../app.py", default_timeout=60).run()
    at.button(key="scene_branch").click().run()
    selected_account = at.selectbox(key="branch_account").value
    at.text_input(key="branch_search").set_value(selected_account).run()
    assert not at.exception
    assert at.selectbox(key="branch_account").value == selected_account
    assert any("Branch customer inquiry" in m.value for m in at.markdown)
    at.text_input(key="branch_search").set_value("no-such-customer").run()
    assert not at.exception
    assert any("No matching customers" in item.value for item in at.info)


@requires_db
def test_invalid_scene_falls_back_to_aml():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("../app.py", default_timeout=60)
    at.query_params["scene"] = "invalid"
    at.run()
    assert not at.exception
    assert any("Alert queue" in m.value for m in at.markdown)


@requires_db
@pytest.mark.parametrize(
    ("scene", "title"),
    [("customer", "Account overview"), ("branch", "Branch customer inquiry")],
)
def test_public_demo_direct_scene_links(monkeypatch, scene, title):
    from streamlit.testing.v1 import AppTest

    import config

    monkeypatch.setattr(config, "PUBLIC_DEMO", True)
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.query_params["scene"] = scene
    at.run()
    assert not at.exception
    assert any(title in item.value for item in at.markdown)
    assert not any(button.label == "Re-score transactions" for button in at.sidebar.button)
    assert any("not authentication" in item.value for item in at.caption)


def _database_totals():
    with get_engine().connect() as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT (SELECT count(*) FROM transactions), "
                    "(SELECT sum(amount) FROM transactions), "
                    "(SELECT count(*) FROM transaction_risk_scores), "
                    "(SELECT sum(risk_score) FROM transaction_risk_scores)"
                )
            ).one()
        )


@requires_db
def test_customer_dummy_transfer_and_services_do_not_change_database():
    from streamlit.testing.v1 import AppTest

    before = _database_totals()
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.query_params["scene"] = "customer"
    at.run()
    at.button(key="customer_shortcut_transfer").click().run()
    assert not at.exception
    assert all(item.disabled for item in at.text_input)
    assert at.number_input[0].disabled
    next(button for button in at.button if "Review sample" in button.label).click().run()
    assert any("No payment" in item.value for item in at.info)
    at.button(key="customer_nav_services").click().run()
    at.button(key="customer_service_card").click().run()
    assert not at.exception
    assert any("no application" in item.value for item in at.info)
    assert _database_totals() == before


@requires_db
def test_branch_request_preview_does_not_change_database():
    from streamlit.testing.v1 import AppTest

    before = _database_totals()
    at = AppTest.from_file("../app.py", default_timeout=60)
    at.query_params["scene"] = "branch"
    at.run()
    at.button(key="branch_nav_requests").click().run()
    at.button(key="branch_request_preview").click().run()
    assert not at.exception
    assert any("No request was updated" in item.value for item in at.info)
    assert _database_totals() == before
