"""Fictional account-opening campaign and browser-local application."""

from __future__ import annotations

import re
from datetime import date

import streamlit as st

from demos.ui import banner, demo_note, reset_button, table


def validate_application(
    name: str, email: str, birth_date: date, accepted: bool, *, today: date | None = None
) -> list[str]:
    today = today or date.today()
    errors = []
    if not name.strip() or len(name.strip()) > 80:
        errors.append("氏名を1〜80文字で入力してください / Enter a sample name (1–80 characters).")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()) or len(email) > 254:
        errors.append("メール形式を確認してください / Check the email format.")
    age = (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
    )
    if birth_date > today or age < 18 or age > 110:
        errors.append("このデモは18歳以上を想定しています / This demo assumes an adult applicant.")
    if not accepted:
        errors.append("架空情報のみを使用する確認を選択してください / Confirm fictional data only.")
    return errors


def render() -> None:
    import pandas as pd

    banner("SENTINEL BANK · 口座開設 / Account opening", "Fictional campaign · DEMO")
    st.markdown(
        '<div class="bank-hero"><div><p>新しい毎日を、ここから。</p>'
        "<h3>スマートな口座で、銀行をもっと身近に。</h3>"
        "<p>Banking that fits your everyday life.</p>"
        '</div><div class="bank-balance"><span>架空のキャンペーン特典</span>'
        "<strong>最大 3,000 pt</strong><span>Illustrative offer · No rewards are issued</span>"
        "</div></div>",
        unsafe_allow_html=True,
    )
    first, second, third = st.columns(3)
    first.metric("スマホで申込 / Online", "3 steps")
    second.metric("振込手数料 / Sample fee", "¥0*")
    third.metric("口座管理 / Account access", "24/7*")
    st.caption("*すべて架空のサービス条件です。実際の銀行・特典・手数料を示すものではありません。")
    with st.expander("キャンペーン条件 / Sample terms"):
        st.write(
            "架空の新規口座とサンプル入金を条件とした画面例です。実際の募集期間、"
            "契約、特典付与はありません。 / Fictional terms; no contract or reward is offered."
        )
    draft = st.session_state.get("opening_draft")
    if st.session_state.get("opening_complete"):
        st.success(
            "デモ受付が完了しました：DEMO-OPEN-001 / Simulated receipt only. No account was opened."
        )
        demo_note()
        reset_button("opening", ["opening_draft", "opening_complete"])
        return
    if draft:
        st.markdown("### 2. 内容確認 / Review")
        table(pd.DataFrame({"項目 / Field": draft.keys(), "内容 / Value": draft.values()}))
        demo_note()
        submit, back = st.columns(2)
        if submit.button(
            "デモ受付を完了 / Finish simulation", key="opening_finish", type="primary"
        ):
            st.session_state["opening_complete"] = True
            st.rerun()
        if back.button("入力に戻る / Back", key="opening_back"):
            st.session_state.pop("opening_draft")
            st.rerun()
        return
    st.markdown("### 1. お客さま情報 / Application")
    st.caption("本名・実際の連絡先は入力しないでください / Do not enter real personal information.")
    with st.form("opening_application"):
        name = st.text_input("氏名 / Sample name", "架空 太郎", max_chars=80)
        email = st.text_input("メール / Sample email", "taro@example.invalid", max_chars=254)
        birth = st.date_input(
            "生年月日 / Sample birth date",
            date(1990, 4, 1),
            min_value=date(1916, 1, 1),
            max_value=date.today(),
        )
        account = st.selectbox(
            "口座タイプ / Account type", ["普通預金 / Savings", "給与受取 / Salary"]
        )
        purpose = st.selectbox("利用目的 / Purpose", ["生活費 / Everyday", "貯蓄 / Saving"])
        accepted = st.checkbox("架空情報のみでデモを体験します / I will use fictional data only")
        submitted = st.form_submit_button("内容を確認 / Review application", type="primary")
    if submitted:
        errors = validate_application(name, email, birth, accepted)
        if errors:
            for error in errors:
                st.error(error)
        else:
            st.session_state["opening_draft"] = {
                "氏名 / Name": name.strip(),
                "メール / Email": email.strip(),
                "生年月日 / Birth date": birth.isoformat(),
                "口座 / Account": account,
                "目的 / Purpose": purpose,
            }
            st.rerun()
    demo_note()
