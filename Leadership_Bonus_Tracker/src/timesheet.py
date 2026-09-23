import pandas as pd

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_TO_NUM = {m: i + 1 for i, m in enumerate(MONTHS)}

BENCH_PROJECT = "Certification & Upskilling"
APPROVED = "approved"


# def load_timesheet(file) -> pd.DataFrame:
#     df = pd.read_excel(file)
#     if "Month of Date" not in df.columns:
#         if "Date" in df.columns:
#             df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
#             df["Month of Date"] = df["Date"].dt.strftime("%b")
#         else:
#             raise KeyError("Neither 'Month of Date' nor 'Date' column found in the timesheet file.")
#     df["Month of Date"] = df["Month of Date"].ffill()
#     df["Total Hours"] = pd.to_numeric(df["Total Hours"], errors="coerce").fillna(0)
#     df["Bucket"] = df.apply(_bucket, axis=1)
#     return df
def load_timesheet(file) -> pd.DataFrame:
    df = pd.read_excel(file)

    # ---------------------------------------------------------
    # 1. Make sure we have an actual date column
    # ---------------------------------------------------------
    if "Date" not in df.columns:
        raise KeyError(
            "The timesheet file must contain a 'Date' column "
            "for day-wise hour allocation."
        )

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    # Remove rows where the date could not be determined
    df = df.dropna(subset=["Date"]).copy()

    # Normalize the date so that only YYYY-MM-DD matters
    df["Work Date"] = df["Date"].dt.normalize()

    # ---------------------------------------------------------
    # 2. Derive month information from the actual date
    # ---------------------------------------------------------
    df["Month of Date"] = df["Work Date"].dt.strftime("%b")

    # ---------------------------------------------------------
    # 3. Normalize hours
    # ---------------------------------------------------------
    df["Total Hours"] = pd.to_numeric(
        df["Total Hours"],
        errors="coerce"
    ).fillna(0)

    # ---------------------------------------------------------
    # 4. Categorize the timesheet row
    # ---------------------------------------------------------
    df["Bucket"] = df.apply(_bucket, axis=1)

    return df


def _bucket(row):
    approval = str(row["APPROVAL STATUS"]).strip().lower()
    if approval != APPROVED:
        return None
    billable = str(row["Billable Status"]).strip().lower()
    project = str(row["Project Name"]).strip()
    if billable == "non-billable" and project == BENCH_PROJECT:
        return "Bench"
    if billable == "billable":
        return "Billable"
    if billable == "non-billable":
        return "Non-Billable"
    return None


def available_months(timesheet_df: pd.DataFrame) -> list[str]:
    present = [m for m in MONTHS if m in set(timesheet_df["Month of Date"].dropna().unique())]
    return present


def hours_for_month(timesheet_df: pd.DataFrame, month_abbr: str) -> pd.DataFrame:
    sub = timesheet_df[
        (timesheet_df["Month of Date"] == month_abbr) & (timesheet_df["Bucket"].notna())
    ]
    agg = (
        sub.groupby(["Employee ID", "First Name", "Bucket"], as_index=False)["Total Hours"]
        .sum()
        .pivot_table(index=["Employee ID", "First Name"], columns="Bucket", values="Total Hours", fill_value=0)
        .reset_index()
    )
    for col in ("Billable", "Non-Billable", "Bench"):
        if col not in agg.columns:
            agg[col] = 0
    return agg[["Employee ID", "First Name", "Billable", "Non-Billable", "Bench"]]
