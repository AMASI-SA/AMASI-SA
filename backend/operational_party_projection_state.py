"""Select one party from the verified snapshot for its canonical balance projection."""
from operational_financial_activity_runtime import checked_rows


def party_projection_state(state, kind, identity):
    def belongs(row):
        return row.get("party_type") == kind and row.get("party_id") == identity

    # Fees booked from another party's transfer are credited to these built-in expenses.
    if kind == "operating_expense" and identity in {"pos_settlement_fee", "advertising_bank_fx_fee"}:
        return state
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


def cash_account_projection(state, kind, identity):
    """Only cash legs and claims concerning the selected money account are needed."""
    from operational_financial_activity import cash_positions
    engine = state.get("engine", {})
    currencies = {r["currency"] for r in checked_rows(state.get("openings", {}).values())
                  if (r["party_type"], r["party_id"]) == (kind, identity)}
    for row in checked_rows(state.get("movements", [])):
        if (row.get("bank_kind"), row.get("bank_id")) == (kind, identity):
            currencies.add(row.get("bank_currency") or row["currency"])
        if (row["party_type"], row["party_id"]) == (kind, identity):
            currencies.add(row["currency"])
    currencies.update(r["currency"] for r in checked_rows(state.get("customer_returns", []))
                      if (r.get("refund_source_type"), r.get("refund_source_id")) == (kind, identity))
    for case in checked_rows(state.get("customer_returns", [])):
        if (case.get("refund_source_type"), case.get("refund_source_id"), case.get("status")) == (kind, identity, "salla_refund_confirmed"):
            from operational_provider_statement_reconciliation import fact_backed_employee_refund
            fact_backed_employee_refund(state, case)
    parties = cash_positions(state, {(kind, identity, currency) for currency in currencies})
    claims = [r for r in checked_rows(engine.get("obligations", {}).values()) if not r.get("superseded")
              and (r["party_type"], r["party_id"]) == (kind, identity)]
    return {"parties": list(parties.values()), "obligations": claims, "details": {}, "issues": [], "as_of": engine.get("as_of")}

async def directory_cash_positions(db, owner, kind, resources, *, funding_ids=()):
    """Read only cash evidence belonging to visible balances in the opened group."""
    from operational_balance_store import STATES
    from operational_financial_activity import cash_positions
    from operational_financial_activity_runtime import prepare_in_thread
    keys = {(kind, row["id"], row["currency"]) for row in resources if row["access"]["balance"] or row["id"] in funding_ids}
    if not keys:
        return {}
    identities = sorted(key[1] for key in keys)
    def matches(field, id_field):
        return {"$and": [{"$eq": [field, kind]}, {"$in": [id_field, {"$literal": identities}]}]}
    def selected(array, condition):
        return {"$filter": {"input": {"$ifNull": [array, []]}, "as": "r", "cond": condition}}
    openings = {"$objectToArray": {"$ifNull": ["$openings", {}]}}
    pipeline = [
        {"$match": {"_id": owner, "owner_id": owner}},
        {"$project": {
            "openings": {"$arrayToObject": selected(openings, matches("$$r.v.party_type", "$$r.v.party_id"))},
            "movements": selected("$movements", {"$or": [matches("$$r.party_type", "$$r.party_id"),
                                                        matches("$$r.bank_kind", "$$r.bank_id")]}),
            "customer_returns": selected("$customer_returns", matches("$$r.refund_source_type", "$$r.refund_source_id")),
        }},
        {"$project": {"_id": 0, "openings": 1, "customer_returns": 1,
                      **{"movements." + field: 1 for field in ("party_type", "party_id", "currency", "bank_kind",
                          "bank_id", "bank_currency", "bank_amount", "amount", "kind", "direction")}}},
    ]
    result = await db[STATES].aggregate(pipeline).to_list(1)
    return await prepare_in_thread(cash_positions, result[0] if result else {}, keys)
