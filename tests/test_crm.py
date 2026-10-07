from datetime import date

import pytest

from demos.crm import LOST, WON, sample_deals, summarize_deals, validate_deal


def test_pipeline_excludes_closed_deals_and_uses_explicit_stage_weights():
    deals = sample_deals(date(2026, 10, 7))
    summary = summarize_deals(deals)
    assert summary["open_count"] == 8
    assert summary["win_rate"] == 0.5
    assert summary["weighted"] < summary["pipeline"]
    assert summarize_deals([{"stage": WON, "amount": 10}])["pipeline"] == 0
    assert summarize_deals([{"stage": LOST, "amount": 10}])["win_rate"] == 0
    assert summarize_deals([])["win_rate"] == 0


def test_deal_validation_and_independent_seeds():
    assert not validate_deal("サンプル", "架空", 100, "架空の訪問")
    assert len(validate_deal("", "", float("nan"), "")) == 3
    first = sample_deals()
    first[0]["amount"] = 0
    assert sample_deals()[0]["amount"] > 0


def test_registered_opportunity_updates_same_session_dashboard():
    from streamlit.testing.v1 import AppTest

    script = (
        "import streamlit as st\nfrom demos.crm import render_manager, render_opportunity\n"
        "if st.session_state.get('show_manager'): render_manager()\nelse: render_opportunity()"
    )
    at = AppTest.from_string(script).run()
    assert not at.exception
    next(item for item in at.text_input if "Opportunity name" in item.label).set_value("追加商談")
    next(item for item in at.text_input if "Next action" in item.label).set_value("架空の打合せ")
    next(button for button in at.button if "Save opportunity" in button.label).click().run()
    assert not at.exception
    assert len(at.session_state["crm_deals"]) == 13
    at.session_state["show_manager"] = True
    at.run()
    assert not at.exception
    assert at.metric[0].value != "¥0.0M"
    other = AppTest.from_string("from demos.crm import render_manager\nrender_manager()").run()
    assert len(other.session_state["crm_deals"]) == 12


@pytest.mark.parametrize("function", ["render_manager", "render_opportunity"])
def test_crm_scenes_render(function):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string(f"from demos.crm import {function}\n{function}()").run()
    assert not at.exception
