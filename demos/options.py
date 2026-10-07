"""Real QuantLib European-option pricing with explicit Greek units."""

from __future__ import annotations

import math
import threading
from datetime import date

import numpy as np
import pandas as pd
import plotly.express as px
import QuantLib as ql  # noqa: N813
import streamlit as st

from demos.ui import banner

_QL_LOCK = threading.RLock()


def price_option(
    spot: float,
    strike: float,
    rate: float,
    volatility: float,
    days: int,
    option_type: str = "call",
    dividend: float = 0,
    *,
    valuation_date: date | None = None,
) -> dict[str, float]:
    values = (spot, strike, rate, volatility, dividend)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("All pricing inputs must be finite.")
    if spot <= 0 or strike <= 0 or not 0 < volatility <= 3 or not -0.1 <= rate <= 0.25:
        raise ValueError("Invalid spot, strike, volatility, or interest rate.")
    if not isinstance(days, int) or not 1 <= days <= 3650 or not 0 <= dividend <= 0.25:
        raise ValueError("Invalid maturity or dividend yield.")
    if option_type not in {"call", "put"}:
        raise ValueError("Option type must be call or put.")
    valuation_date = valuation_date or date.today()
    # QuantLib settings are process-global. Restore them and serialize access across sessions.
    with _QL_LOCK, ql.SavedSettings():
        evaluation = ql.Date(valuation_date.day, valuation_date.month, valuation_date.year)
        ql.Settings.instance().evaluationDate = evaluation
        counter = ql.Actual365Fixed()
        process = ql.BlackScholesMertonProcess(
            ql.QuoteHandle(ql.SimpleQuote(spot)),
            ql.YieldTermStructureHandle(ql.FlatForward(evaluation, dividend, counter)),
            ql.YieldTermStructureHandle(ql.FlatForward(evaluation, rate, counter)),
            ql.BlackVolTermStructureHandle(
                ql.BlackConstantVol(evaluation, ql.NullCalendar(), volatility, counter)
            ),
        )
        option = ql.VanillaOption(
            ql.PlainVanillaPayoff(
                ql.Option.Call if option_type == "call" else ql.Option.Put, strike
            ),
            ql.EuropeanExercise(evaluation + days),
        )
        option.setPricingEngine(ql.AnalyticEuropeanEngine(process))
        return {
            "price": option.NPV(),
            "delta": option.delta(),
            "gamma": option.gamma(),
            "vega_per_pct": option.vega() / 100,
            "theta_per_day": option.theta() / 365,
            "rho_per_pct": option.rho() / 100,
        }


