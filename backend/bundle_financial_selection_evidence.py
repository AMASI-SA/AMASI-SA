"""Provable label-to-ID recovery for a bundle's *financial* evidence only.

Salla order-item options sometimes contain a real option_id and the selected
value's exact label but no value_id. G47 inventory/service execution must keep
using the unmodified provider tokens. This resolver can freeze an *estimate*
only after a unique match to the option's catalog value IDs. It is not a general
catalog repair, cannot manufacture a stock selection, and makes no DB writes.
"""
from __future__ import annotations

from hashlib import sha256
import json
import unicodedata

from fastapi import HTTPException

VERSION = "bundle-financial-provider-label-v1"


def _clean(value):
    if not isinstance(value, str):
        return None
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold() or None


def _identity(value):
    return str(value) if value not in (None, "") and not isinstance(value, (dict, list, bool)) else None


def _error():
    # No customer names / customization / provider labels in error responses.
    raise HTTPException(409, detail={"code": "bundle_financial_source_choice_unverified"})


def _raw(row):
    return (row.get("raw") if isinstance(row.get("raw"), dict) else row)


def _option_id(row):
    option = row.get("option") if isinstance(row.get("option"), dict) else {}
    return _identity(row.get("option_id") or row.get("id") or option.get("id"))


def _option_name(row):
    option = row.get("option") if isinstance(row.get("option"), dict) else {}
    return row.get("option_name") or row.get("name") or option.get("name")


def _selected_values(row):
    if "values" in row:
        value = row["values"]
    else:
        value = row.get("value", row.get("value_name", row.get("selected")))
    if value is None or value == "" or value == []:
        return []
    return value if isinstance(value, list) else [value]


def _label(value):
    if isinstance(value, dict):
        for key in ("name", "label", "text", "option_value", "value"):
            candidate = value.get(key)
            if isinstance(candidate, str) and _clean(candidate):
                return candidate
        return None
    return value if isinstance(value, str) and _clean(value) else None


def _hash(payload):
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")).hexdigest()


def verify_selection_recovery(snapshot, record=None):
    """Verify the frozen proof without consulting today's mutable catalog."""
    if not isinstance(snapshot, dict):
        return False
    proof = snapshot.get("financial_selection_recovery")
    if not isinstance(proof, dict) or proof.get("version") != VERSION:
        return False
    if set(proof) != {"version", "source_line_fingerprint", "resolved_pairs", "evidence_hash"}:
        return False
    fingerprint = snapshot.get("line_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint or proof["source_line_fingerprint"] != fingerprint:
        return False
    if record is not None and (record.get("bundle_financial") is not True
            or record.get("line_fingerprint") != fingerprint
            or record.get("selected_options") != snapshot.get("selected_options")):
        return False
    pairs = proof.get("resolved_pairs")
    if not isinstance(pairs, list) or not pairs or any(not isinstance(p, dict)
            or set(p) != {"option_id", "value_id"} or not p["option_id"] or not p["value_id"]
            or not isinstance(p["option_id"], str) or not isinstance(p["value_id"], str)
            for p in pairs):
        return False
    selected = snapshot.get("selected_options")
    if not isinstance(selected, list):
        return False
    selected_pairs = {(str(p.get("option_id")), str(p.get("value_id")))
                      for p in selected if isinstance(p, dict)}
    if len(selected_pairs) != len(selected) or not all(
            (p["option_id"], p["value_id"]) in selected_pairs for p in pairs):
        return False
    return proof.get("evidence_hash") == _hash({k: v for k, v in proof.items() if k != "evidence_hash"})


def recover_financial_choices(line, product, original, fingerprint):
    """Return original IDs plus uniquely provable provider-name choices.

    Only existing explicit provider option IDs may be used. Ignore text-only
    personalization fields; do not expose their values or add physical demand.
    Reject ambiguous labels, mismatched provider IDs, conflicting IDs and
    non-catalog choices. Caller must store any recovery as financial-only.
    """
    options = product.get("options") if isinstance(product, dict) else None
    if not isinstance(options, list):
        _error()
    catalog = {}
    for opt in options:
        if not isinstance(opt, dict) or _identity(opt.get("id")) is None:
            _error()
        oid = _identity(opt["id"])
        if oid in catalog:
            _error()
        catalog[oid] = opt
    pairs = {(str(p["option_id"]), str(p["value_id"])) for p in original}
    if len(pairs) != len(original):
        _error()
    recovered = set()
    rows = line.get("options_raw") or line.get("selected_options") or line.get("options") or []
    if not isinstance(rows, list):
        _error()
    for source in rows:
        if not isinstance(source, dict):
            _error()
        row = _raw(source)
        values = _selected_values(row)
        if not values:
            continue
        # Preserve the preexisting ID-authoritative contract. Salla sometimes
        # translates labels after checkout; their names cannot veto a *real*
        # ID already recorded by selection_tokens. The opt-in cost resolver
        # touches only rows whose chosen value IDs are actually absent.
        missing_ids = any(
            not (_identity(row.get("value_id"))
                 or (_identity(value.get("id")) if isinstance(value, dict) else None))
            for value in values)
        if not missing_ids:
            continue
        oid = _option_id(row)
        opt = catalog.get(oid)
        if oid is None or opt is None:
            # An unrelated/unknown provider option is not pricing evidence.
            _error()
        provider_name = _clean(_option_name(row))
        if provider_name is not None and provider_name != _clean(opt.get("name")):
            _error()
        available = opt.get("values") or []
        if not isinstance(available, list):
            _error()
        if not available:
            continue  # Text/personalization is handled by existing option_answers.
        options_by_label = {}
        catalog_ids = set()
        for available_value in available:
            if not isinstance(available_value, dict):
                _error()
            vid = _identity(available_value.get("id"))
            label = _clean(available_value.get("name"))
            if vid is None or label is None or vid in catalog_ids:
                _error()
            catalog_ids.add(vid)
            options_by_label.setdefault(label, []).append(vid)
        for value in values:
            embedded_id = (_identity(row.get("value_id"))
                           or _identity(value.get("id")) if isinstance(value, dict)
                           else _identity(row.get("value_id")))
            if embedded_id is not None:
                if embedded_id not in catalog_ids:
                    _error()
                if (oid, embedded_id) not in pairs:
                    # Existing selection_tokens should have captured real IDs.
                    _error()
                continue
            label = _clean(_label(value))
            matches = options_by_label.get(label) if label else None
            if not matches or len(matches) != 1:
                _error()
            vid = matches[0]
            # A different value for the same single-select field is a conflict.
            if any(x == oid and y != vid for x, y in pairs):
                _error()
            recovered.add((oid, vid))
    new = recovered - pairs
    if not new:
        return list(original), None
    pairs |= new
    selected = [{"option_id": oid, "value_id": vid} for oid, vid in sorted(pairs)]
    proof = {"version": VERSION, "source_line_fingerprint": fingerprint,
             "resolved_pairs": [{"option_id": oid, "value_id": vid} for oid, vid in sorted(new)]}
    proof["evidence_hash"] = _hash(proof)
    return selected, proof
