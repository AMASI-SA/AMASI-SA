"""EXIT-2D: real HTTP preparation lifecycle, memory-only controller state.

Fixture writes are confined to seed_inputs, before the first HTTP phase.
No ready batch, registry, allocation or physical piece is seeded. Provider HTTP is explicitly simulated in independent disposable instances.
Runtime preflight remains blocked by the unchanged credential guard.
All application phases remain unaccepted until real Linux execution.
"""
from __future__ import annotations
import ast
import base64
import copy
import hashlib
import io
import os
from datetime import timedelta
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

REGISTRY = "mezan_preparation_file_registry_v2"
BATCHES = "mezan_preparation_batches_v2"
ALLOCATIONS = "mezan_preparation_unit_allocations_v2"
PIECES = "mezan_preparation_pieces_v1"
EVENTS = "mezan_preparation_piece_events_v1"
WORKFLOWS = "order_review_workflows"
IMAGES = "order_review_mezan_images"
SUPPLIERS = "mezan_suppliers_v2"
GENERATED = (REGISTRY, BATCHES, ALLOCATIONS, PIECES)
ORDERS = ("EXIT2D-1001", "EXIT2D-1002")


def require(condition):
    if not condition:
        raise AssertionError("preparation acceptance invariant failed")


def backend_root():
    installed = Path("/opt/mezan/backend")
    return installed if installed.is_dir() else Path(__file__).resolve().parents[2] / "backend"


def provider_barrier(kind):
    """Fail closed on the audited source contract; no provider is called here.

    An approved business change must first update the focused reproduction and
    this exact guard after review. Absence of this AST pattern alone is not a
    certificate of no provider calls.
    """
    targets = {
        "review": ("order_review_routes.py", "complete_review", "_sync_salla_reviewed"),
        "assembly": ("preparation_piece_operations.py", "mark_assembly_piece_ready", "sync_completed_carrier_label"),
    }
    filename, function, called = targets[kind]
    tree = ast.parse((backend_root() / filename).read_text(encoding="utf-8"))
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == function]
    require(len(nodes) == 1)
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == called
           for n in ast.walk(nodes[0])):
        raise AssertionError("known provider-dependent application contract")


def png(color):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (48, 48), color).save(buffer, format="PNG")
    return buffer.getvalue()


