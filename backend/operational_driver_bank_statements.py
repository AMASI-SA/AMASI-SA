"""Original bank-statement evidence for operational courier transfers only.

Intake preserves evidence and never records money. An accountant's later review
consumes one proved incoming row inside the operational review transaction.
"""
from datetime import date
from hashlib import sha256
from io import BytesIO

from fastapi import HTTPException
from openpyxl import load_workbook

from accounting_daily_movements import MAX_FILE_BYTES, parse_daily_movement_xlsx
from accounting_module_contract import accounting_permissions_for_user, require_accounting_permission
from excel_upload_security import validate_xlsx_archive
from operational_balance_store import STATES, digest, fail, now
from operational_driver_bank_store import FILES, MOVEMENTS, IMPORTS, SOURCE, transact
from operational_salary_start import instant, RIYADH
from store_delivery_bank_identity import collection_bank_accounts, find_collection_bank, require_saved_bank_identity

FILE_PREFIX = "operational-bank-file:"
MOVEMENT_PREFIX = "operational-bank-movement:"
IMPORT_PERMISSION = "accounting.movements.import"
IMMUTABLE_EXCLUSIONS = {"_id", "seal", "content", "status", "operational_event_id", "operational_consumed_at"}
PUBLIC_MOVEMENT_FIELDS = ("id", "file_id", "bank_account_id", "movement_date", "amount", "currency",
                          "direction", "reference", "description", "row_no", "status")


def seal(row):
    return digest({key: value for key, value in row.items() if key not in IMMUTABLE_EXCLUSIONS})


async def authorize_import(db, owner, actor):
    from operational_driver_payment_review import authorize
    await authorize(db, owner, actor)
    user = await db.users.find_one({"id": actor})
    require_accounting_permission(user, IMPORT_PERMISSION)


def _parse_original(content):
    if not content or len(content) > MAX_FILE_BYTES:
        fail("operational_driver_bank_file_size_invalid", status=413)
    validate_xlsx_archive(content)
    try:
        parsed = parse_daily_movement_xlsx(content)
    except (ValueError, TypeError, OSError, KeyError) as error:
        raise HTTPException(422, detail={"code": "operational_driver_bank_file_invalid",
                                        "message": str(error)[:200]}) from None
    if parsed["errors"]:
        raise HTTPException(422, detail={"code": "operational_driver_bank_rows_invalid",
            "errors": parsed["errors"][:50], "error_count": len(parsed["errors"])})
    # The shared parser is SAR-only. Do not silently ignore an explicit foreign
    # currency column in a statement uploaded against a SAR bank identity.
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
    try:
        sheet = workbook.active
        headers = next(sheet.iter_rows(min_row=parsed["header_row"], max_row=parsed["header_row"], values_only=True))
        columns = [index for index, value in enumerate(headers)
                   if str(value or "").strip().casefold() in {"currency", "currency code", "العملة", "رمز العملة"}]
        if len(columns) > 1:
            fail("operational_driver_bank_currency_ambiguous", status=422)
        if columns:
            row_numbers = {row["row_no"] for row in parsed["rows"]}
            for row_no, values in enumerate(sheet.iter_rows(min_row=parsed["header_row"] + 1, values_only=True),
                                             start=parsed["header_row"] + 1):
                if row_no in row_numbers and (len(values) <= columns[0] or str(values[columns[0]] or "").strip().upper() != "SAR"):
                    fail("operational_driver_bank_currency_invalid", status=422)
    finally:
        workbook.close()
    return parsed


def parse_original(content):
    try:
        return _parse_original(content)
    except HTTPException:
        raise
    except Exception:
        fail('operational_driver_bank_file_invalid', 'تعذر قراءة أصل كشف البنك؛ ارفع ملف Excel صالحًا', status=422)


async def _one(db, collection, query, code):
    rows = await db[collection].find(query).limit(2).to_list(2)
    if len(rows) != 1:
        fail(code)
    return rows[0]


