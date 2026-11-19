"""Read-only supplier invoice actuals for the selected sales report cohort.

This is deliberately separate from immutable order cost capture. A finalized
receipt proves a physical unit, and the approved invoice prices that unit's
product and services. Internal materials remain separately evidenced costs.
"""
from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
import sys
import unicodedata

from order_cost_authority import decimal, economic_item, item_dict, line_aliases, line_fingerprint, line_id, order_items

PIECES = "mezan_preparation_pieces_v1"
EVENTS = "mezan_supplier_receiving_events_v1"
INVOICES = "mezan_supplier_invoices_v2"
SUPPLIERS = "mezan_suppliers_v2"
PRODUCTS = "mezan_products_v2"
CHUNK_SIZE = 250
MAX_ROWS = 50000
MAX_COST_UNITS = 10000
INVOICE_MEMO_MAX_ENTRIES = 64
INVOICE_MEMO_MAX_BYTES = 8 * 1024 * 1024
PIECE_FIELDS = {"_id": 0, **dict.fromkeys(("id", "piece_id", "order_number", "order_item_id", "unit_index", "product_id",
    "variant_id", "salla_variant_id", "experiment_mode", "experiment_run_id", "services", "service_plan_status",
    "remaining_service_count", "supplier_dispatch_status", "received_at", "supplier_receiving_history.invoice_id",
    "service_specifications_snapshot", "options_raw", "selected_options", "options", "custom_fields"), 1)}
EVENT_FIELDS = {"_id": 0, **dict.fromkeys(("id", "piece_id", "order_number", "order_item_id", "unit_index", "product_id",
    "variant_id", "salla_variant_id", "event_type", "session_id", "supplier_invoice_id", "finalized_at",
    "experiment_mode", "experiment_run_id", "financial_invoice_created", "liability_created", "supplier_context.id",
    "service_specifications_snapshot", "options_raw", "selected_options", "options", "custom_fields", "services.condition"), 1)}
INVOICE_FIELDS = {"_id": 0, **dict.fromkeys(("id", "session_id", "supplier_id", "supplier_snapshot.id", "status", "approved_at",
    "currency", "lines", "subtotal_halalas", "total_halalas", "piece_count", "line_count", "purchase_tax",
    "tax", "vat", "tax_halalas", "vat_halalas", "tax_amount", "vat_amount", "experiment_mode", "experiment_run_id",
    "financial_invoice_created", "liability_created", "financial_integrity_verified", "reversed_at", "reversal_id",
    "cancelled_at", "voided_at", "mz2_txn_group_id", "mz2_financial_contract", "mz2_effective_at",
    "mz2_close_payload_hash", "mz2_debit_mapping_snapshots"), 1)}


def _text(value):
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _journal_id(value):
    """Exclude malformed stored IDs from the API query, never from validation."""
    identity = value.strip() if isinstance(value, str) else ""
    return identity if identity and len(identity) <= 240 and "\x00" not in identity else ""


def _now():
    return datetime.now(timezone.utc)


def _instant(value):
    if isinstance(value, datetime):
        result = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("supplier_receipt_time_missing")
    return result.astimezone(timezone.utc)


def _number(value):
    result = None if isinstance(value, bool) else decimal(value)
    if result is None:
        raise ValueError("supplier_invoice_amount_invalid")
    return result


def _unit(value):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
        return None
    result = int(value)
    return result if 0 < result <= 999999999 else None


def _money(value):
    result = _number(value)
    if result != result.to_integral_value():
        raise ValueError("supplier_invoice_halalas_invalid")
    return result


def _amount(value):
    return float(value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _same_invoice_value(left, right):
    """Type-sensitive equality, including values whose JSON digest differs.

    Plain dict equality considers True, 1 and 1.0 interchangeable. Reusing a
    validated numeric amount for a later boolean would bypass its validation.
    Unknown BSON/application value types simply miss this optional memo.
    """
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return (left.keys() == right.keys()
                and all(_same_invoice_value(value, right[key]) for key, value in left.items()))
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_invoice_value(a, b) for a, b in zip(left, right))
    if isinstance(left, datetime):
        return left.isoformat() == right.isoformat()
    if isinstance(left, Decimal):
        return left.as_tuple() == right.as_tuple()
    if isinstance(left, float):
        return left.hex() == right.hex()
    return type(left) in (str, int, bool, bytes, type(None)) and left == right


def _retained_size(value, limit):
    """Bound retained pure-work objects, stopping early for a large invoice."""
    pending, seen, total = [value], set(), 0
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        total += sys.getsizeof(current)
        if total > limit:
            return total
        if isinstance(current, dict):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, (list, tuple, set, frozenset)):
            pending.extend(current)
    return total


