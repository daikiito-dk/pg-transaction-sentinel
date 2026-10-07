"""Employee-facing, session-local attendance and annual-leave simulation."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from demos.ui import banner, demo_note, reset_button, table

JST = ZoneInfo("Asia/Tokyo")
ACTIONS = {
    "in": "出勤 / Clock in",
    "break": "休憩開始 / Break",
    "resume": "休憩終了 / Resume",
    "out": "退勤 / Clock out",
}
LABELS = {
    "off": "未出勤 / Not started",
    "working": "勤務中 / Working",
    "break": "休憩中 / On break",
    "done": "退勤済 / Finished",
}
TRANSITIONS = {
    "off": {"in": "working"},
    "working": {"break": "break", "out": "done"},
    "break": {"resume": "working"},
    "done": {},
}


def clock_state(events: list[dict], day: date) -> str:
    state = "off"
    for event in events:
        if event["at"].astimezone(JST).date() == day:
            state = TRANSITIONS[state][event["action"]]
    return state


def record_clock(events: list[dict], action: str, at: datetime) -> list[dict]:
    if at.tzinfo is None:
        raise ValueError("Clock timestamps must include a timezone.")
    state = clock_state(events, at.astimezone(JST).date())
    if action not in TRANSITIONS[state]:
        raise ValueError("現在の状態ではこの打刻はできません / Invalid clock transition.")
    if events and at < events[-1]["at"]:
        raise ValueError("Clock timestamps cannot go backwards.")
    return [*events, {"action": action, "at": at}]


def worked_minutes(events: list[dict], day: date, now: datetime) -> float:
    cutoff = min(now, datetime.combine(day + timedelta(days=1), time.min, JST))
    seconds = 0.0
    started = None
    for event in events:
        if event["at"].astimezone(JST).date() != day:
            continue
        if event["action"] in {"in", "resume"}:
            started = event["at"]
        elif event["action"] in {"break", "out"} and started is not None:
            seconds += max(0, (event["at"] - started).total_seconds())
            started = None
    if started is not None:
        seconds += max(0, (cutoff - started).total_seconds())
    return seconds / 60


def leave_days(start: date, end: date) -> int:
    if end < start or (end - start).days > 365:
        raise ValueError("休暇期間を確認してください / Invalid leave period.")
    return sum(
        (start + timedelta(days=offset)).weekday() < 5 for offset in range((end - start).days + 1)
    )


def validate_leave(start: date, end: date, requests: list[dict], *, today: date) -> list[str]:
    if start < today:
        return ["過去日の申請はできません / Choose today or a future date."]
    try:
        days = leave_days(start, end)
    except ValueError as error:
        return [str(error)]
    errors = []
    if days == 0:
        errors.append("平日を含む日程を選んでください / Select a weekday.")
    active = [request for request in requests if request["status"] != "取り下げ / Withdrawn"]
    if days > 15 - sum(request["days"] for request in active):
        errors.append("デモの休暇残日数を超えています / Insufficient demo leave allowance.")
    if any(start <= request["end"] and end >= request["start"] for request in active):
        errors.append("既存の申請と日程が重複しています / Overlapping leave request.")
    return errors


def render() -> None:
    now = datetime.now(JST)
    today = now.date()
    banner("SENTINEL · 勤怠 / Employee attendance", "EMP-DEMO-01 · 架空行員 / Fictional employee")
    events = st.session_state.setdefault("attendance_events", [])
    requests = st.session_state.setdefault("leave_requests", [])
    state = clock_state(events, today)
    status, hours, leave = st.columns(3)
    status.metric("勤務状態 / Status", LABELS[state])
    hours.metric("本日の勤務 / Worked today", f"{worked_minutes(events, today, now) / 60:.2f} h")
    reserved = sum(
        request["days"] for request in requests if request["status"] != "取り下げ / Withdrawn"
    )
    leave.metric("デモ休暇残 / Available leave", f"{15 - reserved} days")
    punch, history, vacation = st.tabs(["打刻 / Clock", "勤怠一覧 / History", "休暇申請 / Leave"])
    with punch:
        st.markdown(f"### {now:%Y/%m/%d %H:%M} JST")
        for column, (action, label) in zip(st.columns(4), ACTIONS.items(), strict=True):
            if column.button(
                label,
                key=f"clock_{action}",
                disabled=action not in TRANSITIONS[state],
                width="stretch",
            ):
                st.session_state["attendance_events"] = record_clock(
                    events, action, datetime.now(JST)
                )
                st.rerun()
        current = [event for event in events if event["at"].date() == today]
        table(
            pd.DataFrame(
                [
                    {
                        "時刻 / Time": event["at"].strftime("%H:%M:%S"),
                        "打刻 / Action": ACTIONS[event["action"]],
                    }
                    for event in current
                ],
                columns=["時刻 / Time", "打刻 / Action"],
            )
        )
        st.caption(
            "JSTの日次デモ。夜勤・給与計算・法令チェックは対象外です / "
            "Daily JST demo; no payroll or overnight shifts."
        )
    with history:
        rows = []
        event_days = {event["at"].astimezone(JST).date() for event in events}
        for offset in range(14, 0, -1):
            day = today - timedelta(days=offset)
            if day.weekday() < 5 and day not in event_days:
                rows.append(
                    {
                        "日付 / Date": day.isoformat(),
                        "出勤 / In": "09:00",
                        "退勤 / Out": "18:00",
                        "勤務 / Hours": "8.00",
                        "記録 / Source": "サンプル / Sample",
                    }
                )
        for day in sorted(event_days):
            daily = [event for event in events if event["at"].date() == day]
            start = next(
                (event["at"].strftime("%H:%M") for event in daily if event["action"] == "in"), "—"
            )
            end = next(
                (event["at"].strftime("%H:%M") for event in daily if event["action"] == "out"), "—"
            )
            rows.append(
                {
                    "日付 / Date": day.isoformat(),
                    "出勤 / In": start,
                    "退勤 / Out": end,
                    "勤務 / Hours": f"{worked_minutes(events, day, now) / 60:.2f}",
                    "記録 / Source": "このセッション / Session",
                }
            )
        table(pd.DataFrame(rows).sort_values("日付 / Date", ascending=False))
    with vacation:
        if st.session_state.get("leave_notice"):
            st.success(
                "デモ内で申請しました。上司や人事への送信はありません / No external submission."
            )
        with st.form("leave_application"):
            left, right = st.columns(2)
            start = left.date_input("開始日 / Start", today, min_value=today)
            end = right.date_input("終了日 / End", today, min_value=today)
            reason = st.text_input("理由 / Sample reason", "私用（架空）", max_chars=200)
            submitted = st.form_submit_button("デモ内で申請 / Request in demo")
        if submitted:
            errors = validate_leave(start, end, requests, today=today)
            if not reason.strip():
                errors.append("架空の理由を入力してください / Enter a sample reason.")
            if errors:
                for error in errors:
                    st.error(error)
            else:
                requests.append(
                    {
                        "id": f"DEMO-LEAVE-{len(requests) + 1:03d}",
                        "start": start,
                        "end": end,
                        "days": leave_days(start, end),
                        "reason": reason.strip(),
                        "status": "申請中 / Requested",
                    }
                )
                st.session_state["leave_notice"] = True
                st.rerun()
        table(
            pd.DataFrame(
                [
                    {
                        "受付 / Reference": request["id"],
                        "開始 / Start": request["start"].isoformat(),
                        "終了 / End": request["end"].isoformat(),
                        "日数 / Days": request["days"],
                        "状態 / Status": request["status"],
                    }
                    for request in requests
                ]
            )
        )
        for request in requests:
            if request["status"] != "取り下げ / Withdrawn" and st.button(
                f"取り下げ / Withdraw {request['id']}", key=f"withdraw_{request['id']}"
            ):
                request["status"] = "取り下げ / Withdrawn"
                st.session_state.pop("leave_notice", None)
                st.rerun()
        st.caption(
            "15日を仮設定し、申請中の日数も差し引きます。土日を除外、祝日は未対応。 / "
            "Illustrative 15-day allowance; pending days reserved; "
            "weekdays only, no holiday calendar."
        )
    demo_note()
    reset_button("attendance", ["attendance_events", "leave_requests", "leave_notice"])
