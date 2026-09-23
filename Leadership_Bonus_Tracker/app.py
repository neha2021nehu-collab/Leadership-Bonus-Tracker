from asyncio import streams
import io
import pandas as pd
import streamlit as st

from src.reporting import load_reporting, manager_as_of, manager_segments_for_month, manager_by_day_for_month
from src.timesheet import load_timesheet, available_months, hours_for_month, MONTH_TO_NUM
from src.hierarchy import build_hierarchy
from src.bonus import (
    load_rates, save_rates,  BUCKETS,
    earning_people, build_employee_view,  earner_summary, earner_totals,
    orphan_hours, apply_rates_manager_hours
)
from src.attendance import load_attendance, parse_filename

st.set_page_config(page_title="Leadership Bonus Tracker", layout="wide")
st.title("Leadership Bonus Tracker")

st.markdown(
    "Upload the three inputs, pick month(s), set per-person rates, then click **Calculate**."
)

col1, col2, col3 = st.columns(3)
with col1:
    reporting_file = st.file_uploader(
        "1. Reporting audit log (.xlsx)", type=["xlsx"],
        help="Audit log of manager changes. Used to derive each person's manager, level (Director / PL / L1 / L2 / L3), and full chain as of the last day of the selected month.",
    )
with col2:
    timesheet_file = st.file_uploader(
        "2. Timesheet (.xlsx)", type=["xlsx"],
        help="Employee × Project × Month hours. Only rows with APPROVAL STATUS = Approved are counted.",
    )
with col3:
    attendance_files = st.file_uploader(
        "3. Attendance files (optional, for records)", type=["xlsx"], accept_multiple_files=True,
        help="One file per month, named Attendance_YYYY-MM_Month.xlsx. Present Days = only cells equal to 'P' count as 1; cells containing '0.5P' count as 0.5; every other value counts as 0. Shown on the UI only; not used in bonus math.",
    )

if not (reporting_file and timesheet_file):
    st.info("Please upload the Reporting and Timesheet files to continue.")
    st.stop()

reporting_df = load_reporting(reporting_file)
timesheet_df = load_timesheet(timesheet_file)


st.success(f"Loaded {len(reporting_df)} audit rows and {len(timesheet_df)} timesheet rows.")

months = available_months(timesheet_df)
if not months:
    st.error("No months found in the timesheet file.")
    st.stop()

year_guess = pd.Timestamp.now().year
c1, c2 = st.columns([1, 1])
with c1:
    selected_months = st.multiselect(
        "Months", months, default=[months[0]],
        help="Pick one or more months. Hours and bonuses are computed per month and then summed per earner.",
    )
with c2:
    year = st.number_input(
        "Year (for manager resolution as-of month-end)", value=year_guess, step=1,
        help="Used to build each selected month's end date, and to match attendance files by filename.",
    )

if not selected_months:
    st.info("Pick at least one month.")
    st.stop()

# Attendance lookup by month number
attendance_by_month: dict[int, pd.DataFrame] = {}
if attendance_files:
    for f in attendance_files:
        parsed = parse_filename(f.name)
        if not parsed:
            continue
        yr, mn, _ = parsed
        if yr != int(year):
            continue
        if mn in [MONTH_TO_NUM[m] for m in selected_months]:
            try:
                attendance_by_month[mn] = load_attendance(f, f.name)
                
            except Exception as e:
                st.warning(f"Could not read {f.name}: {e}")
    

    

missing_att = [m for m in selected_months if MONTH_TO_NUM[m] not in attendance_by_month]
if missing_att:
    st.info(f"No attendance file for: {', '.join(missing_att)} {int(year)} — Present Days blank for those months.")

# Build per-month employee view + hierarchy + day-weighted manager segments
per_month_frames = []
hierarchy_by_month: dict[str, pd.DataFrame] = {}
hours_by_month: dict[str, pd.DataFrame] = {}
segments_by_month: dict[str, pd.DataFrame] = {}
manager_final_by_month = {}
daily_attribution_by_month = {}
for m in selected_months:
    mn = MONTH_TO_NUM[m]
    month_end = pd.Timestamp(year=int(year), month=mn, day=1) + pd.offsets.MonthEnd(0)
    mgr_df = manager_as_of(reporting_df, month_end)
    hier = build_hierarchy(mgr_df)
    hierarchy_by_month[m] = hier
    hours_df = hours_for_month(timesheet_df, m)
    hours_by_month[m] = hours_df
    segments_by_month[m] = manager_segments_for_month(reporting_df, int(year), mn)
    
    #Neha 
    manager_days = manager_by_day_for_month(
    reporting_df,
    int(year),
    mn
    )

    attendance_df = attendance_by_month.get(mn)

    if attendance_df is not None:
        daily_manager_attendance = manager_days.merge(
            attendance_df[
                ["Employee ID", "Date", "Attendance"]
            ],
            on=["Employee ID", "Date"],
            how="left"
        )
    else:
        daily_manager_attendance = manager_days.copy()
        daily_manager_attendance["Attendance"] = 0

    # timesheet_daily = timesheet_df.copy()

    # daily_manager_timesheet = daily_manager_attendance.merge(
    #     timesheet_daily,
    #     left_on=["Employee ID", "Date"],
    #     right_on=["Employee ID", "Work Date"],
    #     how="left"
    # )

    # Aggregate Timesheet entries to one row per employee per day
#     timesheet_daily = (
#         timesheet_df
#         .groupby(
#             ["Employee ID", "Work Date", "Bucket"],
#             as_index=False
#         )["Total Hours"]
#         .sum()
#     )

