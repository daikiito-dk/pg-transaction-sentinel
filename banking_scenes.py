"""Read-only banking scenes backed by the same synthetic AML dataset."""

from __future__ import annotations

import html

import pandas as pd
import plotly.express as px
import streamlit as st

from demos.catalog import SCENES

INFLOW_TYPES = ("DEPOSIT", "TRANSFER_IN")
OUTFLOW_TYPES = ("WITHDRAWAL", "TRANSFER_OUT")
TYPE_LABELS = {
    "DEPOSIT": "Deposit / 入金",
    "WITHDRAWAL": "Withdrawal / 出金",
    "TRANSFER_IN": "Transfer in / 被振込",
    "TRANSFER_OUT": "Transfer out / 振込",
}
CUSTOMER_MENU = {
    "home": "ホーム / Home",
    "statement": "入出金明細 / Statement",
    "transfer": "振込・振替 / Transfer",
    "services": "各種お手続き / Services",
}
BRANCH_MENU = {
    "profile": "顧客照会 / Customer",
    "activity": "取引照会 / Activity",
    "requests": "受付一覧 / Requests",
}
DEMO_BALANCE = 2_450_000


def statement_html(history: pd.DataFrame, *, compact: bool = False) -> str:
    """Render a light statement table while escaping every dataset-derived value."""
    rows = []
    for _, record in statement_rows(history).iterrows():
        cells = [
            pd.Timestamp(record["Timestamp / 日時"]).strftime("%Y/%m/%d %H:%M"),
            record["Type / 種別"],
            f"¥{record['Amount (JPY) / 金額']:,.0f}",
            "" if pd.isna(record["Counterparty / 相手口座"]) else record["Counterparty / 相手口座"],
        ]
        rows.append(
            "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in cells) + "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="4">取引はありません / No transactions</td></tr>')
    variant = " branch-statement" if compact else ""
    return (
        f'<div class="bank-table{variant}"><table aria-label="Transaction statement / 入出金明細">'
        "<thead><tr><th>日時 / Date</th><th>お取引 / Type</th>"
        "<th>金額 / Amount</th><th>相手口座 / Counterparty</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _set_view(scene: str, view: str) -> None:
    st.session_state[f"{scene}_view"] = view


def _menu(scene: str, options: dict[str, str]) -> str:
    key = f"{scene}_view"
    current = st.session_state.get(key, next(iter(options)))
    with st.container(key=f"{scene}-menu"):
        for column, (view, label) in zip(st.columns(len(options)), options.items(), strict=True):
            column.button(
                label,
                key=f"{scene}_nav_{view}",
                type="primary" if current == view else "secondary",
                on_click=_set_view,
                args=(scene, view),
                width="stretch",
            )
    return current


def account_directory(data: pd.DataFrame, query: str = "") -> pd.DataFrame:
    """Find synthetic customers by literal account ID or customer name."""
    accounts = (
        data[["account_id", "customer_name", "risk_category"]]
        .drop_duplicates("account_id")
        .sort_values("account_id")
        .reset_index(drop=True)
    )
    query = query.strip()
    if query:
        matches = accounts["account_id"].str.contains(query, case=False, regex=False) | accounts[
            "customer_name"
        ].str.contains(query, case=False, regex=False)
        accounts = accounts[matches].reset_index(drop=True)
    return accounts


def account_totals(history: pd.DataFrame) -> dict[str, float]:
    incoming = float(history.loc[history["transaction_type"].isin(INFLOW_TYPES), "amount"].sum())
    outgoing = float(history.loc[history["transaction_type"].isin(OUTFLOW_TYPES), "amount"].sum())
    return {"incoming": incoming, "outgoing": outgoing, "net": incoming - outgoing}


def statement_rows(history: pd.DataFrame) -> pd.DataFrame:
    """Expose statement fields only, never AML scores or investigation reasons."""
    return (
        history.sort_values(["timestamp", "transaction_id"], ascending=[False, False])[
            ["transaction_id", "timestamp", "transaction_type", "amount", "destination_account"]
        ]
        .assign(transaction_type=lambda frame: frame["transaction_type"].map(TYPE_LABELS))
        .rename(
            columns={
                "transaction_id": "Txn ID / 取引ID",
                "timestamp": "Timestamp / 日時",
                "transaction_type": "Type / 種別",
                "amount": "Amount (JPY) / 金額",
                "destination_account": "Counterparty / 相手口座",
            }
        )
        .reset_index(drop=True)
    )


def render_demo_header() -> str:
    requested = st.query_params.get("scene", "aml")
    scene = requested if requested in SCENES else "aml"
    light_style = (
        """
        :root { color-scheme: light; }
        [data-testid="stApp"], [data-testid="stAppViewContainer"],
        [data-testid="stMain"], [data-testid="stHeader"] {
            background: #f5f7fa; color: #172b4d;
        }
        [data-testid="stMarkdownContainer"], [data-testid="stWidgetLabel"],
        [data-testid="stMetricLabel"], [data-testid="stMetricValue"],
        [data-testid="stText"], [data-testid="stCaptionContainer"] { color: #172b4d; }
        [data-testid="stCaptionContainer"] { color: #64748b; }
        [data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-tertiary"],
        [data-testid="stDownloadButton"] button {
            background: #fff; color: #24466c; border: 1px solid #d8e0ea;
        }
        [data-testid="stBaseButton-primary"], [data-testid="stFormSubmitButton"] button {
            background: #1769aa; color: #fff; border-color: #1769aa;
        }
        [data-testid="stBaseButton-primary"] [data-testid="stMarkdownContainer"],
        [data-testid="stFormSubmitButton"] button [data-testid="stMarkdownContainer"] {
            color: #fff;
        }
        [data-testid="stAlert"] { background: #edf5ff; color: #24466c; }
        [data-testid="stSelectbox"] div:has(> input),
        [data-testid="stTextInputRootElement"],
        [data-testid="stNumberInput"] div:has(> input),
        [data-testid="stDateInput"] div:has(> input),
        [data-testid="stTextArea"] div:has(> textarea),
        [data-testid="stTextArea"] textarea {
            background: #fff; color: #172b4d; border-color: #d8e0ea;
        }
        [data-testid="stTextInputRootElement"]:focus-within,
        [data-testid="stSelectbox"] div:has(> input):focus-within {
            border-color: #1769aa;
        }
        [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
        [data-testid="stDateInput"] input, [data-testid="stTextArea"] textarea {
            color: #172b4d; -webkit-text-fill-color: #172b4d;
        }
        [data-testid="stSelectbox"] input { color: #172b4d; }
        [data-testid="stSelectbox"] svg { fill: #64748b; }
        [data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"] {
            background: #fff; color: #172b4d;
        }
        [role="option"] { background: #fff; color: #172b4d; }
        [data-testid="stExpander"] { background: #fff; border-color: #d8e0ea; }
        [data-testid="stExpander"] summary { color: #334155; }
        [data-testid="stMetric"] {
            background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
            padding: 12px 16px;
        }
        .bank-brand { display:flex; justify-content:space-between; align-items:center;
            padding:16px 0 20px; border-bottom:2px solid #1769aa; margin-bottom:18px; }
        .bank-brand strong { font-size:22px; letter-spacing:0.06em; color:#16558c; }
        .bank-brand span, .bank-brand small { font-size:12px; color:#64748b; }
        .bank-hero { background:#fff; border:1px solid #dce5ef; border-radius:12px;
            padding:24px; margin:20px 0; display:flex; justify-content:space-between;
            gap:24px; align-items:center; }
        .bank-hero h3 { color:#172b4d; margin:0 0 8px; padding:0; font-size:21px; }
        .bank-hero p { margin:5px 0; color:#64748b; font-size:13px; }
        .bank-balance { text-align:right; }
        .bank-balance strong { display:block; font-size:34px; color:#16558c; }
        .bank-balance span { color:#64748b; font-size:12px; }
        .bank-notice { border-left:3px solid #1769aa; padding:10px 14px;
            background:#eaf3fb; color:#24466c; font-size:13px; margin:12px 0; }
        .bank-table { overflow:auto; max-height:380px; border:1px solid #e2e8f0;
            border-radius:8px; background:#fff; margin:12px 0; }
        .bank-table table { border-collapse:collapse; width:100%; font-size:13px; }
        .bank-table th { background:#edf3f8; color:#36516d; text-align:left;
            position:sticky; top:0; font-weight:600; }
        .bank-table td, .bank-table th { padding:12px 14px; border-bottom:1px solid #e8edf3;
            white-space:nowrap; }
        .bank-table td:nth-child(3) { text-align:right; font-variant-numeric:tabular-nums; }
        .branch-statement td, .branch-statement th { padding:8px 12px; }
        .branch-banner { border-bottom:1px solid #cbd5e1; padding:12px 0;
            margin-bottom:16px; display:flex; justify-content:space-between; }
        .branch-banner strong { color:#334155; font-size:16px; }
        .branch-banner span { color:#64748b; font-size:12px; }
        .st-key-branch-menu button { border-radius:3px; min-height:38px; }
        .st-key-customer-menu button { border-radius:6px; min-height:44px; }
        .bank-footer { color:#64748b; font-size:11px; padding:20px 0; border-top:1px solid #e2e8f0;
            margin-top:24px; }
        @media (max-width:640px) {
            .bank-hero { flex-direction:column; align-items:flex-start; padding:18px; }
            .bank-balance { text-align:left; }
            .bank-brand { gap:12px; align-items:flex-start; }
            .bank-brand strong { font-size:18px; }
        }
    """
        if scene not in {"aml", "events"}
        else ""
    )
    st.markdown(
        "<style>"
        + light_style
        + """
        .st-key-demo-navigation button {
            min-height: 42px; border-radius: 0; padding: 6px 24px;
            clip-path: polygon(0 0, calc(100% - 16px) 0, 100% 50%,
                               calc(100% - 16px) 100%, 0 100%, 16px 50%);
            font-weight: 700;
        }
        .st-key-demo-navigation button:focus-visible {
            box-shadow: inset 0 0 0 3px #93c5fd;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    title, gallery = st.columns([3, 1])
    title.markdown("#### Banking Scenario Lab")
    if gallery.button("デモ一覧 / Gallery", key="scene_home", width="stretch"):
        st.query_params["scene"] = "home"
        st.rerun()
    with st.container(key="demo-navigation"):
        for column, key in zip(st.columns(3), ("customer", "branch", "aml"), strict=True):
            if column.button(
                SCENES[key],
                key=f"scene_{key}",
                type="primary" if scene == key else "secondary",
                width="stretch",
            ):
                st.query_params["scene"] = key
                st.rerun()
    if scene not in {"customer", "branch", "aml"}:
        st.caption(f"Active demo / 表示中: {SCENES[scene]}")
    with st.expander("More demos / 他のデモ"):
        extras = [
            (key, label)
            for key, label in SCENES.items()
            if key not in {"home", "customer", "branch", "aml"}
        ]
        for offset in range(0, len(extras), 3):
            chunk = extras[offset : offset + 3]
            for column, (key, label) in zip(st.columns(len(chunk)), chunk, strict=True):
                if column.button(
                    label,
                    key=f"scene_{key}",
                    type="primary" if scene == key else "secondary",
                    width="stretch",
                ):
                    st.query_params["scene"] = key
                    st.rerun()
    with st.expander("About this demo / デモについて"):
        st.caption(
            "Synthetic data only. Role switching is not authentication or access control. / "
            "架空データの体験用画面です。役割切り替えは認証やアクセス権管理ではありません。"
        )
        st.write(
            "Chevron buttons select independent demos, not workflow steps. "
            "Workflows keep changes in this browser session only; "
            "no real payments or applications. "
            "Models perform actual calculations on synthetic data. Network writes in data tools "
            "require explicit local-only enablement and are disabled in public mode. / "
            "業務操作はブラウザのデモ内だけで保持し、実際の送金や申込は行いません。"
            "モデルは合成データで実際に計算します。データ作成ツールの書き込みはローカル限定で"
            "明示的な有効化が必要です。公開モードでは無効です。"
        )
    return scene


def _choose_account(data: pd.DataFrame, accounts: pd.DataFrame, scene: str) -> pd.DataFrame:
    labels = accounts.set_index("account_id")["customer_name"].to_dict()
    options = accounts["account_id"].tolist()
    account_id = st.selectbox(
        "Demo account / デモ用口座",
        options,
        index=options.index("ACC-000002") if "ACC-000002" in options else 0,
        format_func=lambda value: f"{value} · {labels[value]}",
        key=f"{scene}_account",
    )
    return data[data["account_id"] == account_id].sort_values("timestamp")


def _show_totals(history: pd.DataFrame) -> None:
    totals = account_totals(history)
    incoming, outgoing, net = st.columns(3)
    incoming.metric("Recorded inflow / 記録期間の入金", f"¥{totals['incoming']:,.0f}")
    outgoing.metric("Recorded outflow / 記録期間の出金", f"¥{totals['outgoing']:,.0f}")
    net.metric("Net movement / 入出金差額", f"¥{totals['net']:,.0f}")
    st.caption(
        f"Recorded period / 記録期間: {history['timestamp'].min():%Y-%m-%d} – "
        f"{history['timestamp'].max():%Y-%m-%d}. "
        "Net movement is not an account balance; no opening balance is stored. / "
        "入出金差額は残高ではありません。期首残高は保存していません。"
    )


def _show_statement(history: pd.DataFrame, *, compact: bool = False) -> None:
    st.markdown("#### Transaction statement / 入出金明細")
    st.markdown(statement_html(history, compact=compact), unsafe_allow_html=True)
    st.download_button(
        "明細をダウンロード / Download CSV",
        statement_rows(history).to_csv(index=False).encode("utf-8-sig"),
        file_name="demo-statement.csv",
        mime="text/csv",
        key=f"{'branch' if compact else 'customer'}_statement_download",
    )


def render_customer_scene(data: pd.DataFrame) -> None:
    st.markdown(
        '<div class="bank-brand"><strong>SENTINEL BANK <small>DEMO</small></strong>'
        "<span>インターネットバンキング<br>Internet banking</span></div>",
        unsafe_allow_html=True,
    )
    accounts = account_directory(data)
    if accounts.empty:
        st.info("No accounts available. / 口座データがありません。")
        return
    with st.expander("デモ口座を切り替える / Sample account"):
        history = _choose_account(data, accounts, "customer")
    profile = history.iloc[0]
    st.markdown(
        '<div class="bank-hero"><div>'
        f"<h3>{html.escape(str(profile['customer_name']))} 様</h3>"
        "<p>普通預金 · デモ支店 / Savings · Demo branch</p>"
        f"<p>口座 / Account: {html.escape(str(profile['account_id']))}</p>"
        '</div><div class="bank-balance"><span>デモ表示残高 / Illustrative balance</span>'
        f"<strong>¥{DEMO_BALANCE:,.0f}</strong>"
        "<span>明細とは連動しません / Not linked to the statement</span></div></div>",
        unsafe_allow_html=True,
    )
    view = _menu("customer", CUSTOMER_MENU)
    if view == "transfer":
        _transfer_preview()
    elif view == "services":
        _service_previews()
    elif view == "statement":
        _show_totals(history)
        _show_statement(history)
    else:
        _customer_home(history)
    st.markdown(
        '<div class="bank-footer">SENTINEL BANK は架空の銀行です。'
        "実際の金融サービスではありません。"
        "<br>Fictional bank and synthetic data. No real financial services.</div>",
        unsafe_allow_html=True,
    )


def _customer_home(history: pd.DataFrame) -> None:
    st.markdown("#### 口座ホーム / Account overview")
    st.markdown(
        '<div class="bank-notice">お知らせ / Notice：デモへようこそ。'
        "下のメニューから振込や手続きの画面を体験できます。</div>",
        unsafe_allow_html=True,
    )
    with st.container(key="customer-shortcuts"):
        transfer, statement, services = st.columns(3)
        for column, view, label in [
            (transfer, "transfer", "振込する / Transfer"),
            (statement, "statement", "明細を見る / Statement"),
            (services, "services", "カード・各種手続き / Services"),
        ]:
            column.button(
                label,
                key=f"customer_shortcut_{view}",
                on_click=_set_view,
                args=("customer", view),
                width="stretch",
            )
    st.markdown("#### 最近のお取引 / Recent transactions")
    recent = history.sort_values(["timestamp", "transaction_id"], ascending=[False, False]).head(5)
    st.markdown(statement_html(recent), unsafe_allow_html=True)
    st.caption("PostgreSQLの架空取引を表示 / Synthetic transactions from PostgreSQL")
    daily = history.assign(
        day=history["timestamp"].dt.date,
        direction=history["transaction_type"].map(
            {
                "DEPOSIT": "Inflow / 入金",
                "TRANSFER_IN": "Inflow / 入金",
                "WITHDRAWAL": "Outflow / 出金",
                "TRANSFER_OUT": "Outflow / 出金",
            }
        ),
    )
    daily = daily.groupby(["day", "direction"], as_index=False)["amount"].sum()
    figure = px.bar(
        daily,
        x="day",
        y="amount",
        color="direction",
        barmode="group",
        labels={"day": "Date / 日付", "amount": "JPY / 円", "direction": "Direction / 入出金"},
        color_discrete_map={"Inflow / 入金": "#38bdf8", "Outflow / 出金": "#f59e0b"},
        title="Daily account activity / 日ごとの入出金",
    )
    figure.update_layout(
        template="plotly_white",
        height=270,
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font={"color": "#334155"},
        margin={"l": 20, "r": 20, "t": 45, "b": 20},
    )
    st.plotly_chart(figure, width="stretch", theme=None)


def _transfer_preview() -> None:
    st.markdown("#### 振込・振替 / Transfers")
    st.caption("画面プレビューのみ。送金は実行されません / Preview only. No payment is executed.")
    with st.form("customer_transfer_preview"):
        st.text_input("振込先銀行 / Destination bank", "サンプル銀行", disabled=True)
        branch, account = st.columns(2)
        branch.text_input("支店 / Branch", "デモ支店", disabled=True)
        account.text_input("口座番号 / Account number", "0000000", disabled=True)
        st.text_input("受取人 / Recipient", "架空の受取人 / Fictional recipient", disabled=True)
        st.number_input("振込金額 / Amount (JPY)", value=10_000, disabled=True)
        if st.form_submit_button("内容を確認 / Review sample"):
            st.info(
                "サンプルの確認画面です。送金・残高変更・取引登録は行っていません。 / "
                "Sample reviewed. No payment, balance change, or transaction was recorded."
            )


def _service_previews() -> None:
    st.markdown("#### 各種お手続き / Services")
    services = [
        ("card", "カード管理 / Card settings"),
        ("deposit", "定期預金 / Term deposit"),
        ("details", "住所・連絡先変更 / Contact details"),
        ("limits", "振込限度額 / Transfer limits"),
    ]
    for key, label in services:
        if st.button(label, key=f"customer_service_{key}", width="stretch"):
            st.session_state["customer_service_preview"] = label
    selected = st.session_state.get("customer_service_preview")
    if selected:
        st.info(
            f"{selected}：デモのため受付・変更は行いません。 / "
            "Preview only; no application or setting change is submitted."
        )


def render_branch_scene(data: pd.DataFrame) -> None:
    st.markdown(
        '<div class="branch-banner"><strong>SENTINEL · 支店業務端末 / Branch console</strong>'
        "<span>照会モード · DEMO / Read-only</span></div>",
        unsafe_allow_html=True,
    )
    st.markdown("#### Branch customer inquiry / 顧客照会")
    view = _menu("branch", BRANCH_MENU)
    query = st.text_input("Customer name or account ID / 顧客名・口座ID", key="branch_search")
    accounts = account_directory(data, query)
    st.caption(f"Matching accounts / 該当口座: {len(accounts):,}")
    if accounts.empty:
        st.info("No matching customers. / 該当する顧客がいません。")
        return
    history = _choose_account(data, accounts, "branch")
    profile = history.iloc[0]
    st.markdown(
        '<div class="bank-notice">'
        f"{html.escape(str(profile['customer_name']))} · "
        f"{html.escape(str(profile['account_id']))} · "
        f"KYC: {html.escape(str(profile['risk_category']))} · "
        "口座状態: サンプル / Status: sample</div>",
        unsafe_allow_html=True,
    )
    if view == "requests":
        st.markdown("#### 受付一覧 / Request queue")
        st.caption("サンプル表示。実際の申込や受付情報ではありません / Illustrative requests only.")
        st.markdown(
            '<div class="bank-table branch-statement"><table><thead><tr>'
            "<th>受付 / Reference</th><th>内容 / Request</th><th>状態 / Status</th>"
            "</tr></thead><tbody><tr><td>SAMPLE-001</td><td>住所変更 / Contact update</td>"
            "<td>確認待ち / Pending</td></tr><tr><td>SAMPLE-002</td>"
            "<td>カード再発行 / Card replacement</td><td>受付済 / Received</td></tr>"
            "</tbody></table></div>",
            unsafe_allow_html=True,
        )
        if st.button("受付内容を確認 / Review sample request", key="branch_request_preview"):
            st.info("サンプルの閲覧のみです。受付情報の更新は行いません / No request was updated.")
    else:
        if view == "profile":
            st.caption(
                "登録情報 / Reference data · KYC区分は架空の登録値です / Synthetic KYC category"
            )
            _show_totals(history)
        _show_statement(history, compact=True)
    st.markdown(
        '<div class="bank-footer">照会専用 · 架空データ / Read-only · Synthetic data'
        " · 本人確認・制裁リスト照合は行いません / No identity or sanctions checks</div>",
        unsafe_allow_html=True,
    )
