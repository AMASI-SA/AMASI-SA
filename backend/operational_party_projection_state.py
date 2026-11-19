"""Select one party from the verified snapshot for its canonical balance projection."""
from operational_financial_activity_runtime import checked_rows


def party_projection_state(state, kind, identity):
    def belongs(row):
        return row.get("party_type") == kind and row.get("party_id") == identity

    engine = state.get("engine", {})
    openings = {key: row for key, row in checked_rows(state["openings"].items()) if belongs(row)}
    obligations = {key: row for key, row in checked_rows(engine.get("obligations", {}).items()) if belongs(row)}
    movements = []
    for row in checked_rows(state["movements"]):
        if belongs(row):
            movements.append(row)
        elif ((row.get("bank_kind"), row.get("bank_id")) == (kind, identity) or
              any(a["obligation_id"] in obligations or a["obligation_id"] in openings
                  for a in row.get("allocations", []))):
            # Legacy cross-account evidence requires the complete canonical projection.
            return state

    # Keep the complete binding registry for conflict checks. Both native and SAR
    # financial rows for the selected identity participate in the ordinary report.
    return {key: state.get(key) for key in ("_id", "owner_id", "revision", "status", "started_at")} | {
        "openings": openings, "movements": movements, "ad_funding": state.get("ad_funding", {}),
        "engine": {"obligations": obligations, "as_of": engine.get("as_of"),
                   "ad_balance_sar_basis": engine.get("ad_balance_sar_basis", {})}}
