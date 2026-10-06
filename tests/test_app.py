import pytest
from sqlalchemy import text

from config import get_engine


def _scores_available() -> bool:
    try:
        with get_engine().connect() as conn:
            return conn.execute(text("SELECT count(*) FROM transaction_risk_scores")).scalar() > 0
    except Exception:
        return False


@pytest.mark.skipif(not _scores_available(), reason="requires PostgreSQL with scored data")
def test_dashboard_renders_without_errors():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("../app.py", default_timeout=60).run()
    assert not at.exception
    assert any("Alert queue" in m.value for m in at.markdown)
    assert any("Detection reasons" in m.value for m in at.markdown)
