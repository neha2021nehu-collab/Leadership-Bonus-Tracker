import json
import pandas as pd
from pathlib import Path

RATES_PATH = Path(__file__).resolve().parent.parent / "rates.json"

BUCKETS = ["Billable", "Non-Billable", "Bench"]

# Which manager-Levels earn a "direct" bonus on their direct reports' hours.
DIRECT_EARNING_LEVELS = {"PL", "L1", "L2"}


def load_rates() -> dict:
    if RATES_PATH.exists():
        try:
            return json.loads(RATES_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_rates(rates: dict) -> None:
    RATES_PATH.write_text(json.dumps(rates, indent=2))


def empty_rate_set() -> dict:
    return {b: 0.0 for b in BUCKETS}


def person_rate_for(person_rates: dict, person_id: int, kind: str) -> dict:
    """Return the rate dict for a person's earning kind ('direct' or 'indirect')."""
    entry = person_rates.get(str(int(person_id)), {})
    return entry.get(kind, {}) if isinstance(entry, dict) else {}


def earning_people(hierarchy_df: pd.DataFrame, extra_ids: set | None = None) -> pd.DataFrame:
    """People eligible to earn: PL/L1/L2 with at least one report at month-end, PLUS any
    id in extra_ids (e.g. managers who held a report only mid-month, or PL-ancestors of
    such managers) so every possible earner gets a rate row."""
    extra_ids = {int(x) for x in (extra_ids or set())}
    child_counts = hierarchy_df["Manager ID"].dropna().astype(int).value_counts().to_dict()
    df = hierarchy_df.copy()
    df["Reports"] = df["Person ID"].map(lambda p: child_counts.get(int(p), 0))
    keep = (df["Level"].isin(DIRECT_EARNING_LEVELS)) & (
        (df["Reports"] > 0) | (df["Person ID"].astype(int).isin(extra_ids))
    )
    return df[keep].sort_values(["Level", "Person Name"]).reset_index(drop=True)


def build_employee_view(hours_df: pd.DataFrame, hierarchy_df: pd.DataFrame) -> pd.DataFrame:
    h = hierarchy_df.rename(columns={"Person ID": "Employee ID"})
    merged = hours_df.merge(
        h[[
            "Employee ID", "Manager ID", "Level",
            "Director ID", "Director Name",
            "PL ID", "PL Name",
            "L1 ID", "L1 Name",
            "L2 ID", "L2 Name",
        ]],
        on="Employee ID", how="left",
    )
    return merged


def orphan_hours(emp_view: pd.DataFrame) -> pd.DataFrame:
    """Employees who have timesheet hours but are NOT placed in the hierarchy
    (no reporting entry as of month-end). Their hours are excluded from all bonuses."""
    orphans = emp_view[emp_view["Level"].isna()].copy()
    if orphans.empty:
        return orphans
    cols = ["Employee ID", "First Name", "Month", "Billable", "Non-Billable", "Bench"]
    cols = [c for c in cols if c in orphans.columns]
    return orphans[cols].reset_index(drop=True)


EMPTY_BONUS_COLS = [
    "Earner ID", "Earner Name", "Level", "Basis", "Month",
    "Billable_Hrs", "NonBillable_Hrs", "Bench_Hrs",
    "Billable_Bonus", "NonBillable_Bonus", "Bench_Bonus", "Total_Bonus",
]


def attribution_detail(hours_df: pd.DataFrame, segments_df: pd.DataFrame, hierarchy_df: pd.DataFrame,
                       month_label) -> pd.DataFrame:
    """
    One row per (employee, month, manager-segment) showing how the employee's hours were
    day-weighted and to whom they flowed. This is the source data behind the bonuses, so:
      • summing Attributed_* grouped by Direct Manager ID  == that manager's Direct earner hours;
      • summing Attributed_* grouped by PL Name (rows where Mgr Level is not PL/Director)
        == that PL's Indirect earner hours.
    'Attributed' columns = raw hours × the day-share fraction.
    """
    from src.hierarchy import LEVEL_NAMES

    def _level_name(depth):
        return LEVEL_NAMES.get(depth, f"L{depth - 1}")

    node_by_id = hierarchy_df.set_index("Person ID").to_dict("index")
    seg_by_emp: dict = {}
    for _, s in segments_df.iterrows():
        seg_by_emp.setdefault(int(s["Employee ID"]), []).append(s)

    rows = []
    for _, h in hours_df.iterrows():
        if pd.isna(h["Employee ID"]):
            continue
        eid = int(h["Employee ID"])
        raw = {b: float(h.get(b, 0) or 0) for b in BUCKETS}
        for s in seg_by_emp.get(eid, []):
            mid = int(s["Manager ID"])
            frac = float(s["Fraction"])
            m_node = node_by_id.get(mid, {})
            m_level = m_node.get("Level", "")
            m_depth = m_node.get("Depth")

            # Employee's chain for THIS segment = the manager's strict-ancestor chain,
            # with the manager placed into its own level slot.
            chain = {
                "Director Name": m_node.get("Director Name", ""),
                "PL Name": m_node.get("PL Name", ""),
                "L1 Name": m_node.get("L1 Name", ""),
                "L2 Name": m_node.get("L2 Name", ""),
            }
            if f"{m_level} Name" in chain:
                chain[f"{m_level} Name"] = s["Manager Name"]
            emp_level = _level_name(m_depth + 1) if m_depth is not None else ""

            # PL who earns the INDIRECT bonus on this row (only when the direct manager
            # is L1 or deeper — i.e. the employee sits at L2 or below). None otherwise.
            pl_ind_id = None
            if m_level not in ("Director", "PL"):
                _pl = m_node.get("PL ID")
                pl_ind_id = int(_pl) if pd.notna(_pl) else None

            rows.append({
                "Month": month_label,
                "Employee ID": eid,
                "Employee Name": h.get("First Name"),
                "Employee Level": emp_level,
                "Director Name": chain["Director Name"] or "",
                "PL Name": chain["PL Name"] or "",
                "L1 Name": chain["L1 Name"] or "",
                "L2 Name": chain["L2 Name"] or "",
                "Direct Manager ID": mid,
                "Direct Manager": s["Manager Name"],
                "Mgr Level": m_level,
                "PL (indirect) ID": pl_ind_id,
                "Days": int(s["Days"]),
                "Share %": round(frac * 100, 1),
                "Raw Billable": raw["Billable"],
                "Raw Non-Billable": raw["Non-Billable"],
                "Raw Bench": raw["Bench"],
                "Attributed Billable": raw["Billable"] * frac,
                "Attributed Non-Billable": raw["Non-Billable"] * frac,
                "Attributed Bench": raw["Bench"] * frac,
            })
    cols = [
        "Month", "Employee ID", "Employee Name", "Employee Level",
        "Director Name", "PL Name", "L1 Name", "L2 Name",
        "Direct Manager ID", "Direct Manager", "Mgr Level", "PL (indirect) ID", "Days", "Share %",
        "Raw Billable", "Raw Non-Billable", "Raw Bench",
        "Attributed Billable", "Attributed Non-Billable", "Attributed Bench",
    ]
    return pd.DataFrame(rows, columns=cols)


def apply_rates_month(hours_df: pd.DataFrame, segments_df: pd.DataFrame, hierarchy_df: pd.DataFrame,
                      person_rates: dict, month_label, exclude_ids: set | None = None) -> pd.DataFrame:
    """
    Attribute one month's hours to earners, splitting each employee's hours across the
    managers they reported to during that month (day-weighted fractions from segments_df).

    For an employee E with hours H under manager m for fraction f of the month:
      • Direct  : m earns on H×f (if m is PL / L1 / L2) at m's Direct rate.
      • Indirect: if m is L1 or deeper (i.e. E sits at L2 or below), m's PL-ancestor earns
                  on H×f at that PL's Indirect rate.
    An earner is NEVER credited for their own hours (hours flow up to a manager, never to self).
    Unassigned-day fractions simply don't appear in segments_df, so those hours are dropped.
    """
    exclude_ids = {int(x) for x in (exclude_ids or set())}
    node_by_id = hierarchy_df.set_index("Person ID").to_dict("index")

    # hours[employee_id] = {bucket: hrs}
    hours: dict = {}
    for _, r in hours_df.iterrows():
        if pd.isna(r["Employee ID"]):
            continue
        hours[int(r["Employee ID"])] = {b: float(r.get(b, 0) or 0) for b in BUCKETS}

    # segments grouped by employee
    seg_by_emp: dict = {}
    for _, s in segments_df.iterrows():
        seg_by_emp.setdefault(int(s["Employee ID"]), []).append((int(s["Manager ID"]), float(s["Fraction"])))

    records = []
    for emp_id, H in hours.items():
        if sum(H.values()) == 0:
            continue
        for mgr_id, frac in seg_by_emp.get(emp_id, []):
            if frac <= 0:
                continue
            m_node = node_by_id.get(mgr_id)
            if not m_node:
                continue
            level = m_node.get("Level")
            hrs_f = {b: H[b] * frac for b in BUCKETS}

            # DIRECT — the manager themselves
            if level in DIRECT_EARNING_LEVELS and mgr_id not in exclude_ids:
                _add_record(records, mgr_id, m_node, "Direct", month_label, hrs_f,
                            person_rate_for(person_rates, mgr_id, "direct"))

            # INDIRECT — the PL ancestor, when the employee sits at L2 or deeper
            if level not in ("Director", "PL"):
                pl_id = m_node.get("PL ID")
                if pd.notna(pl_id):
                    pl_id = int(pl_id)
                    pl_node = node_by_id.get(pl_id)
                    if pl_node and pl_id not in exclude_ids:
                        _add_record(records, pl_id, pl_node, "Indirect (PL)", month_label, hrs_f,
                                    person_rate_for(person_rates, pl_id, "indirect"))

    if not records:
        return pd.DataFrame(columns=EMPTY_BONUS_COLS)
    return pd.DataFrame(records)


#Neha 
# def apply_rates_manager_hours(
#     manager_final_df: pd.DataFrame,
#     hierarchy_df: pd.DataFrame,
#     person_rates: dict,
#     month_label,
#     exclude_ids: set | None = None,
# ) -> pd.DataFrame:
#     """
#     Apply bonus rates to hours that have already been attributed
#     to the correct manager based on the employee's date-wise
#     reporting relationship.
#     """
#     exclude_ids = {int(x) for x in (exclude_ids or set())}

#     node_by_id = hierarchy_df.set_index("Person ID").to_dict("index")

#     records = []

#     for _, row in manager_final_df.iterrows():
#         if pd.isna(row["Employee ID"]) or pd.isna(row["Manager ID"]):
#             continue

#         employee_id = int(row["Employee ID"])
#         manager_id = int(row["Manager ID"])

#         manager_node = node_by_id.get(manager_id)

#         if not manager_node:
#             continue

#         if manager_id in exclude_ids:
#             continue

#         hours = {
#             "Billable": float(row.get("Billable Hours", 0) or 0),
#             "Non-Billable": float(row.get("Non-Billable Hours", 0) or 0),
#             "Bench": float(row.get("Bench Hours", 0) or 0),
#         }

#         if sum(hours.values()) == 0:
#             continue

#         level = manager_node.get("Level")

#         # DIRECT BONUS
#         if level in DIRECT_EARNING_LEVELS:
#             _add_record(
#                 records,
#                 manager_id,
#                 manager_node,
#                 "Direct",
#                 month_label,
#                 hours,
#                 person_rate_for(
#                     person_rates,
#                     manager_id,
#                     "direct",
#                 ),
#             )

#         # INDIRECT BONUS
#         if level not in ("Director", "PL"):
#             pl_id = manager_node.get("PL ID")

#             if pd.notna(pl_id):
#                 pl_id = int(pl_id)
#                 pl_node = node_by_id.get(pl_id)

#                 if pl_node and pl_id not in exclude_ids:
#                     _add_record(
#                         records,
#                         pl_id,
#                         pl_node,
#                         "Indirect (PL)",
#                         month_label,
#                         hours,
#                         person_rate_for(
#                             person_rates,
#                             pl_id,
#                             "indirect",
#                         ),
#                     )

#     if not records:
#         return pd.DataFrame(columns=EMPTY_BONUS_COLS)

#     return pd.DataFrame(records)

def apply_rates_manager_hours(
    manager_final_df: pd.DataFrame,
    hierarchy_df: pd.DataFrame,
    person_rates: dict,
    month_label,
    exclude_ids: set | None = None,
) -> pd.DataFrame:
    """
    Apply bonus rates to actual hours already attributed
    to the correct manager based on the employee's date-wise
    reporting relationship.
    """

    exclude_ids = {int(x) for x in (exclude_ids or set())}

    # Normalize manager/person IDs so "1054" and 1054 match
    hierarchy_lookup = hierarchy_df.copy()
    hierarchy_lookup["Person ID"] = pd.to_numeric(
        hierarchy_lookup["Person ID"],
        errors="coerce",
    )

    node_by_id = {}

    for _, node in hierarchy_lookup.iterrows():
        if pd.notna(node["Person ID"]):
            node_by_id[int(node["Person ID"])] = node.to_dict()

    records = []

    for _, row in manager_final_df.iterrows():

        if pd.isna(row["Employee ID"]) or pd.isna(row["Manager ID"]):
            continue

        employee_id = int(row["Employee ID"])
        manager_id = int(row["Manager ID"])

        manager_node = node_by_id.get(manager_id)

        if not manager_node:
            continue

        if manager_id in exclude_ids:
            continue

        # These are the NEW manager-attributed actual hours
        hours = {
            "Billable": float(row.get("Billable", 0) or 0),
            "Non-Billable": float(row.get("Non-Billable", 0) or 0),
            "Bench": float(row.get("Bench", 0) or 0),
        }

        # Nothing to calculate if this manager has no actual hours
        if sum(hours.values()) == 0:
            continue

        level = manager_node.get("Level")

        # -------------------------------------------------
        # DIRECT BONUS
        # -------------------------------------------------
        if level in DIRECT_EARNING_LEVELS:
            _add_record(
                records,
                manager_id,
                manager_node,
                "Direct",
                month_label,
                hours,
                person_rate_for(
                    person_rates,
                    manager_id,
                    "direct",
                ),
            )

        # -------------------------------------------------
        # INDIRECT BONUS
        # -------------------------------------------------
        if level not in ("Director", "PL"):

            pl_id = manager_node.get("PL ID")

            if pd.notna(pl_id):

                pl_id = int(pl_id)

                pl_node = node_by_id.get(pl_id)

                if pl_node and pl_id not in exclude_ids:
                    _add_record(
                        records,
                        pl_id,
                        pl_node,
                        "Indirect (PL)",
                        month_label,
                        hours,
                        person_rate_for(
                            person_rates,
                            pl_id,
                            "indirect",
                        ),
                    )

    if not records:
        return pd.DataFrame(columns=EMPTY_BONUS_COLS)

    return pd.DataFrame(records)


def _add_record(records, earner_id, earner_node, basis, month, hrs, rate_set):
    b = float(rate_set.get("Billable", 0) or 0)
    nb = float(rate_set.get("Non-Billable", 0) or 0)
    bench = float(rate_set.get("Bench", 0) or 0)
    records.append({
        "Earner ID": earner_id,
        "Earner Name": earner_node.get("Person Name", f"#{earner_id}"),
        "Level": earner_node.get("Level"),
        "Basis": basis,
        "Month": month,
        "Billable_Hrs": hrs["Billable"],
        "NonBillable_Hrs": hrs["Non-Billable"],
        "Bench_Hrs": hrs["Bench"],
        "Billable_Bonus": hrs["Billable"] * b,
        "NonBillable_Bonus": hrs["Non-Billable"] * nb,
        "Bench_Bonus": hrs["Bench"] * bench,
        "Total_Bonus": hrs["Billable"] * b + hrs["Non-Billable"] * nb + hrs["Bench"] * bench,
    })


def earner_summary(bonus_df: pd.DataFrame) -> pd.DataFrame:
    if bonus_df.empty:
        return bonus_df
    grp = bonus_df.groupby(["Earner ID", "Earner Name", "Level", "Basis"], as_index=False).agg(
        Billable_Hrs=("Billable_Hrs", "sum"),
        NonBillable_Hrs=("NonBillable_Hrs", "sum"),
        Bench_Hrs=("Bench_Hrs", "sum"),
        Billable_Bonus=("Billable_Bonus", "sum"),
        NonBillable_Bonus=("NonBillable_Bonus", "sum"),
        Bench_Bonus=("Bench_Bonus", "sum"),
        Total_Bonus=("Total_Bonus", "sum"),
    )
    return grp.sort_values(["Level", "Total_Bonus"], ascending=[True, False]).reset_index(drop=True)


def earner_totals(bonus_df: pd.DataFrame) -> pd.DataFrame:
    if bonus_df.empty:
        return bonus_df
    grp = bonus_df.groupby(["Earner ID", "Earner Name", "Level"], as_index=False).agg(
        Direct_Bonus=("Total_Bonus", lambda s: s[bonus_df.loc[s.index, "Basis"] == "Direct"].sum()),
        Indirect_Bonus=("Total_Bonus", lambda s: s[bonus_df.loc[s.index, "Basis"] == "Indirect (PL)"].sum()),
        Total_Bonus=("Total_Bonus", "sum"),
    )
    return grp.sort_values(["Level", "Total_Bonus"], ascending=[True, False]).reset_index(drop=True)