#     # Merge the aggregated daily Timesheet data
#     daily_manager_timesheet = daily_manager_attendance.merge(
#         timesheet_daily,
#         left_on=["Employee ID", "Date"],
#         right_on=["Employee ID", "Work Date"],
#         how="left"
# )

    # Aggregate Timesheet entries to one row per employee per day
    # with separate columns for each bucket.
    timesheet_daily = (
        timesheet_df
        .pivot_table(
            index=["Employee ID", "Work Date"],
            columns="Bucket",
            values="Total Hours",
            aggfunc="sum",
            fill_value=0
        )
        .reset_index()
    )

    timesheet_daily.columns.name = None

    # Make sure all buckets exist
    for bucket in ["Billable", "Non-Billable", "Bench"]:
        if bucket not in timesheet_daily.columns:
            timesheet_daily[bucket] = 0.0

    # Merge one Timesheet row per employee/date
    daily_manager_timesheet = daily_manager_attendance.merge(
        timesheet_daily,
        left_on=["Employee ID", "Date"],
        right_on=["Employee ID", "Work Date"],
        how="left"
    )

    # Fill missing hours with zero
    for bucket in ["Billable", "Non-Billable", "Bench"]:
        daily_manager_timesheet[bucket] = (
            pd.to_numeric(
                daily_manager_timesheet[bucket],
                errors="coerce"
            ).fillna(0)
        )

    # Total actual hours for the day
    daily_manager_timesheet["Total Hours"] = (
        daily_manager_timesheet["Billable"]
        + daily_manager_timesheet["Non-Billable"]
        + daily_manager_timesheet["Bench"]
    )

    daily_manager_timesheet["Attendance"] = (
        pd.to_numeric(
            daily_manager_timesheet["Attendance"],
            errors="coerce"
        )
        .fillna(0)
    )

    daily_manager_timesheet["Expected Hours"] = (
        daily_manager_timesheet["Attendance"] * 8
    )

    daily_manager_timesheet["Actual Hours"] = (
        pd.to_numeric(
            daily_manager_timesheet["Total Hours"],
            errors="coerce"
        )
        .fillna(0)
    )

    daily_manager_timesheet["Hours Status"] = "Normal"

    daily_manager_timesheet.loc[
        daily_manager_timesheet["Actual Hours"] < daily_manager_timesheet["Expected Hours"],
        "Hours Status"
    ] = "Below Expected"

    daily_manager_timesheet.loc[
        daily_manager_timesheet["Actual Hours"] > daily_manager_timesheet["Expected Hours"],
        "Hours Status"
    ] = "Above Expected"

    manager_present_days = (
    daily_manager_timesheet
    .groupby(
        [
            "Employee ID",
            "First Name",
            "Last Name",
            "Manager ID",
            "Manager Name"
        ],
        as_index=False
    )["Attendance"]
    .sum()
    .rename(columns={
        "First Name": "First Name",
        "Last Name": "Last Name",
        "Attendance": "Present Days"
    })

)

    manager_present_days["Expected Hours"] = (
    manager_present_days["Present Days"] * 8
    )


    manager_actual_hours = (
        daily_manager_timesheet
        .groupby(
            [
                "Employee ID",
                "First Name",
                "Last Name",
                "Manager ID",
                "Manager Name"
            ],
            as_index=False
        )["Actual Hours"]
        .sum()
        .rename(columns={
            "First Name_x": "First Name"
            
        })
    )

    manager_category_hours = (
    daily_manager_timesheet
    .groupby(
        [
            "Employee ID",
            "First Name",
            "Last Name",
            "Manager ID",
            "Manager Name"
        ],
        as_index=False
    )[
        ["Billable", "Non-Billable", "Bench"]
    ]
    .sum()
    .rename(columns={
        "Billable": "Billable Hours",
        "Non-Billable": "Non-Billable Hours",
        "Bench": "Bench Hours"
    })
)
    
    manager_final = manager_present_days.merge(
    manager_category_hours,
    on=[
        "Employee ID",
        "First Name",
        "Last Name",
        "Manager ID",
        "Manager Name"
    ],
    how="left"
)
    manager_final_by_month[m] = manager_final

    for col in ["Billable Hours", "Non-Billable Hours", "Bench Hours"]:
        if col in manager_final.columns:
            manager_final[col] = manager_final[col].fillna(0)
    manager_final["Actual Hours"] = (
    manager_final["Billable Hours"]
    + manager_final["Non-Billable Hours"]
    + manager_final["Bench Hours"]



)
    manager_final["Hours Review"] = "OK"

    manager_final.loc[
        manager_final["Actual Hours"] < manager_final["Expected Hours"],
        "Hours Review"
    ] = "Review - Below Expected"

    manager_final.loc[
        manager_final["Actual Hours"] > manager_final["Expected Hours"],
        "Hours Review"
    ] = "Review - Above Expected"

# ---------------------------------------------------------
# NEW: Daily/Manager attribution basis
# ---------------------------------------------------------
    daily_attr = manager_final.copy()

    daily_attr["Month"] = m

    # Get hierarchy information for each manager
    manager_lookup = hierarchy_by_month[m][
        ["Person ID", "Level", "PL ID", "Director Name", "PL Name", "L1 Name", "L2 Name"]
    ].copy()

    manager_lookup = manager_lookup.rename(
        columns={
            "Person ID": "Manager ID",
            "Level": "Manager Level",
            "PL ID": "PL (indirect) ID",
        }
    )

    daily_attr = daily_attr.merge(
        manager_lookup,
        on="Manager ID",
        how="left",
    )

    # Employee name
    daily_attr["Employee Name"] = (
        daily_attr["First Name"].fillna("").astype(str).str.strip()
        + " "
        + daily_attr["Last Name"].fillna("").astype(str).str.strip()
    ).str.strip()

    # Employee is one level below their direct manager
    next_level = {
        "Director": "PL",
        "PL": "L1",
        "L1": "L2",
        "L2": "L3",
    }

    daily_attr["Employee Level"] = daily_attr["Manager Level"].map(next_level)

    # A Director/PL does not get an indirect PL attribution
    daily_attr["PL (indirect) ID"] = daily_attr["PL (indirect) ID"].where(
        ~daily_attr["Manager Level"].isin(["Director", "PL"]),
        pd.NA,
    )

    # Rename the actual-hour buckets to the names we will use for bonus calculation
    daily_attr = daily_attr.rename(
        columns={
            "Billable Hours": "Billable",
            "Non-Billable Hours": "Non-Billable",
            "Bench Hours": "Bench",
        }
    )

    daily_attribution_by_month[m] = daily_attr
    # #Temporary
    # st.write("NEW Daily/Manager Attribution")
    # st.dataframe(
    #     daily_attribution_by_month[m][
    #         [
    #             "Month",
    #             "Employee ID",
    #             "Employee Name",
    #             "Employee Level",
    #             "Manager ID",
    #             "Manager Name",
    #             "Manager Level",
    #             "Present Days",
    #             "Expected Hours",
    #             "Actual Hours",
    #             "Billable",
    #             "Non-Billable",
    #             "Bench",
    #             "PL (indirect) ID",
    #         ]
    #     ],
    #     use_container_width=True,
    # )

    # st.write("Final Manager-Level Hours")
    # st.dataframe(manager_final)
    # st.write("Actual Hours by Category and Manager")
    # st.dataframe(manager_category_hours)
    
    # st.write("Actual Hours by Manager")
    # st.dataframe(manager_actual_hours)
    
    # st.write("Present Days and Expected Hours by Manager")
    # st.dataframe(manager_present_days)
    
    # st.write("Present Days by Manager")
    # st.dataframe(manager_present_days)

    
    # st.write(f"Manager + Attendance + Timesheet — {m}")

    # st.dataframe(
    #     daily_manager_timesheet,
    #     use_container_width=True
    # )


    
    
    #Temporary
    

    ev = build_employee_view(hours_df, hier)
    ev["Month"] = m
    # if mn in attendance_by_month:
    #     ev = ev.merge(
    #         attendance_by_month[mn][["Employee ID", "Present Days"]],
    #         on="Employee ID", how="left",
    #     )
    # else:
    #     ev["Present Days"] = None

    #Neha
    if mn in attendance_by_month:
        # Temporary compatibility layer:
        # convert day-wise attendance back into monthly Present Days.
        #
        # This is NOT the final calculation logic.
        # Later, Present Days will be calculated per manager segment.
        monthly_attendance = (
            attendance_by_month[mn]
            .groupby("Employee ID", as_index=False)["Attendance"]
            .sum()
            .rename(columns={"Attendance": "Present Days"})
        )

        ev = ev.merge(
            monthly_attendance,
            on="Employee ID",
            how="left",
        )
    else:
        ev["Present Days"] = None
    per_month_frames.append(ev)

