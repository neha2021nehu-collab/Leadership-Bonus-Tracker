import re
import pandas as pd

NEW_VALUE_RE = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")


def load_reporting(file) -> pd.DataFrame:
    df = pd.read_excel(file)
    df["Modified Time"] = pd.to_datetime(df["Modified Time"])
    df = df.sort_values(["Employee ID", "Modified Time"]).reset_index(drop=True)
    parsed = df["New Value"].fillna("").astype(str).apply(_parse_manager)
    df["Manager ID"] = [p[0] for p in parsed]
    df["Manager Name"] = [p[1] for p in parsed]
    return df


def _parse_manager(val: str):
    m = NEW_VALUE_RE.match(val)
    if not m:
        return (None, None)
    return (int(m.group(1)), m.group(2).strip())


def manager_as_of(reporting_df: pd.DataFrame, month_end: pd.Timestamp) -> pd.DataFrame:
    """Return one row per employee with the manager active as of month_end."""
    eligible = reporting_df[reporting_df["Modified Time"] <= month_end]
    latest = eligible.sort_values("Modified Time").groupby("Employee ID", as_index=False).tail(1)
    return latest[["Employee ID", "First Name", "Last Name", "Manager ID", "Manager Name"]].reset_index(drop=True)


def manager_segments_for_month(reporting_df: pd.DataFrame, year: int, month_num: int) -> pd.DataFrame:
    """
    Split each employee's month into day-level manager tenure and return, per
    (employee, manager), the fraction of the month's calendar days they were under
    that manager.

    Rule:
      • A reporting change takes effect on its CALENDAR DATE (from the start of that day).
        The manager active on a given calendar day = the New Value of the latest audit
        row whose date <= that day. (If two changes fall on the same date, the later one
        that day wins.)
      • Days before the employee's first assignment have NO manager and are DROPPED
        (they do not appear as a segment; the fraction denominator stays the full month,
        so those hours are discarded, not redistributed).
      • Fraction = days_under_manager / total_calendar_days_in_month.

    For a month with no manager change, this yields a single segment with Fraction 1.0.
    """
    month_start = pd.Timestamp(year=year, month=month_num, day=1)
    days_in_month = month_start.days_in_month
    day_starts = [month_start + pd.Timedelta(days=d) for d in range(days_in_month)]

    meta = (
        reporting_df.sort_values("Modified Time")
        .groupby("Employee ID", as_index=False)
        .agg({"First Name": "first", "Last Name": "first"})
    )
    meta_by_emp = meta.set_index("Employee ID").to_dict("index")

    records = []
    for emp_id, grp in reporting_df.sort_values("Modified Time").groupby("Employee ID"):
        # Normalize change timestamps to their calendar date (effective from start of that day)
        times = [t.normalize() for t in grp["Modified Time"].tolist()]
        mgr_ids = grp["Manager ID"].tolist()
        mgr_names = grp["Manager Name"].tolist()

        day_counts: dict = {}
        for ds in day_starts:
            # latest change whose date <= this day
            active_idx = None
            for i, t in enumerate(times):
                if t <= ds:
                    active_idx = i
                else:
                    break
            if active_idx is None:
                continue  # unassigned day -> dropped
            mid = mgr_ids[active_idx]
            if mid is None or pd.isna(mid):
                continue
            key = (int(mid), mgr_names[active_idx])
            day_counts[key] = day_counts.get(key, 0) + 1

        m = meta_by_emp.get(emp_id, {})
        for (mid, mname), days in day_counts.items():
            records.append({
                "Employee ID": int(emp_id),
                "First Name": m.get("First Name"),
                "Last Name": m.get("Last Name"),
                "Manager ID": mid,
                "Manager Name": mname,
                "Days": days,
                "Fraction": days / days_in_month,
            })

    cols = ["Employee ID", "First Name", "Last Name", "Manager ID", "Manager Name", "Days", "Fraction"]
    return pd.DataFrame(records, columns=cols)

def manager_by_day_for_month(
    reporting_df: pd.DataFrame,
    year: int,
    month_num: int,
) -> pd.DataFrame:
    """
    Return the manager assignment for every calendar day
    for every employee.

    One row per:

        Employee + Date

    Columns:
        Employee ID
        First Name
        Last Name
        Date
        Manager ID
        Manager Name

    A reporting change becomes effective from the start
    of its calendar date.

    Days before the employee's first manager assignment
    have no manager and are excluded.
    """

    month_start = pd.Timestamp(
        year=year,
        month=month_num,
        day=1,
    )

    days_in_month = month_start.days_in_month

    day_starts = [
        month_start + pd.Timedelta(days=d)
        for d in range(days_in_month)
    ]

    records = []

    # Process each employee independently
    for emp_id, grp in (
        reporting_df
        .sort_values("Modified Time")
        .groupby("Employee ID")
    ):

        grp = grp.sort_values("Modified Time").reset_index(drop=True)

        # Normalize reporting timestamps to calendar dates.
        effective_dates = (
            grp["Modified Time"]
            .dt.normalize()
            .tolist()
        )

        manager_ids = grp["Manager ID"].tolist()
        manager_names = grp["Manager Name"].tolist()

        first_name = (
            grp["First Name"].iloc[0]
            if "First Name" in grp.columns
            else None
        )

        last_name = (
            grp["Last Name"].iloc[0]
            if "Last Name" in grp.columns
            else None
        )

        for current_date in day_starts:

            active_idx = None

            # Find the latest reporting change whose
            # effective date is <= current date.
            for i, effective_date in enumerate(effective_dates):

                if effective_date <= current_date:
                    active_idx = i
                else:
                    break

            # No manager assignment yet
            if active_idx is None:
                continue

            manager_id = manager_ids[active_idx]
            manager_name = manager_names[active_idx]

            if pd.isna(manager_id):
                continue

            records.append(
                {
                    "Employee ID": int(emp_id),
                    "First Name": first_name,
                    "Last Name": last_name,
                    "Date": current_date,
                    "Manager ID": int(manager_id),
                    "Manager Name": manager_name,
                }
            )

    return pd.DataFrame(
        records,
        columns=[
            "Employee ID",
            "First Name",
            "Last Name",
            "Date",
            "Manager ID",
            "Manager Name",
        ],
    )
