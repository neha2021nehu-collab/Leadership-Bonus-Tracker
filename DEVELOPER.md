# Leadership Bonus Tracker — Developer Guide

A single-page Streamlit application that calculates monthly **leadership bonuses** from three
Excel inputs. It derives a management hierarchy from a reporting audit log, splits each
employee's monthly hours across the managers they reported to (day-weighted for mid-month
changes), and pays each manager a bonus computed from **their team's hours × their own rates**.

There is no database. The user uploads Excel files, sets rates in the UI, clicks **Calculate**,
and downloads Excel results. Rates persist to a local `rates.json`.

---

## 1. Tech stack

| Concern | Choice |
|---|---|
| Language | Python 3.11+ |
| UI | Streamlit (`app.py`) |
| Data | pandas |
| Excel read | openpyxl |
| Excel write | xlsxwriter |
| Persistence | `rates.json` (flat file) |

`requirements.txt`:
```
streamlit>=1.30
pandas>=2.0
openpyxl>=3.1
xlsxwriter>=3.1
```

Run:
```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## 2. Directory structure

```
Leadership_Bonus_Tracker/
├── app.py               # Streamlit UI + orchestration (all wiring lives here)
├── requirements.txt
├── rates.json           # persisted rates (auto-created/updated by the app)
├── README.md            # short user-facing note (may lag behind features)
├── DEVELOPER.md         # this document
├── outputs/             # scratch dir for generated files (not required at runtime)
└── src/
    ├── __init__.py
    ├── reporting.py     # audit log parse, manager-as-of, day-weighted segments
    ├── hierarchy.py     # build the Director→PL→L1→L2→L3 tree, assign levels
    ├── timesheet.py     # parse timesheet, bucket hours, monthly aggregation
    ├── attendance.py    # parse monthly attendance grid → Present Days
    └── bonus.py         # rates I/O, earner selection, attribution, bonus math