def seed_inputs(database, state):
    from auth import hash_password
    from mfa_security import encrypt_totp_secret
    import acceptance as a
    for name in GENERATED:
        require(database[name].count_documents({}) == 0)
    owner = database.users.find_one({"email": "admin@hesab.app"})
    require(owner is not None and owner["role"] == "owner")
    scenario = os.environ.get("EXIT2D_SIM_MODE")
    require(scenario in {"success", "deny", "unavailable"})
    state["provider_scenario"] = scenario
    state.update(owner=owner["id"], employee="exit2d-employee", viewer="exit2d-viewer",
                 outsider="exit2d-other-owner", files=[], raw_expected={}, expected={}, images={})
    database.users.update_one({"id": state["owner"]}, {"$set": {
        "mfa_enabled": True, "mfa_totp_secret_enc": encrypt_totp_secret(a.TOTP)},
        "$unset": {"mfa_last_totp_counter": "", "password_updated_at": ""}})
    from salla_integration.crypto import encrypt_token
    database.salla_integrations.insert_one({"user_id": state["owner"], "status": "connected",
        "access_token_encrypted": encrypt_token(os.environ["EXIT2D_SIM_TOKEN"]),
        "expires_at": a.now() + timedelta(hours=1), "token_revision": 1})
    for actor, role in (("employee", "operations"), ("viewer", "viewer"), ("outsider", "owner")):
        database.users.insert_one({"id": state[actor], "email": actor + "@exit2d.example.test",
            "name": "\u0645\u0648\u0638\u0641 \u0627\u0635\u0637\u0646\u0627\u0639\u064a " + actor, "role": role, "is_active": True,
            "created_by": state["owner"] if actor != "outsider" else state[actor],
            "password_hash": hash_password(a.EMPLOYEE_PASSWORD),
            **({"mfa_enabled": True, "mfa_totp_secret_enc": encrypt_totp_secret(a.TOTP)} if actor == "outsider" else {})})
    database.mezan_mobile_app_access_v1.insert_one({"owner_user_id": state["owner"],
        "user_id": state["employee"], "enabled": True,
        "permissions": ["app.access", "app.page.my_products"]})
    database[SUPPLIERS].insert_one({"user_id": state["owner"], "id": "exit2d-supplier",
        "company_name": "\u0645\u0648\u0631\u062f \u0627\u062e\u062a\u0628\u0627\u0631 \u0645\u062d\u0644\u064a", "status": "active", "service_ids": ["synthetic-service"],
        "service_links": [{"service_id": "synthetic-service"}]})
    for number_index, number in enumerate(ORDERS):
        raw_items = []
        for item_index in range(2):
            pid = f"exit2d-product-{number_index}-{item_index}"
            iid = f"exit2d-item-{number_index}-{item_index}"
            options = {"\u0627\u0644\u0644\u0648\u0646": ("\u0630\u0647\u0628\u064a", "\u0641\u0636\u064a")[item_index], "\u0627\u0644\u0646\u0642\u0634": ("\u0646\u0648\u0631", "\u0623\u0645\u0644")[number_index]}
            image_bytes = png((30 + number_index * 80, 40 + item_index * 90, 170))
            image_urls = []
            for variant in ("a", "b"):
                image_id = pid + "-" + variant
                url = "/api/order-reviews-v1/mezan-images/" + image_id
                image_urls.append(url)
                database[IMAGES].insert_one({"id": image_id, "user_id": state["owner"],
                    "product_key": "product:" + pid, "content_type": "image/png",
                    "data_base64": base64.b64encode(image_bytes).decode(),
                    "sha256": hashlib.sha256(image_bytes).hexdigest(), "size": len(image_bytes)})
            database.salla_products.insert_one({"user_id": state["owner"], "product_id": pid,
                "sku": pid, "name": "\u0645\u0646\u062a\u062c \u0627\u0635\u0637\u0646\u0627\u0639\u064a " + pid, "main_image": image_urls[0],
                "images": [{"url": u} for u in image_urls], "gallery_refreshed_at": a.now().isoformat()})
            raw_items.append({"id": iid, "quantity": 2, "name": "\u0645\u0646\u062a\u062c \u0627\u0635\u0637\u0646\u0627\u0639\u064a " + pid,
                "product": {"id": pid, "name": "\u0645\u0646\u062a\u062c \u0627\u0635\u0637\u0646\u0627\u0639\u064a " + pid, "sku": pid,
                            "main_image": image_urls[0], "images": [{"url": u} for u in image_urls]},
                "options": [{"name": k, "value": v} for k, v in options.items()],
                "amounts": {"price_without_tax": {"amount": 10, "currency": "SAR"}}})
            state["raw_expected"][(number, iid)] = {"quantity": 2, "options": options, "product_id": pid}
        raw = {"id": "raw-" + number, "reference_id": number, "date": "2026-09-07T00:00:00Z",
               "status": {"slug": "under_review", "name": "\u0628\u0627\u0646\u062a\u0638\u0627\u0631 \u0627\u0644\u0645\u0631\u0627\u062c\u0639\u0629"},
               "customer": {"full_name": "\u0639\u0645\u064a\u0644 \u0627\u062e\u062a\u0628\u0627\u0631", "email": "customer@example.test"},
               "items": raw_items, "amounts": {"total": {"amount": 40, "currency": "SAR"}}}
        for tenant in (state["owner"], state["outsider"]):
            database.unified_orders.insert_one({"user_id": tenant, "order_number": number,
                "order_date": raw["date"], "order_status": "\u0628\u0627\u0646\u062a\u0638\u0627\u0631 \u0627\u0644\u0645\u0631\u0627\u062c\u0639\u0629",
                "raw_by_source": {"salla_direct": copy.deepcopy(raw)}})
            # Initial experiment guard only: never a reviewed/ready workflow.
            database[WORKFLOWS].insert_one({"user_id": tenant, "order_number": number,
                "stage": "pending_review", "revision": 0, "items": [],
                "experiment_mode": scenario != "success", "salla_status_writes_allowed": scenario == "success",
                **({"experiment_run_id": "exit2d-local-input", "experiment_generation": 1} if scenario != "success" else {})})
    require(all(database[n].count_documents({}) == 0 for n in GENERATED))


