# Restaurant Tip Distributor — Cursor Project Context

## 1. Project purpose

Build an automated restaurant tip-distribution calculator that replaces the restaurant's current manual Excel workflow.

The calculator determines employee tip earnings for two daily shifts using:

- Employee name
- Employee role
- Employee start and end time
- AM cash tips
- AM credit-card tips
- AM auto-gratuity
- PM cash tips
- PM credit-card tips
- PM auto-gratuity
- The number of active waiters and bussers during each shift

The existing calculation engine is implemented in:

```text
tip_distributor.py
```

The calculation engine currently works and has been manually tested. Future work should extend it without changing the approved business rules unless the owner explicitly requests a change.

---

## 2. Business context

The restaurant currently calculates tips manually in an Excel workbook. The active formulas came from the workbook's `Tipout` sheet.

Only two employee categories are needed for version 1:

1. Waiters
2. Bussers

Restaurant bartenders and bar staff are equivalent to bussers for tip-distribution purposes. The code therefore accepts `bar` and `bartender` as aliases and converts them to the `busser` role.

Do not include host or dishwasher allocations in version 1. Those formulas are obsolete and should be disregarded.

---

## 3. Shift definitions

There are two independently calculated shifts:

| Shift | Start | End | Full-shift duration |
|---|---:|---:|---:|
| AM | 10:00 AM | 4:00 PM | 6 hours |
| PM | 4:00 PM | 8:30 PM | 4.5 hours |

An employee can work:

- AM only
- PM only
- Both shifts
- Custom hours

Examples:

| Employee schedule | AM hours | PM hours |
|---|---:|---:|
| 10:00 AM–4:00 PM | 6 | 0 |
| 4:00 PM–8:30 PM | 0 | 4.5 |
| 10:00 AM–8:30 PM | 6 | 4.5 |
| 10:00 AM–1:00 PM | 3 | 0 |
| 2:00 PM–6:00 PM | 2 | 2 |

Actual start and end times are the source of truth. A future UI may offer AM, PM, Both, and Custom shortcuts, but those selections should simply populate the appropriate start and end times.

Employees are assumed to work a continuous, same-day interval. Overnight shifts are not supported in version 1.

---

## 4. Shift tip inputs

The user enters three monetary values separately for AM and PM:

```text
cash tips
credit-card tips
auto-gratuity
```

The total tip amount for a shift is:

```text
shift total = cash tips + credit-card tips + auto-gratuity
```

Cash tips have not already been taken by employees. They are part of the total amount that must be distributed.

All three tip types are placed into the same shift pool before the staffing percentages are applied.

Negative monetary values are invalid.

---

## 5. Supported staffing formulas

The active rules from the `Tipout` sheet are:

| Waiters | Bussers | Waiter percentage | Busser percentage | Fee percentage |
|---:|---:|---:|---:|---:|
| 3 | 2 | 75.0% | 22.5% | 2.5% |
| 4 | 2 | 80.0% | 17.5% | 2.5% |
| 4 | 3 | 77.0% | 20.5% | 2.5% |

The percentages for each rule total 100%.

In the code, these are represented by `TIP_RULES`, keyed by:

```python
(number_of_waiters, number_of_bussers)
```

Example:

```python
TIP_RULES = {
    (3, 2): StaffingRule(Decimal("0.75"), Decimal("0.225")),
    (4, 2): StaffingRule(Decimal("0.80"), Decimal("0.175")),
    (4, 3): StaffingRule(Decimal("0.77"), Decimal("0.205")),
}
```

A worker counts toward a shift's staffing headcount whenever the worker overlaps that shift by at least one minute. A partial-shift worker still counts as one employee when selecting the staffing formula.

Unsupported staffing combinations must raise a clear error. Never guess a percentage or silently fall back to a different configuration.

Examples of unsupported combinations:

- 2 waiters and 1 busser
- 3 waiters and 3 bussers
- 5 waiters and 2 bussers

New combinations can be added later by adding approved entries to `TIP_RULES`.

---

## 6. Credit-card fee rule

The existing workbook calculates a 2.5% fee from the entire shift total:

```text
fee = shift total × 2.5%
```

This means the fee is currently based on:

```text
cash + credit-card tips + auto-gratuity
```

Although the feature may be revised later, the current model must continue following the workbook rule.

The fee should be calculated and included in reconciliation, even though fee enhancements are not the current priority.

---

## 7. Role-pool calculation

Once a shift's staffing rule is selected:

```text
waiter pool = shift total × waiter percentage
busser pool = shift total × busser percentage
fee = shift total × fee percentage
```

Example for a shift with 3 waiters, 2 bussers, and $1,000 in total tips:

```text
waiter pool = $1,000 × 75.0% = $750
busser pool = $1,000 × 22.5% = $225
fee         = $1,000 × 2.5%  = $25
```

Waiters only share the waiter pool. Bussers only share the busser pool.

---

## 8. Full-shift and partial-shift distribution logic

### 8.1 All employees in a role work the full shift

Divide the role's pool equally by role headcount.

Example:

```text
waiter pool = $600
3 full-shift waiters
normal share = $600 ÷ 3 = $200 each
```

### 8.2 One or more employees in a role work partial hours

The approved logic is:

1. Calculate the equal full-shift share using the total role headcount, including partial employees.
2. Convert that equal share into an hourly value for the applicable shift.
3. Give each partial worker the prorated portion of that equal share.
4. Subtract all partial-worker amounts from the role pool.
5. Divide the entire remaining pool equally among the full-shift workers in that role.

Example:

- AM waiter pool: $600
- Waiter 1: 6 hours
- Waiter 2: 6 hours
- Waiter 3: 3 hours

Calculation:

```text
equal full-shift share = $600 ÷ 3 = $200
partial ratio = 3 hours ÷ 6 hours = 0.5
Waiter 3 = $200 × 0.5 = $100
remaining pool = $600 - $100 = $500
Waiter 1 = $500 ÷ 2 = $250
Waiter 2 = $500 ÷ 2 = $250
```

This is intentionally different from simply allocating the entire pool proportionally by total hours.

### 8.3 Every worker in a role is partial

There would be no full-shift employee to absorb the unused portions. The current fallback is therefore:

```text
allocate the entire role pool proportionally by minutes worked
```

This fallback ensures that the complete role pool can still be allocated.

### 8.4 Apply the logic independently by role and shift

Partial calculations must be performed separately for:

- AM waiters
- AM bussers
- PM waiters
- PM bussers

AM and PM must never be merged into one tip pool.

---

## 9. Rounding policy

The restaurant's approved policy is to always round monetary payments down to the nearest cent.

Use Python `Decimal` and `ROUND_DOWN`. Do not use binary floating-point arithmetic for business calculations.

Example:

```text
$100 ÷ 3 = $33.333...
Each displayed payment = $33.33
Total distributed = $99.99
Rounding remainder = $0.01
```

Never silently discard the remainder. Report it as an undistributed rounding remainder.

The reconciliation relationship is:

```text
employee distributions + fee + rounding remainder = total shift tips
```

The current implementation rounds each employee's tip down, rounds the fee down, and assigns all remaining cents to the reported rounding remainder.

---

## 10. Current Python implementation

### 10.1 Major public objects

The current module contains these key classes and functions:

```python
Role
ShiftDefinition
StaffingRule
EmployeeShift
ShiftTipInput
EmployeeShiftResult
ShiftResult
DailyEmployeeResult
DailyResult

calculate_shift(...)
calculate_day(...)
overlap_minutes(...)
parse_time(...)
round_down_money(...)
print_daily_report(...)
run_interactive_cli()
example()
```

### 10.2 Core usage

```python
from tip_distributor import EmployeeShift, ShiftTipInput, calculate_day

employees = [
    EmployeeShift("Waiter 1", "waiter", "10:00 AM", "8:30 PM"),
    EmployeeShift("Waiter 2", "waiter", "10:00 AM", "8:30 PM"),
    EmployeeShift("Waiter 3", "waiter", "10:00 AM", "1:00 PM"),
    EmployeeShift("Waiter 4", "waiter", "4:00 PM", "8:30 PM"),
    EmployeeShift("Busser 1", "busser", "10:00 AM", "8:30 PM"),
    EmployeeShift("Busser 2", "bar", "10:00 AM", "8:30 PM"),
]

result = calculate_day(
    employees=employees,
    am_tips=ShiftTipInput(
        cash="88.00",
        credit_card="399.00",
        gratuity="12.00",
    ),
    pm_tips=ShiftTipInput(
        cash="0.00",
        credit_card="521.52",
        gratuity="118.21",
    ),
)
```

### 10.3 Command-line usage

Run the interactive calculator:

```bash
python tip_distributor.py --interactive
```

Run the built-in example:

```bash
python tip_distributor.py --example
```

Running without an argument also runs the example.

### 10.4 Role aliases

The following inputs are accepted:

| Input | Internal role |
|---|---|
| `waiter` | waiter |
| `server` | waiter |
| `busser` | busser |
| `bus` | busser |
| `bar` | busser |
| `bartender` | busser |

A future UI should normally display only `Waiter` and `Busser`, because bar employees are operationally combined with bussers.