class _InvoiceCalculationMemo:
    """Request-local LRU of pure work, never accepted financial authority.

    Every batch still reads current complete invoices, suppliers and journal /
    reversal evidence. Only identical complete projected content can reuse the
    allocation and digest. Entries and retained bytes are bounded; eviction or
    an oversized invoice only causes recomputation, never a missing cost.
    """
    def __init__(self, *, enabled=True):
        self.enabled = enabled
        self.entries = OrderedDict()
        self.retained_bytes = 0

    def get(self, invoice):
        key = _text(invoice.get("id"))
        cached = self.entries.get(key)
        if cached is None:
            return None
        entry, size = cached
        if not _same_invoice_value(entry["invoice"], invoice):
            self.entries.pop(key)
            self.retained_bytes -= size
            return None
        self.entries.move_to_end(key)
        return entry

    def remember(self, invoice, amounts, digest):
        pieces = {pid: (position, line) for position, (pid, line) in enumerate(
            (pid, line) for line in invoice["lines"] for pid in line["piece_ids"])}
        entry = {"invoice": invoice, "amounts": amounts, "digest": digest, "pieces": pieces}
        key = _text(invoice.get("id"))
        if not self.enabled or not key or INVOICE_MEMO_MAX_ENTRIES <= 0 or INVOICE_MEMO_MAX_BYTES <= 0:
            return entry
        # Include conservative per-entry LRU/key bookkeeping. Shared immutable
        # objects in different entries are deliberately counted again.
        size = _retained_size(entry, INVOICE_MEMO_MAX_BYTES) + sys.getsizeof(key) + 256
        if size > INVOICE_MEMO_MAX_BYTES:
            return entry
        previous = self.entries.pop(key, None)
        if previous:
            self.retained_bytes -= previous[1]
        while self.entries and (len(self.entries) >= INVOICE_MEMO_MAX_ENTRIES
                or self.retained_bytes + size > INVOICE_MEMO_MAX_BYTES):
            _, (_, removed_size) = self.entries.popitem(last=False)
            self.retained_bytes -= removed_size
        self.entries[key] = (entry, size)
        self.retained_bytes += size
        return entry


async def _read(db, collection, query, *, projection=None):
    rows = await db[collection].find(query, projection or {"_id": 0}).to_list(length=MAX_ROWS + 1)
    if len(rows) > MAX_ROWS:
        raise ValueError("supplier_invoice_reporting_read_limit:" + collection)
    return rows


async def _checkpoint(budget, index):
    if budget is not None and index % 128 == 0:
        await budget.checkpoint()


def _label(value):
    return " ".join(unicodedata.normalize("NFKC", _text(value)).casefold().split()).rstrip(":：").strip()


def _choices(rows):
    ids, names, names_by_id = defaultdict(set), defaultdict(set), defaultdict(set)
    contexts = defaultdict(set)
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = raw.get("raw") if isinstance(raw.get("raw"), dict) else raw
        nested = row.get("option") if isinstance(row.get("option"), dict) else {}
        oid = _text(row.get("option_id") or row.get("product_option_id") or nested.get("id"))
        field = row.get("field") if isinstance(row.get("field"), dict) else {}
        fid = _text(row.get("field_id") or field.get("id"))
        name = _label(row.get("option_name") or row.get("field_name") or row.get("name") or nested.get("name"))
        values = row.get("values") if "values" in row else row.get("value", row.get("value_name", row.get("text", row.get("answer"))))
        for value in values if isinstance(values, list) else [values]:
            data = value if isinstance(value, dict) else {}
            vid = _text(row.get("value_id") or data.get("value_id") or data.get("option_value_id"))
            label = _label(data.get("name") or data.get("label") or data.get("value") or data.get("text")) if data else _label(value)
            if oid and vid:
                ids[oid].add(vid)
                if name:
                    names_by_id[oid].add(name)
            if name and label:
                names[name].add(label)
            if not vid and (oid or fid):
                text = data.get("name") or data.get("label") or data.get("value") or data.get("text") if data else value
                # Preserve typed value case for a stable input identity. Display
                # renames of a stable selected value are handled by IDs above.
                context = " ".join(unicodedata.normalize("NFKC", _text(text)).split())
                if context or any(key in row for key in ("value", "value_name", "values", "text", "answer")):
                    contexts[("option:" + oid) if oid else "field:" + fid].add(context)
    return ids, names, names_by_id, contexts


def _selection_conflict(piece, item):
    """Compare only saved original choices; supplier PDF wording is editable."""
    original = [*(piece.get("options_raw") or piece.get("selected_options") or piece.get("options") or []),
        *(piece.get("custom_fields") or []),
        *(piece.get("service_specifications_snapshot") or []),
        *(row.get("condition") for row in piece.get("services") or [] if isinstance(row.get("condition"), dict)
          and _text(row["condition"].get("value_id")) not in {"", "filled", "__option__"})]
    current = economic_item(item)
    current_rows = [*(current.get("options_raw") or []), *(current.get("custom_fields") or [])]
    current_rows.extend({"name": name, "value": value} for name, value in (current.get("options_normalized") or {}).items())
    saved_ids, saved_names, saved_id_names, saved_contexts = _choices(original)
    current_ids, current_names, current_id_names, current_contexts = _choices(current_rows)
    shared = saved_ids.keys() & current_ids.keys()
    if any(saved_ids[oid] != current_ids[oid] for oid in shared):
        return True
    if any(saved_contexts[key] != current_contexts[key] for key in saved_contexts.keys() & current_contexts.keys()):
        return True
    # A stable value ID permits a display-label rename. Do not let old names
    # overrule that proof, and never treat checkout row IDs as option IDs.
    proven_names = set().union(*(saved_id_names[oid] | current_id_names[oid] for oid in shared)) if shared else set()
    return any(saved_names[name] != current_names[name] for name in saved_names.keys() & current_names.keys() - proven_names)


