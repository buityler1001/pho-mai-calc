"""Automated restaurant tip distributor.

This module recreates the active waiter/busser allocation rules from the
"Tipout" sheet of the Eden Prairie calculator.

Version 1 assumptions
---------------------
* AM shift: 10:00 AM to 4:00 PM (6 hours)
* PM shift: 4:00 PM to 8:30 PM (4.5 hours)
* Supported roles: waiter and busser
* "bar" / "bartender" may be entered and is treated as busser
* Shift tips = cash + credit-card tips + gratuity
* Employees with any overlap in a shift count toward that shift's headcount
* Partial-shift workers receive a prorated equal share. The unused portion is
  split equally among full-shift workers in the same role.
* If every worker in a role is partial, the role pool is allocated in
  proportion to minutes worked.
* All displayed payments are rounded down to the nearest cent. Any leftover
  cents are reported as an undistributed rounding remainder.

The core functions have no third-party dependencies and can later be called
from a web app, desktop app, API, or other UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
import argparse
from decimal import Decimal, InvalidOperation, ROUND_DOWN, getcontext
from enum import Enum
from typing import Iterable, Mapping, Sequence


# Extra precision prevents intermediate calculations from being prematurely
# rounded. Currency outputs are explicitly rounded down to cents later.
getcontext().prec = 28

CENT = Decimal("0.01")
ZERO = Decimal("0")
MINUTES_PER_HOUR = Decimal("60")


class TipDistributionError(ValueError):
    """Base error for invalid tip-distribution inputs."""


class UnsupportedStaffingError(TipDistributionError):
    """Raised when a shift's waiter/busser combination has no rule."""


