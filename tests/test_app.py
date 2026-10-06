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