```

**Design rule:** `src/` modules are pure/stateless (dataframes in, dataframes out). All
Streamlit state, uploads, session handling, and Excel export live in `app.py`.

---

## 3. Inputs (exact formats)

### 3.1 Reporting audit log (single `.xlsx`)
One sheet. Columns:

```
Employee ID | First Name | Last Name | Email ID | Modified Time | Old Value | New Value
```

Each row is a manager-change event. `New Value` is the **new manager**, formatted `"<id> <name>"`
(e.g. `1366 Ravi Mishra`). `Old Value` is the previous manager (blank for the first assignment).
An employee may have multiple rows over time.

### 3.2 Timesheet (single `.xlsx`)
One sheet named `Timesheet`. Columns:

```
Month of Date | Employee ID | First Name | Project Name | Billable Status | APPROVAL STATUS | Total Hours
```

`Month of Date` is a **section header** — populated only on the first row of each month block,
blank thereafter — so it is forward-filled on load. Values are month abbreviations (`Apr`…`Mar`).

### 3.3 Attendance (one `.xlsx` per month, optional)
Filename **must** match `Attendance_YYYY-MM_Month.xlsx` (e.g. `Attendance_2026-05_May.xlsx`) —
the year/month are parsed from the name. The sheet has ~5 metadata rows, then a header row whose
first cell is `Employee Id`, then one row per employee with daily status codes across the columns
(`P`, `A`, `H`, `W`, `W/P`, `0.5CL/0.5P`, leave codes, …).

Attendance is **display-only** — it never affects bonus amounts.

---

## 4. Core domain concepts

### 4.1 Hierarchy & levels (`src/hierarchy.py`)
The org tree is derived **entirely from data** — nothing is hardcoded.

- Build a `parent` map `employee_id → manager_id` from the manager snapshot.
- A **root** (Director) is any manager id that never appears as an employee id (`parent[id] is None`).
  Detected automatically; supports one or many roots.
- **Depth** = number of steps to the root. **Level** name by depth:

  | Depth | Level |
  |------|-------|
  | 0 | Director |
  | 1 | PL |
  | 2 | L1 |
  | 3 | L2 |
  | 4 | L3 |
  | 5+ | L4, L5, … (`_level_name`) |

`build_hierarchy(mgr_map)` returns one row per person with:
`Person ID, Person Name, Manager ID, Depth, Level, Director ID/Name, PL ID/Name,
L1 ID/Name, L2 ID/Name, Ancestors` (list of ids, direct→root).

### 4.2 Hour buckets (`src/timesheet.py`)
Only rows with `APPROVAL STATUS == "Approved"` (case-insensitive) count. A row is bucketed:

- **Bench** — `Project Name == "Certification & Upskilling"` **and** `Billable Status == Non-Billable`.
- **Billable** — `Billable Status == Billable`.
- **Non-Billable** — `Billable Status == Non-Billable` (and not Bench).
- Anything else / not approved → `None` (dropped).

Bench is exclusive: a Certification+Non-Billable+Approved row is Bench only, never also Non-Billable.

### 4.3 Day-weighted manager segments (`src/reporting.py`)
Because the timesheet is **monthly** (no daily hours), a mid-month manager change is handled by
**pro-rating the month's hours by calendar days under each manager**.

`manager_segments_for_month(reporting_df, year, month_num)` returns, per
`(Employee ID, Manager ID)`, the `Days` and `Fraction` of the month spent under that manager.

Rules:
- A reporting change takes effect on **its calendar date** (start of that day). If two changes
  fall on the same date, the later one wins that day.
- Days **before an employee's first assignment** have no manager → **dropped** (they never appear
  as a segment; the denominator stays the full month, so those hours are discarded, not
  redistributed).
- `Fraction = days_under_manager / calendar_days_in_month`.
- No-change month → single segment, `Fraction == 1.0`.

### 4.4 Who earns, and from whom
- **Director** earns nothing.
- **PL** earns **Direct** on their L1 reports' hours, and **Indirect** on all hours below those
  L1s (L2 + L3 + …).
- **L1** earns **Direct** on their L2 reports' hours.
- **L2** earns **Direct** on their L3 reports' hours.
- **L3** (leaf) earns nothing.

An earner is **never** credited for their own hours — hours always flow **up** to a manager.
`DIRECT_EARNING_LEVELS = {"PL", "L1", "L2"}`.

### 4.5 Rates — per person, per basis
Rates are keyed by **person id**, not by level (each manager can have different rates):

```json
{
  "persons": {
    "1229": { "direct":   { "Billable": 10, "Non-Billable": 5, "Bench": 2 },
              "indirect": { "Billable": 3,  "Non-Billable": 1, "Bench": 0.5 } },
    "1366": { "direct":   { "Billable": 8,  "Non-Billable": 4, "Bench": 1.5 } }
  }
}
```

- Only **PLs** use the `indirect` block.
- L1/L2 have only a `direct` block.
- `src/bonus.py` reads/writes this via `load_rates()` / `save_rates()`.

> Note: an older flat `{ "<id>": {Billable,…} }` shape may exist in a stale `rates.json`; the
> current code reads only the `persons` key and ignores anything else, so old files are harmless
> but their values won't load — re-enter and **Save rates** to migrate.

---

## 5. Module reference

### `src/reporting.py`
- `load_reporting(file) -> DataFrame` — reads the audit log, parses `Modified Time` to datetime,
  parses `New Value` into `Manager ID` / `Manager Name`, sorts by employee & time.
- `manager_as_of(reporting_df, month_end) -> DataFrame` — one row per employee: the manager active
  as of `month_end` (latest change `≤ month_end`). Feeds `build_hierarchy`.
- `manager_segments_for_month(reporting_df, year, month_num) -> DataFrame` — day-weighted segments
  (§4.3). Columns: `Employee ID, First Name, Last Name, Manager ID, Manager Name, Days, Fraction`.

### `src/hierarchy.py`
- `LEVEL_NAMES`, `_level_name(depth)` — depth→level naming.
- `build_hierarchy(mgr_map) -> DataFrame` — the tree (§4.1).

### `src/timesheet.py`
- `MONTHS`, `MONTH_TO_NUM` — month abbreviation ↔ number.
- `load_timesheet(file) -> DataFrame` — forward-fills `Month of Date`, coerces hours, adds `Bucket`.
- `available_months(df) -> list[str]` — months present, in calendar order.
- `hours_for_month(df, month_abbr) -> DataFrame` — approved-only, pivoted to one row per
  `(Employee ID, First Name)` with `Billable / Non-Billable / Bench` hour totals.

### `src/attendance.py`
- `parse_filename(name) -> (year, month_num, month_name) | None`.
- `load_attendance(file, filename) -> DataFrame` — finds the `Employee Id` header row, sums P-values
  across day columns. `_p_value`: `"P"`→1.0, any cell containing `"0.5P"`→0.5, everything else→0.
  Returns `Employee ID, Employee Name, Present Days, Year, Month, Month Name`.

### `src/bonus.py`
- `BUCKETS = ["Billable", "Non-Billable", "Bench"]`, `DIRECT_EARNING_LEVELS = {"PL","L1","L2"}`.
- `load_rates()` / `save_rates(rates)` — read/write `rates.json`.
- `person_rate_for(person_rates, person_id, kind)` — rate dict for `kind ∈ {"direct","indirect"}`.
- `earning_people(hierarchy_df, extra_ids=None) -> DataFrame` — PL/L1/L2 who have ≥1 report at
  month-end, **plus** any id in `extra_ids` (managers who only held a report mid-month, and their
  PL ancestors) so every possible earner gets a rate row. Adds a `Reports` count.
- `build_employee_view(hours_df, hierarchy_df)` — merges monthly hours with the month-end hierarchy
  (used for display + orphan detection, **not** for the bonus math).
- `orphan_hours(emp_view)` — employees with timesheet hours but no hierarchy placement (`Level` NaN).
- `apply_rates_month(hours_df, segments_df, hierarchy_df, person_rates, month_label, exclude_ids=None)`
  → one row per `(Earner, Month, Basis)` with hour + bonus totals. **This is the bonus engine**
  (§6). `Basis ∈ {"Direct", "Indirect (PL)"}`.
- `earner_summary(bonus_df)` — per `(Earner, Basis)` aggregation (the "Earner by Basis" sheet).
- `earner_totals(bonus_df)` — per earner with `Direct_Bonus`, `Indirect_Bonus`, `Total_Bonus`.
- `attribution_detail(hours_df, segments_df, hierarchy_df, month_label)` — one row per
  `(employee, month, manager-segment)` with the segment's chain, `Direct Manager ID`,
  `PL (indirect) ID`, `Share %`, and both **Raw** and **Attributed** hours (§7). This is the
  reconcilable source behind the per-employee breakdown.

---

## 6. Bonus engine (`apply_rates_month`)

For each employee E with month hours `H = {Billable, Non-Billable, Bench}` and each segment
`(manager m, fraction f)` from `segments_df`:

```
hrs_f = H × f                       # day-weighted slice of the month

