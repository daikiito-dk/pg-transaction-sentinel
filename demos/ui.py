"""Small shared presentation helpers for session-local demos."""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st


def banner(title: str, subtitle: str, *, accent: str = "#1769aa") -> None:
    st.markdown(
        f'<div class="branch-banner" style="border-bottom:2px solid {accent}">'
        f"<strong>{html.escape(title)}</strong><span>{html.escape(subtitle)}</span></div>",
        unsafe_allow_html=True,
    )


def demo_note() -> None:
    st.caption(
        "Session-only simulation · Synthetic data · No external submission / "
        "このブラウザのデモ内だけで保持します。実際の申込・送信ではありません。"
    )


def table(frame: pd.DataFrame, *, label: str = "Demo records") -> None:
    st.markdown(
        '<div class="bank-table branch-statement">'
        + frame.to_html(index=False, escape=True, border=0, classes="demo-records").replace(
            "<table ", f'<table aria-label="{html.escape(label, quote=True)}" ', 1
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def reset_button(prefix: str, keys: list[str]) -> None:
    if st.button("デモをリセット / Reset demo", key=f"{prefix}_reset"):
        for key in keys:
            st.session_state.pop(key, None)
        st.rerun()
