"""Dashboard-only projected inputs; canonical operating/payroll math is unchanged.

All matching records are read in original cursor order, without the legacy
5k/10k caps. Temporary buffers are owned by the Dashboard request. No durable
writes, salary changes, Ledger or Journal operations occur in this adapter.
"""
from uuid import uuid4

from employee_payroll_status import (
    EMPLOYEES_COLLECTION, SALARY_CONTRACTS_COLLECTION,
    contract_salary_row, normalized_salary_revisions,
)

BATCH_SIZE = 128


def projection(*fields):
    return {"_id": 0, **dict.fromkeys(fields, 1)}


SALARY_FIELDS = projection("category", "status", "start_date", "monthly_amount",
                           "payroll_source", "effective_to", "payroll_suspension_periods")
CONTRACT_FIELDS = projection(
    "id", "employee_id", "effective_from", "effective_to", "start_date", "monthly_amount",
    "salary_revisions", "payroll_state", "legacy_salary_id", "suspension_periods",
    "accrual_mode", "accrual_start_date",
)
EMPLOYEE_FIELDS = projection(
    "id", "status", "hire_date", "updated_at", "country", "display_name", "name",
    "archived", "is_archived", "deleted", "is_deleted",
)
RENTAL_FIELDS = projection("status", "start_date", "end_date", "annual_amount")
PREPAID_FIELDS = projection("status", "start_date", "end_date", "amount", "expense_type")


class DashboardOperatingInputs:
    def __init__(self, store, *, collect_details=True):
        self.store = store
        self.collect_details = collect_details
        self.namespace = "operating-" + uuid4().hex
        self.salaries = store.sequence(self.namespace + "-salaries")
        self.rentals = store.sequence(self.namespace + "-rentals")
        self.prepaids = store.sequence(self.namespace + "-prepaids")
        self.metrics = {"max_mongo_batch": 0, "documents_loaded": {}}

    async def rows(self, db, name, query, fields):
        cursor = db[name].find(query, fields).batch_size(BATCH_SIZE)
        try:
            while True:
                batch = await cursor.to_list(length=BATCH_SIZE)
                if not batch:
                    break
                self.metrics["max_mongo_batch"] = max(self.metrics["max_mongo_batch"], len(batch))
                counts = self.metrics["documents_loaded"]
                counts[name] = counts.get(name, 0) + len(batch)
                for row in batch:
                    yield row
        finally:
            await cursor.close()

    def daily_expenses(self, db, user_id, start, end):
        return self.rows(db, "operating_daily_expenses", {
            "user_id": user_id, "date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
        }, projection("amount"))


async def _append_rows(inputs, target, db, collection, query, fields):
    rows = inputs.rows(db, collection, query, fields)
    try:
        async for row in rows:
            target.append(row)
    finally:
        await rows.aclose()


async def load_dashboard_operating_inputs(db, user_id, store, *, collect_details=True):
    inputs = DashboardOperatingInputs(store, collect_details=collect_details)
    scope = inputs.namespace
    non_employee = store.sequence(scope + "-non-employee")
    await _append_rows(inputs, non_employee, db, "operating_salaries", {
        "user_id": user_id, "category": {"$in": ["household", "charity"]},
    }, SALARY_FIELDS)
    contracts = store.sequence(scope + "-contracts")
    await _append_rows(inputs, contracts, db, SALARY_CONTRACTS_COLLECTION, {"user_id": user_id}, CONTRACT_FIELDS)
    identities = store.set(scope + "-employee-ids")
    identity_count = 0
    for contract in contracts:
        identity = str(contract.get("employee_id") or "").strip()
        if identity:
            identities.add(identity)
            identity_count += 1
    employees = store.map(scope + "-employees", mutable=False)
    if identities:
        # Equivalent to the legacy string $in membership without an unbounded
        # query array. Numeric/space-padded employee ids must not start matching.
        rows = inputs.rows(db, EMPLOYEES_COLLECTION, {"user_id": user_id}, EMPLOYEE_FIELDS)
        matched_count = 0
        try:
            async for employee in rows:
                raw_id = employee.get("id")
                if isinstance(raw_id, str) and raw_id in identities:
                    matched_count += 1
                    if not any(employee.get(flag) for flag in ("archived", "is_archived", "deleted", "is_deleted")):
                        employees[raw_id.strip()] = employee
                    # Preserve the original employee lookup's length bound,
                    # including archived rows and repeated contract ids.
                    if matched_count >= identity_count:
                        break
        finally:
            await rows.aclose()
    accepted = store.sequence(scope + "-accepted-contracts")
    seen = store.set(scope + "-seen-contracts")
    for contract in contracts:
        identity = str(contract.get("employee_id") or "").strip()
        if not identity or identity not in employees:
            continue
        if identity in seen:
            raise ValueError("employee_salary_multiple_contracts")
        seen.add(identity)
        accepted.append(contract)
    # Match the original two passes: duplicate identity validation precedes
    # revision validation; canonical projection controls every payroll field.
    for contract in accepted:
        identity = str(contract.get("employee_id") or "").strip()
        if normalized_salary_revisions(contract):
            inputs.salaries.append(contract_salary_row(contract, employees[identity]))
    inputs.salaries.extend(non_employee)
    for rows in (contracts, accepted, non_employee):
        store.discard_sequence(rows.name)
    for mapping in (employees, identities.mapping, seen.mapping):
        store.discard_map(mapping.name)
    await _append_rows(inputs, inputs.rentals, db, "operating_rentals", {"user_id": user_id}, RENTAL_FIELDS)
    await _append_rows(inputs, inputs.prepaids, db, "operating_prepaid_expenses", {"user_id": user_id}, PREPAID_FIELDS)
    return inputs