def _aliases(item, number):
    result = set(line_aliases(item))
    prefix = "salla:" + number + ":"
    for alias in list(result):
        if alias.startswith(prefix):
            result.add(alias[len(prefix):])
        elif ":" not in alias:
            result.add(prefix + alias)
    return result


def _product_ids(row):
    return {_text(row.get(key)) for key in ("id", "mezan_product_id", "salla_product_id") if row.get(key) not in (None, "")}


def _invoice_amounts(invoice):
    """Validate the complete document before allocating a subset of its pieces."""
    lines = invoice.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("supplier_invoice_lines_missing")
    seen, subtotal = set(), Decimal(0)
    for row in lines:
        ids = row.get("piece_ids")
        if (not isinstance(ids, list) or not ids or any(not isinstance(pid, str) or not pid for pid in ids)
                or len(set(ids)) != len(ids) or seen.intersection(ids)
                or row.get("product_price_authority") != "mezan_v2"
                or _number(row.get("quantity")) != len(ids)):
            raise ValueError("supplier_invoice_piece_coverage_invalid")
        seen.update(ids)
        if len(seen) > MAX_COST_UNITS:
            raise ValueError("supplier_invoice_piece_limit")
        product = _money(row.get("product_unit_price_halalas")) * len(ids)
        if row.get("product_charge_eligible") is False and product:
            raise ValueError("supplier_invoice_product_charge_invalid")
        if _money(row.get("product_total_halalas")) != product:
            raise ValueError("supplier_invoice_product_total_invalid")
        services, service_total = set(), Decimal(0)
        for service in row.get("services") or []:
            identity = _text(service.get("service_id"))
            quantity = _number(service.get("quantity_per_piece"))
            if not identity or identity in services or quantity <= 0:
                raise ValueError("supplier_invoice_service_coverage_invalid")
            services.add(identity)
            total = (_money(service.get("unit_price_halalas")) * quantity * len(ids)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
            if _money(service.get("total_halalas")) != total:
                raise ValueError("supplier_invoice_service_total_invalid")
            service_total += total
        if row.get("services_total_halalas") is not None and _money(row["services_total_halalas"]) != service_total:
            raise ValueError("supplier_invoice_service_total_invalid")
        if _money(row.get("total_halalas")) != product + service_total:
            raise ValueError("supplier_invoice_line_total_invalid")
        subtotal += product + service_total
    if _money(invoice.get("subtotal_halalas")) != subtotal:
        raise ValueError("supplier_invoice_subtotal_invalid")
    if subtotal <= 0:
        raise ValueError("supplier_invoice_total_invalid")
    if invoice.get("piece_count") is not None and _number(invoice["piece_count"]) != len(seen):
        raise ValueError("supplier_invoice_piece_count_invalid")
    if invoice.get("line_count") is not None and _number(invoice["line_count"]) != len(lines):
        raise ValueError("supplier_invoice_line_count_invalid")
    # This existing pure allocator validates the native INPUT_VAT contract and
    # distributes fractional service quantities across all invoice pieces.
    from operational_balance_sources import invoice_component_amounts
    amounts = invoice_component_amounts(invoice)
    _money(invoice.get("total_halalas"))
    return amounts


async def load_supplier_invoice_reporting_costs(db: Any, user_id: str, orders: list[dict], *, budget=None) -> dict:
    """Batch only this tenant's selected order identities; never write or sync."""
    result = {"lines": {}, "read_batches": 0, "as_of": _now()}
    now = result["as_of"]
    orders_by_number = defaultdict(list)
    for order in orders:
        if order.get("order_number"):
            orders_by_number[_text(order["order_number"])].append(order)
    numbers = sorted(orders_by_number)
    invoice_memo = _InvoiceCalculationMemo(enabled=len(numbers) > CHUNK_SIZE)
    for offset in range(0, len(numbers), CHUNK_SIZE):
        selected = numbers[offset:offset + CHUNK_SIZE]
        items, aliases = {}, defaultdict(set)
        for number in selected:
            for order in orders_by_number[number]:
                for item in order_items(order):
                    key = (number, line_id(item))
                    if key in items:
                        result["lines"][key]["issues"].append("supplier_receipt_line_identity_ambiguous")
                        continue
                    items[key] = item
                    result["lines"][key] = {"line_fingerprint": line_fingerprint(item), "units": {},
                        "issues": ["supplier_receipt_order_identity_ambiguous"] if len(orders_by_number[number]) > 1 else []}
                    for alias in _aliases(item, number):
                        aliases[(number, alias)].add(key)

        def record_for(row):
            candidates = aliases.get((_text(row.get("order_number")), _text(row.get("order_item_id"))), set())
            if len(candidates) != 1:
                for key in candidates:
                    result["lines"][key]["issues"].append("supplier_receipt_line_identity_ambiguous")
                return None
            key = next(iter(candidates))
            unit = _unit(row.get("unit_index"))
            quantity = decimal(items[key].get("quantity", 1))
            if unit is None or quantity is None or quantity != quantity.to_integral_value() or unit > quantity:
                result["lines"][key]["issues"].append("supplier_receipt_quantity_mismatch")
                return None
            record = result["lines"][key]["units"].setdefault(unit, {"product": None, "services": {}, "issues": [], "scope_complete": False})
            return key, unit, record

        scope = {"user_id": user_id, "order_number": {"$in": selected}}
        pieces = [row for row in await _read(db, PIECES, scope, projection=PIECE_FIELDS) if not row.get("experiment_mode") and not row.get("experiment_run_id")]
        events = await _read(db, EVENTS, {**scope, "event_type": "supplier_piece_service_recorded"}, projection=EVENT_FIELDS)
        piece_groups = defaultdict(list)
        for index, piece in enumerate(pieces):
            await _checkpoint(budget, index)
            piece_groups[_text(piece.get("piece_id") or piece.get("id"))].append(piece)
        known_piece_ids = sorted(pid for pid in piece_groups if pid)
        known_piece_id_set = set(known_piece_ids)
        invoice_ids = {_text(event.get("supplier_invoice_id")) for event in events if event.get("supplier_invoice_id")}
        for piece in pieces:
            invoice_ids.update(_text(row.get("invoice_id")) for row in piece.get("supplier_receiving_history") or [] if row.get("invoice_id"))
        predicates = ([{"id": {"$in": sorted(invoice_ids)}}] if invoice_ids else []) + ([{"lines.piece_ids": {"$in": known_piece_ids}}] if known_piece_ids else [])
        invoices = await _read(db, INVOICES, {"user_id": user_id, "$or": predicates}, projection=INVOICE_FIELDS) if predicates else []
        supplier_ids = sorted({_text(row.get("supplier_id")) for row in invoices if row.get("supplier_id")})
        suppliers = await _read(db, SUPPLIERS, {"user_id": user_id, "id": {"$in": supplier_ids}}, projection={"_id": 0, "id": 1}) if supplier_ids else []
        supplier_counts = Counter(_text(row.get("id")) for row in suppliers)
        wanted_products = {_text(item.get(name)) for item in items.values() for name in ("parent_product_id", "product_id", "salla_product_id") if item.get(name)}
        wanted_products.update(_text(row.get("product_id")) for row in [*pieces, *events] if row.get("product_id"))
        catalog = await _read(db, PRODUCTS, {"user_id": user_id, "$or": [{name: {"$in": sorted(wanted_products)}} for name in ("id", "mezan_product_id", "salla_product_id")]},
                              projection={"_id": 0, "id": 1, "mezan_product_id": 1, "salla_product_id": 1}) if wanted_products and (pieces or events) else []
        product_aliases = defaultdict(list)
        for product in catalog:
            identities = _product_ids(product)
            for identity in identities:
                product_aliases[identity].append(identities)

        def product_matches(value, item):
            identity = _text(value)
            direct = {_text(item.get(key)) for key in ("parent_product_id", "product_id", "salla_product_id") if item.get(key)}
            return bool(identity and (identity in direct or len(product_aliases[identity]) == 1 and direct.intersection(product_aliases[identity][0])))

        journal_ids = sorted({identity for row in invoices if (identity := _journal_id(row.get("mz2_txn_group_id")))})
        from accounting_ledger_v2 import read_supplier_invoice_journal_evidence_v2
        journals = await read_supplier_invoice_journal_evidence_v2(db, user_id=user_id, txn_group_ids=journal_ids) if journal_ids else []
        journal_groups = defaultdict(list)
        reversals = set()
        for journal in journals:
            journal_groups[_text(journal.get("txn_group_id"))].append(journal)
            if journal.get("reversal_of_txn_group_id"):
                reversals.add(_text(journal["reversal_of_txn_group_id"]))
        invoice_counts = Counter(_text(row.get("id")) for row in invoices)
        event_counts = Counter(_text(row.get("id")) for row in events)
        event_groups = defaultdict(list)
        for event in events:
            event_groups[(_text(event.get("supplier_invoice_id")), _text(event.get("piece_id")))].append(event)
        claims = defaultdict(list)
        accepted_events = set()
        for invoice in invoices:
            if budget is not None:
                await budget.checkpoint()
            identity = _text(invoice.get("id"))
            cached = invoice_memo.get(invoice)
            related = (cached["pieces"].keys() if cached else
                       {pid for line in invoice.get("lines") or [] for pid in line.get("piece_ids") or []}) & known_piece_id_set

            def problem(reason):
                for pid in related:
                    for piece in piece_groups[pid]:
                        found = record_for(piece)
                        if found:
                            found[2]["issues"].append(reason)

            if invoice.get("experiment_mode") or invoice.get("experiment_run_id"):
                continue
            if not invoice.get("approved_at"):
                if any(event_groups[(identity, pid)] for pid in related):
                    problem("supplier_invoice_approval_missing")
                continue
            try:
                approved = _instant(invoice["approved_at"])
                if approved > now:
                    continue
                if (not identity or invoice_counts[identity] != 1 or invoice.get("status") != "payable_posted"
                        or invoice.get("currency", "SAR") != "SAR" or supplier_counts[_text(invoice.get("supplier_id"))] != 1
                        or any(invoice.get(key) for key in ("reversed_at", "reversal_id", "cancelled_at", "voided_at"))
                        or invoice.get("financial_invoice_created") is False or invoice.get("liability_created") is False
                        or invoice.get("financial_integrity_verified") is False
                        or (invoice.get("supplier_snapshot") or {}).get("id", invoice.get("supplier_id")) != invoice.get("supplier_id")):
                    raise ValueError("supplier_invoice_approval_invalid")
                gid = _journal_id(invoice.get("mz2_txn_group_id"))
                raw_gid = invoice.get("mz2_txn_group_id")
                if (invoice.get("mz2_financial_contract") or raw_gid is not None and raw_gid != "") and not gid:
                    raise ValueError("supplier_invoice_journal_evidence_missing")
                digest = None
                if gid:
                    from supplier_native_invoice_v2 import invoice_digest
                    groups = journal_groups[gid]
                    if gid in reversals or len(groups) != 1 or groups[0].get("status") != "posted":
                        raise ValueError("supplier_invoice_journal_evidence_invalid")
                    digest = cached["digest"] if cached else invoice_digest(invoice)
                    if ((groups[0].get("metadata") or {}).get("invoice_digest") != digest
                            or (groups[0].get("metadata") or {}).get("supplier_invoice_id") != identity):
                        raise ValueError("supplier_invoice_journal_evidence_invalid")
                if cached is None:
                    cached = invoice_memo.remember(invoice, _invoice_amounts(invoice), digest)
                amounts = cached["amounts"]
            except (ValueError, TypeError, KeyError, ArithmeticError):
                problem("supplier_invoice_evidence_invalid")
                continue
            # Keep original invoice piece order, but inspect only this batch's
            # pieces after the complete invoice has been validated/allocated.
            for index, pid in enumerate(sorted(related, key=lambda value: cached["pieces"][value][0])):
                await _checkpoint(budget, index)
                invoice_line = cached["pieces"][pid][1]
                rows, receipts = piece_groups[pid], event_groups[(identity, pid)]
                if len(rows) != 1 or len(receipts) != 1:
                    problem("supplier_receipt_identity_ambiguous" if len(rows) > 1 or len(receipts) > 1 else "supplier_receipt_finalization_missing")
                    continue
                piece, event = rows[0], receipts[0]
                found, event_found = record_for(piece), record_for(event)
                if not found:
                    continue
                key, unit, record = found
                item = items[key]
                try:
                    finalized = _instant(event.get("finalized_at"))
                    if (event_found is None or event_found[:2] != found[:2] or not event.get("id") or event_counts[_text(event["id"])] != 1
                            or event.get("experiment_mode") or event.get("experiment_run_id")
                            or event.get("financial_invoice_created") is not True or event.get("liability_created") is not True
                            or finalized != approved or finalized > now
                            or not event.get("session_id") or event.get("session_id") != invoice.get("session_id")
                            or (event.get("supplier_context") or {}).get("id", invoice.get("supplier_id")) != invoice.get("supplier_id")
                            or not product_matches(piece.get("product_id"), item) or not product_matches(event.get("product_id"), item)
                            or any(invoice_line.get(name) and not product_matches(invoice_line[name], item) for name in ("product_id", "product_v2_id"))
                            or _selection_conflict(piece, item) or _selection_conflict(event, item)
                            or len({_text(row.get("variant_id") or row.get("salla_variant_id")) for row in (item, piece, event, invoice_line)
                                    if row.get("variant_id") or row.get("salla_variant_id")}) > 1):
                        raise ValueError("supplier_receipt_source_mismatch")
                except (ValueError, TypeError):
                    record["issues"].append("supplier_receipt_source_mismatch")
                    continue
                accepted_events.add((identity, pid))
                service_rows = invoice_line.get("services") or []
                product_claim = (invoice_line.get("product_charge_eligible") is True or
                                 invoice_line.get("product_charge_eligible") is not False and
                                 (_number(invoice_line["product_unit_price_halalas"]) > 0 or not service_rows))
                components = [("product", None)] if product_claim else []
                components += [("service:" + _text(service["service_id"]), service) for service in service_rows]
                for component, service in components:
                    claims[(key, unit, component)].append({"amount": amounts[(pid, component)]["net_amount"],
                        "invoice_id": identity, "piece_id": pid, "approved_at": approved,
                        "label": (service or {}).get("service_name") or invoice_line.get("product_name"),
                        "quantity": (service or {}).get("quantity_per_piece")})
        for index, ((key, unit, component), values) in enumerate(claims.items()):
            await _checkpoint(budget, index)
            record = result["lines"][key]["units"][unit]
            if len(values) != 1:
                record["issues"].append("supplier_invoice_component_coverage_ambiguous")
            elif component == "product":
                record["product"] = values[0]
            else:
                record["services"][component[len("service:"):]] = values[0]
        # Completed pieces keep their final service scope after assembly handoff.
        # Catalog additions do not edit these invoiced pieces. A pending/partial
        # scope can never prove that an absent service was unnecessary.
        accepted_piece_ids = {pid for _, pid in accepted_events}
        for index, piece in enumerate(pieces):
            await _checkpoint(budget, index)
            found = record_for(piece)
            if not found:
                continue
            _, _, record = found
            services = piece.get("services")
            pid = _text(piece.get("piece_id") or piece.get("id"))
            accepted_piece = pid in accepted_piece_ids
            try:
                received = _instant(piece.get("received_at")) <= now
            except (ValueError, TypeError):
                received = False
            if not accepted_piece:
                if any(row.get("invoice_id") for row in piece.get("supplier_receiving_history") or []):
                    record["issues"].append("supplier_receipt_invoice_evidence_missing")
                continue
            if isinstance(services, list):
                required_by_id = {_text(row.get("service_id")): row for row in record.get("required_services") or []}
                for service in services:
                    service_id = _text(service.get("service_id"))
                    previous = required_by_id.get(service_id)
                    if previous and decimal(previous.get("required_quantity")) != decimal(service.get("required_quantity")):
                        record["issues"].append("supplier_receipt_service_scope_ambiguous")
                    actual = record["services"].get(service_id)
                    required_quantity = decimal(service.get("required_quantity"))
                    if actual and (required_quantity is None or required_quantity <= 0 or decimal(actual.get("quantity")) != required_quantity):
                        record["issues"].append("supplier_receipt_service_quantity_mismatch:" + service_id)
                    required_by_id[service_id] = deepcopy(service)
                record["required_services"] = list(required_by_id.values())
            if not received:
                if piece.get("supplier_dispatch_status") == "received" and not record["product"]:
                    record["issues"].append("supplier_receipt_product_invoice_missing")
                continue
            if not record["product"]:
                record["issues"].append("supplier_receipt_product_invoice_missing")
            if (not isinstance(services, list) or piece.get("supplier_dispatch_status") != "received"
                    or piece.get("service_plan_status") != "completed" or piece.get("remaining_service_count") != 0):
                continue
            identities = [_text(row.get("service_id")) for row in services]
            complete = len(set(identities)) == len(identities) and all(identities)
            for service in services:
                evidence = record["services"].get(_text(service.get("service_id")))
                required = decimal(service.get("required_quantity"))
                completed = decimal(service.get("completed_quantity"))
                if not evidence:
                    record["issues"].append("supplier_receipt_service_invoice_missing:" + _text(service.get("service_id")))
                elif required is None or required <= 0 or decimal(evidence.get("quantity")) != required:
                    record["issues"].append("supplier_receipt_service_quantity_mismatch:" + _text(service.get("service_id")))
                complete = bool(complete and evidence and service.get("status") == "completed"
                                and required is not None and required > 0 and completed is not None and completed >= required
                                and decimal(evidence.get("quantity")) == required
                                and service.get("supplier_invoice_id") == evidence["invoice_id"])
            record["scope_complete"] = record["scope_complete"] or bool(complete and record["product"] and not record["issues"])
        for key in items:
            line = result["lines"][key]
            for record in line["units"].values():
                if record["issues"] or any(_text(service.get("service_id")) not in record["services"] for service in record.get("required_services") or []):
                    record["scope_complete"] = False
        for index, event in enumerate(events):
            await _checkpoint(budget, index)
            if not event.get("supplier_invoice_id") and not event.get("financial_invoice_created") and not event.get("liability_created"):
                continue
            if (event.get("experiment_mode") or event.get("experiment_run_id")
                    or (_text(event.get("supplier_invoice_id")), _text(event.get("piece_id"))) in accepted_events):
                continue
            try:
                if _instant(event.get("finalized_at")) > now:
                    continue
            except (ValueError, TypeError):
                pass
            found = record_for(event)
            if found:
                found[2]["issues"].append("supplier_receipt_invoice_evidence_missing")
        result["read_batches"] += 1
        if budget is not None:
            await budget.checkpoint()
    return result


def _scope_marker(cost):
    cells = cost.get("cost_detail_cells") or []
    quantity = decimal(cost.get("quantity"))
    total = sum((decimal(cell.get("unit_cost")) or Decimal(0) for cell in cells if cell.get("status") == "costed"), Decimal(0))
    return bool(cost.get("calculation_cost_available") and cells and quantity is not None
                and all(cell.get("status") in {"costed", "not_required", "included_in_base"} for cell in cells)
                and all(decimal(cell.get("unit_cost")) is not None for cell in cells if cell.get("status") == "costed")
                and total * quantity == decimal(cost.get("line_total")))


def _cell_reason(cell):
    key = _text(cell.get("key"))
    if key == "base":
        return "base_cost_or_fx_missing"
    if key.startswith("option:") and cell.get("status") == "unresolved":
        return "option_selection_unresolved:" + key.split(":", 1)[1]
    return "component_cost_or_fx_missing:" + key.split(":", 1)[-1]


def resolve_supplier_invoice_reporting_cost(item: Any, *, order_number: str, catalog_cost: dict,
                                            historical_cost: dict, supplier_authority: dict | None) -> dict:
    """Replace proven invoice components once; estimate only uncovered scope."""
    item = item_dict(item)
    historical = historical_cost or {}
    # Historical/source contradictions and approved bundle corrections keep the
    # established authority. This read cannot repair or reinterpret them.
    if (str(historical.get("cost_authority") or "").startswith(("bundle", "current_bundle"))
            or "correction" in str(historical.get("cost_authority") or "")
            or historical.get("repair_version") or historical.get("cost_authority") == "unresolved_snapshot"
            or any(reason.startswith(("cost_snapshot_", "cost_correction_", "bundle_"))
                   for reason in historical.get("missing_cost_reasons") or [])):
        return deepcopy(historical)
    estimate = deepcopy(catalog_cost)
    estimate["cost_detail_scope_complete"] = _scope_marker(estimate)
    record = ((supplier_authority or {}).get("lines") or {}).get((_text(order_number), line_id(item)))
    if not record or not record.get("issues") and not any(unit.get("product") or unit.get("services") or unit.get("issues") for unit in record.get("units", {}).values()):
        return estimate
    quantity = decimal(item.get("quantity", 1))
    if quantity is None or quantity <= 0 or quantity != quantity.to_integral_value():
        estimate.update(calculation_cost_available=False, base_complete=False, mezan_cost_complete=False,
            mezan_cost_missing=True, cost_detail_scope_complete=False,
            missing_cost_reasons=sorted(set([*estimate.get("missing_cost_reasons", []), "supplier_receipt_quantity_mismatch"])))
        return estimate
    issues = list(record.get("issues") or [])
    if record.get("line_fingerprint") != line_fingerprint(item):
        issues.append("supplier_receipt_source_changed")
    source = historical if historical.get("historical_cost_verified") and historical.get("cost_authority") != "catalog_estimate" else estimate
    historical_cells = source.get("cost_detail_cells")
    missing_internal = not historical_cells and decimal(source.get("components_total")) != 0
    # An explicitly empty frozen component total is evidence, not a reason to
    # add a material configured after this physical unit was received.
    internal = [deepcopy(cell) for cell in historical_cells or [] if cell.get("kind") == "component"]
    entries = []
    indexes = sorted(index for index, unit in record.get("units", {}).items() if unit.get("product") or unit.get("services") or unit.get("issues"))
    if len(indexes) > MAX_COST_UNITS:
        estimate.update(calculation_cost_available=False, base_complete=False, mezan_cost_complete=False,
            mezan_cost_missing=True, cost_detail_scope_complete=False,
            missing_cost_reasons=[*estimate.get("missing_cost_reasons", []), "supplier_invoice_reporting_unit_limit"])
        return estimate
    # Unreceived quantities share the current per-unit estimate; materializing
    # one row per unsupplied unit is unnecessary and could block the event loop.
    residual = quantity - len(indexes)
    residual_entry = None
    if residual:
        entry = deepcopy(estimate)
        for name in ("line_total", "base_total", "components_total", "product_components_total", "selected_options_total"):
            entry[name] = _amount((decimal(estimate.get(name)) or Decimal(0)) * residual / quantity)
        entry["quantity"] = float(residual)
        if issues:
            entry.update(calculation_cost_available=False, base_complete=False, mezan_cost_complete=False,
                cost_detail_scope_complete=False, missing_cost_reasons=sorted(set(entry.get("missing_cost_reasons", []) + issues)))
        residual_entry = entry
    for index in indexes:
        evidence = record.get("units", {}).get(index) or {}
        actual_product, actual_services = evidence.get("product"), evidence.get("services") or {}
        unit_issues = [*issues, *(evidence.get("issues") or [])]
        if not actual_product and not actual_services and not unit_issues:
            entry = deepcopy(estimate)
            for name in ("line_total", "base_total", "components_total", "product_components_total", "selected_options_total"):
                entry[name] = _amount((decimal(estimate.get(name)) or Decimal(0)) / quantity)
            entry["quantity"] = 1
            entries.append(entry)
            continue
        internal_keys = {cell.get("key") for cell in internal}
        cells = [deepcopy(cell) for cell in estimate.get("cost_detail_cells") or [] if cell.get("kind") != "component" and cell.get("key") not in internal_keys]
        cells.extend(deepcopy(internal))
        if missing_internal:
            cells = [cell for cell in cells if cell.get("kind") != "component"]
            cells.append({"key": "supplier:internal-evidence", "kind": "component", "label": "تفصيل تكلفة المكونات الداخلية غير متاح", "status": "unresolved", "unit_cost": None})
            unit_issues.append("supplier_invoice_material_evidence_missing")
        if actual_product:
            base = next((cell for cell in cells if cell.get("key") == "base"), None)
            if base is None:
                base = {"key": "base", "kind": "product", "label": actual_product.get("label") or "المنتج"}
                cells.insert(0, base)
            base.update(status="costed", unit_cost=float(decimal(actual_product["amount"])), supplier_invoice_id=actual_product["invoice_id"])
            for cell in cells:
                if _text(cell.get("key")).startswith("option:") and cell.get("kind") == "service" and not cell.get("resource_id"):
                    cell.update(status="included_in_base", unit_cost=None)
        for service_id, service in actual_services.items():
            matches = [cell for cell in cells if _text(cell.get("resource_id")) == service_id]
            if len(matches) == 1:
                target = matches[0]
            else:
                for cell in matches:
                    cell.update(status="included_in_base", unit_cost=None)
                target = {"key": "supplier:service:" + service_id, "label": service.get("label") or service_id, "kind": "service", "resource_id": service_id}
                cells.append(target)
            target.update(kind="service", status="costed", unit_cost=float(decimal(service["amount"])), supplier_invoice_id=service["invoice_id"])
        if actual_product or evidence.get("scope_complete"):
            for cell in cells:
                if cell.get("kind") == "service" and cell.get("resource_id") and _text(cell["resource_id"]) not in actual_services and cell.get("status") != "included_in_base":
                    cell.update(status="not_required", unit_cost=None)
        for service in evidence.get("required_services") or []:
            service_id = _text(service.get("service_id"))
            if not service_id or service_id in actual_services:
                continue
            # Work still planned can create a later invoice. It is not a charge
            # on the product invoice already issued for this unit.
            if actual_product and not service.get("supplier_invoice_id") and service.get("status") != "completed":
                continue
            matches = [cell for cell in cells if cell.get("kind") == "service" and _text(cell.get("resource_id")) == service_id]
            if not matches or all(cell.get("status") in {"not_required", "included_in_base"} for cell in matches):
                cells.append({"key": "supplier:service:" + service_id, "kind": "service", "resource_id": service_id,
                    "label": service.get("service_name") or service_id, "status": "missing_cost", "unit_cost": None})
                unit_issues.append("supplier_receipt_service_cost_missing:" + service_id)
        if not cells:
            cells = [{"key": "supplier:cost-evidence", "kind": "product", "label": "بيانات التكلفة", "status": "unresolved", "unit_cost": None}]
        for cell in cells:
            if cell.get("status") not in {"costed", "not_required", "included_in_base"} or cell.get("status") == "costed" and decimal(cell.get("unit_cost")) is None:
                unit_issues.append(_cell_reason(cell))
        totals = {"base_total": Decimal(0), "product_components_total": Decimal(0), "selected_options_total": Decimal(0)}
        expected = False
        for cell in cells:
            if cell.get("status") == "costed":
                field = "base_total" if cell.get("key") == "base" else "selected_options_total" if _text(cell.get("key")).startswith("option:") else "product_components_total"
                totals[field] += decimal(cell.get("unit_cost")) or Decimal(0)
                expected = expected or not cell.get("supplier_invoice_id")
        complete = not unit_issues
        entry = {"quantity": 1, **{key: _amount(value) for key, value in totals.items()},
            "components_total": _amount(totals["product_components_total"] + totals["selected_options_total"]),
            "line_total": _amount(sum(totals.values())), "cost_detail_cells": cells,
            "calculation_cost_available": complete, "base_complete": complete,
            "uses_salla_fallback": bool(not actual_product and estimate.get("uses_salla_fallback")),
            "mezan_cost_complete": complete and bool(actual_product or estimate.get("mezan_cost_complete")),
            "cost_stage": "mixed" if expected or not complete else "actual",
            "missing_cost_reasons": sorted(set(unit_issues)),
            "historical_cost_verified": complete and not expected}
        entry["cost_detail_scope_complete"] = _scope_marker(entry)
        entries.append(entry)
    if residual_entry is not None:
        entries.append(residual_entry)
    complete = all(entry["calculation_cost_available"] for entry in entries)
    actual = complete and all(entry["cost_stage"] == "actual" for entry in entries)
    result = deepcopy(estimate)
    for name in ("line_total", "base_total", "components_total", "product_components_total", "selected_options_total"):
        result[name] = _amount(sum((decimal(entry.get(name)) or Decimal(0) for entry in entries), Decimal(0)))
    result.update(quantity=float(quantity), calculation_cost_available=complete, base_complete=complete,
        mezan_cost_complete=complete and all(entry.get("mezan_cost_complete") for entry in entries),
        mezan_cost_missing=not (complete and all(entry.get("mezan_cost_complete") for entry in entries)),
        uses_salla_fallback=any(entry.get("uses_salla_fallback") for entry in entries),
        cost_authority="supplier_invoice" if actual else "supplier_invoice_mixed",
        cost_stage="actual" if actual else "mixed", base_cost_source="supplier_invoice", calculation_cost_source="supplier_invoice",
        historical_cost_verified=actual, cost_detail_units=entries,
        missing_cost_reasons=sorted({reason for entry in entries for reason in entry.get("missing_cost_reasons") or []}),
        applied_product_resources=[], applied_option_costs=[], cost_detail_scope_complete=all(entry.get("cost_detail_scope_complete") for entry in entries))
    # Consumers that support slices use exact units; an aggregate cell set must
    # not present one unit's actual cost as an average for all the others.
    result["cost_detail_cells"] = entries[0]["cost_detail_cells"] if quantity == 1 else []
    return result