---

## 11. Existing validation behavior

The calculation engine currently validates the following:

- Employee name cannot be blank.
- Employee names must be unique for the day, case-insensitively.
- Role must resolve to waiter or busser.
- Start and end times must be valid.
- End time must be later than start time on the same day.
- Monetary inputs cannot be blank, invalid, non-finite, or negative.
- A shift with entered tips must have employees overlapping it.
- The staffing combination must exist in `TIP_RULES`.
- A nonzero role pool must have employees to receive it.

Validation errors inherit from:

```python
TipDistributionError
```

Unsupported staffing combinations raise:

```python
UnsupportedStaffingError
```

A UI should catch these errors and display their messages clearly instead of exposing a stack trace.

---

## 12. Required output

The user-facing result should show one row per employee:

| Employee | Role | AM hours | AM tips | PM hours | PM tips | Daily tips |
|---|---|---:|---:|---:|---:|---:|

It should also show an AM summary and PM summary containing:

- Staffing configuration
- Cash tips
- Credit-card tips
- Auto-gratuity
- Total shift tips
- Waiter pool
- Busser pool
- Fee
- Employee distributions
- Rounding remainder
- Reconciliation status

Daily totals should show:

- Total input tips
- Total employee distributions
- Total fees
- Total rounding remainder
- Reconciled total

The UI should make any nonzero rounding remainder visible.

---

## 13. Recommended application architecture

Keep the calculation engine independent from the interface.

Suggested separation:

```text
tip_distributor.py       # Approved business logic and data models
app.py                   # Future UI or web application
storage.py               # Optional persistence layer added later
tests/                   # Automated tests
README.md                # Installation and usage instructions
CURSOR_CONTEXT.md        # This project context
```

Important architectural rules:

1. Do not place UI-specific code inside core calculation functions.
2. Do not duplicate AM and PM calculation logic.
3. Continue using one reusable `calculate_shift` function called by `calculate_day`.
4. Keep staffing rules configurable in one location.
5. Use `Decimal` throughout calculations.
6. Do not use floats for percentages, money, or allocation math.
7. Preserve pure, deterministic calculation functions so they can be tested easily.
8. Do not silently alter business rules while refactoring.
9. Do not read the original Excel file at runtime for version 1; the approved active rules are already represented in code.

---

## 14. Suggested first UI

A future interface should allow the user to add employee rows containing:

```text
Employee name
Role: Waiter or Busser
Schedule shortcut: AM, PM, Both, or Custom
Start time
End time
```

Behavior:

- AM populates 10:00 AM and 4:00 PM.
- PM populates 4:00 PM and 8:30 PM.
- Both populates 10:00 AM and 8:30 PM.
- Custom allows direct time entry.
- The generated start and end times should be submitted to `EmployeeShift`.

The interface should have separate AM and PM tip sections:

```text
Cash
Credit-card tips
Auto-gratuity
```

The primary action should be:

```text
Calculate Tips
```

The UI should not ask the user to manually select the number of waiters or bussers. Headcount should be derived from employees whose hours overlap each shift.

A user may initially believe they need to enter headcount, but automatic headcount is safer and avoids inconsistencies between the employee list and the selected formula.

---

## 15. Minimum automated test cases

Create automated tests before making major modifications.

### Test 1: Three waiters and two bussers, all full AM

Verify:

- `(3, 2)` rule is selected.
- Waiter pool is 75%.
- Busser pool is 22.5%.
- Fee is 2.5%.
- Each role pool is divided equally.
- Reconciliation succeeds.

### Test 2: Four waiters and two bussers, all full PM

Verify the `(4, 2)` percentages and 4.5-hour shift duration.

### Test 3: Four waiters and three bussers

Verify the `(4, 3)` percentages.

### Test 4: Partial AM waiter

Use:

- Waiter 1: 10:00 AM–4:00 PM
- Waiter 2: 10:00 AM–4:00 PM
- Waiter 3: 10:00 AM–1:00 PM

Confirm the third waiter receives half of the normal equal share and the remaining amount is divided equally between the two full-shift waiters.

### Test 5: Employee crosses shift boundary

Use an employee working 2:00 PM–6:00 PM.

Confirm:

- AM overlap is 2 hours.
- PM overlap is 2 hours.
- The employee counts in both shift headcounts.

### Test 6: Both-shift employee

Use 10:00 AM–8:30 PM and confirm 6 AM hours and 4.5 PM hours.

### Test 7: Bar alias

Confirm `bar` and `bartender` are converted to busser.

### Test 8: Unsupported combination

Confirm an unsupported configuration raises `UnsupportedStaffingError` and does not calculate with guessed percentages.