class Role(str, Enum):
    WAITER = "waiter"
    BUSSER = "busser"

    @classmethod
    def parse(cls, value: str | "Role") -> "Role":
        if isinstance(value, cls):
            return value

        normalized = value.strip().lower()
        aliases = {
            "waiter": cls.WAITER,
            "server": cls.WAITER,
            "busser": cls.BUSSER,
            "bus": cls.BUSSER,
            "bar": cls.BUSSER,
            "bartender": cls.BUSSER,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise TipDistributionError(
                f"Unsupported role {value!r}. Use 'waiter' or 'busser'."
            ) from exc


@dataclass(frozen=True)
class ShiftDefinition:
    name: str
    start: time
    end: time

    @property
    def duration_minutes(self) -> int:
        return _minutes_between(self.start, self.end)

    @property
    def duration_hours(self) -> Decimal:
        return Decimal(self.duration_minutes) / MINUTES_PER_HOUR


AM_SHIFT = ShiftDefinition("AM", time(10, 0), time(16, 0))
PM_SHIFT = ShiftDefinition("PM", time(16, 0), time(20, 30))
SHIFTS: tuple[ShiftDefinition, ShiftDefinition] = (AM_SHIFT, PM_SHIFT)


@dataclass(frozen=True)
class StaffingRule:
    waiter_percentage: Decimal
    busser_percentage: Decimal
    credit_card_fee_percentage: Decimal = Decimal("0.025")

    def __post_init__(self) -> None:
        total = (
            self.waiter_percentage
            + self.busser_percentage
            + self.credit_card_fee_percentage
        )
        if total != Decimal("1"):
            raise ValueError(f"Staffing rule percentages must total 1.00; got {total}.")


# Active waiter/busser formulas from the Tipout sheet.
# Key format: (number_of_waiters, number_of_bussers)
TIP_RULES: Mapping[tuple[int, int], StaffingRule] = {
    (3, 2): StaffingRule(Decimal("0.75"), Decimal("0.225")),
    (4, 2): StaffingRule(Decimal("0.80"), Decimal("0.175")),
    (4, 3): StaffingRule(Decimal("0.77"), Decimal("0.205")),
}


@dataclass(frozen=True)
class EmployeeShift:
    """One employee's continuous work interval for the day."""

    name: str
    role: Role | str
    start_time: time | str
    end_time: time | str

    def __post_init__(self) -> None:
        clean_name = self.name.strip()
        if not clean_name:
            raise TipDistributionError("Employee name cannot be blank.")

        parsed_role = Role.parse(self.role)
        parsed_start = parse_time(self.start_time)
        parsed_end = parse_time(self.end_time)

        if _time_to_minutes(parsed_end) <= _time_to_minutes(parsed_start):
            raise TipDistributionError(
                f"{clean_name}: end time must be later than start time on the same day."
            )

        object.__setattr__(self, "name", clean_name)
        object.__setattr__(self, "role", parsed_role)
        object.__setattr__(self, "start_time", parsed_start)
        object.__setattr__(self, "end_time", parsed_end)


@dataclass(frozen=True)
class ShiftTipInput:
    cash: Decimal | str | int | float
    credit_card: Decimal | str | int | float
    gratuity: Decimal | str | int | float

    def __post_init__(self) -> None:
        cash = to_decimal(self.cash, "cash tips")
        credit_card = to_decimal(self.credit_card, "credit-card tips")
        gratuity = to_decimal(self.gratuity, "gratuity")

        for label, amount in (
            ("cash tips", cash),
            ("credit-card tips", credit_card),
            ("gratuity", gratuity),
        ):
            if amount < ZERO:
                raise TipDistributionError(f"{label.capitalize()} cannot be negative.")

        # Monetary inputs are stored at cent precision using the restaurant's
        # required round-down policy.
        object.__setattr__(self, "cash", round_down_money(cash))
        object.__setattr__(self, "credit_card", round_down_money(credit_card))
        object.__setattr__(self, "gratuity", round_down_money(gratuity))

    @property
    def total(self) -> Decimal:
        return self.cash + self.credit_card + self.gratuity


@dataclass(frozen=True)
class EmployeeShiftResult:
    name: str
    role: Role
    hours: Decimal
    tip_amount: Decimal
    tip_per_hour: Decimal
    worked_full_shift: bool


@dataclass(frozen=True)
class ShiftResult:
    shift_name: str
    shift_start: time
    shift_end: time
    cash: Decimal
    credit_card: Decimal
    gratuity: Decimal
    total_tips: Decimal
    waiter_count: int
    busser_count: int
    waiter_pool: Decimal
    busser_pool: Decimal
    credit_card_fee: Decimal
    employee_results: tuple[EmployeeShiftResult, ...]
    rounding_remainder: Decimal

    @property
    def total_employee_tips(self) -> Decimal:
        return sum((result.tip_amount for result in self.employee_results), ZERO)

    @property
    def reconciled_total(self) -> Decimal:
        return self.total_employee_tips + self.credit_card_fee + self.rounding_remainder


@dataclass
class DailyEmployeeResult:
    name: str
    role: Role
    am_hours: Decimal = ZERO
    am_tips: Decimal = ZERO
    pm_hours: Decimal = ZERO
    pm_tips: Decimal = ZERO

    @property
    def total_hours(self) -> Decimal:
        return self.am_hours + self.pm_hours

    @property
    def total_tips(self) -> Decimal:
        return self.am_tips + self.pm_tips


@dataclass(frozen=True)
class DailyResult:
    am: ShiftResult
    pm: ShiftResult
    employees: tuple[DailyEmployeeResult, ...]

    @property
    def total_input_tips(self) -> Decimal:
        return self.am.total_tips + self.pm.total_tips

    @property
    def total_employee_tips(self) -> Decimal:
        return sum((employee.total_tips for employee in self.employees), ZERO)

    @property
    def total_credit_card_fees(self) -> Decimal:
        return self.am.credit_card_fee + self.pm.credit_card_fee

    @property
    def total_rounding_remainder(self) -> Decimal:
        return self.am.rounding_remainder + self.pm.rounding_remainder


@dataclass(frozen=True)
class _ActiveEmployee:
    employee: EmployeeShift
    minutes: int
    is_full_shift: bool

    @property
    def hours(self) -> Decimal:
        return Decimal(self.minutes) / MINUTES_PER_HOUR


def to_decimal(value: Decimal | str | int | float, label: str = "value") -> Decimal:
    """Convert a currency-like value to Decimal without binary-float artifacts."""

    if isinstance(value, Decimal):
        result = value
    else:
        cleaned = str(value).strip().replace("$", "").replace(",", "")
        if not cleaned:
            raise TipDistributionError(f"{label.capitalize()} cannot be blank.")
        try:
            result = Decimal(cleaned)
        except InvalidOperation as exc:
            raise TipDistributionError(
                f"Invalid {label}: {value!r}. Enter a numeric amount."
            ) from exc

    if not result.is_finite():
        raise TipDistributionError(f"{label.capitalize()} must be a finite number.")
    return result


def round_down_money(value: Decimal) -> Decimal:
    """Round a nonnegative currency amount down to the nearest cent."""

    if value < ZERO:
        raise TipDistributionError("Currency amounts cannot be negative.")
    return value.quantize(CENT, rounding=ROUND_DOWN)


def parse_time(value: time | str) -> time:
    """Parse common 12-hour and 24-hour time formats."""

    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)

    cleaned = value.strip()
    formats = (
        "%I:%M %p",
        "%I %p",
        "%H:%M",
        "%H%M",
    )
    for fmt in formats:
        try:
            return datetime.strptime(cleaned.upper(), fmt).time()
        except ValueError:
            continue

    raise TipDistributionError(
        f"Invalid time {value!r}. Examples: '10:00 AM', '4:00 PM', or '16:00'."
    )