emp_view = pd.concat(per_month_frames, ignore_index=True)
combined_hierarchy = pd.concat(hierarchy_by_month.values(), ignore_index=True).drop_duplicates("Person ID")

# Managers who actually receive hours in some month (incl. mid-month), plus their PL ancestors,
# so every possible earner gets a rate row even if they hold no report at month-end.
_seg_mgr_ids = set()
for _sdf in segments_by_month.values():
    _seg_mgr_ids |= set(_sdf["Manager ID"].dropna().astype(int).tolist())
_node = combined_hierarchy.set_index("Person ID").to_dict("index")
_extra_earner_ids = set(_seg_mgr_ids)
for _mid in list(_seg_mgr_ids):
    _pl = _node.get(_mid, {}).get("PL ID")
    if pd.notna(_pl):
        _extra_earner_ids.add(int(_pl))

st.caption(
    f"Levels resolved per-month at each month-end. Months: **{', '.join(selected_months)} {int(year)}**."
)

# ---------------- Hierarchy tree ----------------
with st.expander("Detected hierarchy (as of latest selected month-end)"):
    latest_hier = hierarchy_by_month[selected_months[-1]]

    st.dataframe(
        latest_hier["Level"].value_counts().rename_axis("Level").reset_index(name="People"),
        hide_index=True,
    )
    st.markdown("**Tree view** — click any node to expand or collapse.")

    LEVEL_COLORS = {"Director": "#7c3aed", "PL": "#2563eb", "L1": "#0891b2", "L2": "#059669", "L3": "#6b7280"}
    level_order = {"Director": 0, "PL": 1, "L1": 2, "L2": 3, "L3": 4}

    def _render_tree(hier_df: pd.DataFrame) -> str:
        rows = hier_df.to_dict("records")
        by_id = {int(r["Person ID"]): r for r in rows}
        children: dict = {}
        for r in rows:
            pid = int(r["Person ID"])
            mgr = r.get("Manager ID")
            key = int(mgr) if pd.notna(mgr) else None
            children.setdefault(key, []).append(pid)
        for k in children:
            children[k].sort(key=lambda i: (level_order.get(by_id[i].get("Level"), 99), str(by_id[i].get("Person Name", ""))))
        roots = children.get(None, [])

        def node_html(pid: int, depth: int = 0) -> str:
            r = by_id[pid]
            level = r.get("Level", "")
            color = LEVEL_COLORS.get(level, "#374151")
            name = r.get("Person Name", f"#{pid}")
            kids = children.get(pid, [])
            label = (
                f"<span style='color:{color};font-weight:600;'>[{level}]</span> "
                f"<span style='color:#111827;'>{name}</span> "
                f"<span style='color:#6b7280;font-size:0.85em;'>(ID {pid})</span>"
                f" <span style='color:#9ca3af;font-size:0.8em;'>· {len(kids)} report{'s' if len(kids)!=1 else ''}</span>"
            )
            if not kids:
                return f"<div style='margin:2px 0 2px {depth*16}px;'>&nbsp;&nbsp;• {label}</div>"
            open_attr = " open" if depth == 0 else ""
            inner = "".join(node_html(c, depth + 1) for c in kids)
            return (
                f"<details{open_attr} style='margin:2px 0 2px {depth*16}px;'>"
                f"<summary style='cursor:pointer;list-style:disclosure-closed;'>{label}</summary>"
                f"<div style='margin-left:8px;border-left:1px dashed #d1d5db;padding-left:8px;'>{inner}</div>"
                f"</details>"
            )

        return "".join(node_html(r) for r in roots) or "<em>No hierarchy detected.</em>"

    st.markdown(_render_tree(latest_hier), unsafe_allow_html=True)

    with st.expander("Also show as a flat table"):
        st.dataframe(
            latest_hier[["Person ID", "Person Name", "Level", "Director Name", "PL Name", "L1 Name", "L2 Name"]],
            use_container_width=True, hide_index=True,
        )

# ---------------- Per-person rate editor ----------------
st.subheader(
    "Per-person rates (per hour)",
    help=(
        "Each earning person has their own rates.\n\n"
        "• Director earns nothing.\n"
        "• Each PL has TWO rate sets: 'Direct' (applied to L1 hours under them) and 'Indirect' (applied to L2 + L3 hours under them).\n"
        "• Each L1 has ONE 'Direct' rate set applied to their L2 reports' hours.\n"
        "• Each L2 (with L3 reports) has ONE 'Direct' rate set applied to their L3 reports' hours.\n"
        "• L3 are leaves — earn nothing."
    ),
)
st.caption("Rates persist locally to `rates.json` (keyed by employee ID).")

saved = load_rates()
saved_persons = saved.get("persons", {}) if isinstance(saved, dict) else {}

earners = earning_people(combined_hierarchy, extra_ids=_extra_earner_ids)
pl_people = earners[earners["Level"] == "PL"]
l1_people = earners[earners["Level"] == "L1"]
l2_people = earners[earners["Level"] == "L2"]


def _rate(person_id, kind, bucket):
    entry = saved_persons.get(str(int(person_id)), {})
    return float((entry.get(kind, {}) if isinstance(entry, dict) else {}).get(bucket, 0) or 0)


tab_pl, tab_l1, tab_l2 = st.tabs([
    f"PL ({len(pl_people)})",
    f"L1 ({len(l1_people)})",
    f"L2 ({len(l2_people)})",
])