class Lifecycle:
    def __init__(self, state):
        import acceptance as a
        self.a, self.state, self.database = a, state, a.db()

    def call(self, method, path, *, actor="owner", expected=200, **kwargs):
        require(path.startswith("/api/") and "://" not in path)
        return self.a.request(0, method, path, cookie=self.state[actor + "_cookie"],
                              expected=expected, **kwargs)

    def rows(self, collection):
        return list(self.database[collection].find({"user_id": self.state["owner"]}, {"_id": 0}))

    def invariant(self):
        require(all(r.get("experiment_mode") is (self.state["provider_scenario"] != "success") and r.get("salla_status_writes_allowed") is (self.state["provider_scenario"] == "success")
                    for r in self.rows(WORKFLOWS)))
        require(not self.database.backend_startup_leases_v1.find_one({
            "_id": "backend-heavy-initialization:independent-worker:singleton", "status": "running"}))
        require(self.database.salla_connections.count_documents({}) == 0)
        require(self.database.salla_integrations.count_documents({}) == 1)
        from salla_http_simulator import validate_addresses
        validate_addresses(os.environ)

    def provider_counts(self):
        result = self.a.httpx.get("http://127.0.0.1:8093/__fixture__/counts", follow_redirects=False, trust_env=False, timeout=2)
        require(result.status_code == 200)
        counts = result.json()
        require(counts["unexpected"] == 0)
        self.state["simulated_provider_calls"] = counts["simulated_provider_calls"]
        return counts

    def sessions(self):
        for actor in ("owner", "employee", "viewer", "outsider"):
            response = self.call("GET", "/api/auth/me", actor=actor).json()
            require(response["id"] == self.state[actor])

    def login_otp(self, actor):
        a = self.a
        if actor == "outsider":
            from mfa_security import hotp
            response = a.request(0, "POST", "/api/auth/login", expected=202,
                json={"email": actor + "@exit2d.example.test", "password": a.EMPLOYEE_PASSWORD, "force_totp": True})
            require(response.json().get("mfa_required") is True)
            device = a.cookies(response)
            verified = a.request(1, "POST", "/api/auth/mfa/verify", cookie=device,
                json={"challenge_token": response.json()["challenge_token"],
                      "code": hotp(a.TOTP, int(time.time() // 30))})
            self.state[actor + "_cookie"] = a.cookies(verified, device)
            return
        a.seed_otp(self.database, self.state[actor])
        response = a.request(0, "POST", "/api/auth/login", expected=202,
            json={"email": actor + "@exit2d.example.test", "password": a.EMPLOYEE_PASSWORD})
        device = a.cookies(response)
        verified = a.request(1, "POST", "/api/auth/email-otp/verify", cookie=device,
            json={"challenge_token": response.json()["challenge_token"], "code": a.OTP})
        self.state[actor + "_cookie"] = a.cookies(verified, device)

    def review(self):
        self.state["owner_cookie"] = self.a.owner_session()
        for actor in ("employee", "viewer", "outsider"):
            self.login_otp(actor)
        self.sessions()
        self.invariant()
        self.state["other_workflows"] = list(self.database[WORKFLOWS].find({"user_id": self.state["outsider"]}, {"_id": 0}))
        for number in ORDERS:
            path = "/api/order-reviews-v1/" + number
            detail = self.call("GET", path).json()
            require(len(detail["items"]) == 2)
            for item in detail["items"]:
                iid = item["order_item_id"]
                source_id = item["source"]["source_order_item_id"]
                require((number, source_id) in self.state["raw_expected"])
                expected = self.state["raw_expected"][(number, source_id)]
                require(item["quantity"] == expected["quantity"])
                require({o["name"]: o["value"] for o in item["options"]} == expected["options"])
                self.state["expected"][(number, iid)] = expected
                image = png((40, 120, 60 if number == ORDERS[0] else 190))
                updated = self.call("POST", path + "/items/" + quote(iid, safe="") + "/mezan-images",
                    json={"filename": "local.png", "content_type": "image/png",
                          "data_base64": base64.b64encode(image).decode()}).json()
                gallery = next(i for i in updated["items"] if i["order_item_id"] == iid)["gallery"]
                fresh = [u for u in gallery if u not in item["gallery"]]
                require(len(fresh) == 1 and fresh[0].startswith("/api/order-reviews-v1/mezan-images/"))
                image_url = fresh[0]
                detail = self.call("POST", path + "/items/" + quote(iid, safe="") + "/image-choice",
                    json={"expected_revision": updated["revision"], "selected_image_url": image_url,
                          "mode": "order_only"}).json()
                detail = self.call("PATCH", path + "/items/" + quote(iid, safe=""),
                    json={"expected_revision": detail["revision"], "preparation_note": "\u0627\u062e\u062a\u0628\u0627\u0631 \u062a\u062c\u0647\u064a\u0632 \u0645\u062d\u0644\u064a"}).json()
                self.state["images"][(number, iid)] = (image_url, hashlib.sha256(image).hexdigest())
                self.call("GET", image_url, actor="outsider", expected=404)
            self.call("POST", path + "/complete", actor="viewer", expected=403,
                      json={"expected_revision": detail["revision"]})
            # Actual application route and client. Successful provider replies
            # are fixture-only; the denied fixtures never manufacture sent.
            if self.state["provider_scenario"] != "success":
                response = self.call("POST", path + "/complete", expected=502,
                    json={"expected_revision": detail["revision"]}).json()
                require(response["detail"]["code"] == "salla_review_status_sync_failed")
                workflow = self.database[WORKFLOWS].find_one({"user_id": self.state["owner"], "order_number": number})
                require(workflow["stage"] == "pending_review" and workflow.get("salla_sync_status") != "sent")
                require(all(self.database[name].count_documents({}) == 0 for name in GENERATED))
            else:
                self.call("POST", path + "/complete", json={"expected_revision": detail["revision"]})
        counts = self.provider_counts()
        if self.state["provider_scenario"] != "success":
            require(counts["status_writes"] == 0 and counts["denied"] >= len(ORDERS))
        else:
            require(counts["status_writes"] >= len(ORDERS))
        self.invariant()

    def catalog(self):
        data = self.call("GET", "/api/reviewed-products-v1/catalog").json()
        require(not data.get("truncated"))
        rows = data["products"]
        require(all(p.get("piece_level") is True for p in rows))
        for row in rows:
            require(row["group_key"] and len(row["revision"]) == 64)
        return rows

    def draft(self, request_id, count):
        payload = {"client_request_id": request_id, "file_title": "\u0645\u0644\u0641 \u0627\u062e\u062a\u0628\u0627\u0631 \u0639\u0631\u0628\u064a",
                   "responsible_employee_id": self.state["employee"], "expected_quantity": count,
                   "selected_product_count": count, "schedule_mode": "automatic"}
        return self.call("POST", "/api/preparation-file-safety-v1/drafts", json=payload).json()

    def build(self, request_id, selections):
        self.draft(request_id, len(selections))
        payload = {"client_request_id": request_id, "selections": selections}
        response = self.call("POST", "/api/reviewed-preparation-batches-v1/batches", json=payload).json()
        # Match the actual service fallback; never mandate finalize on success.
        if not (response.get("file_registered") is True and response.get("registry_status") == "ready"
                and response.get("piece_registry_status") == "ready"):
            response.update(self.call("POST", "/api/preparation-file-registry-v1/finalize/" + request_id).json())
        require(response["file_number"] and response["batch_id"])
        before = self.snapshot()
        duplicate = self.call("POST", "/api/reviewed-preparation-batches-v1/batches", json=payload).json()
        require(duplicate["batch_id"] == response["batch_id"])
        # Stable materialized IDs and piece events may not duplicate on retry.
        require(self.identity() == before["identity"])
        require(self.rows(EVENTS) == before["documents"][EVENTS])
        self.state["files"].append({"request_id": request_id, **response})
        return response

    def identity(self):
        return identity(self.rows(ALLOCATIONS), self.rows(PIECES), self.rows(REGISTRY))

    def snapshot(self):
        return {"identity": self.identity(), "documents": {
            name: copy.deepcopy(self.rows(name)) for name in (REGISTRY, BATCHES, ALLOCATIONS, PIECES, EVENTS, WORKFLOWS)}}

    def persistence(self):
        self.sessions()
        self.invariant()
        require(self.snapshot() == self.state["checkpoint"])
        require(list(self.database[WORKFLOWS].find({"user_id": self.state["outsider"]}, {"_id": 0})) == self.state["other_workflows"])
        self.pdf_and_images()

    def pdf_and_images(self):
        import fitz
        for url, digest in self.state["images"].values():
            require(hashlib.sha256(self.call("GET", url).content).hexdigest() == digest)
        files = self.call("GET", "/api/preparation-file-registry-v1/files").json()["items"]
        for file in self.state["files"]:
            require(any(r["file_number"] == file["file_number"] and r["batch_id"] == file["batch_id"] for r in files))
            pdf = self.call("GET", "/api/reviewed-preparation-batches-v1/batches/" + file["batch_id"] + "/pdf")
            require(pdf.content.startswith(b"%PDF"))
            with fitz.open(stream=pdf.content, filetype="pdf") as document:
                require(document.page_count > 0)
                text = "".join(page.get_text() for page in document)
                require(file["file_number"] in text)
                normalized = unicodedata.normalize("NFKC", text)
                for piece in self.rows(PIECES):
                    if piece["batch_id"] == file["batch_id"]:
                        for value in self.state["expected"][(piece["order_number"], piece["order_item_id"])]["options"].values():
                            require(value in normalized or value[::-1] in normalized)
                require(any("\u0600" <= char <= "\u06ff" or "\ufb50" <= char <= "\ufeff" for char in text))
                require(any(page.get_images() for page in document))
            self.call("GET", "/api/reviewed-preparation-batches-v1/batches/" + file["batch_id"] + "/pdf",
                      actor="outsider", expected=404)

    def create(self):
        employees = self.call("GET", "/api/preparation-file-registry-v1/employees").json()["items"]
        require(any(e["id"] == self.state["employee"] for e in employees))
        catalog = self.catalog()
        require(len(catalog) == 8)  # Four source lines, two physical units each.
        selections = [{"group_key": p["group_key"], "revision": p["revision"], "quantity": 1} for p in catalog]
        self.state["old_selection"] = selections[0]
        first = self.build("exit2d-partial-0001", selections[:1])
        self.call("POST", "/api/preparation-work-v1/files/" + first["file_number"] + "/start",
                  expected=409, json={})
        self.call("POST", "/api/preparation-file-safety-v1/drafts", actor="viewer", expected=403,
                  json={"client_request_id": "exit2d-denied-0001", "file_title": "\u0627\u062e\u062a\u0628\u0627\u0631",
                        "responsible_employee_id": self.state["employee"], "expected_quantity": 1,
                        "selected_product_count": 1})
        self.pdf_and_images()
        # Incomplete draft/finalize is produced through HTTP before restart.
        self.draft("exit2d-incomplete-0001", 1)
        self.call("POST", "/api/preparation-file-registry-v1/finalize/exit2d-incomplete-0001", expected=409)
        self.state["checkpoint"] = self.snapshot()

    def recover(self):
        # Real draft + premature finalize, not a fabricated ready batch/failure.
        released = self.call("POST", "/api/preparation-file-safety-v1/requests/exit2d-incomplete-0001/release").json()
        require(released["released"] is True)
        require(not any(r["client_request_id"] == "exit2d-incomplete-0001" for r in self.rows(REGISTRY)))
        for file in self.state["files"]:
            before = self.identity()
            kept = self.call("POST", "/api/preparation-file-safety-v1/requests/" + file["request_id"] + "/release").json()
            require(kept["released"] is False and self.identity() == before)

    def resume(self):
        self.persistence()
        self.recover()
        # Old selector must not reclaim its already allocated physical unit.
        allocated_before = self.identity()
        self.draft("exit2d-reallocate-0001", 1)
        rejection = self.call("POST", "/api/reviewed-preparation-batches-v1/batches", expected=409,
                  json={"client_request_id": "exit2d-reallocate-0001", "selections": [self.state["old_selection"]]}).json()
        require(rejection["detail"]["code"] in {"reviewed_product_not_available", "reviewed_selection_stale", "preparation_quantity_exceeds_remaining"})
        self.state["allocated_unit_rejection"] = rejection["detail"]["code"]
        self.call("POST", "/api/preparation-file-safety-v1/requests/exit2d-reallocate-0001/release")
        require(self.identity() == allocated_before)
        fresh = self.catalog()
        require(sum(p["remaining_quantity"] for p in fresh) == 7)
        selections = [{"group_key": p["group_key"], "revision": p["revision"], "quantity": 1}
                      for p in fresh if p["remaining_quantity"]]
        # The stale catalogue selection above is the original exact key/revision.
        # Its rejection proves unavailable-unit safety. It does NOT independently
        # prove the reviewed_selection_stale error for an available unit. Frozen
        # unit identity can keep its revision when other units are allocated;
        # do not substitute a different card's hash and call it an old revision.
        self.build("exit2d-remaining-0001", selections)
        require(all(w["preparation_assignment_status"] == "assigned" for w in self.rows(WORKFLOWS)))
        require(all(w["preparation_progress"]["remaining_quantity"] == 0 for w in self.rows(WORKFLOWS)))
        verify_units(self.state["expected"], self.rows(ALLOCATIONS), self.rows(PIECES), self.state["employee"])
        first = self.state["files"][0]
        start_path = "/api/preparation-work-v1/files/" + first["file_number"] + "/start"
        started = self.call("POST", start_path, actor="employee", json={}).json()
        require(started["mezan_only"] is True and started["salla_updated"] is False)
        events = self.rows(EVENTS)
        self.call("POST", start_path, actor="employee", json={})
        require(self.rows(EVENTS) == events)
        self.dispatch_receive()
        self.pdf_and_images()
        self.state["checkpoint"] = self.snapshot()

    def finish(self):
        self.persistence()
        # Revision reachability remains a separate, explicitly unaccepted gate.
        # Never turn an unavailable-unit rejection into a stale-revision claim.
        counts = self.provider_counts()
        require(counts["shipping_attempted"] == len(ORDERS))
        require(counts["shipping_failed"] == len(ORDERS))

    def dispatch_receive(self):
        for file in self.state["files"]:
            workspace = self.call("GET", "/api/supplier-dispatch-v1/workspace", actor="employee",
                                  params={"grain": "piece"}).json()
            supplier = next(s for s in workspace["suppliers"] if s["id"] == "exit2d-supplier")
            source = next(f for f in workspace["files"] if f["file_number"] == file["file_number"])
            selections = [{"group_key": p["group_key"], "quantity": 1} for p in source["products"] if p["available_quantity"]]
            payload = {"client_request_id": "dispatch-" + file["request_id"], "supplier_id": supplier["id"],
                       "files": [{"file_number": file["file_number"], "selections": selections}]}
            require({p["piece_id"] for p in source["products"]} ==
                    {p["piece_id"] for p in self.rows(PIECES) if p["file_number"] == file["file_number"]})
            result = self.call("POST", "/api/supplier-dispatch-v1/dispatches", actor="employee", expected=201, json=payload).json()
            dispatch_id = result["dispatch"]["id"]
            event_before = self.rows(EVENTS)
            repeat = self.call("POST", "/api/supplier-dispatch-v1/dispatches", actor="employee", expected=201, json=payload).json()
            require(repeat["dispatch"]["id"] == dispatch_id and self.rows(EVENTS) == event_before)
            self.call("POST", "/api/supplier-dispatch-v1/dispatches/" + dispatch_id + "/ready", actor="employee", json={})
        for piece in self.rows(PIECES):
            require(piece["supplier_id"] == "exit2d-supplier")
            require(piece["responsible_employee_id"] == self.state["employee"])
            self.call("GET", "/api/preparation-work-v1/receiving/search", params={"q": piece["piece_id"]})
            path = "/api/preparation-work-v1/receiving/pieces/" + piece["piece_id"] + "/receive"
            payload = {"client_request_id": "receive-" + piece["piece_id"]}
            self.call("POST", path, json=payload)
            before, events = self.identity(), self.rows(EVENTS)
            self.call("POST", path, json=payload)
            require(self.identity() == before and self.rows(EVENTS) == events)
        require(all(r["execution_status"] == "completed" for r in self.rows(REGISTRY)))
        for piece in self.rows(PIECES):
            self.call("GET", "/api/preparation-work-v1/assembly/search", params={"q": piece["order_number"]})
            # The real route attempts a label. The simulator rejects its
            # first authoritative order lookup; no shipment is fabricated.
            result = self.call("POST", "/api/preparation-work-v1/assembly/pieces/" + piece["piece_id"] + "/ready",
                      json={"client_request_id": "assembly-" + piece["piece_id"]}).json()
            if result["progress"].get("order_completed"):
                require(result["carrier_label"]["ready"] is False)
                require(result["carrier_label"]["error_code"] == "salla_shipping_unavailable")
        require(all(w.get("stage") == "completed" and w.get("assembly_status") == "completed" and w.get("carrier_label_ready") is False and w.get("carrier_label_status") == "failed"
                    for w in self.rows(WORKFLOWS)))
        self.provider_counts()
        self.invariant()


def identity(allocations, pieces, registries):
    def units(rows):
        return sorted((r["order_number"], r["order_item_id"], r["unit_index"], r["batch_id"]) for r in rows)
    return (units(allocations), units(pieces), sorted((r["client_request_id"], r["file_number"], r.get("batch_id")) for r in registries))


def verify_units(expected, allocations, pieces, employee):
    wanted = {(number, item, unit) for (number, item), row in expected.items() for unit in range(1, row["quantity"] + 1)}
    for rows in (allocations, pieces):
        actual = [(r["order_number"], r["order_item_id"], r["unit_index"]) for r in rows]
        require(len(actual) == len(set(actual)) and set(actual) == wanted)
    require(all(r["status"] == "committed" for r in allocations))
    require(all(r["responsible_employee_id"] == employee for r in pieces))
    for piece in pieces:
        options = expected[(piece["order_number"], piece["order_item_id"])]["options"]
        require(all(piece["product_options_snapshot"].get(k) == v for k, v in options.items()))


def phases():
    def setup(state):
        import acceptance as a
        database = a.db()
        try:
            seed_inputs(database, state)
        finally:
            database.client.close()
    def execute(method):
        def phase(state):
            lifecycle = Lifecycle(state)
            try:
                getattr(lifecycle, method)()
            finally:
                lifecycle.database.client.close()
        return phase
    return {"prep-setup": setup, "prep-review": execute("review"),
            "prep-create": execute("create"), "prep-resume": execute("resume"),
            "prep-finish": execute("finish")}