def calculate_shift(
    shift: ShiftDefinition,
    tips: ShiftTipInput,
    employees: Sequence[EmployeeShift],
    rules: Mapping[tuple[int, int], StaffingRule] = TIP_RULES,
) -> ShiftResult:
    """Calculate one shift's employee tip allocations."""

    active = _active_employees_for_shift(employees, shift)
    waiters = [item for item in active if item.employee.role is Role.WAITER]
    bussers = [item for item in active if item.employee.role is Role.BUSSER]

    waiter_count = len(waiters)
    busser_count = len(bussers)

    if not active:
        if tips.total != ZERO:
            raise TipDistributionError(
                f"{shift.name}: tips were entered, but no employees overlap the shift."
            )
        return ShiftResult(
            shift_name=shift.name,
            shift_start=shift.start,
            shift_end=shift.end,
            cash=tips.cash,
            credit_card=tips.credit_card,
            gratuity=tips.gratuity,
            total_tips=ZERO,
            waiter_count=0,
            busser_count=0,
            waiter_pool=ZERO,
            busser_pool=ZERO,
            credit_card_fee=ZERO,
            employee_results=(),
            rounding_remainder=ZERO,
        )

    combination = (waiter_count, busser_count)
    try:
        rule = rules[combination]
    except KeyError as exc:
        supported = ", ".join(
            f"{waiter_count_} waiter(s) / {busser_count_} busser(s)"
            for waiter_count_, busser_count_ in sorted(rules)
        )
        raise UnsupportedStaffingError(
            f"{shift.name}: no Tipout rule exists for {waiter_count} waiter(s) "
            f"and {busser_count} busser(s). Supported combinations: {supported}."
        ) from exc

    exact_waiter_pool = tips.total * rule.waiter_percentage
    exact_busser_pool = tips.total * rule.busser_percentage
    exact_fee = tips.total * rule.credit_card_fee_percentage

    waiter_results = _allocate_role_pool(
        active_employees=waiters,
        exact_role_pool=exact_waiter_pool,
        shift=shift,
    )
    busser_results = _allocate_role_pool(
        active_employees=bussers,
        exact_role_pool=exact_busser_pool,
        shift=shift,
    )

    employee_results = tuple(
        sorted(
            (*waiter_results, *busser_results),
            key=lambda item: (item.role.value, item.name.lower()),
        )
    )

    rounded_fee = round_down_money(exact_fee)
    total_paid = sum((item.tip_amount for item in employee_results), ZERO)
    remainder = round_down_money(tips.total - total_paid - rounded_fee)

    return ShiftResult(
        shift_name=shift.name,
        shift_start=shift.start,
        shift_end=shift.end,
        cash=round_down_money(tips.cash),
        credit_card=round_down_money(tips.credit_card),
        gratuity=round_down_money(tips.gratuity),
        total_tips=round_down_money(tips.total),
        waiter_count=waiter_count,
        busser_count=busser_count,
        waiter_pool=round_down_money(exact_waiter_pool),
        busser_pool=round_down_money(exact_busser_pool),
        credit_card_fee=rounded_fee,
        employee_results=employee_results,
        rounding_remainder=remainder,
    )