with tab_pl:
    st.caption("Each PL has a Direct rate set (paid on L1's hours) and an Indirect rate set (paid on L2 + L3 hours). Uncheck **Include** to drop a person from the calculation.")
    if pl_people.empty:
        st.info("No PLs detected in the current hierarchy.")
        pl_edit = pd.DataFrame()
    else:
        pl_rows = [
            {
                "Include": True,
                "Person ID": int(r["Person ID"]),
                "Name": r["Person Name"],
                "Reports": int(r["Reports"]),
                "Direct Billable": _rate(r["Person ID"], "direct", "Billable"),
                "Direct Non-Billable": _rate(r["Person ID"], "direct", "Non-Billable"),
                "Direct Bench": _rate(r["Person ID"], "direct", "Bench"),
                "Indirect Billable": _rate(r["Person ID"], "indirect", "Billable"),
                "Indirect Non-Billable": _rate(r["Person ID"], "indirect", "Non-Billable"),
                "Indirect Bench": _rate(r["Person ID"], "indirect", "Bench"),
            }
            for _, r in pl_people.iterrows()
        ]
        pl_edit = st.data_editor(
            pd.DataFrame(pl_rows),
            hide_index=True, num_rows="fixed",
            disabled=["Person ID", "Name", "Reports"],
            use_container_width=True, key="pl_rates_editor",
            column_config={
                "Include": st.column_config.CheckboxColumn(help="Uncheck to exclude this person from the bonus calculation."),
                "Reports": st.column_config.NumberColumn(help="Number of direct reports (L1s)."),
                "Direct Billable": st.column_config.NumberColumn(help="Rate per Billable hour of this PL's L1 direct reports.", min_value=0, step=1.0),
                "Direct Non-Billable": st.column_config.NumberColumn(help="Rate per Non-Billable hour of this PL's L1 direct reports.", min_value=0, step=1.0),
                "Direct Bench": st.column_config.NumberColumn(help="Rate per Bench hour of this PL's L1 direct reports.", min_value=0, step=1.0),
                "Indirect Billable": st.column_config.NumberColumn(help="Rate per Billable hour of everyone strictly below this PL's L1s (i.e. L2 + L3).", min_value=0, step=1.0),
                "Indirect Non-Billable": st.column_config.NumberColumn(help="Rate per Non-Billable hour of L2 + L3 under this PL.", min_value=0, step=1.0),
                "Indirect Bench": st.column_config.NumberColumn(help="Rate per Bench hour of L2 + L3 under this PL.", min_value=0, step=1.0),
            },
        )

with tab_l1:
    st.caption("Each L1's rate is paid on their direct reports' (L2) hours.")
    if l1_people.empty:
        st.info("No L1s with reports.")
        l1_edit = pd.DataFrame()
    else:
        l1_rows = [
            {
                "Include": True,
                "Person ID": int(r["Person ID"]),
                "Name": r["Person Name"],
                "PL Name": r["PL Name"],
                "Reports": int(r["Reports"]),
                "Billable": _rate(r["Person ID"], "direct", "Billable"),
                "Non-Billable": _rate(r["Person ID"], "direct", "Non-Billable"),
                "Bench": _rate(r["Person ID"], "direct", "Bench"),
            }
            for _, r in l1_people.iterrows()
        ]
        l1_edit = st.data_editor(
            pd.DataFrame(l1_rows),
            hide_index=True, num_rows="fixed",
            disabled=["Person ID", "Name", "PL Name", "Reports"],
            use_container_width=True, key="l1_rates_editor",
            column_config={
                "Include": st.column_config.CheckboxColumn(help="Uncheck to exclude this person from the bonus calculation."),
                "Billable": st.column_config.NumberColumn(help="Rate per Billable hour of this L1's L2 reports.", min_value=0, step=1.0),
                "Non-Billable": st.column_config.NumberColumn(help="Rate per Non-Billable hour of this L1's L2 reports.", min_value=0, step=1.0),
                "Bench": st.column_config.NumberColumn(help="Rate per Bench hour of this L1's L2 reports.", min_value=0, step=1.0),
            },
        )

with tab_l2:
    st.caption("L2s with L3 reports — rate is paid on their L3 reports' hours.")
    if l2_people.empty:
        st.info("No L2s with L3 reports.")
        l2_edit = pd.DataFrame()
    else:
        l2_rows = [
            {
                "Include": True,
                "Person ID": int(r["Person ID"]),
                "Name": r["Person Name"],
                "L1 Name": r["L1 Name"],
                "Reports": int(r["Reports"]),
                "Billable": _rate(r["Person ID"], "direct", "Billable"),
                "Non-Billable": _rate(r["Person ID"], "direct", "Non-Billable"),
                "Bench": _rate(r["Person ID"], "direct", "Bench"),
            }
            for _, r in l2_people.iterrows()
        ]
        l2_edit = st.data_editor(
            pd.DataFrame(l2_rows),
            hide_index=True, num_rows="fixed",
            disabled=["Person ID", "Name", "L1 Name", "Reports"],
            use_container_width=True, key="l2_rates_editor",
            column_config={
                "Include": st.column_config.CheckboxColumn(help="Uncheck to exclude this person from the bonus calculation."),
                "Billable": st.column_config.NumberColumn(help="Rate per Billable hour of this L2's L3 reports.", min_value=0, step=1.0),
                "Non-Billable": st.column_config.NumberColumn(help="Rate per Non-Billable hour of this L2's L3 reports.", min_value=0, step=1.0),
                "Bench": st.column_config.NumberColumn(help="Rate per Bench hour of this L2's L3 reports.", min_value=0, step=1.0),
            },
        )


def _collect_person_rates() -> dict:
    out = dict(saved_persons)  # start from what was already saved
    if not pl_edit.empty:
        for _, r in pl_edit.iterrows():
            pid = str(int(r["Person ID"]))
            out[pid] = {
                "direct": {
                    "Billable": float(r["Direct Billable"] or 0),
                    "Non-Billable": float(r["Direct Non-Billable"] or 0),
                    "Bench": float(r["Direct Bench"] or 0),
                },
                "indirect": {
                    "Billable": float(r["Indirect Billable"] or 0),
                    "Non-Billable": float(r["Indirect Non-Billable"] or 0),
                    "Bench": float(r["Indirect Bench"] or 0),
                },
            }
    for df_edit in (l1_edit, l2_edit):
        if df_edit is None or df_edit.empty:
            continue
        for _, r in df_edit.iterrows():
            pid = str(int(r["Person ID"]))
            out[pid] = {
                "direct": {
                    "Billable": float(r["Billable"] or 0),
                    "Non-Billable": float(r["Non-Billable"] or 0),
                    "Bench": float(r["Bench"] or 0),
                }
            }
    return out


