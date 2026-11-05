"""Resolve report choices to provider IDs without changing source order payloads.

Bare Salla order-option/value ``id`` fields may identify checkout rows. Only
catalog IDs or explicit option_id/value_id are identities. Catalog translations
are a recovery bridge for legacy name-only rows, never fuzzy/global Yes matching.
The returned evidence contains IDs only and is retained in cost snapshots.
"""
from collections import defaultdict


def _id(value):
    return str(value).strip() if value is not None and not isinstance(value, (dict, list, bool)) else ""


def _label(value):
    return " ".join(str(value or "").casefold().split())


def _labels(row, value=False):
    labels = {_label(row.get("name"))}
    field = "option_details_name" if value else "option_name"
    translations = row.get("translations") or {}
    if isinstance(translations, dict):
        labels.update(_label(t.get(field) or t.get("name")) for t in translations.values() if isinstance(t, dict))
    labels.update(_label(label) for label in row.get("identity_labels") or [] if isinstance(label, str))
    return labels - {""}


def catalog_options(product):
    """Merge normalized options with provider translations by stable ID."""
    result = {}
    for source in (product.get("raw_salla"), product.get("raw_salla_details"), product):
        if not isinstance(source, dict):
            continue
        rows = source.get("options") or []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not _id(row.get("id")):
                continue
            oid = _id(row["id"])
            entry = result.setdefault(oid, {"labels": set(), "values": {}, "required": False})
            entry["labels"].update(_labels(row))
            entry["required"] = bool(row.get("required") or row.get("is_required"))
            entry["multiple"] = row.get("type") in {"checkbox", "multiple", "multi_select"}
            for val in row.get("values") or []:
                if isinstance(val, dict) and _id(val.get("id")):
                    entry["values"].setdefault(_id(val["id"]), set()).update(_labels(val, True))
    return result


def resolve_report_choices(item, product, bindings):
    """Return binding decisions (True/False/None) plus stable selection evidence.

    None means unresolved, not an unselected free option. Legacy bindings without
    IDs keep their existing compatibility matcher; all identified bindings use
    the same identity resolver, irrespective of display language.
    """
    from stock_component_consumption_service import binding_selected, selection_tokens
    if not bindings:
        return [], []
    catalog = catalog_options(product or {})
    catalog_ids = set(catalog)
    # A stored binding itself supplies an identity/old label for deleted or
    # renamed catalog entries; it never supplies a customer's selection.
    for binding in bindings:
        oid, vid = _id(binding.get("option_id")), _id(binding.get("value_id"))
        if oid and vid:
            entry = catalog.setdefault(oid, {"labels": set(), "values": {}, "required": False})
            if binding.get("option_name"):
                entry["labels"].add(_label(binding["option_name"]))
            if vid not in {"filled", "__option__"}:
                entry["values"].setdefault(vid, set()).add(_label(binding.get("value_name")))
    selected, present, invalid = defaultdict(set), set(), set()
    unknown = False
    rows = item.get("options_raw") or item.get("options") or item.get("selected_options") or []
    rows = list(rows) if isinstance(rows, list) else []
    if not rows:
        rows = [{"name": name, "values": value}
                for name, value in (item.get("options_normalized") or {}).items()]
    for original in rows:
        if not isinstance(original, dict):
            unknown = True
            continue
        row = original.get("raw") if isinstance(original.get("raw"), dict) else original
        nested = row.get("option") if isinstance(row.get("option"), dict) else {}
        strong = {_id(row.get("option_id")), _id(nested.get("id"))} - {""}
        known = strong & catalog.keys()
        if len(strong) > 1:
            invalid.update(known)
            unknown = True
            continue
        oid = next(iter(strong), "")
        if not oid and _id(row.get("id")) in catalog:
            oid = _id(row["id"])
        name = _label(row.get("option_name") or row.get("name") or nested.get("name"))
        if not oid:
            candidates = [key for key, entry in catalog.items() if name and name in entry["labels"]]
            if len(candidates) == 1:
                oid = candidates[0]
            elif candidates:
                invalid.update(candidates)
        if oid not in catalog:
            unknown = True
            continue
        present.add(oid)
        values = row.get("values") if "values" in row else row.get("value", row.get("value_name"))
        values = values if isinstance(values, list) else [values]
        choices = catalog[oid]["values"]
        for value in values:
            data = value if isinstance(value, dict) else {}
            explicit = {_id(row.get("value_id")), _id(data.get("value_id")), _id(data.get("option_value_id"))} - {""}
            if len(explicit) > 1:
                invalid.add(oid)
                continue
            vid = next(iter(explicit), "")
            if not vid and _id(data.get("id")) in choices:
                vid = _id(data["id"])
            label = _label(data.get("name") or data.get("label") or data.get("value") or data.get("text")) if data else _label(value)
            if not choices:
                if label or _label(row.get("text") or row.get("answer")):
                    selected[oid].add("filled")
                continue
            if not vid:
                candidates = [key for key, labels in choices.items() if label and label in labels]
                if len(candidates) == 1:
                    vid = candidates[0]
            if vid in choices or explicit and oid not in catalog_ids:
                selected[oid].add(vid)
            else:
                invalid.add(oid)
        if len(selected[oid]) > 1 and not catalog[oid].get("multiple"):
            invalid.add(oid)
    tokens = selection_tokens(item)
    decisions = []
    for binding in bindings:
        oid, vid = _id(binding.get("option_id")), _id(binding.get("value_id"))
        if not oid or not vid or vid == "filled" and oid not in present:
            matched = binding_selected(binding, tokens)
            # A legacy unmatched label is not proof that the service was declined.
            name_key = "name:" + " ".join(str(binding.get("option_name") or "").strip().casefold().split())
            known_values = {_label(b.get("value_name")) for b in bindings
                            if _label(b.get("option_name")) == _label(binding.get("option_name"))}
            for entry in catalog.values():
                if _label(binding.get("option_name")) in entry["labels"]:
                    known_values.update(label for labels in entry["values"].values() for label in labels)
            answers = {value[5:] for key, value in tokens if key == name_key and str(value).startswith("name:")}
            answered = bool(answers) and answers <= (known_values - {""})
            decisions.append(True if matched else False if answered else None)
        elif oid in invalid:
            decisions.append(None)
        elif oid in present:
            decisions.append(bool(selected[oid]) if vid == "filled" else True if vid == "__option__" else vid in selected[oid])
        else:
            # Missing optional answers also need evidence; optional does not mean free.
            decisions.append(None)
    evidence = [{"option_id": oid, "value_id": vid} for oid in sorted(selected) if oid not in invalid for vid in sorted(selected[oid])]
    return decisions, evidence


def retain_option_identity_labels(previous, patch):
    """Retain observed legacy labels under the SAME provider ID on refresh.

    This is catalog metadata, never customer answers. A recreated option with
    a new ID cannot inherit old bindings or labels. No positional/value guessing.
    """
    before, after = catalog_options(previous), catalog_options(patch)
    for option in patch.get("options") or []:
        oid = _id(option.get("id"))
        old, new = before.get(oid, {}), after.get(oid, {})
        option["identity_labels"] = sorted(old.get("labels", set()) | new.get("labels", set()))
        for value in option.get("values") or []:
            vid = _id(value.get("id"))
            value["identity_labels"] = sorted(old.get("values", {}).get(vid, set()) | new.get("values", {}).get(vid, set()))