def calculate_day(
    employees: Sequence[EmployeeShift],
    am_tips: ShiftTipInput,
    pm_tips: ShiftTipInput,
    rules: Mapping[tuple[int, int], StaffingRule] = TIP_RULES,
) -> DailyResult:
    """Calculate AM, PM, and combined daily totals."""

    _validate_unique_employee_names(employees)

    am_result = calculate_shift(AM_SHIFT, am_tips, employees, rules)
    pm_result = calculate_shift(PM_SHIFT, pm_tips, employees, rules)

    combined: dict[str, DailyEmployeeResult] = {
        employee.name: DailyEmployeeResult(name=employee.name, role=employee.role)
        for employee in employees
    }

    for result in am_result.employee_results:
        daily = combined[result.name]
        daily.am_hours = result.hours
        daily.am_tips = result.tip_amount

    for result in pm_result.employee_results:
        daily = combined[result.name]
        daily.pm_hours = result.hours
        daily.pm_tips = result.tip_amount

    return DailyResult(
        am=am_result,
        pm=pm_result,
        employees=tuple(sorted(combined.values(), key=lambda item: item.name.lower())),
    )


def _allocate_role_pool(
    active_employees: Sequence[_ActiveEmployee],
    exact_role_pool: Decimal,
    shift: ShiftDefinition,
) -> tuple[EmployeeShiftResult, ...]:
    if not active_employees:
        if exact_role_pool != ZERO:
            raise TipDistributionError(
                f"{shift.name}: a nonzero role pool has no employees to receive it."
            )
        return ()

    headcount = len(active_employees)
    equal_full_share = exact_role_pool / Decimal(headcount)
    full_workers = [item for item in active_employees if item.is_full_shift]
    partial_workers = [item for item in active_employees if not item.is_full_shift]

    exact_allocations: dict[str, Decimal] = {}

    if full_workers:
        # A partial worker receives the hourly value of an equal full-shift share.
        # The unused part of that equal share is absorbed equally by full workers.
        for worker in partial_workers:
            ratio = Decimal(worker.minutes) / Decimal(shift.duration_minutes)
            exact_allocations[worker.employee.name] = equal_full_share * ratio

        amount_left_for_full_workers = exact_role_pool - sum(
            exact_allocations.values(), ZERO
        )
        full_worker_share = amount_left_for_full_workers / Decimal(len(full_workers))
        for worker in full_workers:
            exact_allocations[worker.employee.name] = full_worker_share
    else:
        # Edge-case fallback: if everyone is partial, allocate by minutes worked so
        # the full role pool is still assigned according to time on the shift.
        total_minutes = sum(item.minutes for item in active_employees)
        if total_minutes <= 0:
            raise TipDistributionError(
                f"{shift.name}: active employees must have positive worked minutes."
            )
        for worker in active_employees:
            exact_allocations[worker.employee.name] = (
                exact_role_pool * Decimal(worker.minutes) / Decimal(total_minutes)
            )

    results: list[EmployeeShiftResult] = []
    for worker in active_employees:
        exact_amount = exact_allocations[worker.employee.name]
        rounded_amount = round_down_money(exact_amount)
        hours = worker.hours
        tip_per_hour = (
            round_down_money(rounded_amount / hours) if hours > ZERO else ZERO
        )
        results.append(
            EmployeeShiftResult(
                name=worker.employee.name,
                role=worker.employee.role,
                hours=hours,
                tip_amount=rounded_amount,
                tip_per_hour=tip_per_hour,
                worked_full_shift=worker.is_full_shift,
            )
        )

    return tuple(results)