def _excluded_ids() -> set:
    """Earner IDs whose 'Include' checkbox is unchecked — dropped from the calculation."""
    excluded = set()
    for df_edit in (pl_edit, l1_edit, l2_edit):
        if df_edit is None or df_edit.empty or "Include" not in df_edit.columns:
            continue
        for _, r in df_edit.iterrows():
            if not bool(r["Include"]):
                excluded.add(int(r["Person ID"]))
    return excluded


if st.button("Save rates"):
    save_rates({"persons": _collect_person_rates()})
    st.success("Rates saved.")

# ---------------- Orphan (dropped-hours) warning ----------------
orphans = orphan_hours(emp_view)
if not orphans.empty:
    n = orphans["Employee ID"].nunique()
    with st.expander(f"⚠️ {n} employee(s) have timesheet hours but no manager in the reporting log — their hours are NOT counted", expanded=False):
        st.caption("These IDs never appear in the reporting audit log as of the selected month-end, so they can't be placed in the hierarchy. Fix the reporting file to include them, or ignore if intended.")
        st.dataframe(orphans, use_container_width=True, hide_index=True)

# # ---------------- Mid-month manager change transparency ----------------
# _split_rows = []

# for m in selected_months:
#     seg = segments_by_month[m]
#     hrs_ids = set(hours_by_month[m]["Employee ID"].astype(int))
#     counts = seg.groupby("Employee ID").size()
#     fsum = seg.groupby("Employee ID")["Fraction"].sum()
#     for eid in hrs_ids:
#         if counts.get(eid, 0) > 1 or fsum.get(eid, 1) < 0.999:
#             for _, s in seg[seg["Employee ID"] == eid].iterrows():
#                 _split_rows.append({
#                     "Month": m,
#                     "Employee ID": eid,
#                     "Name": f"{s['First Name']} {s['Last Name']}",
#                     "Manager ID": int(s["Manager ID"]),
#                     "Manager": s["Manager Name"],
#                     "Days": int(s["Days"]),
#                     "Share %": round(s["Fraction"] * 100, 1),
#                 })
# if _split_rows:
#     split_df = pd.DataFrame(_split_rows)
#     n_split = split_df["Employee ID"].nunique()
#     with st.expander(f"ℹ️ {n_split} employee(s) changed manager mid-month — their hours are split by days under each manager", expanded=False):
#         st.caption("Each employee's monthly hours are pro-rated by the number of calendar days spent under each manager (a reporting change takes effect on its date). Rows summing to under 100% had unassigned days that were dropped.")
#         st.dataframe(split_df, use_container_width=True, hide_index=True)

