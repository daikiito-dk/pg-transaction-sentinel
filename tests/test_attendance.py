from datetime import date, datetime

import pytest

from demos.attendance import (
    JST,
    clock_state,
    leave_days,
    record_clock,
    validate_leave,
    worked_minutes,
)


def test_clock_transitions_and_break_deduction():
    events = []
    day = date(2026, 10, 7)
    for action, hour in [("in", 9), ("break", 12), ("resume", 13), ("out", 18)]:
        events = record_clock(events, action, datetime(2026, 10, 7, hour, tzinfo=JST))
    assert clock_state(events, day) == "done"
    assert worked_minutes(events, day, datetime(2026, 10, 7, 19, tzinfo=JST)) == 480
    with pytest.raises(ValueError):
        record_clock(events, "in", datetime(2026, 10, 7, 20, tzinfo=JST))
    with pytest.raises(ValueError):
        record_clock([], "out", datetime(2026, 10, 7, 18, tzinfo=JST))
    with pytest.raises(ValueError):
        record_clock([], "in", datetime(2026, 10, 7, 9))


def test_weekday_leave_overlap_allowance_and_withdrawal():
    start, end = date(2026, 10, 9), date(2026, 10, 12)
    assert leave_days(start, end) == 2
    request = {"start": start, "end": end, "days": 2, "status": "申請中 / Requested"}
    assert validate_leave(start, end, [request], today=date(2026, 10, 7))
    request["status"] = "取り下げ / Withdrawn"
    assert not validate_leave(start, end, [request], today=date(2026, 10, 7))
    assert validate_leave(date(2026, 10, 10), date(2026, 10, 11), [], today=date(2026, 10, 7))
    with pytest.raises(ValueError):
        leave_days(end, start)


def test_employee_clock_ui_and_isolated_session():
    from streamlit.testing.v1 import AppTest

    script = "from demos.attendance import render\nrender()"
    at = AppTest.from_string(script).run()
    assert not at.exception
    assert at.button(key="clock_out").disabled
    at.button(key="clock_in").click().run()
    assert not at.exception
    assert at.button(key="clock_in").disabled
    at.button(key="clock_break").click().run()
    assert at.button(key="clock_out").disabled
    at.button(key="clock_resume").click().run()
    at.button(key="clock_out").click().run()
    assert len(at.session_state["attendance_events"]) == 4
    other = AppTest.from_string(script).run()
    assert not other.session_state["attendance_events"]


def test_leave_request_reserves_days_and_withdrawal_restores_them():
    from datetime import timedelta

    from streamlit.testing.v1 import AppTest

    day = datetime.now(JST).date()
    while day.weekday() >= 5:
        day += timedelta(days=1)
    at = AppTest.from_string("from demos.attendance import render\nrender()").run()
    at.date_input[0].set_value(day)
    at.date_input[1].set_value(day)
    next(button for button in at.button if "Request in demo" in button.label).click().run()
    assert not at.exception
    assert at.metric[2].value == "14 days"
    at.button(key="withdraw_DEMO-LEAVE-001").click().run()
    assert at.metric[2].value == "15 days"
