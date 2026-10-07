import numpy as np
import pytest

from demos.lending import monthly_payment, repayment_schedule, sample_cases


def test_zero_rate_and_amortization():
    assert monthly_payment(120_000, 0, 12) == 10_000
    schedule = repayment_schedule(20_000_000, 1.5, 300)
    assert schedule.iloc[-1]["balance"] == pytest.approx(0, abs=0.001)
    assert schedule["principal"].sum() == pytest.approx(20_000_000)
    assert (np.diff(schedule["balance"]) < 0).all()
    assert monthly_payment(20_000_000, 2, 240) > monthly_payment(20_000_000, 1, 240)


@pytest.mark.parametrize("inputs", [(0, 1, 12), (10, -1, 12), (10, 1, 0), (float("nan"), 1, 12)])
def test_invalid_repayment_inputs(inputs):
    with pytest.raises(ValueError):
        monthly_payment(*inputs)


def test_case_seed_is_not_shared():
    first = sample_cases()
    first[0]["checks"][0] = True
    assert sample_cases()[0]["checks"][0] is False


def test_loan_workbench_renders_and_saves_session_review():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string("from demos.lending import render\nrender()").run()
    assert not at.exception
    at.text_area[0].set_value("架空の確認メモ")
    next(button for button in at.button if "Save in demo" in button.label).click().run()
    assert not at.exception
    assert at.session_state["loan_cases"][0]["memo"] == "架空の確認メモ"
