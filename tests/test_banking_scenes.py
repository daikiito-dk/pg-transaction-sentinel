import pandas as pd

from banking_scenes import account_directory, account_totals, statement_html, statement_rows


def _data():
    return pd.DataFrame(
        {
            "account_id": ["A-1", "A-1", "A-1", "A-1", "B-2"],
            "customer_name": ["架空顧客"] * 4 + ["Example"],
            "risk_category": ["LOW"] * 4 + ["HIGH"],
            "transaction_id": [1, 2, 3, 4, 5],
            "timestamp": pd.to_datetime(["2026-01-01"] * 5),
            "transaction_type": [
                "DEPOSIT",
                "TRANSFER_IN",
                "WITHDRAWAL",
                "TRANSFER_OUT",
                "DEPOSIT",
            ],
            "amount": [100, 200, 50, 80, 1000],
            "destination_account": [None] * 5,
            "risk_score": [0, 0, 90, 80, 0],
            "reasons": ["internal AML information"] * 5,
        }
    )


def test_account_directory_deduplicates_and_searches_literal_text():
    data = _data()
    assert account_directory(data)["account_id"].tolist() == ["A-1", "B-2"]
    assert account_directory(data, " a-1 ")["account_id"].tolist() == ["A-1"]
    assert account_directory(data, "架空")["account_id"].tolist() == ["A-1"]
    assert account_directory(data, "[.*]").empty


def test_totals_use_both_inflow_and_outflow_types_for_selected_account():
    data = _data()
    history = data[data["account_id"] == "A-1"]
    assert account_totals(history) == {"incoming": 300, "outgoing": 130, "net": 170}
    assert account_totals(history.iloc[0:0]) == {"incoming": 0, "outgoing": 0, "net": 0}


def test_customer_statement_excludes_internal_aml_fields():
    statement = statement_rows(_data())
    assert statement["Txn ID / 取引ID"].tolist() == [5, 4, 3, 2, 1]
    assert "risk_score" not in statement.columns
    assert "reasons" not in statement.columns
    assert len(statement.columns) == 5


def test_statement_html_escapes_data_and_hides_internal_fields():
    data = _data()
    data.loc[0, "destination_account"] = '<img src=x onerror="alert(1)">'
    rendered = statement_html(data)
    assert "<img" not in rendered
    assert "&lt;img" in rendered
    assert "internal AML information" not in rendered
    assert "risk_score" not in rendered
    assert "<table" in rendered
    assert "branch-statement" in statement_html(data, compact=True)