def _active_employees_for_shift(
    employees: Iterable[EmployeeShift], shift: ShiftDefinition
) -> list[_ActiveEmployee]:
    active: list[_ActiveEmployee] = []
    for employee in employees:
        minutes = overlap_minutes(
            employee.start_time,
            employee.end_time,
            shift.start,
            shift.end,
        )
        if minutes > 0:
            active.append(
                _ActiveEmployee(
                    employee=employee,
                    minutes=minutes,
                    is_full_shift=(minutes == shift.duration_minutes),
                )
            )
    return active


def overlap_minutes(
    employee_start: time,
    employee_end: time,
    shift_start: time,
    shift_end: time,
) -> int:
    """Return same-day overlap in minutes between an employee and a shift."""

    start = max(_time_to_minutes(employee_start), _time_to_minutes(shift_start))
    end = min(_time_to_minutes(employee_end), _time_to_minutes(shift_end))
    return max(0, end - start)


def _time_to_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _minutes_between(start: time, end: time) -> int:
    minutes = _time_to_minutes(end) - _time_to_minutes(start)
    if minutes <= 0:
        raise ValueError("Shift end must be later than shift start on the same day.")
    return minutes


def _validate_unique_employee_names(employees: Sequence[EmployeeShift]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for employee in employees:
        key = employee.name.casefold()
        if key in seen:
            duplicates.add(employee.name)
        seen.add(key)
    if duplicates:
        duplicate_text = ", ".join(sorted(duplicates))
        raise TipDistributionError(
            f"Employee names must be unique for the day. Duplicate(s): {duplicate_text}."
        )


def format_currency(value: Decimal) -> str:
    return f"${value:,.2f}"


def format_hours(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_DOWN)}"


def print_daily_report(result: DailyResult) -> None:
    """Print a readable console report for a calculated day."""

    print("\nEMPLOYEE TIP DISTRIBUTION")
    print("-" * 92)
    print(
        f"{'Employee':<22} {'Role':<8} {'AM Hrs':>7} {'AM Tips':>11} "
        f"{'PM Hrs':>7} {'PM Tips':>11} {'Daily Tips':>13}"
    )
    print("-" * 92)
    for employee in result.employees:
        print(
            f"{employee.name:<22} {employee.role.value.title():<8} "
            f"{format_hours(employee.am_hours):>7} "
            f"{format_currency(employee.am_tips):>11} "
            f"{format_hours(employee.pm_hours):>7} "
            f"{format_currency(employee.pm_tips):>11} "
            f"{format_currency(employee.total_tips):>13}"
        )

    print("-" * 92)
    _print_shift_summary(result.am)
    _print_shift_summary(result.pm)

    print("\nDAILY RECONCILIATION")
    print(f"Total input tips:          {format_currency(result.total_input_tips)}")
    print(f"Employee distributions:   {format_currency(result.total_employee_tips)}")
    print(f"Credit-card fees:          {format_currency(result.total_credit_card_fees)}")
    print(f"Rounding remainder:        {format_currency(result.total_rounding_remainder)}")
    print(
        "Reconciled total:         "
        f"{format_currency(result.total_employee_tips + result.total_credit_card_fees + result.total_rounding_remainder)}"
    )


