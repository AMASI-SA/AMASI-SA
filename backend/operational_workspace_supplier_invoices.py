"""Invoice-level presentation of existing supplier claims. No accounting writes."""
from decimal import Decimal

from operational_balance_store import fail
from operational_financial_activity_runtime import preparation_checkpoint, prepare_in_thread

COLLECTION = "mezan_supplier_invoices_v2"
HEADER = dict.fromkeys(("id", "supplier_id", "invoice_number", "approved_at", "approved_by",
                        "supplier_approved_by", "supplier_approved_by_name", "status",
                        "experiment_mode", "experiment_run_id", "currency", "total_halalas"), 1)


def usable_invoice(doc):
    return bool(doc and doc.get("approved_at") and not doc.get("experiment_mode") and not doc.get("experiment_run_id")
                and doc.get("status") in (None, "payable_posted") and doc.get("currency", "SAR") == "SAR")


def invoice_actor(doc):
    actor = doc.get("approved_by") or doc.get("supplier_approved_by")
    if not actor or doc.get("supplier_approved_by") not in (None, actor):
        return None, None
    name = doc.get("supplier_approved_by_name")
    return actor, name if isinstance(name, str) and name.strip() and "@" not in name and name != "غير موثق" else None


def linked_claim(item):
    return (item.get("party_type") == "supplier" and item.get("kind") == "supplier"
            and item.get("direction") == "payable" and item.get("source_invoice_id")
            and not item.get("derived_credit") and not item.get("virtual"))


def _missing_invoice_actors(resources, computed, state):
    suppliers = {r["id"] for r in resources if r["kind"] == "supplier" and r["access"]["movements"] != "none"}
    missing = {}
    missing_names = {}
    cached = state.get("engine", {}).get("supplier_invoice_actors", {})
    for item in computed["obligations"] if suppliers else ():
        preparation_checkpoint()
        if item.get("party_id") not in suppliers or not linked_claim(item):
            continue
        saved = cached.get(item["source_invoice_id"], {})
        if saved.get("id") and saved.get("supplier_id") == item["party_id"]:
            item.update(source_invoice_actor_id=saved["id"], source_invoice_actor_name=saved.get("name"))
        if not item.get("source_invoice_actor_id"):
            missing.setdefault((item["party_id"], item["source_invoice_id"]), []).append(item)
        elif not item.get("source_invoice_actor_name"):
            missing_names.setdefault(item["source_invoice_actor_id"], []).append(item)
    return missing, missing_names


def _missing_movement_actors(resources, movements, policy):
    visible = {(r["kind"], r["id"]): r["access"]["movements"] for r in resources if r["access"]["movements"] != "none"}
    manual = {}
    for item in movements:
        preparation_checkpoint()
        actor = item.get("actor_id")
        saved = item.get("recorded_by") or {}
        if (not actor or actor in {"operational_worker", "operational_order_worker"}
                or (saved.get("id") == actor and saved.get("name") not in (None, "", "غير موثق"))):
            continue
        scopes = (visible.get((item.get("party_type"), item.get("party_id"))), visible.get((item.get("bank_kind"), item.get("bank_id"))))
        if "all" in scopes or ("own" in scopes and actor == policy.actor["id"]):
            manual.setdefault(actor, []).append(item)
    return manual


async def fill_historical_headers(db, owner, resources, computed, state, policy):
    missing, missing_names = await prepare_in_thread(_missing_invoice_actors, resources, computed, state)
    ids = sorted({key[1] for key in missing})
    for start in range(0, len(ids), 250):
        batch = ids[start:start + 250]
        docs = await db[COLLECTION].find({"user_id": owner, "id": {"$in": batch}}, {"_id": 0, **HEADER}).to_list(len(batch) + 1)
        unique = {}
        for doc in docs:
            key = doc.get("supplier_id"), doc.get("id")
            unique[key] = doc if key not in unique else None
        for key, doc in unique.items():
            if usable_invoice(doc):
                actor, name = invoice_actor(doc)
                for item in missing.get(key, ()):
                    item.update(source_invoice_actor_id=actor, source_invoice_actor_name=name)
                    if actor and not name:
                        missing_names.setdefault(actor, []).append(item)
    manual = await prepare_in_thread(_missing_movement_actors, resources, state.get("movements", ()), policy)
    actor_ids = sorted(set(missing_names) | set(manual))
    for start in range(0, len(actor_ids), 250):
        batch = actor_ids[start:start + 250]
        query = {"id": {"$in": batch}, "$or": [{"id": owner}, {"created_by": owner}]}
        users = await db["users"].find(query, {"_id": 0, "id": 1, "name": 1}).to_list(len(batch))
        for user in users:
            if user.get("name") and user["name"] != "غير موثق":
                for item in missing_names.get(user["id"], ()):
                    item["source_invoice_actor_name"] = user["name"]
                for item in manual.get(user["id"], ()):
                    item["recorded_by"] = {"id": user["id"], "name": user["name"]}