async def _context(db, owner, actor, *, started_at=None, session_scope=None, authorize_read=None):
    from operational_driver_payment_review import authorize, financial_source
    if authorize_read is None:
        await authorize(db, owner, actor)
    else:
        await authorize_read(db)
    if await financial_source(db, owner) != "operational":
        fail("operational_driver_review_phase_changed")
    state = await db[STATES].find_one({"_id": owner, "owner_id": owner}) or {}
    if not isinstance(state.get("started_at"), str) or not state["started_at"]:
        fail("operational_driver_bank_context_invalid")
    if ((started_at is not None and started_at != state["started_at"])
            or (session_scope is not None and session_scope != digest([owner, actor]))):
        fail("operational_driver_bank_context_changed")
    return state


async def review_bank(db, owner, assignment_id):
    review = await _one(db, "store_delivery_payment_reviews", {"user_id": owner, "assignment_id": assignment_id},
                        "driver_payment_review_required")
    collection = await _one(db, "store_delivery_collections", {"user_id": owner, "assignment_id": assignment_id},
                            "driver_collection_required")
    assignment = await _one(db, "store_delivery_assignments", {"user_id": owner, "id": assignment_id},
                            "driver_delivery_required")
    if (review.get("status") != "pending" or review.get("payment_method") != "bank_transfer"
            or collection.get("payment_method") != "bank_transfer" or collection.get("payment_confirmed") is True
            or assignment.get("status") != "delivered" or assignment.get("active") is not True
            or any(record.get("driver_id") != assignment.get("driver_id") for record in (review, collection))):
        fail("operational_driver_bank_pending_review_required")
    bank = await find_collection_bank(db, owner, review.get("bank_account_id"))
    if bank is None:
        fail("driver_payment_bank_context_changed")
    require_saved_bank_identity(bank, review, collection)
    return review, bank


async def context(db, *, owner, actor):
    state = await _context(db, owner, actor)
    choices = await collection_bank_accounts(db, owner, actor_id=actor)
    user = await db.users.find_one({"id": actor})
    return {**choices, "source": SOURCE, "bank_source": choices["source"], "started_at": state["started_at"],
            "can_upload": IMPORT_PERMISSION in accounting_permissions_for_user(user),
            "accepted_file_type": ".xlsx", "max_file_bytes": MAX_FILE_BYTES}


def movement_identity(owner, bank_id, file_id, fact):
    key = ([owner, bank_id, "reference", fact["reference"]] if fact.get("reference") else
           [owner, file_id, fact["row_no"], fact["row_hash"]])
    return MOVEMENT_PREFIX + digest(key)


def bank_fact_conflict(namespace):
    if namespace == "bank_unreferenced_fact":
        raise HTTPException(409, detail={"code": "operational_driver_bank_fact_reconciliation_required",
            "message": "توجد حركة بنفس بيانات البنك دون مرجع يميزها. يلزم التحقق والمطابقة المحاسبية؛ إعادة حفظ الكشف أو تغيير موضع الصف لا تنشئ تحويلًا جديدًا."})
    fail("driver_payment_reference_already_consumed")