### Test 9: All workers in a role are partial

Confirm allocation falls back to proportional minutes and distributes the full role pool before rounding.

### Test 10: Round-down remainder

Use values that produce fractions of cents. Confirm:

- Every employee payment rounds down.
- Fee rounds down.
- The leftover is reported as `rounding_remainder`.
- Reconciliation still equals the shift total.

### Test 11: No employees but nonzero tips

Confirm a validation error is raised.

### Test 12: Duplicate employee names

Confirm duplicate names are rejected case-insensitively.

### Test 13: Invalid time interval

Confirm end time before or equal to start time is rejected.

### Test 14: Negative tip input

Confirm negative cash, credit-card tips, or gratuity is rejected.

---

## 16. Acceptance criteria for version 1

Version 1 is correct when all of the following are true:

- Users can enter every employee and the employee's role and working time.
- The system independently determines AM and PM overlap.
- A partial employee counts toward headcount.
- Bar and bartender entries count as bussers.
- The correct Tipout formula is selected from headcount.
- AM and PM tip totals are calculated independently.
- Partial employees are paid using the approved equal-share proration method.
- Remaining role-pool amounts are divided among full-shift employees.
- All-partial role groups use proportional-minute fallback.
- All monetary amounts round down to cents.
- A rounding remainder is reported.
- Unsupported staffing configurations produce a visible error.
- Each employee receives AM, PM, and daily totals.
- Shift and daily reconciliation totals are visible.
- The calculation engine remains independent of the UI.

---

## 17. Out of scope for version 1

Do not add these features unless explicitly requested:

- Host tip allocations
- Dishwasher tip allocations
- Alternate or obsolete workbook formulas
- Overnight shifts
- Multiple separate intervals for the same employee in one day
- Payroll processing
- Direct Square API integration
- Employee authentication
- Database persistence
- Historical reporting
- Editing staffing percentages from the UI
- Automatically paying employees
- Changing the fee to apply only to credit-card tips

These may become future enhancements, but they must not complicate the approved first version.

---

## 18. Likely future enhancements

Potential later milestones include:

1. A tablet-friendly internal UI.
2. Saving employees and common schedules.
3. Importing tip totals from Square.
4. Importing clock-in and clock-out times.
5. A database for daily calculations and audit history.
6. Exporting results to CSV or Excel.
7. Manager approval before finalizing a day.
8. Editable staffing rules with version history.
9. Improved fee handling.
10. Authentication and role-based access.
11. Support for split shifts or multiple intervals.
12. Automatic tests against known Excel examples.

Any future Square integration should feed data into the existing calculation engine rather than embedding Square-specific logic inside `calculate_shift` or `calculate_day`.

---

## 19. Guidance for Cursor

When working on this repository:

- Read this file and `tip_distributor.py` before changing code.
- Treat the current calculations as approved business logic.
- Ask for an explicit business decision before changing a percentage or allocation method.
- Prefer small, testable changes.
- Add or update automated tests for every behavior change.
- Preserve backward compatibility with current function signatures when reasonable.
- Keep UI, storage, integrations, and calculation logic separated.
- Return friendly validation messages to users.
- Never invent a staffing rule for an unsupported combination.
- Never use floats for financial calculations.
- Never distribute the rounding remainder silently.
- Keep AM and PM as separate calculations.
- Derive staffing headcount from actual shift overlap.

---

## 20. Suggested Cursor starter prompt

Use this prompt when beginning the next development step:

```text
Read CURSOR_CONTEXT.md and tip_distributor.py in full before making changes.
The Python calculation engine is already working and contains approved restaurant business rules. Do not rewrite or alter the calculation logic unless necessary. Keep Decimal-based money calculations and round-down behavior intact.

First, summarize the current architecture and identify the exact files you plan to add or modify. Then implement a simple internal user interface around the existing calculate_day function. The UI must collect employee name, waiter/busser role, AM/PM/Both/Custom schedule, actual start and end time, and separate AM/PM cash, credit-card tip, and gratuity totals. Derive headcount from time overlap rather than asking the user for headcount. Display employee AM, PM, and daily tips plus shift reconciliation and rounding remainders. Catch TipDistributionError and show a friendly message. Add automated tests for any new transformation or validation logic. Do not add hosts, dishwashers, Square integration, authentication, or database storage yet.
```

---

## 21. Single source of truth

For version 1, the hierarchy of authority is:

1. Explicit business decisions documented in this file
2. The working behavior in `tip_distributor.py`
3. The active formulas originally taken from the workbook's `Tipout` sheet

Obsolete host, dishwasher, and alternate workbook formulas are not authoritative.