def invoice_rows(obligations):
    """Replace only explicitly linked components. Retain original effective accounting dates."""
    groups = {}
    for item in obligations:
        preparation_checkpoint()
        if not linked_claim(item):
            yield item
            continue
        key = (item["party_id"], item["currency"], item["source_invoice_id"], item.get("business_date"))
        groups.setdefault(key, []).append(item)
    for (party, currency, invoice, day), components in groups.items():
        first = components[0]
        number = first.get("source_invoice_number")
        actors = {(c.get("source_invoice_actor_id"), c.get("source_invoice_actor_name")) for c in components}
        actor, name = actors.pop() if len(actors) == 1 else (None, None)
        label = "فاتورة شراء آجل" if first.get("credit_detail_key") else "فاتورة استلام"
        title = " · ".join(part for part in (label + (" " + number if number else ""), first.get("supplier_reference")) if part)
        base = {"invoice_label": title, "party_type": "supplier", "party_id": party, "currency": currency, "business_date": day,
                "kind": "supplier_invoice", "approved_at": first.get("source_invoice_approved_at"),
                "reference": number, "invoice_id": invoice, "actor_id": actor,
                "recorded_by": {"id": actor, "name": name} if actor and name else None}
        net = sum((Decimal(c["confirmed"]) for c in components), Decimal(0))
        returns = sum((Decimal(c.get("returned_amount") or 0) for c in components), Decimal(0))
        identity = f"supplier_invoice:{party}:{invoice}:{day}"
        yield {**base, "id": identity, "direction": "payable", "confirmed": str(net),
               "invoice_gross": format(net + returns, ".2f"), "invoice_returns": format(returns, ".2f")}



async def read_invoice(db, owner, supplier, invoice_id, policy, header=None):
    if header and header.get("credit_detail_key"):
        from operational_supplier_credit_purchase import DETAILS
        document = await db[DETAILS].find_one({"_id": header["credit_detail_key"], "owner_id": owner,
                                                "supplier_id": supplier, "invoice_id": invoice_id}, {"detail": 1})
        if not document:
            fail("supplier_purchase_details_unavailable", "تعذر تحميل مستند الفاتورة", 503)
        return {**document["detail"], "id": invoice_id, "number": header["number"]}
    query = {"user_id": owner, "supplier_id": supplier, "id": invoice_id}
    fields = {"_id": 0, **HEADER, "purchase_tax.amount_halalas": 1}
    for field in ("product_name", "sku", "selected_image_url", "quantity", "product_unit_price_halalas", "total_halalas",
                  "services.service_name", "services.total_halalas"):
        fields["lines." + field] = 1
    docs = await db[COLLECTION].find(query, fields).to_list(2)
    doc = docs[0] if len(docs) == 1 else None
    access = policy.resource("supplier", supplier)["movements"]
    if not usable_invoice(doc) or access == "none" or (access == "own" and invoice_actor(doc)[0] != policy.actor["id"]):
        fail("operational_workspace_not_found", "الفاتورة غير متاحة", 404)
    def amount(value):
        return format(Decimal(value) / 100, ".2f") if type(value) is int else None
    return {"id": invoice_id, "number": doc.get("invoice_number"), "currency": doc.get("currency", "SAR"),
            "total": amount(doc.get("total_halalas")), "tax": amount((doc.get("purchase_tax") or {}).get("amount_halalas")),
            "lines": [{"name": line.get("product_name"), "sku": line.get("sku"), "image_url": line.get("selected_image_url"),
                       "quantity": line.get("quantity"),
                       "unit": amount(line.get("product_unit_price_halalas")), "total": amount(line.get("total_halalas")),
                       "services": [{"name": s.get("service_name"), "total": amount(s.get("total_halalas"))}
                                    for s in line.get("services") or []]} for line in doc.get("lines") or []]}