def render() -> None:
    banner("QuantLib · オプション価格ラボ / Option pricing lab", "European · Black–Scholes–Merton")
    default = {
        "spot": 100.0,
        "strike": 100.0,
        "rate": 0.05,
        "volatility": 0.2,
        "days": 365,
        "option_type": "call",
        "dividend": 0.0,
    }
    parameters = st.session_state.setdefault("option_parameters", default)
    with st.form("option_inputs"):
        first, second, third = st.columns(3)
        spot = first.number_input("原資産価格 / Spot", 1.0, 10_000.0, parameters["spot"], 1.0)
        strike = second.number_input("行使価格 / Strike", 1.0, 10_000.0, parameters["strike"], 1.0)
        days = third.number_input("満期までの日数 / Days", 1, 730, parameters["days"])
        first, second, third = st.columns(3)
        rate = first.number_input("年利 (%) / Rate", -2.0, 10.0, parameters["rate"] * 100, 0.1)
        vol = second.number_input(
            "年率ボラ (%) / Volatility", 1.0, 100.0, parameters["volatility"] * 100, 1.0
        )
        dividend = third.number_input(
            "配当利回り (%) / Dividend", 0.0, 10.0, parameters["dividend"] * 100, 0.1
        )
        kind = st.selectbox(
            "種類 / Type", ["call", "put"], index=0 if parameters["option_type"] == "call" else 1
        )
        submitted = st.form_submit_button("計算 / Calculate", type="primary")
    if submitted:
        parameters = {
            "spot": spot,
            "strike": strike,
            "rate": rate / 100,
            "volatility": vol / 100,
            "days": days,
            "option_type": kind,
            "dividend": dividend / 100,
        }
        st.session_state["option_parameters"] = parameters
    result = price_option(**parameters)
    price, delta, gamma = st.columns(3)
    price.metric("理論価格 / Premium", f"{result['price']:.4f}")
    delta.metric("Delta / デルタ", f"{result['delta']:.4f}")
    gamma.metric("Gamma / ガンマ", f"{result['gamma']:.6f}")
    vega, theta, rho = st.columns(3)
    vega.metric("Vega / ボラ +1%pt", f"{result['vega_per_pct']:.4f}")
    theta.metric("Theta / 1暦日", f"{result['theta_per_day']:.4f}")
    rho.metric("Rho / 金利 +1%pt", f"{result['rho_per_pct']:.4f}")
    st.caption(
        "1単位あたりの理論値。満期は実日数/365、金利・配当は連続複利です。 / "
        "Per-unit values; Actual/365 maturity and continuously compounded rates."
    )
    underlying, volatility, surface = st.tabs(
        ["原資産価格 / Spot sensitivity", "ボラティリティ / Volatility", "価格面 / Price surface"]
    )
    spots = np.linspace(parameters["spot"] * 0.6, parameters["spot"] * 1.4, 31)
    with underlying:
        data = pd.DataFrame(
            [
                {"spot": float(value), **price_option(**{**parameters, "spot": float(value)})}
                for value in spots
            ]
        )
        left, right = st.columns(2)
        for column, y, title in [(left, "price", "理論価格 / Price"), (right, "delta", "Delta")]:
            figure = px.line(data, x="spot", y=y, title=title)
            figure.update_layout(template="plotly_white", height=280)
            column.plotly_chart(figure, theme=None, width="stretch")
    with volatility:
        vols = np.linspace(0.05, 0.8, 21)
        data = pd.DataFrame(
            [
                {
                    "volatility_pct": float(value * 100),
                    "price": price_option(**{**parameters, "volatility": float(value)})["price"],
                }
                for value in vols
            ]
        )
        figure = px.line(
            data, x="volatility_pct", y="price", title="ボラ変化と価格 / Volatility sensitivity"
        )
        figure.update_layout(template="plotly_white", height=280)
        st.plotly_chart(figure, theme=None, width="stretch")
    with surface:
        grid_spots = np.linspace(parameters["spot"] * 0.7, parameters["spot"] * 1.3, 9)
        grid_vols = np.linspace(0.1, 0.6, 9)
        grid = np.array(
            [
                [
                    price_option(**{**parameters, "spot": float(s), "volatility": float(v)})[
                        "price"
                    ]
                    for s in grid_spots
                ]
                for v in grid_vols
            ]
        )
        figure = px.imshow(
            grid,
            x=grid_spots,
            y=grid_vols * 100,
            origin="lower",
            aspect="auto",
            labels={"x": "Spot", "y": "Volatility (%)", "color": "Premium"},
            color_continuous_scale="Blues",
        )
        figure.update_layout(template="plotly_white", height=310)
        st.plotly_chart(figure, theme=None, width="stretch")
    with st.expander("モデルと単位 / Model and units"):
        st.write(
            "QuantLibの解析エンジンを実際に実行します。欧州型・一定ボラ・一定金利を仮定し、"
            "早期行使、取引費用、スマイルは扱いません。Vega/Rhoは1%ポイント変化、Thetaは"
            "1暦日あたりです。価格は市場データではなく仮定からの計算です。 / "
            "Actual QuantLib analytic pricing, not market quotes or investment advice. "
            "European exercise, constant volatility/rates; no early exercise, costs, or smile."
        )
