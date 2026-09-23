import re
import pandas as pd

FILENAME_RE = re.compile(r"Attendance_(\d{4})-(\d{2})_([A-Za-z]+)", re.IGNORECASE)


def parse_filename(name: str):
    """Return (year, month_num, month_name) or None."""
    m = FILENAME_RE.search(name)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), m.group(3)


def _p_value(cell) -> float:
    if cell is None:
        return 0.0
    s = str(cell).strip()
    if s == "P":
        return 1.0
    if "0.5P" in s:
        return 0.5
    return 0.0


# def load_attendance(file, filename: str) -> pd.DataFrame:
#     """Return DataFrame: Employee ID, Employee Name, Present Days, Year, Month."""
#     parsed = parse_filename(filename)
#     if not parsed:
#         raise ValueError(f"Cannot parse year/month from filename: {filename}")
#     year, month_num, month_name = parsed

#     raw = pd.read_excel(file, header=None)
#     header_row_idx = None
#     for i in range(min(15, len(raw))):
#         if str(raw.iat[i, 0]).strip().lower() == "employee id":
#             header_row_idx = i
#             break
#     if header_row_idx is None:
#         raise ValueError("Could not find 'Employee Id' header row in attendance file.")

#     data = raw.iloc[header_row_idx + 1 :].reset_index(drop=True)
#     day_cols = list(range(3, raw.shape[1]))
#     present = data.iloc[:, day_cols].map(_p_value).sum(axis=1)

#     out = pd.DataFrame(
#         {
#             "Employee ID": pd.to_numeric(data.iloc[:, 0], errors="coerce"),
#             "Employee Name": data.iloc[:, 1],
#             "Present Days": present,
#         }
#     ).dropna(subset=["Employee ID"])
#     out["Employee ID"] = out["Employee ID"].astype(int)
#     out["Year"] = year
#     out["Month"] = month_num
#     out["Month Name"] = month_name
#     return out.reset_index(drop=True)

def load_attendance(file, filename: str) -> pd.DataFrame:
    """
    Return day-wise attendance data.

    Columns:
        Employee ID
        Employee Name
        Date
        Attendance
        Year
        Month
        Month Name

    Attendance values:
        P              -> 1.0
        0.5P / 0.5CL/0.5P -> 0.5
        everything else -> 0.0
    """

    parsed = parse_filename(filename)

    if not parsed:
        raise ValueError(
            f"Cannot parse year/month from filename: {filename}"
        )

    year, month_num, month_name = parsed

    raw = pd.read_excel(file, header=None)

    # ---------------------------------------------------------
    # Find the Employee ID header row
    # ---------------------------------------------------------
    header_row_idx = None

    for i in range(min(15, len(raw))):
        if str(raw.iat[i, 0]).strip().lower() == "employee id":
            header_row_idx = i
            break

    if header_row_idx is None:
        raise ValueError(
            "Could not find 'Employee Id' header row in attendance file."
        )

    # ---------------------------------------------------------
    # Data starts immediately after the Employee ID header row
    # ---------------------------------------------------------
    data = raw.iloc[header_row_idx + 1:].reset_index(drop=True)

    # ---------------------------------------------------------
    # Number of actual calendar days in this month
    # ---------------------------------------------------------
    month_start = pd.Timestamp(
        year=year,
        month=month_num,
        day=1
    )

    days_in_month = month_start.days_in_month

    # Attendance starts at column 3:
    #
    # 0 -> Employee ID
    # 1 -> Employee Name
    # 2 -> Email ID
    # 3 -> 1 - Apr
    # 4 -> 2 - Apr
    # ...
    #
    # We deliberately stop after the actual number of days.
    # This prevents the final monthly total column from being
    # treated as an attendance date.
    day_cols = list(
        range(3, 3 + days_in_month)
    )

    records = []

    # ---------------------------------------------------------
    # Convert every employee/day combination into one row
    # ---------------------------------------------------------
    for _, row in data.iterrows():

        employee_id = pd.to_numeric(
            row.iloc[0],
            errors="coerce"
        )

        if pd.isna(employee_id):
            continue

        employee_id = int(employee_id)

        employee_name = row.iloc[1]

        for day_number, col_idx in enumerate(day_cols, start=1):

            attendance_value = _p_value(
                row.iloc[col_idx]
            )

            attendance_date = pd.Timestamp(
                year=year,
                month=month_num,
                day=day_number
            )

            records.append(
                {
                    "Employee ID": employee_id,
                    "Employee Name": employee_name,
                    "Date": attendance_date,
                    "Attendance": attendance_value,
                    "Year": year,
                    "Month": month_num,
                    "Month Name": month_name,
                }
            )

    return pd.DataFrame(
        records,
        columns=[
            "Employee ID",
            "Employee Name",
            "Date",
            "Attendance",
            "Year",
            "Month",
            "Month Name",
        ],
    )
