from datetime import date

from demos.onboarding import validate_application


def test_application_requires_sample_name_valid_email_adult_and_confirmation():
    today = date(2026, 10, 7)
    assert not validate_application(
        "架空 太郎", "taro@example.invalid", date(1990, 1, 1), True, today=today
    )
    assert len(validate_application("", "bad", date(2020, 1, 1), False, today=today)) == 4
    assert validate_application("架空", "a@example.invalid", date(2027, 1, 1), True, today=today)


def test_exact_adult_birthday_boundary():
    today = date(2026, 10, 7)
    assert not validate_application(
        "架空", "a@example.invalid", date(2008, 10, 7), True, today=today
    )
    assert validate_application("架空", "a@example.invalid", date(2008, 10, 8), True, today=today)


def test_opening_review_receipt_and_session_isolation():
    from streamlit.testing.v1 import AppTest

    script = "from demos.onboarding import render\nrender()"
    at = AppTest.from_string(script).run()
    at.checkbox[0].set_value(True)
    next(button for button in at.button if "Review application" in button.label).click().run()
    assert not at.exception
    assert "opening_draft" in at.session_state
    at.button(key="opening_finish").click().run()
    assert any("No account was opened" in item.value for item in at.success)
    other = AppTest.from_string(script).run()
    assert "opening_draft" not in other.session_state
    assert not other.success
