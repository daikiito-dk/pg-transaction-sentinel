"""A concise gallery separating real calculations from simulated business workflows."""

from __future__ import annotations

import streamlit as st

from demos.catalog import SCENES

GROUPS = [
    (
        "顧客体験 / Customer experience",
        [
            ("customer", "口座ホーム・明細・振込画面 / Account home and sample transfer"),
            ("onboarding", "キャンペーンLPからデモ受付まで / Campaign to simulated application"),
        ],
    ),
    (
        "行内業務 / Bank operations",
        [
            ("branch", "顧客・取引の照会とサンプル受付 / Customer and transaction inquiry"),
            (
                "lending",
                "案件管理・書類チェック・実返済計算 / Loan workflow and repayment arithmetic",
            ),
            ("sales", "パイプラインと担当者別集計 / Sales pipeline and owner metrics"),
            (
                "opportunity",
                "商談登録から管理画面への反映 / Opportunity record shared within this session",
            ),
            ("attendance", "打刻・勤怠一覧・休暇申請 / Clock, history, and leave simulation"),
        ],
    ),
    (
        "分析・監視 / Analytics and monitoring",
        [
            (
                "aml",
                "PostgreSQL × Isolation Forest / Actual hybrid scoring on synthetic transactions",
            ),
            ("events", "Kafka実接続と明示的サンプルモード / Kafka stream or labeled sample mode"),
            (
                "portfolio",
                "データクレンジングとポートフォリオ分析 / Cleaning and portfolio health scoring",
            ),
            (
                "credit",
                "LightGBMとホールドアウト評価 / Synthetic credit model with held-out evaluation",
            ),
        ],
    ),
    (
        "金融工学・開発環境 / Quant and developer tools",
        [
            ("options", "QuantLibによる実価格・Greeks計算 / Real European option calculations"),
            (
                "generator",
                "金融ダミーデータとローカル連携 / Synthetic data and guarded local integrations",
            ),
        ],
    ),
]


def render() -> None:
    st.markdown("## Banking demos / 銀行業務デモ一覧")
    st.caption(
        "Explore different roles. Workflows are simulations; models and integrations are real "
        "where explicitly indicated. / 利用者の視点を選んで体験できます。"
        "業務操作はデモ、モデル計算や明示された連携は実処理です。"
    )
    for title, entries in GROUPS:
        st.markdown(f"#### {title}")
        for offset in range(0, len(entries), 2):
            row = entries[offset : offset + 2]
            for column, (scene, description) in zip(st.columns(len(row)), row, strict=True):
                with column.container(border=True):
                    st.markdown(f"**{SCENES[scene]}**")
                    st.caption(description)
                    if st.button("開く / Open", key=f"launch_{scene}", width="stretch"):
                        st.query_params["scene"] = scene
                        st.rerun()