async def bank_fact_proof(db, *, owner, bank_id, source_namespace, movement, fact):
    """One bank fact across containers, row positions, and import namespaces.

    Claims use only facts re-read from the original bytes. Unreferenced facts
    that cannot be distinguished require reconciliation, not another credit.
    Existing source claims/consumed rows remain authoritative before this new
    shared claim existed; no backfill or native-importer mutation is required.
    """
    from accounting_advertising_contract import digest as native_digest
    from operational_driver_review_store import EVENTS

    reference = fact.get("reference")
    namespace = "bank_reference" if reference else "bank_unreferenced_fact"
    identity = (reference if reference else {key: fact[key] for key in ("date", "direction", "amount", "description")})
    fingerprint = digest([owner, bank_id, namespace, identity])
    for source in (MOVEMENTS, "mz2_daily_movements"):
        if reference:
            row_id = (MOVEMENT_PREFIX + digest([owner, bank_id, "reference", reference]) if source == MOVEMENTS else
                      native_digest([owner, bank_id, "reference", reference]))
            row = await db[source].find_one({"user_id": owner, "_id": row_id})
            rows = [row] if row else []
        else:
            query = {"user_id": owner, "bank_account_id": bank_id, "movement_date": fact["date"],
                "amount": fact["amount"], "direction": fact["direction"], "reference": None,
                "$or": [{"description": fact["description"]}, {"bank_statement_description": fact["description"]}]}
            rows = await db[source].find(query).limit(101).to_list(101)
            if len(rows) > 100:
                bank_fact_conflict(namespace)
        for row in rows:
            if source == source_namespace and row.get("id") == movement.get("id"):
                continue
            source_claim = digest([owner, "operational-driver-review-claim", "reference", [source, row.get("id")]])
            if (row.get("status") != "unclassified" or any(row.get(key) for key in (
                    "receipt_id", "accounting_event_id", "operational_event_id", "confirmed_provider"))
                    or await db[EVENTS].find_one({"user_id": owner, "_id": source_claim})
                    or await db.mz2_shipping_native_events_v2.find_one({"user_id": owner,
                        "destination.source_namespace": source, "destination.source_record_id": row.get("id")})):
                bank_fact_conflict(namespace)
    return {"bank_fact_namespace": namespace, "bank_fact_fingerprint": fingerprint}


def public_file(row):
    return {key: row.get(key) for key in ("id", "filename", "file_hash", "bank_account_id", "row_count",
                                          "started_at", "uploaded_at", "source")}


def public_movement(row):
    return {**{key: row.get(key) for key in PUBLIC_MOVEMENT_FIELDS},
            "source_namespace": MOVEMENTS if row.get("source") == SOURCE else "mz2_daily_movements"}


async def _saved_result(db, owner, file, *, status):
    if file.get("seal") != seal(file) or sha256(bytes(file.get("content") or b"")).hexdigest() != file.get("file_hash"):
        fail("operational_driver_bank_evidence_invalid")
    rows = await db[MOVEMENTS].find({"user_id": owner, "file_id": file["id"]}).sort("row_no", 1).to_list(5001)
    if (len(rows) != file["row_count"] or {row["id"] for row in rows} != set(file["movement_ids"])
            or any(row.get("seal") != seal(row) for row in rows)):
        fail("operational_driver_bank_evidence_invalid")
    return {"status": status, "file": public_file(file), "items": [public_movement(row) for row in rows],
            "financial_source": "operational", "source": SOURCE}


