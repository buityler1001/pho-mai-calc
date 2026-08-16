"""Internal tip-distribution UI for Phở Mai.

Wraps the approved calculation engine in tip_distributor.py.
Does not change business rules — only collects inputs and displays results.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from uuid import uuid4

import streamlit as st

from tip_distributor import (
    AM_SHIFT,
    PM_SHIFT,
    EmployeeShift,
    ShiftTipInput,
    TipDistributionError,
    calculate_day,
    format_currency,
    format_hours,
)

SCHEDULE_PRESETS = {
    "AM": (AM_SHIFT.start, AM_SHIFT.end),
    "PM": (PM_SHIFT.start, PM_SHIFT.end),
    "Both": (AM_SHIFT.start, PM_SHIFT.end),
    "Custom": None,
}

ROLE_OPTIONS = ("Waiter", "Busser")
ROW_WIDGET_PREFIXES = (
    "emp_name_",
    "emp_role_",
    "emp_schedule_",
    "emp_start_",
    "emp_end_",
    "emp_remove_",
)


def _money(value: Decimal) -> str:
    return format_currency(value)


def _new_row_id() -> str:
    return uuid4().hex


def _employee_row(
    name: str,
    role: str,
    schedule: str,
    start: time,
    end: time,
) -> dict:
    return {
        "id": _new_row_id(),
        "name": name,
        "role": role,
        "schedule": schedule,
        "start": start,
        "end": end,
    }


def _default_employees() -> list[dict]:
    return [
        _employee_row("Waiter 1", "Waiter", "Both", AM_SHIFT.start, PM_SHIFT.end),
        _employee_row("Waiter 2", "Waiter", "Both", AM_SHIFT.start, PM_SHIFT.end),
        _employee_row("Waiter 3", "Waiter", "AM", AM_SHIFT.start, AM_SHIFT.end),
        _employee_row("Waiter 4", "Waiter", "PM", PM_SHIFT.start, PM_SHIFT.end),
        _employee_row("Busser 1", "Busser", "Both", AM_SHIFT.start, PM_SHIFT.end),
        _employee_row("Busser 2", "Busser", "Both", AM_SHIFT.start, PM_SHIFT.end),
    ]


def _clear_row_widgets(row_id: str) -> None:
    for prefix in ROW_WIDGET_PREFIXES:
        st.session_state.pop(f"{prefix}{row_id}", None)


def _next_label(role: str, employees: list[dict]) -> str:
    prefix = "Waiter" if role == "Waiter" else "Busser"
    used = {
        row["name"].strip().casefold()
        for row in employees
        if row["name"].strip()
    }
    index = 1
    while f"{prefix} {index}".casefold() in used:
        index += 1
    return f"{prefix} {index}"


def _apply_schedule(row: dict, schedule: str) -> None:
    row["schedule"] = schedule
    preset = SCHEDULE_PRESETS[schedule]
    if preset is not None:
        row["start"], row["end"] = preset


def _ensure_row_ids(employees: list[dict]) -> None:
    """Backfill stable IDs for rows created before this fix."""

    for row in employees:
        if "id" not in row:
            row["id"] = _new_row_id()


def _init_state() -> None:
    # Reset rows that still use the old index-based widget keys so remove
    # actions cannot shift values onto the wrong employee.
    if st.session_state.get("employee_rows_version") != 2:
        st.session_state.employees = _default_employees()
        st.session_state.employee_rows_version = 2
        st.session_state.result = None
        st.session_state.error = None
    else:
        _ensure_row_ids(st.session_state.employees)

    if "result" not in st.session_state:
        st.session_state.result = None
    if "error" not in st.session_state:
        st.session_state.error = None


def _render_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Serif:wght@600;700&display=swap');

        html, body, [class*="css"] {
            font-family: "IBM Plex Sans", sans-serif;
        }
        .stApp {
            background:
                linear-gradient(165deg, #e8eef0 0%, #f2f5f4 42%, #e4ebe7 100%);
        }
        h1, h2, h3 {
            font-family: "IBM Plex Serif", Georgia, serif !important;
            color: #143028 !important;
            letter-spacing: -0.02em;
        }
        .brand-mark {
            font-family: "IBM Plex Serif", Georgia, serif;
            font-size: 2.35rem;
            font-weight: 700;
            color: #143028;
            margin: 0 0 0.15rem 0;
            line-height: 1.1;
        }
        .brand-sub {
            color: #4d5c56;
            margin: 0 0 1.5rem 0;
            font-size: 1.02rem;
        }
        .section-note {
            color: #4d5c56;
            margin: -0.4rem 0 1rem 0;
            font-size: 0.95rem;
        }
        div[data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.72);
            border: 1px solid rgba(20, 48, 40, 0.1);
            padding: 0.75rem 1rem;
            border-radius: 8px;
        }
        .remainder-flag {
            background: #eef6f1;
            border-left: 4px solid #2f6b52;
            padding: 0.75rem 1rem;
            border-radius: 0 8px 8px 0;
            margin: 0.75rem 0 1.25rem 0;
            color: #1d3d30;
        }
        .error-banner {
            background: #fde8e4;
            border-left: 4px solid #b33b2c;
            padding: 0.85rem 1rem;
            border-radius: 0 8px 8px 0;
            margin: 0.5rem 0 1rem 0;
            color: #6b2218;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_employee_editor() -> None:
    st.subheader("Employees")
    st.markdown(
        '<p class="section-note">Add each waiter and busser. Use labels like '
        "Waiter 1 / Busser 1 for now — real names can come later. "
        "Headcount is derived from who overlaps AM and PM.</p>",
        unsafe_allow_html=True,
    )

    employees: list[dict] = st.session_state.employees
    remove_id: str | None = None

    header = st.columns([2.2, 1.3, 1.3, 1.4, 1.4, 0.7])
    header[0].caption("Name")
    header[1].caption("Role")
    header[2].caption("Schedule")
    header[3].caption("Start")
    header[4].caption("End")
    header[5].caption("")

    for row in employees:
        row_id = row["id"]
        cols = st.columns([2.2, 1.3, 1.3, 1.4, 1.4, 0.7])

        row["name"] = cols[0].text_input(
            "Name",
            value=row["name"],
            key=f"emp_name_{row_id}",
            label_visibility="collapsed",
        )

        role = cols[1].selectbox(
            "Role",
            ROLE_OPTIONS,
            index=ROLE_OPTIONS.index(row["role"]),
            key=f"emp_role_{row_id}",
            label_visibility="collapsed",
        )
        row["role"] = role

        schedule = cols[2].selectbox(
            "Schedule",
            list(SCHEDULE_PRESETS.keys()),
            index=list(SCHEDULE_PRESETS.keys()).index(row["schedule"]),
            key=f"emp_schedule_{row_id}",
            label_visibility="collapsed",
        )
        if schedule != row["schedule"]:
            _apply_schedule(row, schedule)
            st.session_state[f"emp_start_{row_id}"] = row["start"]
            st.session_state[f"emp_end_{row_id}"] = row["end"]
            st.rerun()

        disabled_times = schedule != "Custom"
        start_value = row["start"] if isinstance(row["start"], time) else AM_SHIFT.start
        end_value = row["end"] if isinstance(row["end"], time) else AM_SHIFT.end

        row["start"] = cols[3].time_input(
            "Start",
            value=start_value,
            key=f"emp_start_{row_id}",
            label_visibility="collapsed",
            disabled=disabled_times,
        )
        row["end"] = cols[4].time_input(
            "End",
            value=end_value,
            key=f"emp_end_{row_id}",
            label_visibility="collapsed",
            disabled=disabled_times,
        )

        # Keep non-custom rows locked to the approved shift windows.
        if schedule != "Custom":
            row["start"], row["end"] = SCHEDULE_PRESETS[schedule]

        if cols[5].button("✕", key=f"emp_remove_{row_id}", help="Remove employee"):
            remove_id = row_id

    if remove_id is not None:
        st.session_state.employees = [
            row for row in employees if row["id"] != remove_id
        ]
        _clear_row_widgets(remove_id)
        st.session_state.result = None
        st.rerun()

    add_cols = st.columns([1, 1, 4])
    if add_cols[0].button("Add waiter", use_container_width=True):
        employees.append(
            _employee_row(
                _next_label("Waiter", employees),
                "Waiter",
                "Both",
                AM_SHIFT.start,
                PM_SHIFT.end,
            )
        )
        st.session_state.result = None
        st.rerun()

    if add_cols[1].button("Add busser", use_container_width=True):
        employees.append(
            _employee_row(
                _next_label("Busser", employees),
                "Busser",
                "Both",
                AM_SHIFT.start,
                PM_SHIFT.end,
            )
        )
        st.session_state.result = None
        st.rerun()


def _render_tip_inputs() -> tuple[dict[str, str], dict[str, str]]:
    st.subheader("Tip totals")
    st.markdown(
        '<p class="section-note">Enter cash, credit-card tips, and auto-gratuity '
        "separately for AM (10:00 AM–4:00 PM) and PM (4:00 PM–8:30 PM).</p>",
        unsafe_allow_html=True,
    )

    am_col, pm_col = st.columns(2)

    with am_col:
        st.markdown("**AM shift**")
        am_tips = {
            "cash": st.text_input("AM cash tips", value="0.00", key="am_cash"),
            "credit_card": st.text_input(
                "AM credit-card tips", value="0.00", key="am_cc"
            ),
            "gratuity": st.text_input(
                "AM auto-gratuity", value="0.00", key="am_grat"
            ),
        }

    with pm_col:
        st.markdown("**PM shift**")
        pm_tips = {
            "cash": st.text_input("PM cash tips", value="0.00", key="pm_cash"),
            "credit_card": st.text_input(
                "PM credit-card tips", value="0.00", key="pm_cc"
            ),
            "gratuity": st.text_input(
                "PM auto-gratuity", value="0.00", key="pm_grat"
            ),
        }

    return am_tips, pm_tips


def _build_employees(rows: list[dict]) -> list[EmployeeShift]:
    employees: list[EmployeeShift] = []
    for row in rows:
        name = row["name"].strip()
        if not name:
            raise TipDistributionError("Every employee needs a name.")
        employees.append(
            EmployeeShift(
                name=name,
                role=row["role"].lower(),
                start_time=row["start"],
                end_time=row["end"],
            )
        )
    if not employees:
        raise TipDistributionError("Add at least one employee before calculating.")
    return employees


def _render_shift_summary(title: str, shift) -> None:
    st.markdown(f"#### {title}")
    st.write(
        f"Staffing: **{shift.waiter_count}** waiter(s), "
        f"**{shift.busser_count}** busser(s)"
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Cash", _money(shift.cash))
    m2.metric("Credit card", _money(shift.credit_card))
    m3.metric("Auto-gratuity", _money(shift.gratuity))
    m4.metric("Shift total", _money(shift.total_tips))

    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Waiter pool", _money(shift.waiter_pool))
    p2.metric("Busser pool", _money(shift.busser_pool))
    p3.metric("Fee (2.5%)", _money(shift.credit_card_fee))
    p4.metric("Rounding remainder", _money(shift.rounding_remainder))

    if shift.rounding_remainder > 0:
        st.markdown(
            f'<div class="remainder-flag">Rounding remainder for {shift.shift_name}: '
            f"<strong>{_money(shift.rounding_remainder)}</strong> "
            "(payments and fee round down; leftover cents are not silently dropped).</div>",
            unsafe_allow_html=True,
        )


def _render_results(result) -> None:
    st.subheader("Results")

    table_rows = [
        {
            "Employee": employee.name,
            "Role": employee.role.value.title(),
            "AM hours": format_hours(employee.am_hours),
            "AM tips": _money(employee.am_tips),
            "PM hours": format_hours(employee.pm_hours),
            "PM tips": _money(employee.pm_tips),
            "Daily tips": _money(employee.total_tips),
        }
        for employee in result.employees
    ]
    st.dataframe(table_rows, use_container_width=True, hide_index=True)

    _render_shift_summary("AM shift summary", result.am)
    _render_shift_summary("PM shift summary", result.pm)

    st.markdown("#### Daily reconciliation")
    r1, r2, r3, r4, r5 = st.columns(5)
    r1.metric("Total input tips", _money(result.total_input_tips))
    r2.metric("Employee totals", _money(result.total_employee_tips))
    r3.metric("Total fees", _money(result.total_credit_card_fees))
    r4.metric("Total remainder", _money(result.total_rounding_remainder))
    reconciled = (
        result.total_employee_tips
        + result.total_credit_card_fees
        + result.total_rounding_remainder
    )
    r5.metric("Reconciled total", _money(reconciled))


def main() -> None:
    st.set_page_config(
        page_title="Phở Mai Tip Distributor",
        layout="wide",
    )
    _init_state()
    _render_styles()

    st.markdown('<p class="brand-mark">Phở Mai</p>', unsafe_allow_html=True)
    st.markdown(
        '<p class="brand-sub">Tip distributor — enter staff and shift tip totals</p>',
        unsafe_allow_html=True,
    )

    _render_employee_editor()
    am_tips_raw, pm_tips_raw = _render_tip_inputs()

    st.divider()
    calc_col, clear_col, _ = st.columns([1.4, 1, 3])
    calculate = calc_col.button("Calculate tips", type="primary", use_container_width=True)
    clear = clear_col.button("Clear results", use_container_width=True)

    if clear:
        st.session_state.result = None
        st.session_state.error = None
        st.rerun()

    if calculate:
        st.session_state.error = None
        st.session_state.result = None
        try:
            employees = _build_employees(st.session_state.employees)
            result = calculate_day(
                employees=employees,
                am_tips=ShiftTipInput(
                    cash=am_tips_raw["cash"],
                    credit_card=am_tips_raw["credit_card"],
                    gratuity=am_tips_raw["gratuity"],
                ),
                pm_tips=ShiftTipInput(
                    cash=pm_tips_raw["cash"],
                    credit_card=pm_tips_raw["credit_card"],
                    gratuity=pm_tips_raw["gratuity"],
                ),
            )
            st.session_state.result = result
        except TipDistributionError as exc:
            st.session_state.error = str(exc)

    if st.session_state.error:
        st.markdown(
            f'<div class="error-banner"><strong>Could not calculate:</strong> '
            f"{st.session_state.error}</div>",
            unsafe_allow_html=True,
        )

    if st.session_state.result is not None:
        _render_results(st.session_state.result)


if __name__ == "__main__":
    main()
