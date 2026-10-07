import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
import QuantLib as ql  # noqa: N813

from demos.options import price_option


def test_known_black_scholes_values_put_call_parity_and_greeks():
    call = price_option(100, 100, 0.05, 0.2, 365)
    put = price_option(100, 100, 0.05, 0.2, 365, "put")
    assert call["price"] == pytest.approx(10.450583572, rel=1e-8)
    assert put["price"] == pytest.approx(5.573526022, rel=1e-8)
    assert call["price"] - put["price"] == pytest.approx(100 - 100 * math.exp(-0.05))
    assert call["delta"] == pytest.approx(0.636830651, rel=1e-8)
    assert call["vega_per_pct"] == pytest.approx(0.375240347, rel=1e-8)
    assert call["theta_per_day"] < 0
    assert price_option(100, 100, 0.05, 0.3, 365)["price"] > call["price"]


def test_global_quantlib_date_is_restored_and_parallel_prices_are_consistent():
    before = ql.Settings.instance().evaluationDate
    with ThreadPoolExecutor(max_workers=2) as executor:
        prices = list(
            executor.map(
                lambda day: price_option(100, 100, 0.05, 0.2, 365, valuation_date=day)["price"],
                [date(2026, 10, 7), date(2027, 1, 1)],
            )
        )
    assert prices[0] == pytest.approx(prices[1])
    assert ql.Settings.instance().evaluationDate == before


@pytest.mark.parametrize(
    "inputs",
    [
        (0, 100, 0.05, 0.2, 365),
        (100, 100, 0.05, 0, 365),
        (100, 100, 0.05, 0.2, 0),
        (float("nan"), 100, 0.05, 0.2, 365),
    ],
)
def test_invalid_option_inputs(inputs):
    with pytest.raises(ValueError):
        price_option(*inputs)


def test_option_gui_renders_real_calculation():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_string("from demos.options import render\nrender()").run(timeout=30)
    assert not at.exception
    assert at.metric[0].value == "10.4506"
    at.selectbox[0].set_value("put")
    next(button for button in at.button if "Calculate" in button.label).click().run()
    assert not at.exception
    assert at.metric[0].value == "5.5735"
