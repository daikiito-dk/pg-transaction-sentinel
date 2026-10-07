"""Loan-officer workbench with session-local reviews and real repayment arithmetic."""

from __future__ import annotations

import math

import pandas as pd
import plotly.express as px
import streamlit as st

from demos.ui import banner, demo_note, reset_button, table

CHECKS = [
    "本人確認資料 / ID document",
    "所得資料 / Income",
    "資金用途 / Purpose",
    "既存債務 / Debt",
]
STAGES = ["書類確認 / Documents", "審査中 / Review", "条件調整 / Terms", "デモ完了 / Demo complete"]


def monthly_payment(principal: float, annual_rate: float, months: int) -> float:
    if not all(math.isfinite(value) for value in (principal, annual_rate)):
        raise ValueError("Amounts and rates must be finite.")
    if principal <= 0 or not 0 <= annual_rate <= 100 or not 1 <= months <= 600:
        raise ValueError("Invalid principal, rate, or repayment period.")
    rate = annual_rate / 1200
    return principal / months if rate == 0 else principal * rate / (1 - (1 + rate) ** -months)


def repayment_schedule(principal: float, annual_rate: float, months: int) -> pd.DataFrame:
    payment = monthly_payment(principal, annual_rate, months)
    remaining = principal
    rows = []
    for month in range(1, months + 1):
        interest = remaining * annual_rate / 1200
        paid_principal = min(payment - interest, remaining)
        remaining = max(0, remaining - paid_principal)
        rows.append(
            {
                "month": month,
                "principal": paid_principal,
                "interest": interest,
                "balance": remaining,
            }
        )
    return pd.DataFrame(rows)


def sample_cases() -> list[dict]:
    return [
        {
            "id": "LOAN-001",
            "customer": "架空 太郎",
            "type": "住宅 / Housing",
            "amount": 20_000_000,
            "income": 6_000_000,
            "rate": 1.5,
            "years": 25,
            "existing_monthly": 20_000,
            "stage": STAGES[0],
            "checks": [False] * 4,
            "memo": "",
        },
        {
            "id": "LOAN-002",
            "customer": "架空商店A",
            "type": "事業 / Business",
            "amount": 5_000_000,
            "income": 8_400_000,
            "rate": 2.1,
            "years": 7,
            "existing_monthly": 50_000,
            "stage": STAGES[1],
            "checks": [True, True, False, False],
            "memo": "",
        },
        {
            "id": "LOAN-003",
            "customer": "架空 花子",
            "type": "教育 / Education",
            "amount": 2_000_000,
            "income": 4_800_000,
            "rate": 1.8,
            "years": 10,
            "existing_monthly": 10_000,
            "stage": STAGES[2],
            "checks": [True] * 4,
            "memo": "",
        },
    ]


def render() -> None:
    banner("融資案件ワークベンチ / Loan workbench", "SENTINEL · Internal demo")
    cases = st.session_state.setdefault("loan_cases", sample_cases())
    pending, amount, reviewed = st.columns(3)
    pending.metric("進行中 / Open cases", sum(case["stage"] != STAGES[-1] for case in cases))
    amount.metric("申込総額 / Requested", f"¥{sum(case['amount'] for case in cases):,.0f}")
    reviewed.metric("書類確認済 / Documents ready", sum(all(case["checks"]) for case in cases))
    table(
        pd.DataFrame(
            [
                {
                    "案件 / Case": case["id"],
                    "顧客 / Customer": case["customer"],
                    "種類 / Type": case["type"],
                    "申込額 / Amount": f"¥{case['amount']:,.0f}",
                    "状態 / Stage": case["stage"],
                }
                for case in cases
            ]
        )
    )
    selected = st.selectbox("案件 / Case", [case["id"] for case in cases], key="loan_case")
    case = next(case for case in cases if case["id"] == selected)
    calculator, review = st.tabs(["返済試算 / Repayment", "審査チェック / Review"])
    with calculator:
        principal = st.number_input(
            "借入額 / Principal",
            100_000,
            100_000_000,
            case["amount"],
            100_000,
            key=f"loan_principal_{selected}",
        )
        rate = st.number_input(
            "年利 (%) / Annual rate",
            0.0,
            15.0,
            case["rate"],
            0.05,
            key=f"loan_rate_{selected}",
        )
        years = st.slider("期間 (年) / Years", 1, 35, case["years"], key=f"loan_years_{selected}")
        schedule = repayment_schedule(principal, rate, years * 12)
        payment = monthly_payment(principal, rate, years * 12)
        monthly, interest, ratio = st.columns(3)
        monthly.metric("毎月返済 / Monthly payment", f"¥{payment:,.0f}")
        interest.metric("利息合計 / Total interest", f"¥{schedule['interest'].sum():,.0f}")
        debt_ratio = (payment + case["existing_monthly"]) / (case["income"] / 12)
        ratio.metric("参考返済比率 / Debt service ratio", f"{debt_ratio:.1%}")
        st.caption(
            "固定金利・元利均等の参考計算。税金・手数料・実際の審査基準は含みません。 / "
            "Illustrative fixed-rate amortization; excludes fees, tax, and underwriting policy."
        )
        chart = px.line(
            schedule, x="month", y="balance", title="借入残高推移 / Remaining principal"
        )
        chart.update_layout(template="plotly_white", height=280)
        st.plotly_chart(chart, theme=None, width="stretch")
    with review:
        with st.form(f"loan_review_{selected}"):
            checks = [
                st.checkbox(label, value=done)
                for label, done in zip(CHECKS, case["checks"], strict=True)
            ]
            stage = st.selectbox("状態 / Stage", STAGES, index=STAGES.index(case["stage"]))
            memo = st.text_area("審査メモ / Sample review note", case["memo"], max_chars=500)
            submitted = st.form_submit_button("デモ内に保存 / Save in demo")
        if submitted:
            if stage == STAGES[-1] and not all(checks):
                st.error("デモ完了には全項目の確認が必要です / Complete the checklist first.")
            else:
                case.update(checks=checks, stage=stage, memo=memo.strip())
                st.success("デモ内に保存しました。実際の融資承認ではありません / Simulation saved.")
        st.caption(
            "チェックは画面上の記録のみ。本人確認・信用情報照会は実行しません。 / "
            "Checklist only; no identity verification or credit-bureau inquiry."
        )
    demo_note()
    reset_button("loan", ["loan_cases"])
