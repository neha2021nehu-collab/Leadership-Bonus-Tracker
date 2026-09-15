# Leadership Bonus Tracker

A simple browser-based tool to calculate monthly leadership bonuses from three Excel files:

1. Reporting audit log
2. Timesheet
3. Attendance (kept for record only, not used in calculation)

## One-time setup

Install Python 3.11+, then in this folder run:

```bash
pip install -r requirements.txt
```

## Run the app

```bash
streamlit run app.py
```

A browser tab will open. Upload the three files, pick a month + year, fill in each manager's per-hour rates for Billable / Non-Billable / Bench, then click **Calculate bonuses**. Download the result as Excel.

Rates are remembered between runs in `rates.json`.

## How the numbers are computed

- **Manager for the month** = the most recent audit-log entry for that employee with `Modified Time ≤ last day of selected month`.
- **Hour buckets**:
  - `Bench` — project name is `Bench / Internal Pool`
  - `Billable` — `Billable Status = Billable` (and not bench)
  - `Non-Billable` — everything else
- **Bonus per manager** = Σ over their employees of `hours_bucket × rate_bucket`.
- Employees with no manager as of month-end are grouped under **Unassigned**.