async def upload(db, *, owner, actor, assignment_id, bank_account_id, request_id,
                 expected_started_at, expected_session_scope, filename, content, clock=None):
    await authorize_import(db, owner, actor)
    parsed = parse_original(content)
    stamp, hashed = clock or now(), sha256(content).hexdigest()
    file_id = FILE_PREFIX + digest([owner, hashed])
    request_key = digest([owner, "operational-driver-bank-import", request_id])
    fingerprint = digest([owner, actor, assignment_id, bank_account_id, hashed,
                          expected_started_at, expected_session_scope])

    async def save(scoped):
        state = await _context(scoped, owner, actor, started_at=expected_started_at, session_scope=expected_session_scope)
        prior_request = await scoped[IMPORTS].find_one({"user_id": owner, "_id": request_key})
        if prior_request and prior_request.get("fingerprint") != fingerprint:
            fail("operational_driver_bank_import_idempotency_conflict")
        # A file belongs to exactly one bank namespace and one operational period.
        # This prevents re-uploading the same bytes under a different destination.
        previous = await scoped[FILES].find_one({"user_id": owner, "_id": file_id})
        if previous and (previous.get("bank_account_id") != bank_account_id
                         or previous.get("started_at") != state["started_at"]):
            fail("operational_driver_bank_file_context_conflict")
        if prior_request and previous:
            return await _saved_result(scoped, owner, previous, status="duplicate")
        review, bank = await review_bank(scoped, owner, assignment_id)
        if bank["id"] != bank_account_id or bank["bank_financial_source"] != "operational":
            fail("driver_payment_destination_proof_invalid")
        if previous:
            result = await _saved_result(scoped, owner, previous, status="duplicate")
        else:
            if await scoped.mz2_daily_movement_files.find_one({"user_id": owner, "file_hash": hashed}):
                fail("operational_driver_bank_existing_native_statement")
            common = {"user_id": owner, "source": SOURCE, "financial_source": "operational",
                "bank_account_id": bank_account_id, "bank_identity_source": bank["bank_identity_source"],
                "bank_identity_version": 2, "started_at": state["started_at"]}
            rows, identities = [], set()
            for fact in parsed["rows"]:
                identity = movement_identity(owner, bank_account_id, file_id, fact)
                if identity in identities or await scoped[MOVEMENTS].find_one({"user_id": owner, "_id": identity}):
                    fail("operational_driver_bank_reference_conflict")
                identities.add(identity)
                row = {**common, "_id": identity, "id": identity, "file_id": file_id, "file_hash": hashed,
                    "row_no": fact["row_no"], "row_hash": fact["row_hash"], "movement_date": fact["date"],
                    "amount": fact["amount"], "currency": "SAR", "direction": fact["direction"],
                    "reference": fact["reference"], "description": fact["description"],
                    "explicit_provider": fact["explicit_provider"], "suggested_provider": fact["suggested_provider"],
                    "status": "unclassified" if fact["direction"] == "in" and not fact["explicit_provider"]
                        and not fact["suggested_provider"] else "not_courier_payment"}
                row["seal"] = seal(row)
                rows.append(row)
            file = {**common, "_id": file_id, "id": file_id, "file_hash": hashed,
                "filename": str(filename or "statement.xlsx").replace("\\", "/").split("/")[-1][:180],
                "content": content, "row_count": len(rows), "movement_ids": [row["id"] for row in rows],
                "uploaded_by": actor, "uploaded_at": stamp, "session_scope": expected_session_scope,
                "assignment_id": assignment_id}
            file["seal"] = seal(file)
            await scoped[FILES].insert_one(file)
            for row in rows:
                await scoped[MOVEMENTS].insert_one(row)
            result = {"status": "imported", "file": public_file(file), "items": [public_movement(row) for row in rows],
                      "financial_source": "operational", "source": SOURCE}
        await scoped[IMPORTS].insert_one({"_id": request_key, "id": request_key, "user_id": owner,
            "request_id": request_id, "fingerprint": fingerprint, "file_id": file_id, "actor_id": actor, "created_at": stamp})
        return result

    return await transact(db, owner, save, lambda scoped: authorize_import(scoped, owner, actor))


async def candidates(db, *, owner, actor, assignment_id, clock=None, authorize_bank_movements=None):
    if authorize_bank_movements is None:
        state = await _context(db, owner, actor)
        review, bank = await review_bank(db, owner, assignment_id)
    else:
        # Only the workspace's server-owned callback can enter this read path.
        # Bind it to the actual saved bank before querying any statement rows.
        review, bank = await review_bank(db, owner, assignment_id)
        async def authorize_read(database):
            await authorize_bank_movements(database, bank['id'])
        state = await _context(db, owner, actor, authorize_read=authorize_read)
    from operational_driver_payment_review import exact_money
    value = exact_money(review.get("amount"))
    rows = await db[MOVEMENTS].find({"user_id": owner, "bank_account_id": bank["id"],
        "started_at": state["started_at"], "status": "unclassified", "direction": "in", "amount": value,
        "currency": "SAR"}).sort("movement_date", -1).limit(201).to_list(201)
    # Canonical proofs imported before this operational adapter remain usable
    # through their original validator. They are never copied into the new store.
    if bank['bank_identity_source'] == 'mz2_financial_accounts':
        native = await db.mz2_daily_movements.find({'user_id': owner, 'bank_account_id': bank['id'],
            'status': 'unclassified', 'direction': 'in', 'amount': value, 'currency': 'SAR',
            'source': {'$in': ['bank_statement_import', 'manual_reconciled_bank_statement']}}).limit(201).to_list(201)
        rows.extend(row for row in native if row.get('file_id') and row.get('file_hash')
                    and not any(row.get(key) for key in ('receipt_id', 'accounting_event_id', 'operational_event_id',
                                                         'explicit_provider', 'suggested_provider', 'confirmed_provider')))
    if len(rows) > 200:
        fail("operational_driver_bank_candidate_scope_too_large")
    lower = instant(state["started_at"]).astimezone(RIYADH).date()
    upper = instant(clock or now()).astimezone(RIYADH).date()
    items = []
    for row in rows:
        try:
            day = date.fromisoformat(row["movement_date"])
        except (ValueError, KeyError, TypeError):
            continue
        if lower < day <= upper and (row.get('source') != SOURCE or row.get("seal") == seal(row)):
            items.append(public_movement(row))
    if authorize_bank_movements is not None:
        await authorize_read(db)
    return {"items": items, "total": len(items), "source": SOURCE, "financial_source": "operational",
            "bank_account_id": bank["id"], "started_at": state["started_at"], "session_scope": digest([owner, actor])}