# ---------------- Calculate ----------------
if st.button("Calculate bonuses", type="primary"):
    active_rates = _collect_person_rates()
    # st.write("DEBUG ACTIVE RATES")
    # st.write(active_rates)
    excluded = _excluded_ids()

    # bonus_parts = [
    #     apply_rates_month(
    #         hours_by_month[m], segments_by_month[m], hierarchy_by_month[m],
    #         active_rates, m, exclude_ids=excluded,
    #     )
    #     for m in selected_months
    # ]


    #Neha
    new_bonus_parts = [
        apply_rates_manager_hours(
            daily_attribution_by_month[m],
            hierarchy_by_month[m],
            active_rates,
            m,
            exclude_ids=excluded,
        )
        for m in selected_months
    ]

    new_bonus_rows = (
        pd.concat(new_bonus_parts, ignore_index=True)
        if new_bonus_parts
        else pd.DataFrame()
    )

    # #Temporary
    # st.write("NEW BONUS ROW COLUMNS")
    # st.write(new_bonus_rows.columns.tolist())
    # #Temporary

    # ---------------------------------------------------------
    # NEW: Aggregate calculated bonuses by earner and month
    # ---------------------------------------------------------
    new_bonus_summary = (
    new_bonus_rows
    .groupby(
        ["Earner ID", "Earner Name", "Level", "Basis", "Month"],
        as_index=False,
    )[
        [
            "Billable_Hrs",
            "NonBillable_Hrs",
            "Bench_Hrs",
            "Billable_Bonus",
            "NonBillable_Bonus",
            "Bench_Bonus",
            "Total_Bonus",
        ]
    ]
    .sum()
)

    # st.write("NEW BONUS SUMMARY")
    # st.dataframe(
    #     new_bonus_summary,
    #     use_container_width=True,
    # )


   

    #Temporary

    

    # st.write("NEW BONUS SUMMARY")
    # st.dataframe(new_bonus_summary, use_container_width=True)
    # st.write("NEW manager-based bonus calculation")
    # # st.dataframe(new_bonus_rows)

    # st.write("NEW bonus rows for Ishaan")
    # st.dataframe(
    #     new_bonus_rows[
    #         new_bonus_rows["Earner Name"].str.contains(
    #             "Ishaan",
    #             case=False,
    #             na=False
    #         )
    #     ]
    # )
    #Temporary

    # bonus_rows = pd.concat(bonus_parts, ignore_index=True) if bonus_parts else pd.DataFrame()

    # #Temporary
    # st.write("OLD bonus rows for Ishaan")
    # st.dataframe(
    #     bonus_rows[
    #         bonus_rows["Earner Name"].str.contains(
    #             "Ishaan",
    #             case=False,
    #             na=False
    #         )
    #     ]
    # )
    # #Temporary
    summary_by_basis = earner_summary(new_bonus_rows)
    totals = earner_totals(new_bonus_rows)

    # attribution_full = pd.concat(
    #     [
    #         attribution_detail(hours_by_month[m], segments_by_month[m], hierarchy_by_month[m], m)
    #         for m in selected_months
    #     ],
    #     ignore_index=True,
    # )

    
    # present = emp_view[["Employee ID", "Month", "Present Days"]].drop_duplicates(["Employee ID", "Month"])
    # attribution_full = attribution_full.merge(present, on=["Employee ID", "Month"], how="left")
    # attribution_full = attribution_full.sort_values(
    #     ["Director Name", "PL Name", "L1 Name", "L2 Name", "Employee ID", "Month"]
    # ).reset_index(drop=True)

    # attribution_full = attribution_full.rename(columns={
    #     "Attributed Billable": "Billable",
    #     "Attributed Non-Billable": "Non-Billable",
    #     "Attributed Bench": "Bench",
    # })


    # ---------------------------------------------------------
    # NEW attribution table based on actual manager-attributed hours
    # ---------------------------------------------------------

    new_attribution_parts = []

    for m in selected_months:
        df = daily_attribution_by_month[m].copy()

        df = df.rename(
            columns={
                "Manager ID": "Direct Manager ID",
                "Manager Name": "Direct Manager Name",
                "Manager Level": "Direct Manager Level",
            }
        )

        df["Billable"] = pd.to_numeric(
            df["Billable"], errors="coerce"
        ).fillna(0)

        df["Non-Billable"] = pd.to_numeric(
            df["Non-Billable"], errors="coerce"
        ).fillna(0)

        df["Bench"] = pd.to_numeric(
            df["Bench"], errors="coerce"
        ).fillna(0)

        new_attribution_parts.append(
            df[
                [
                    "Month",
                    "Employee ID",
                    "Employee Name",
                    "Employee Level",
                    "Direct Manager ID",
                    "Direct Manager Name",
                    "Direct Manager Level",
                    "PL (indirect) ID",
                    "Present Days",
                    "Expected Hours",
                    "Actual Hours",
                    "Hours Review",
                    "Billable",
                    "Non-Billable",
                    "Bench",
                ]
            ]
        )

        new_attribution_full = pd.concat(
            new_attribution_parts,
            ignore_index=True,
        )


    

    #Temporary

    
    # st.write("NEW Attribution Results")
    # st.dataframe(new_attribution_full, use_container_width=True)

    
    #Temporary
    # ---------------------------------------------------------
    # Calculate row-level bonuses from actual manager-attributed hours
    # ---------------------------------------------------------

    def _new_rate(person_id, kind, bucket):
        if pd.isna(person_id):
            return 0.0

        entry = active_rates.get(str(int(person_id)), {})

        return float(
            (entry.get(kind, {}) if isinstance(entry, dict) else {})
            .get(bucket, 0) or 0
        )


    # Direct bonus based on the actual hours attributed to the direct manager
    for bkt in BUCKETS:
        new_attribution_full[f"Bonus {bkt}"] = new_attribution_full.apply(
            lambda r: r[bkt]
            * _new_rate(
                r["Direct Manager ID"],
                "direct",
                bkt,
            ),
            axis=1,
        )

    new_attribution_full["Direct Bonus"] = (
        new_attribution_full["Bonus Billable"]
        + new_attribution_full["Bonus Non-Billable"]
        + new_attribution_full["Bonus Bench"]
    )


    # Indirect bonus based on the actual hours attributed to the employee's PL
    new_attribution_full["Indirect Bonus"] = new_attribution_full.apply(
        lambda r: (
            r["Billable"] * _new_rate(
                r["PL (indirect) ID"],
                "indirect",
                "Billable",
            )
            + r["Non-Billable"] * _new_rate(
                r["PL (indirect) ID"],
                "indirect",
                "Non-Billable",
            )
            + r["Bench"] * _new_rate(
                r["PL (indirect) ID"],
                "indirect",
                "Bench",
            )
        ),
        axis=1,
    )


    new_attribution_full["Total Bonus"] = (
        new_attribution_full["Direct Bonus"]
        + new_attribution_full["Indirect Bonus"]
    )

    # #Temporary
    # st.write("NEW Attribution WITH ROW-LEVEL BONUS")
    # st.dataframe(
    #     new_attribution_full[
    #         [
    #             "Employee ID",
    #             "Employee Name",
    #             "Direct Manager ID",
    #             "Billable",
    #             "Non-Billable",
    #             "Bench",
    #             "Bonus Billable",
    #             "Bonus Non-Billable",
    #             "Bonus Bench",
    #             "Direct Bonus",
    #             "Indirect Bonus",
    #             "Total Bonus",
    #         ]
    #     ],
    #     use_container_width=True,
    # )
    # #Temporary

    # # Bonus (money) columns for the breakdown = hours × the DIRECT manager's direct rate.
    # def _rate(person_id, kind, bucket):
    #     if pd.isna(person_id):
    #         return 0.0
    #     entry = active_rates.get(str(int(person_id)), {})
    #     return float((entry.get(kind, {}) if isinstance(entry, dict) else {}).get(bucket, 0) or 0)

    # for bkt in BUCKETS:
    #     attribution_full[f"Bonus {bkt}"] = attribution_full.apply(
    #         lambda r: r[bkt] * _rate(r["Direct Manager ID"], "direct", bkt), axis=1
    #     )

    # # Add Direct/Indirect/Total Bonus per row
    # attribution_full["Direct Bonus"] = (
    #     attribution_full["Bonus Billable"] + attribution_full["Bonus Non-Billable"] + attribution_full["Bonus Bench"]
    # )
    # # Indirect Bonus: for rows where employee is L2 or below, calculate PL's indirect earnings
    # attribution_full["Indirect Bonus"] = attribution_full.apply(
    #     lambda r: (
    #         r["Billable"] * _rate(r["PL (indirect) ID"], "indirect", "Billable")
    #         + r["Non-Billable"] * _rate(r["PL (indirect) ID"], "indirect", "Non-Billable")
    #         + r["Bench"] * _rate(r["PL (indirect) ID"], "indirect", "Bench")
    #     ) if pd.notna(r["PL (indirect) ID"]) else 0.0,
    #     axis=1,
    # )
    # attribution_full["Total Bonus"] = attribution_full["Direct Bonus"] + attribution_full["Indirect Bonus"]

    # id -> "Name" and "Level" for building the team-filter options
    earner_name = dict(zip(earners["Person ID"].astype(int), earners["Person Name"]))
    earner_level = dict(zip(earners["Person ID"].astype(int), earners["Level"]))

    rates_used_rows = []
    for pid, r in active_rates.items():
        node = combined_hierarchy[combined_hierarchy["Person ID"] == int(pid)]
        name = node["Person Name"].iloc[0] if len(node) else ""
        level = node["Level"].iloc[0] if len(node) else ""
        d = r.get("direct", {}) or {}
        i = r.get("indirect", {}) or {}
        rates_used_rows.append({
            "Person ID": int(pid), "Name": name, "Level": level,
            "Direct Billable": d.get("Billable", 0), "Direct Non-Billable": d.get("Non-Billable", 0), "Direct Bench": d.get("Bench", 0),
            "Indirect Billable": i.get("Billable", 0), "Indirect Non-Billable": i.get("Non-Billable", 0), "Indirect Bench": i.get("Bench", 0),
        })
    rates_used_df = pd.DataFrame(rates_used_rows).sort_values(["Level", "Name"])

    all_zero_count = sum(
        1 for pid, r in active_rates.items()
        if int(pid) not in excluded
        and all((r.get(k, {}).get(b, 0) or 0) == 0 for k in ("direct", "indirect") for b in BUCKETS)
    )

    st.session_state["results"] = {
        "totals": totals,
        "summary_by_basis": summary_by_basis,
        "attribution_full": new_attribution_full,
        "active_rates": active_rates,
        "earner_name": earner_name,
        "earner_level": earner_level,
        "rates_used_df": rates_used_df,
        "months_label": ", ".join(selected_months) + f" {int(year)}",
        "file_tag": "-".join(selected_months),
        "year": int(year),
        "excluded": sorted(excluded),
        "all_zero_count": all_zero_count,
    }

