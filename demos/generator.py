"""Synthetic financial data generator scene: preview, CSV/JSON downloads, guarded writes.

Deterministic, clearly-fictional market data (FX rates, JPY term-deposit quotes,
QuantLib-priced synthetic bonds) is generated per session. By default nothing is
written anywhere: the scene shows a bounded preview and hands the user CSV/JSON
downloads generated in memory.

Optional local-only writes are guarded on the server side:

* ``PUBLIC_DEMO=true`` disables every write path (the UI hides it and the
  underlying functions refuse it);
* ``LOCAL_TOOL_WRITES=true`` is an additional explicit opt-in;
* inserts go to a dedicated new PostgreSQL schema ``demo_sandbox`` only, with
  generated idempotent keys, inside one transaction — the existing AML tables
  are never touched, truncated, or dropped;
* the database target must be a loopback host (no arbitrary connection URLs);
* an explicit confirmation checkbox is required before any insert;
* optional Keycloak test-user provisioning follows the same guards, sets
  generated temporary passwords, and never displays or logs them.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from demo_tools import env_flag
from demo_tools import generate as gen
from demo_tools.generate import (
    ToolDisabledError,
    count_batch_rows,
    describe_target,
    generate_batch,
    insert_batch,
    make_params,
    sandbox_engine,
    to_csv_bytes,
    to_json_bytes,
)
from demos.ui import banner, demo_note, reset_button, table

MAX_PREVIEW_ROWS = 300


def _params() -> gen.GeneratorParams:
    return make_params(
        seed=st.session_state.get("gen_seed", 42),
        days=st.session_state.get("gen_days", 30),
        label=st.session_state.get("gen_label", "demo"),
        fx_pairs=st.session_state.get("gen_pairs"),
    )


def _current_batch() -> gen.GeneratedBatch:
    """Return the session batch, generating a default preview on first visit."""
    if st.session_state.get("gen_batch") is None:
        st.session_state["gen_batch"] = generate_batch(_params())
    return st.session_state["gen_batch"]


def _status_line(label: str, ok: bool, *, ok_text: str = "OK", bad_text: str = "off") -> str:
    state = ok_text if ok else bad_text
    return f"- {label}: **{state}**"


def _write_guards() -> tuple[bool, list[str]]:
    """Evaluate the server-side write guards for display (no secrets shown)."""
    public_demo = env_flag("PUBLIC_DEMO")
    tool_writes = env_flag("LOCAL_TOOL_WRITES")
    target: str | None = None
    loopback = False
    reachable = False
    if not public_demo and tool_writes:
        try:
            engine = sandbox_engine()
            loopback = True
            target = describe_target(engine)
            with engine.connect():
                reachable = True
        except Exception:  # noqa: BLE001 - guard display must survive an unreachable DB
            reachable = False
    lines = [
        _status_line("PUBLIC_DEMO disabled", not public_demo, bad_text="true — all writes off"),
        _status_line("LOCAL_TOOL_WRITES=true", tool_writes),
        _status_line("Loopback PostgreSQL target", loopback and reachable, ok_text=target or "—"),
    ]
    allowed = not public_demo and tool_writes and loopback and reachable
    return allowed, lines


def _dataset_tab(name: str, frame: pd.DataFrame, batch_id: str) -> None:
    preview = frame.head(MAX_PREVIEW_ROWS)
    st.caption(
        f"Showing first {len(preview):,} of {len(frame):,} rows · generated keys, "
        "idempotent by (seed, batch id, business fields) / 先頭行のみ表示。キーは"
        "（シード・バッチID・項目）から決定的に生成され、再実行しても同じです。"
    )
    st.dataframe(preview, width="stretch", hide_index=True, height=280)
    csv, json_button = st.columns(2)
    with csv:
        st.download_button(
            "Download CSV / CSVダウンロード",
            data=to_csv_bytes(frame),
            file_name=f"sentinel-{batch_id}-{name}.csv",
            mime="text/csv",
            key=f"gen_download_csv_{name}",
        )
    with json_button:
        st.download_button(
            "Download JSON / JSONダウンロード",
            data=to_json_bytes(frame),
            file_name=f"sentinel-{batch_id}-{name}.json",
            mime="application/json",
            key=f"gen_download_json_{name}",
        )


def _sandbox_section() -> None:
    allowed, guard_lines = _write_guards()
    with st.expander(
        "Local sandbox write (admin only) / ローカルサンドボックス書き込み（管理者用）"
    ):
        for line in guard_lines:
            st.markdown(line)
        if not allowed:
            st.info(
                "Inserts are disabled here. Set PUBLIC_DEMO=false and LOCAL_TOOL_WRITES=true "
                "in your local environment (see docs/TOOLS.md). / 挿入は無効です。ローカルで "
                "PUBLIC_DEMO=false かつ LOCAL_TOOL_WRITES=true を設定してください。"
            )
            return
        batch = _current_batch()
        confirmed = st.checkbox(
            f"I confirm writing {batch.total_rows:,} synthetic records into the local "
            "demo_sandbox schema / 上記の合成レコードをローカルの demo_sandbox スキーマに"
            "書き込むことを確認します",
            key="gen_confirm",
        )
        if st.button(
            f"Insert into demo_sandbox / demo_sandbox に挿入 ({batch.total_rows:,} rows)",
            key="gen_insert",
            disabled=not confirmed,
            type="primary" if confirmed else "secondary",
        ):
            try:
                result = insert_batch(batch, sandbox_engine(), confirmed=True)
            except (ToolDisabledError, PermissionError) as exc:
                st.error(f"Insert refused / 挿入を拒否しました：{exc}")
            except Exception as exc:  # noqa: BLE001 - surface DB errors honestly
                st.error(f"Insert failed / 挿入に失敗しました：{type(exc).__name__}")
            else:
                st.session_state["gen_result"] = {
                    "batch_id": result.batch_id,
                    "created": result.created,
                    "attempted": result.attempted,
                    "inserted": result.inserted,
                    "skipped": result.skipped,
                    "target": result.target,
                    "verified": count_batch_rows(sandbox_engine(), result.batch_id),
                }
        result = st.session_state.get("gen_result")
        if result:
            st.success(
                f"Batch **{result['batch_id']}** on {result['target']} — "
                f"attempted {result['attempted']:,}, inserted **{result['inserted']:,}**, "
                f"skipped {result['skipped']:,} (idempotent re-run). "
                "Existing AML tables were not touched. / 既存のAMLテーブルは変更していません。"
            )
            table(
                pd.DataFrame(
                    {
                        "table / テーブル": result["verified"].keys(),
                        "rows stored / 保存行数": result["verified"].values(),
                    }
                ),
                label="demo_sandbox rows for this batch",
            )
            st.caption(
                "Only the dedicated demo_sandbox schema is written, with CREATE IF NOT EXISTS "
                "and INSERT ... ON CONFLICT DO NOTHING. No DROP/TRUNCATE anywhere. / "
                "専用スキーマ demo_sandbox にのみ CREATE IF NOT EXISTS と ON CONFLICT DO "
                "NOTHING で書き込みます。DROP/TRUNCATE は一切使いません。"
            )


def _keycloak_section() -> None:
    from urllib.parse import urlsplit

    from demo_tools import is_loopback_host
    from demo_tools.keycloak import (
        KeycloakUnavailableError,
        ToolDisabledError,
        keycloak_settings,
        provision_test_users,
    )

    settings = keycloak_settings()
    password_set = bool(settings["password"])
    loopback_url = is_loopback_host(urlsplit(settings["url"]).hostname or "")
    with st.expander("Local Keycloak test users (optional admin tool) / Keycloak テストユーザー"):
        for line in (
            _status_line("Writes allowed", settings["writes_enabled"]),
            _status_line("KEYCLOAK_ADMIN_PASSWORD set", password_set),
            _status_line(
                "Loopback Keycloak URL", loopback_url, ok_text=settings["url"], bad_text="refused"
            ),
        ):
            st.markdown(line)
        if not (settings["writes_enabled"] and password_set):
            st.info(
                "Provisioning is disabled. Start the local Keycloak profile and set the "
                "admin password in the untracked .env.tools (docs/TOOLS.md). / "
                "無効です。ローカルの Keycloak を起動し、.env.tools に管理者パスワードを"
                "設定してください。"
            )
            return
        if st.button("Provision test users / テストユーザーを登録", key="gen_keycloak"):
            try:
                result = provision_test_users()
            except (ToolDisabledError, PermissionError, KeycloakUnavailableError) as exc:
                st.error(f"Provisioning failed / 登録に失敗しました：{exc}")
            else:
                st.session_state["gen_keycloak_result"] = result.summary()
        summary = st.session_state.get("gen_keycloak_result")
        if summary:
            st.success(
                f"Realm **{summary['realm']}** ({summary['url']}): roles "
                f"{', '.join(summary['roles'])}; users "
                + ", ".join(user["username"] for user in summary["users"])
            )
            st.caption(
                "Temporary passwords were generated in memory and are not displayed or "
                "logged; each user must set a new password at first login. No static "
                "credentials are stored in this repository. / "
                "一時パスワードはメモリ上のみで生成・非表示です。初回ログイン時に変更が"
                "必要です。リポジトリに認証情報は保存しません。"
            )


def render() -> None:
    """Render the synthetic data generator scene (registered by the app)."""
    banner(
        "SENTINEL · Synthetic Market Data Generator / 合成マーケットデータ生成",
        "Deterministic fictional data · FX · deposits · QuantLib bond pricing",
        accent="#1769aa",
    )
    demo_note()
    st.caption(
        "All values are fictional and generated locally from a seed. No real market "
        "data, customers, or accounts. Preview and downloads are the default; writes "
        "need explicit local opt-in. / すべて架空の値です。既定はプレビューとダウンロード"
        "のみで、書き込みには明示的なローカル設定が必要です。"
    )

    controls, meta = st.columns([2.6, 1])
    with controls:
        st.select_slider(
            "Calendar days / 生成日数",
            options=(7, 14, 30, 60, 90),
            value=30,
            key="gen_days",
        )
        st.multiselect(
            "FX pairs / 通貨ペア",
            sorted(gen.FX_PAIRS),
            default=sorted(gen.FX_PAIRS),
            key="gen_pairs",
        )
    with meta:
        st.number_input(
            "Seed / シード",
            min_value=0,
            max_value=99_999,
            value=42,
            key="gen_seed",
        )
        st.text_input("Batch label / バッチラベル", value="demo", key="gen_label")

    if st.button("Generate preview / プレビューを生成", type="primary", key="gen_refresh"):
        st.session_state["gen_batch"] = generate_batch(_params())
        st.session_state.pop("gen_result", None)

    batch = _current_batch()
    summary = batch.summary()
    columns = st.columns(4)
    columns[0].metric("Batch ID / バッチID", summary["batch_id"])
    columns[1].metric("FX rows / FX行", f"{summary['fx_rows']:,}")
    columns[2].metric("Deposit rows / 預金行", f"{summary['deposit_rows']:,}")
    columns[3].metric("Bond rows / 債券行", f"{summary['bond_rows']:,}")
    st.caption(
        f"Generated {datetime.now():%Y-%m-%d %H:%M:%S} · as-of {summary['as_of']} · "
        "re-running with the same seed and label reproduces identical rows and keys. / "
        "同じシードとラベルで再生成すると同一の行とキーになります。"
    )

    tab_fx, tab_deposits, tab_bonds = st.tabs(
        ("FX rates / FXレート", "Term deposits / 期間預金", "Bond quotes / 債券")
    )
    with tab_fx:
        _dataset_tab("fx", batch.fx_rates, batch.batch_id)
    with tab_deposits:
        _dataset_tab("deposits", batch.term_deposits, batch.batch_id)
    with tab_bonds:
        _dataset_tab("bonds", batch.bond_quotes, batch.batch_id)

    _sandbox_section()
    _keycloak_section()
    reset_button(
        "generator",
        ["gen_batch", "gen_confirm", "gen_result", "gen_keycloak_result"],
    )