def _print_shift_summary(result: ShiftResult) -> None:
    print(f"\n{result.shift_name} SHIFT SUMMARY")
    print(
        f"Staffing: {result.waiter_count} waiter(s), {result.busser_count} busser(s)"
    )
    print(f"Total tips:                {format_currency(result.total_tips)}")
    print(f"Waiter pool:               {format_currency(result.waiter_pool)}")
    print(f"Busser pool:               {format_currency(result.busser_pool)}")
    print(f"Credit-card fee:           {format_currency(result.credit_card_fee)}")
    print(f"Rounding remainder:        {format_currency(result.rounding_remainder)}")


def run_interactive_cli() -> None:
    """Collect a day's inputs in the terminal and print the calculation."""

    print("Restaurant Tip Distributor")
    print("AM: 10:00 AM-4:00 PM | PM: 4:00 PM-8:30 PM")
    print("Supported staffing: 3W/2B, 4W/2B, 4W/3B\n")

    employee_count = _prompt_positive_int("How many employees worked today? ")
    employees: list[EmployeeShift] = []

    for index in range(1, employee_count + 1):
        print(f"\nEmployee {index}")
        name = input("  Name: ").strip()
        role = input("  Role (waiter/busser): ").strip()
        schedule = input("  Schedule (AM/PM/BOTH/CUSTOM): ").strip().upper()

        if schedule == "AM":
            start, end = AM_SHIFT.start, AM_SHIFT.end
        elif schedule == "PM":
            start, end = PM_SHIFT.start, PM_SHIFT.end
        elif schedule == "BOTH":
            start, end = AM_SHIFT.start, PM_SHIFT.end
        elif schedule == "CUSTOM":
            start = input("  Start time: ").strip()
            end = input("  End time: ").strip()
        else:
            raise TipDistributionError(
                "Schedule must be AM, PM, BOTH, or CUSTOM."
            )

        employees.append(EmployeeShift(name, role, start, end))

    am_tips = _prompt_shift_tips("AM")
    pm_tips = _prompt_shift_tips("PM")
    result = calculate_day(employees, am_tips, pm_tips)
    print_daily_report(result)


def _prompt_shift_tips(shift_name: str) -> ShiftTipInput:
    print(f"\n{shift_name} tip totals")
    return ShiftTipInput(
        cash=input("  Cash tips: $"),
        credit_card=input("  Credit-card tips: $"),
        gratuity=input("  Gratuity: $"),
    )


def _prompt_positive_int(prompt: str) -> int:
    raw = input(prompt).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise TipDistributionError("Employee count must be a whole number.") from exc
    if value <= 0:
        raise TipDistributionError("Employee count must be greater than zero.")
    return value


def example() -> DailyResult:
    """Return a sample day demonstrating full, partial, and both-shift workers."""

    employees = [
        EmployeeShift("Waiter 1", "waiter", "10:00 AM", "8:30 PM"),
        EmployeeShift("Waiter 2", "waiter", "10:00 AM", "8:30 PM"),
        EmployeeShift("Waiter 3", "waiter", "10:00 AM", "1:00 PM"),
        EmployeeShift("Waiter 4", "waiter", "4:00 PM", "8:30 PM"),
        EmployeeShift("Busser 1", "busser", "10:00 AM", "8:30 PM"),
        EmployeeShift("Busser 2", "bar", "10:00 AM", "8:30 PM"),
    ]

    return calculate_day(
        employees=employees,
        am_tips=ShiftTipInput(cash="88", credit_card="399", gratuity="12"),
        pm_tips=ShiftTipInput(cash="0", credit_card="521.52", gratuity="118.21"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calculate restaurant AM and PM tip distributions."
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for employees, schedules, and AM/PM tip totals.",
    )
    parser.add_argument(
        "--example",
        action="store_true",
        help="Run the built-in demonstration data (the default behavior).",
    )
    args = parser.parse_args()

    try:
        if args.interactive:
            run_interactive_cli()
        else:
            print_daily_report(example())
    except TipDistributionError as exc:
        parser.exit(status=2, message=f"Error: {exc}\n")


if __name__ == "__main__":
    main()