if m.Level in {PL, L1, L2} and m not excluded:
    credit m  "Direct"        with hrs_f × m.direct_rate

if m.Level not in {Director, PL}:   # employee is L2 or deeper
    pl = m.PL ancestor
    if pl exists and not excluded:
        credit pl "Indirect (PL)" with hrs_f × pl.indirect_rate
```

- **Own hours excluded** by construction: E's hours only ever credit E's manager / PL, never E.
- **Mid-month changes** split correctly, including across levels and across PLs (each fraction
  resolves its own manager's level and PL ancestor).
- **Unassigned days** are absent from `segments_df`, so that fraction of hours is dropped.
- **No-change months** collapse to `f = 1.0` (identical to a simple month-end attribution).

`bonus = hours × rate` per bucket; `Total_Bonus = Σ buckets`.

---

## 7. Attribution detail & reconciliation

`attribution_detail` produces the reconcilable per-employee data. Key columns:

- Chain reflects the **segment's manager** (not just month-end): `Employee Level, Director Name,
  PL Name, L1 Name, L2 Name`.
- `Direct Manager ID` — the manager who earns Direct from this row.
- `PL (indirect) ID` — the PL who earns Indirect from this row (null when the direct manager is a
  PL or Director).
- `Share %`, `Days`.
- **Raw** hours (full month) and **Attributed** hours (`Raw × fraction`).

**Invariants (verified):**
- Σ `Attributed` grouped by `Direct Manager ID` == that earner's **Direct** hours in
  `earner_summary`.
- Σ `Attributed` grouped by `PL (indirect) ID` == that PL's **Indirect** hours.

`app.py` additionally derives **Bonus** (money) columns for display/export:
`Bonus <bucket> = Attributed <bucket> × direct-manager's direct rate`. In the **team filter**,
bonus is recomputed with the selected manager's own rate per basis (direct rate for Direct rows,
indirect rate for Indirect rows).

---

## 8. `app.py` flow & session state