async def verified_movement(db, *, owner, bank_id, movement_id, value, state, stamp):
    bank = await find_collection_bank(db, owner, bank_id)
    if bank is None or bank["bank_financial_source"] != "operational":
        fail("driver_payment_bank_context_changed")
    movement = await _one(db, MOVEMENTS, {"user_id": owner, "_id": movement_id}, "operational_driver_bank_evidence_required")
    if (movement.get("seal") != seal(movement) or movement.get("status") != "unclassified"
            or movement.get("bank_account_id") != bank_id or movement.get("currency") != "SAR"
            or movement.get("source") != SOURCE or movement.get("financial_source") != "operational"
            or movement.get("started_at") != state["started_at"] or movement.get("direction") != "in"
            or movement.get("bank_identity_source") != bank["bank_identity_source"]
            or any(movement.get(key) for key in ("operational_event_id", "explicit_provider", "suggested_provider"))):
        fail("operational_driver_bank_evidence_required")
    file = await _one(db, FILES, {"user_id": owner, "_id": movement.get("file_id")}, "operational_driver_bank_evidence_required")
    content = bytes(file.get("content") or b"")
    hashed = sha256(content).hexdigest()
    if (file.get("seal") != seal(file) or not content or hashed != file.get("file_hash") or hashed != movement.get("file_hash")
            or file.get("id") != FILE_PREFIX + digest([owner, hashed]) or file.get("source") != SOURCE
            or file.get("bank_account_id") != bank_id or file.get("started_at") != state["started_at"]
            or file.get("bank_identity_source") != bank["bank_identity_source"]
            or movement_id not in file.get("movement_ids", [])):
        fail("operational_driver_bank_evidence_required")
    try:
        facts = [fact for fact in parse_original(content)["rows"] if fact["row_no"] == movement.get("row_no")]
        if len(facts) != 1:
            fail("operational_driver_bank_evidence_required")
        fact = facts[0]
        from operational_driver_payment_review import exact_money
        if (movement.get("_id") != movement_id or movement_id != movement_identity(owner, bank_id, file["id"], fact)
                or exact_money(fact["amount"]) != value or exact_money(movement["amount"]) != value
                or fact["direction"] != "in" or fact["date"] != movement.get("movement_date")
                or fact["reference"] != movement.get("reference") or fact["row_hash"] != movement.get("row_hash")
                or fact["explicit_provider"] or fact["suggested_provider"]):
            fail("operational_driver_bank_evidence_required")
        day = date.fromisoformat(fact["date"])
        if not instant(state["started_at"]).astimezone(RIYADH).date() < day <= instant(stamp).astimezone(RIYADH).date():
            fail("operational_cutover_reconciliation_required")
    except (ValueError, KeyError, TypeError, ArithmeticError):
        fail("operational_driver_bank_evidence_required")
    return bank, movement, {"source_namespace": MOVEMENTS, "source_record_id": movement_id,
        "source_revision": digest([owner, bank_id, hashed, fact, state["started_at"]]),
        "statement_row_fingerprint": digest([owner, hashed, fact["row_no"], fact["row_hash"]]),
        **await bank_fact_proof(db, owner=owner, bank_id=bank_id, source_namespace=MOVEMENTS,
                               movement=movement, fact=fact)}