# ---------------- Results (render from session_state so widget interactions don't wipe them) ----------------
if "results" in st.session_state:
    R = st.session_state["results"]
    totals = R["totals"]
    summary_by_basis = R["summary_by_basis"]
    attribution_full = R["attribution_full"].copy()
    active_rates = R["active_rates"]
    earner_name = R["earner_name"]
    earner_level = R["earner_level"]
    rates_used_df = R["rates_used_df"]
    months_label = R["months_label"]
    file_tag = R["file_tag"]
    res_year = R["year"]

    # DISPLAY_COLS = [
    #     "Month", "Employee Name", "Employee Level",
    #     "Director Name", "PL Name", "L1 Name", "L2 Name", "Present Days",
    #     "Share %", "Billable", "Non-Billable", "Bench",
    #     "Bonus Billable", "Bonus Non-Billable", "Bonus Bench",
    #     "Direct Bonus", "Indirect Bonus",
    # ]
    DISPLAY_COLS = [
    "Month",
    "Employee ID",
    "Employee Name",
    "Direct Manager ID",
    "Direct Manager Name",
    "Present Days",
    "Expected Hours",
    "Actual Hours",
    "Hours Review",
    "Billable",
    "Non-Billable",
    "Bench",
    "Bonus Billable",
    "Bonus Non-Billable",
    "Bonus Bench",
    "Direct Bonus",
    "Indirect Bonus",
    "Total Bonus",
]

    attribution_df = attribution_full[DISPLAY_COLS].reset_index(drop=True)

    if R["excluded"]:
        st.info(f"{len(R['excluded'])} person(s) excluded via the Include checkbox: {R['excluded']}")
    if R["all_zero_count"]:
        st.warning(f"{R['all_zero_count']} included earner(s) have all rates at zero — they will earn nothing.")

    st.subheader(f"Earner totals — {months_label}")
    totals_cfg = {
        "Direct_Bonus": st.column_config.NumberColumn(help="For a PL: L1 hours × this PL's Direct rate. For an L1: L2 hours × this L1's rate. For an L2: L3 hours × this L2's rate.", format="%.2f"),
        "Indirect_Bonus": st.column_config.NumberColumn(help="Only PLs earn this — L2 + L3 hours under this PL × their Indirect rate.", format="%.2f"),
        "Total_Bonus": st.column_config.NumberColumn(help="Direct + Indirect.", format="%.2f"),
    }
    st.dataframe(totals, use_container_width=True, hide_index=True, column_config=totals_cfg)
    st.markdown(f"**Grand total payout: {totals['Total_Bonus'].sum():,.2f}**")

    st.subheader("Earner summary by basis")
    st.dataframe(summary_by_basis, use_container_width=True, hide_index=True)

    # ---------------- Per-employee breakdown ----------------
    st.subheader("Per-employee breakdown")
    st.caption(
    "One row per employee-month **per manager they reported to**. "
    "Present Days and hours are based on the dates the employee was assigned to that manager. "
    "**Bonus** is calculated from the actual hours attributed to that manager "
    "using the applicable Direct or Indirect rate. "
    "The PL's separate indirect earning is shown in the Earner tables and the manager team-filter below."
)
    attr_cfg = {
        "Employee Level": st.column_config.Column(help="This employee's level for the given segment (one below their manager)."),
        "Share %": st.column_config.NumberColumn(help="Percent of the month's calendar days the employee spent under this manager chain.", format="%.1f"),
        # "Present Days": st.column_config.NumberColumn(help="Employee's attendance for the month (P=1, 0.5P=0.5, else 0). Same for all segment rows of an employee-month."),
        # "Billable": st.column_config.NumberColumn(help="Approved Billable HOURS × Share%.", format="%.2f"),
        # "Non-Billable": st.column_config.NumberColumn(help="Approved Non-Billable HOURS × Share%.", format="%.2f"),
        # "Bench": st.column_config.NumberColumn(help="Approved Bench HOURS × Share%.", format="%.2f"),
        "Present Days": st.column_config.NumberColumn(
    help="Attendance days while the employee was assigned to this manager. P=1, 0.5P=0.5, otherwise 0.",
),

"Billable": st.column_config.NumberColumn(
    help="Actual approved Billable hours attributed to this manager.",
    format="%.2f",
),

"Non-Billable": st.column_config.NumberColumn(
    help="Actual approved Non-Billable hours attributed to this manager.",
    format="%.2f",
),

"Bench": st.column_config.NumberColumn(
    help="Actual approved Bench hours attributed to this manager.",
    format="%.2f",
),
        "Bonus Billable": st.column_config.NumberColumn(help="MONEY = Billable hours × direct manager's Billable rate.", format="%.2f"),
        "Bonus Non-Billable": st.column_config.NumberColumn(help="MONEY = Non-Billable hours × direct manager's Non-Billable rate.", format="%.2f"),
        "Bonus Bench": st.column_config.NumberColumn(help="MONEY = Bench hours × direct manager's Bench rate.", format="%.2f"),
        "Direct Bonus": st.column_config.NumberColumn(help="Total Direct Bonus = sum of Bonus Billable + Bonus Non-Billable + Bonus Bench for this segment.", format="%.2f"),
        "Indirect Bonus": st.column_config.NumberColumn(help="Indirect Bonus earned by PL ancestor from this segment (if employee is L2 or below).", format="%.2f"),
        "Total Bonus": st.column_config.NumberColumn(help="Direct Bonus + Indirect Bonus for this segment.", format="%.2f"),
    }
    disabled_cols = {c: True for c in DISPLAY_COLS}
    edited_df = st.data_editor(
        attribution_df,
        use_container_width=True,
        hide_index=True,
        column_config=attr_cfg,
        disabled=disabled_cols,
        key="per_emp_breakdown_editor",
    )
    # ---------------- Full Excel export ----------------
    def _write_with_totals(writer, df: pd.DataFrame, sheet_name: str):
        """Write DataFrame to Excel with a total row for numeric columns."""
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        if not numeric_cols:
            df.to_excel(writer, sheet_name=sheet_name, index=False)
            return
        total_row = {col: ("TOTAL" if col == df.columns[0] else "") for col in df.columns}
        for col in numeric_cols:
            total_row[col] = df[col].sum()
        df_with_total = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)
        df_with_total.to_excel(writer, sheet_name=sheet_name, index=False)
    # ---------------- Manager team-contribution filter + scoped Excel download ----------------
    st.markdown("**Show a manager's team contribution and download it**")
    st.caption(
        "Pick one or more managers. You'll get the **rows of their team's work that earns their bonus** — "
        "for an L1/L2 that's their direct reports; for a PL it's their direct L1 reports (Basis = Direct) "
        "plus all L2/L3 below them (Basis = Indirect)."
    )

    # Options = everyone who actually earns from a team in this result set.
    manager_ids = sorted(
        set(attribution_full["Direct Manager ID"].dropna().astype(int))
        | set(attribution_full["PL (indirect) ID"].dropna().astype(int))
    )
    mgr_labels = {
        f"{mid} — {earner_name.get(mid, '')} ({earner_level.get(mid, '')})": mid
        for mid in manager_ids
    }
    picked = st.multiselect(
        "Search managers (type an ID or name — pick one or more)",
        options=list(mgr_labels.keys()),
        default=[],
        placeholder="Select one or more managers to see the team work behind their bonus…",
        help="Shows only the reportees whose work contributes to the selected manager(s)' bonus. No unrelated data is included.",
        key="mgr_multiselect",
    )

    def _new_rate(person_id, kind, bucket):
        if pd.isna(person_id):
            return 0.0

        entry = active_rates.get(str(int(person_id)), {})

        return float(
            (entry.get(kind, {}) if isinstance(entry, dict) else {})
            .get(bucket, 0) or 0
        )

    TEAM_COLS = ["Manager ID", "Manager", "Basis"] + DISPLAY_COLS

    def _team_rows(mid: int) -> pd.DataFrame:
        direct = attribution_full[attribution_full["Direct Manager ID"].fillna(-1).astype(int) == mid].copy()
        direct["Basis"] = "Direct"
        indirect = attribution_full[attribution_full["PL (indirect) ID"].fillna(-1).astype(int) == mid].copy()
        indirect["Basis"] = "Indirect"
        team = pd.concat([direct, indirect], ignore_index=True)
        if team.empty:
            return team
        team["Manager ID"] = mid
        team["Manager"] = earner_name.get(mid, "")
        # Bonus columns = the SELECTED manager's own earnings: direct rate on Direct rows,
        # indirect rate on Indirect rows.
        for bkt in BUCKETS:
            team[f"Bonus {bkt}"] = team.apply(
                lambda r: r[bkt] * _new_rate(mid, "direct" if r["Basis"] == "Direct" else "indirect", bkt),
                axis=1,
            )
        team["Direct Bonus"] = 0.0
        team["Indirect Bonus"] = 0.0

        team.loc[team["Basis"] == "Direct", "Direct Bonus"] = (
            team.loc[team["Basis"] == "Direct", "Bonus Billable"]
            + team.loc[team["Basis"] == "Direct", "Bonus Non-Billable"]
            + team.loc[team["Basis"] == "Direct", "Bonus Bench"]
        )

        team.loc[team["Basis"] == "Indirect", "Indirect Bonus"] = (
            team.loc[team["Basis"] == "Indirect", "Bonus Billable"]
            + team.loc[team["Basis"] == "Indirect", "Bonus Non-Billable"]
            + team.loc[team["Basis"] == "Indirect", "Bonus Bench"]
        )

        team["Total Bonus"] = team["Direct Bonus"] + team["Indirect Bonus"]
        return team[TEAM_COLS]

    if picked:
        mgr_ids = [mgr_labels[p] for p in picked]
        all_team = pd.concat([_team_rows(m) for m in mgr_ids], ignore_index=True)
        all_team = all_team.sort_values(["Manager ID", "Basis", "Employee ID", "Month"]).reset_index(drop=True)

        if all_team.empty:
            st.warning("The selected manager(s) have no team contribution in this result (e.g. a leaf with no reports).")
        else:
            st.caption(f"{len(mgr_ids)} manager(s) selected · {all_team['Employee ID'].nunique()} reportees · {len(all_team)} rows.")
            st.dataframe(all_team, use_container_width=True, hide_index=True, column_config=attr_cfg)

            sel_buf = io.BytesIO()
            with pd.ExcelWriter(sel_buf, engine="xlsxwriter") as w:
                _write_with_totals(w, all_team, "Team Contribution")
                # Add bonus summary per selected manager
                for mid in mgr_ids:
                    mgr_totals = totals[totals["Earner ID"] == mid]
                    if not mgr_totals.empty:
                        nm = earner_name.get(mid, str(mid))
                        sheet = f"{mid} {nm} Summary"[:31]
                        _write_with_totals(w, mgr_totals, sheet)
                used = set()
                for mid in mgr_ids:
                    tdf = all_team[all_team["Manager ID"] == mid]
                    if tdf.empty:
                        continue
                    nm = earner_name.get(mid, str(mid))
                    sheet = f"{mid} {nm}"[:31]
                    base, k = sheet, 1
                    while sheet in used:
                        sheet = f"{base[:28]}_{k}"; k += 1
                    used.add(sheet)
                    _write_with_totals(w, tdf, sheet)

            tag = "-".join(str(e) for e in mgr_ids) if len(mgr_ids) <= 5 else f"{len(mgr_ids)}-managers"
            st.download_button(
                f"Download {len(mgr_ids)} manager(s) team data (Excel)",
                data=sel_buf.getvalue(),
                file_name=f"team_contribution_{tag}_{file_tag}_{res_year}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="team_download",
            )

    # # ---------------- Full Excel export ----------------
    # def _write_with_totals(writer, df: pd.DataFrame, sheet_name: str):
    #     """Write DataFrame to Excel with a total row for numeric columns."""
    #     numeric_cols = df.select_dtypes(include="number").columns.tolist()
    #     if not numeric_cols:
    #         df.to_excel(writer, sheet_name=sheet_name, index=False)
    #         return
    #     total_row = {col: ("TOTAL" if col == df.columns[0] else "") for col in df.columns}
    #     for col in numeric_cols:
    #         total_row[col] = df[col].sum()
    #     df_with_total = pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)
    #     df_with_total.to_excel(writer, sheet_name=sheet_name, index=False)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        _write_with_totals(w, totals, "Earner Totals")
        _write_with_totals(w, summary_by_basis, "Earner by Basis")
        _write_with_totals(w, edited_df, "Employee Breakdown")
        _write_with_totals(w, rates_used_df, "Rates Used")
    st.download_button(
        "Download results (Excel)",
        data=buf.getvalue(),
        file_name=f"leadership_bonus_{file_tag}_{res_year}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="full_download",
    )
