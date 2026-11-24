"""Atomic native observation batches; incomplete source prefixes are never evidence."""
from copy import deepcopy
from hashlib import sha256
import json


FIELD = "native_transaction_batch"
PENDING_FIELD = "salla_provider_capture_pending"
PENDING_CODE = "provider_capture_acquisition_pending"
SCHEMA = "provider_native_capture_batch_v1"
MAX_ROWS = 300
MAX_BYTES = 262144


def _encoded(value):
    # Stop serialization at the source byte limit instead of allocating an
    # arbitrarily large encoded document before checking its size.
    from operational_demand_refresh import calculation_checkpoint
    result = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for index, part in enumerate(encoder.iterencode(value)):
        if index % 128 == 0:
            calculation_checkpoint()
        if len(part) > MAX_BYTES - len(result):
            raise ValueError("provider_native_batch_limit")
        chunk = part.encode()
        if len(chunk) > MAX_BYTES - len(result):
            raise ValueError("provider_native_batch_limit")
        result.extend(chunk)
    return bytes(result)


def build_batch(rows, *, owner, order_number, provider_order_id):
    """Create one append-only source document for the entire completed API read."""
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_ROWS:
        raise ValueError("provider_native_batch_limit")
    records = []
    for value in rows:
        payment = deepcopy(value)
        evidence = payment.pop("native_transaction_evidence")
        if not isinstance(evidence, dict):
            raise ValueError("provider_native_batch_invalid")
        payment.update(user_id=owner, source="salla_transactions", order_number=order_number,
            provider_order_id=provider_order_id, native_transaction_evidence=[evidence])
        records.append(payment)
    records.sort(key=lambda row: row["fingerprint"])
    batch = {"schema": SCHEMA, "complete": True, "count": len(records), "owner_id": owner,
        "order_number": order_number, "provider_order_id": provider_order_id, "rows": records}
    encoded = _encoded(batch)
    if len(encoded) > MAX_BYTES:
        raise ValueError("provider_native_batch_limit")
    digest = sha256(encoded).hexdigest()
    return {"user_id": owner, "source": "salla_transactions", "order_number": order_number,
        "provider_order_id": provider_order_id, "transaction_id": "batch:" + digest,
        "fingerprint": "provider-native-batch:" + digest, FIELD: {**batch, "digest": digest}}


def expand_batches(rows, *, max_rows=20000):
    """Expand only a complete, scoped, digest-bound batch; otherwise block its order.

    Individual legacy source observations keep their existing validation path.
    A malformed batch becomes an invalid observation in that same owner's
    order scope, so an earlier cached positive capture cannot hide the failure.
    """
    expanded = []
    from operational_demand_refresh import calculation_checkpoint
    for index, row in enumerate(rows or ()):
        if index % 64 == 0:
            calculation_checkpoint()
        if not isinstance(row, dict) or FIELD not in row:
            if len(expanded) >= max_rows:
                raise ValueError("operational_source_scope_too_large")
            expanded.append(row)
            continue
        batch = row[FIELD]
        valid = False
        try:
            records = batch["rows"]
            if (not isinstance(batch, dict) or type(batch.get("count")) is not int
                    or not isinstance(records, list) or not 0 < len(records) == batch["count"] <= MAX_ROWS):
                raise ValueError("provider_native_batch_limit")
            body = {key: value for key, value in batch.items() if key != "digest"}
            encoded = _encoded(body)
            digest = sha256(encoded).hexdigest()
            valid = (batch.get("schema") == SCHEMA and batch.get("complete") is True
                and type(batch.get("count")) is int and isinstance(records, list)
                and 0 < len(records) == batch["count"] <= MAX_ROWS and len(encoded) <= MAX_BYTES
                and batch.get("digest") == digest and row.get("fingerprint") == "provider-native-batch:" + digest
                and row.get("transaction_id") == "batch:" + digest and row.get("source") == "salla_transactions"
                and (batch.get("owner_id"), batch.get("order_number"), batch.get("provider_order_id"))
                == (row.get("user_id"), row.get("order_number"), row.get("provider_order_id"))
                and all(isinstance(value, dict) and FIELD not in value
                    and (value.get("user_id"), value.get("order_number"), value.get("provider_order_id"), value.get("source"))
                    == (row.get("user_id"), row.get("order_number"), row.get("provider_order_id"), "salla_transactions")
                    and isinstance(value.get("transaction_id"), str) and bool(value["transaction_id"])
                    and isinstance(value.get("native_transaction_evidence"), list)
                    and 0 < len(value["native_transaction_evidence"]) <= 16
                    for value in records))
        except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError):
            valid = False
        if valid:
            if len(expanded) + len(records) > max_rows:
                raise ValueError("operational_source_scope_too_large")
            expanded.extend(records)
        else:
            if len(expanded) >= max_rows:
                raise ValueError("operational_source_scope_too_large")
            expanded.append({key: row.get(key) for key in ("user_id", "order_number", "provider_order_id", "source")}
                | {"native_transaction_evidence": []})
    return expanded
