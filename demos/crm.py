"""A fictional bank CRM shared between manager and representative demo scenes."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from demos.ui import banner, demo_note, reset_button, table

STAGE_PROBABILITIES = {
    "新規 / Prospect": 0.1,
    "ヒアリング / Discovery": 0.3,
    "提案 / Proposal": 0.6,
    "条件調整 / Negotiation": 0.8,
    "成約 / Won": 1.0,
    "失注 / Lost": 0.0,
}
OWNERS = ["佐藤（架空）", "鈴木（架空）", "高橋（架空）"]
PRODUCTS = ["住宅ローン / Housing", "事業融資 / Business", "決済 / Payments", "預金 / Deposits"]
WON, LOST = list(STAGE_PROBABILITIES)[-2:]


def sample_deals(today: date | None = None) -> list[dict]:
    today = today or date.today()
    stages = list(STAGE_PROBABILITIES)
    return [
        {
            "id": f"DEMO-OPP-{index + 1:04d}",
            "name": f"サンプル商談 {index + 1}",
            "customer": f"架空顧客 {index + 1}",
            "owner": OWNERS[index % len(OWNERS)],
            "product": PRODUCTS[index % len(PRODUCTS)],
            "amount": (index + 1) * 2_000_000,
            "stage": stages[index % len(stages)],
            "close_date": today + timedelta(days=index * 3 - 7),
            "next_action": "架空のお客さまとの条件確認",
            "note": "",
        }
        for index in range(12)
    ]


def summarize_deals(deals: list[dict]) -> dict[str, float]:
    open_deals = [deal for deal in deals if deal["stage"] not in {WON, LOST}]
    won = [deal for deal in deals if deal["stage"] == WON]
    lost = [deal for deal in deals if deal["stage"] == LOST]
    return {
        "open_count": len(open_deals),
        "pipeline": sum(deal["amount"] for deal in open_deals),
        "weighted": sum(deal["amount"] * STAGE_PROBABILITIES[deal["stage"]] for deal in open_deals),
        "won_amount": sum(deal["amount"] for deal in won),
        "win_rate": len(won) / max(len(won) + len(lost), 1),
    }


def validate_deal(name: str, customer: str, amount: float, next_action: str) -> list[str]:
    errors = []
    if not name.strip() or not customer.strip():
        errors.append("商談名と架空顧客名は必須です / Sample deal and customer names are required.")
    if not math.isfinite(amount) or not 0 < amount <= 1_000_000_000:
        errors.append("案件金額を確認してください / Check the opportunity amount.")
    if not next_action.strip():
        errors.append("次のアクションを入力してください / Enter a next action.")
    return errors


def _store() -> list[dict]:
    return st.session_state.setdefault("crm_deals", sample_deals())


def _deal_frame(deals: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "商談 / Opportunity": deal["name"],
                "顧客 / Customer": deal["customer"],
                "担当 / Owner": deal["owner"],
                "商品 / Product": deal["product"],
                "見込額 / Amount": f"¥{deal['amount']:,.0f}",
                "段階 / Stage": deal["stage"],
                "予定日 / Close": deal["close_date"].isoformat(),
            }
            for deal in deals
        ]
    )


def render_manager() -> None:
    banner("SENTINEL CRM · 営業マネージャー / Sales manager", "Pipeline overview · DEMO")
    deals = _store()
    owner = st.selectbox("担当者 / Owner", ["全員 / All", *OWNERS], key="crm_owner_filter")
    filtered = (
        deals if owner == "全員 / All" else [deal for deal in deals if deal["owner"] == owner]
    )
    summary = summarize_deals(filtered)
    pipeline, weighted, won, rate = st.columns(4)
    pipeline.metric("取扱見込額 / Open pipeline", f"¥{summary['pipeline'] / 1e6:,.1f}M")
    weighted.metric("確度加重見込 / Weighted", f"¥{summary['weighted'] / 1e6:,.1f}M")
    won.metric("成約取扱額 / Won volume", f"¥{summary['won_amount'] / 1e6:,.1f}M")
    rate.metric("成約率 / Win rate", f"{summary['win_rate']:.0%}")
    st.caption(
        "金額は取扱見込で、売上・利益ではありません。確度は固定のデモ係数です。 / "
        "Amounts are business volume, not revenue. Probabilities are illustrative stage weights."
    )
    overview, records, tasks = st.tabs(
        ["概要 / Overview", "商談一覧 / Pipeline", "活動 / Activities"]
    )
    with overview:
        frame = pd.DataFrame(filtered)
        left, right = st.columns(2)
        stages = frame.groupby("stage", as_index=False)["amount"].sum()
        figure = px.bar(
            stages,
            x="stage",
            y="amount",
            title="段階別案件金額 / Pipeline by stage",
            category_orders={"stage": list(STAGE_PROBABILITIES)},
            color_discrete_sequence=["#1769aa"],
        )
        figure.update_layout(template="plotly_white", height=300)
        left.plotly_chart(figure, theme=None, width="stretch")
        by_owner = frame.groupby("owner", as_index=False)["amount"].sum()
        figure = px.bar(
            by_owner,
            x="owner",
            y="amount",
            title="担当者別取扱額 / Volume by owner",
            color_discrete_sequence=["#38a89d"],
        )
        figure.update_layout(template="plotly_white", height=300)
        right.plotly_chart(figure, theme=None, width="stretch")
    with records:
        table(_deal_frame(filtered))
    with tasks:
        open_deals = [deal for deal in filtered if deal["stage"] not in {WON, LOST}]
        table(
            pd.DataFrame(
                [
                    {
                        "商談 / Deal": deal["name"],
                        "担当 / Owner": deal["owner"],
                        "次のアクション / Next action": deal["next_action"],
                    }
                    for deal in open_deals
                ]
            )
        )
    demo_note()
    reset_button("crm", ["crm_deals"])


def render_opportunity() -> None:
    banner("SENTINEL CRM · 商談レコード / Opportunity record", "Session-local workspace")
    deals = _store()
    mode = st.radio("操作 / Action", ["新規登録 / New", "既存更新 / Edit"], horizontal=True)
    existing = None
    if mode == "既存更新 / Edit":
        selected = st.selectbox(
            "商談 / Opportunity",
            [deal["id"] for deal in deals],
            format_func=lambda value: next(
                f"{deal['id']} · {deal['name']}" for deal in deals if deal["id"] == value
            ),
        )
        existing = next(deal for deal in deals if deal["id"] == selected)
    record = existing or {
        "name": "",
        "customer": "架空顧客",
        "owner": OWNERS[0],
        "product": PRODUCTS[0],
        "amount": 5_000_000,
        "stage": next(iter(STAGE_PROBABILITIES)),
        "close_date": date.today() + timedelta(days=30),
        "next_action": "",
        "note": "",
    }
    with st.form(f"opportunity_{record.get('id', 'new')}"):
        name = st.text_input("商談名 / Opportunity name", record["name"], max_chars=120)
        customer = st.text_input("架空顧客名 / Sample customer", record["customer"], max_chars=80)
        owner_column, product_column = st.columns(2)
        owner = owner_column.selectbox(
            "担当者 / Owner", OWNERS, index=OWNERS.index(record["owner"])
        )
        product = product_column.selectbox(
            "商品 / Product", PRODUCTS, index=PRODUCTS.index(record["product"])
        )
        amount = st.number_input("取扱見込額 / Amount", 0, 1_000_000_000, record["amount"], 100_000)
        stage_column, date_column = st.columns(2)
        stages = list(STAGE_PROBABILITIES)
        stage = stage_column.selectbox("段階 / Stage", stages, index=stages.index(record["stage"]))
        close_date = date_column.date_input("予定日 / Expected close", record["close_date"])
        next_action = st.text_input(
            "次のアクション / Next action", record["next_action"], max_chars=200
        )
        note = st.text_area("メモ / Sample notes", record["note"], max_chars=1000)
        submitted = st.form_submit_button("デモ内に登録 / Save opportunity", type="primary")
    if submitted:
        errors = validate_deal(name, customer, amount, next_action)
        if errors:
            for error in errors:
                st.error(error)
        else:
            values = {
                "name": name.strip(),
                "customer": customer.strip(),
                "owner": owner,
                "product": product,
                "amount": amount,
                "stage": stage,
                "close_date": close_date,
                "next_action": next_action.strip(),
                "note": note.strip(),
            }
            if existing:
                existing.update(values)
                saved_id = existing["id"]
            else:
                saved_id = f"DEMO-OPP-{len(deals) + 1:04d}"
                deals.append({"id": saved_id, **values})
            st.success(f"デモ内に保存しました / Saved in demo: {saved_id}")
    demo_note()
    st.caption(
        "同じブラウザ内の営業マネージャー画面にも反映されます。 / "
        "The manager dashboard in this browser uses these same demo records."
    )
    table(_deal_frame(deals[-5:]))