1. **Upload** reporting + timesheet (required) and attendance files (optional, multi).
2. **Select** months (multiselect) and year.
3. **Per month** build: `manager_as_of` → `build_hierarchy`, `hours_for_month`,
   `manager_segments_for_month`; assemble `emp_view` (for display/orphans) and the
   `hierarchy_by_month`, `hours_by_month`, `segments_by_month` dicts.
4. Compute `combined_hierarchy` and `_extra_earner_ids` (segment managers + their PL ancestors).
5. **Hierarchy tree** expander — interactive HTML `<details>` tree + flat table.
6. **Per-person rate editor** — three tabs (PL / L1 / L2) via `st.data_editor`, each with an
   `Include` checkbox (unchecking excludes that earner). `_collect_person_rates()` builds the
   `{"persons": …}` dict; **Save rates** persists it.
7. **Orphan** and **mid-month change** transparency expanders.
8. **Calculate** button → runs `apply_rates_month` per month, concatenates, builds
   `earner_totals`, `earner_summary`, `attribution_full`, `rates_used_df`; stores everything in
   `st.session_state["results"]`.
9. **Results block** (`if "results" in st.session_state`) renders **outside** the button block so
   widget interactions (dropdowns, downloads) don't wipe the results on rerun. This is the fix for
   the "page resets on filter" issue — never put results-dependent widgets inside
   `if st.button(...)`.

**Team filter:** select managers; `_team_rows(mid)` returns rows where `Direct Manager ID == mid`
(Basis=Direct) plus `PL (indirect) ID == mid` (Basis=Indirect), with bonus at the selected
manager's rate. Download = one Excel with a combined sheet + one sheet per manager.

---

## 9. Outputs

### Full report (`leadership_bonus_<months>_<year>.xlsx`)
| Sheet | Content |
|---|---|
| Earner Totals | per earner: `Direct_Bonus, Indirect_Bonus, Total_Bonus` |
| Earner by Basis | per `(earner, basis)`: hours + bonus by bucket |
| Employee Breakdown | attribution rows: chain, Present Days, Share %, Attributed hours, Bonus money |
| Rates Used | the exact rates applied |

### Team contribution (`team_contribution_<ids>_<months>_<year>.xlsx`)
Combined "Team Contribution" sheet + one sheet per selected manager (their reportees' rows, with
Direct/Indirect basis and bonus at that manager's rate).

All exports are **Excel** (`xlsxwriter`), never CSV.

---

## 10. Edge cases & invariants

- **Orphans** — timesheet employees absent from the reporting log get no hierarchy placement;
  their hours are dropped and surfaced in the ⚠️ expander (`orphan_hours`).
- **Excluded earners** — `Include` unchecked → passed as `exclude_ids`; they earn nothing and are
  absent from totals.
- **Cross-level / cross-PL mid-month change** — handled per segment; indirect can route to two
  different PLs in the same month.
- **Multiple months** — computed independently per month and summed per earner.
- **Cycle guard** — `depth_of` in `hierarchy.py` guards against a malformed parent cycle.
- **Reconciliation** — the Employee Breakdown's Attributed hours always tie back to the Earner
  tables (see §7). If you change the engine, re-verify this.

---

## 11. Extending

- **Deeper trees (L4+):** `_level_name` already generates `L4, L5…`. `apply_rates_month`'s indirect
  step uses the PL ancestor generically. Rate editor tabs are PL/L1/L2 only — add tabs if deeper
  levels should earn Direct.
- **Attendance affecting pay:** currently display-only. To make it affect bonuses, weight
  `hrs_f` by a Present-Days factor inside `apply_rates_month` (and mirror in `attribution_detail`).
- **Different bench definition:** edit `BENCH_PROJECT` / `_bucket` in `timesheet.py`.
- **New output columns:** add in `attribution_detail` (source of truth) and surface in `app.py`.

**Testing pattern (used throughout dev):** load the three sample files, run the pipeline for a
month, and assert that `attribution_detail` grouped by manager reconciles with `earner_summary`,
and that a hand-computed employee matches the engine. Keep these checks when refactoring the math.

---

## 12. Known limitations

- Timesheet granularity is **monthly**; mid-month splits assume hours are spread evenly across
  calendar days (day-weighted proration).
- `rates.json` is machine-local and unversioned — not multi-user safe.
- No authentication; intended as a local desktop tool for a single operator.
