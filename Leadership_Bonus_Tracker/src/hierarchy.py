import pandas as pd

LEVEL_NAMES = {0: "Director", 1: "PL", 2: "L1", 3: "L2", 4: "L3"}


def _level_name(depth: int) -> str:
    if depth in LEVEL_NAMES:
        return LEVEL_NAMES[depth]
    return f"L{depth - 1}"  # deeper than expected: L4, L5, ...


def build_hierarchy(mgr_map: pd.DataFrame) -> pd.DataFrame:
    """
    Given the as-of-month-end manager map (Employee ID, First Name, Last Name,
    Manager ID, Manager Name), return one row per person (employees + roots)
    with: Person ID, Person Name, Manager ID, Depth, Level, Director ID/Name,
    PL ID/Name, Lead ID/Name, Ancestors (list of IDs from direct→root).
    """
    parent: dict[int, int] = {}
    name: dict[int, str] = {}

    for _, row in mgr_map.iterrows():
        eid = int(row["Employee ID"])
        parent[eid] = int(row["Manager ID"]) if pd.notna(row["Manager ID"]) else None
        fn = str(row.get("First Name") or "").strip()
        ln = str(row.get("Last Name") or "").strip()
        name[eid] = (fn + " " + ln).strip() or f"#{eid}"

    for _, row in mgr_map.iterrows():
        if pd.notna(row["Manager ID"]):
            mid = int(row["Manager ID"])
            if mid not in name:
                name[mid] = str(row.get("Manager Name") or f"#{mid}").strip()
            parent.setdefault(mid, None)  # root unless proven otherwise

    def depth_of(pid: int, seen=None) -> int:
        seen = seen or set()
        if pid in seen:
            return 0  # cycle guard
        seen.add(pid)
        p = parent.get(pid)
        if p is None:
            return 0
        return 1 + depth_of(p, seen)

    def ancestors(pid: int) -> list[int]:
        chain = []
        cur = parent.get(pid)
        seen = set()
        while cur is not None and cur not in seen:
            chain.append(cur)
            seen.add(cur)
            cur = parent.get(cur)
        return chain

    rows = []
    for pid in parent.keys():
        d = depth_of(pid)
        anc = ancestors(pid)
        director_id = anc[-1] if anc else pid
        pl_id = _ancestor_at_depth(anc, 1, parent)
        l1_id = _ancestor_at_depth(anc, 2, parent)
        l2_id = _ancestor_at_depth(anc, 3, parent)
        rows.append(
            {
                "Person ID": pid,
                "Person Name": name.get(pid, f"#{pid}"),
                "Manager ID": parent.get(pid),
                "Depth": d,
                "Level": _level_name(d),
                "Director ID": director_id,
                "Director Name": name.get(director_id, ""),
                "PL ID": pl_id,
                "PL Name": name.get(pl_id, "") if pl_id else "",
                "L1 ID": l1_id,
                "L1 Name": name.get(l1_id, "") if l1_id else "",
                "L2 ID": l2_id,
                "L2 Name": name.get(l2_id, "") if l2_id else "",
                "Ancestors": anc,
            }
        )
    return pd.DataFrame(rows)


def _ancestor_at_depth(ancestors_chain: list[int], target_depth: int, parent: dict) -> int | None:
    """Return the ancestor at absolute depth target_depth (0=Director, 1=PL, ...).
    ancestors_chain is direct→root; walk from root end back to find the one at target_depth."""
    if not ancestors_chain:
        return None
    # Compute depth of the topmost (root) then walk down.
    # Root depth = 0; next = 1; ... ; direct parent = ?
    # Reverse gives root→direct.
    root_to_direct = list(reversed(ancestors_chain))
    if target_depth < len(root_to_direct):
        return root_to_direct[target_depth]
    return None
