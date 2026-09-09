"""EXIT-2D: real HTTP preparation lifecycle, memory-only controller state.

Fixture writes are confined to seed_inputs, before the first HTTP phase.
No ready batch, registry, allocation or physical piece is seeded. Provider HTTP is explicitly simulated in independent disposable instances.
Runtime preflight validates the explicit synthetic-only profile before server import.
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
from acceptance_controller import check
from order_fixture import ORDERS, order_fixture
from unit_contract import verify_units
from source_paths import backend_root
from simulated_status_webhook import OWNER_MERCHANT, OTHER_MERCHANT, signed_body, require_ingestion

REGISTRY = "mezan_preparation_file_registry_v2"
BATCHES = "mezan_preparation_batches_v2"
ALLOCATIONS = "mezan_preparation_unit_allocations_v2"
PIECES = "mezan_preparation_pieces_v1"
EVENTS = "mezan_preparation_piece_events_v1"
WORKFLOWS = "order_review_workflows"
IMAGES = "order_review_mezan_images"
SUPPLIERS = "mezan_suppliers_v2"
GENERATED = (REGISTRY, BATCHES, ALLOCATIONS, PIECES)


def require(condition):
    if not condition:
        raise AssertionError("preparation acceptance invariant failed")


def verify_gallery_links(actual, expected):
    # Exact declared aliases, with no normalization, extras, omissions or duplicates.
    require(len(actual) == len(expected) and len(actual) == len(set(actual)))
    require(set(actual) == set(expected))


def login_email(actor):
    require(actor in ("employee", "viewer", "outsider"))
    return "exit2d-" + actor + "@example.com"


def verify_review_rejected(workflow):
    # A failed sync need not materialize the field. Never manufacture it.
    require(workflow["stage"] == "pending_review")
    require(workflow.get("salla_status_sync") != "sent")


def verify_provider_scenario(counts, scenario, order_count):
    require(counts['unexpected'] == 0 and counts['auth_rejected'] == 0)
    if scenario == 'deny':
        require(counts['status_writes'] == 0 and counts['denied'] >= order_count)
        require(counts['status_write_denied'] >= order_count and counts['status_discovery_unavailable'] == 0)
    elif scenario == 'unavailable':
        require(counts['status_writes'] == 0 and counts['denied'] >= order_count)
        require(counts['status_discovery_unavailable'] >= order_count and counts['status_write_denied'] == 0)
    else:
        require(scenario == 'success' and counts['status_writes'] >= order_count)


def verify_login_fixture_schema():
    """Extract only the audited LoginIn class; never import/execute server."""
    from importlib.metadata import version
    from pydantic import BaseModel, EmailStr, ValidationError
    require(version('pydantic') == '2.13.4' and version('email-validator') == '2.3.0')
    tree = ast.parse((backend_root() / 'server.py').read_text(encoding='utf-8'))
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LoginIn']
    require(len(classes) == 1)
    node = classes[0]
    # Fail if future schema code adds executable logic/dependencies to extraction.
    expected = ast.parse('class LoginIn(BaseModel):\n    email: EmailStr\n    password: str\n').body[0]
    require(ast.unparse(node) == ast.unparse(expected))
    unit = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    scope = {'BaseModel': BaseModel, 'EmailStr': EmailStr}
    exec(compile(unit, '<extracted-LoginIn>', 'exec', dont_inherit=True), scope)
    model = scope['LoginIn']
    for actor in ('employee', 'viewer', 'outsider'):
        try:
            model(email=actor + '@exit2d.example.test', password='synthetic')
        except ValidationError:
            pass
        else:
            raise AssertionError('previous fixture unexpectedly accepted')
        require(str(model(email=login_email(actor), password='synthetic').email) == login_email(actor))


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
                 outsider="exit2d-other-owner", files=[], raw_expected={}, expected={}, images={}, catalog_images={})
    database.users.update_one({"id": state["owner"]}, {"$set": {
        "mfa_enabled": True, "mfa_totp_secret_enc": encrypt_totp_secret(a.TOTP)},
        "$unset": {"mfa_last_totp_counter": "", "password_updated_at": ""}})
    from salla_integration.crypto import encrypt_token
    database.salla_integrations.insert_one({"user_id": state["owner"], "store_id": OWNER_MERCHANT, "status": "connected",
        "access_token_encrypted": encrypt_token(os.environ["EXIT2D_SIM_TOKEN"]),
        "expires_at": a.now() + timedelta(hours=1), "token_revision": 1})
    database.salla_integrations.insert_one({'user_id':state['outsider'],'store_id':OTHER_MERCHANT,'status':'connected'})
    for actor, role in (("employee", "operations"), ("viewer", "viewer"), ("outsider", "owner")):
        database.users.insert_one({"id": state[actor], "email": login_email(actor),
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
        raw = order_fixture("raw-" + number)
        for item_index, raw_item in enumerate(raw['items']):
            product = raw_item['product']
            pid, iid = product['id'], raw_item['id']
            options = {o['name']: o['value'] for o in raw_item['options']}
            image_bytes = png((30 + number_index * 80, 40 + item_index * 90, 170))
            image_urls = [image['url'] for image in product['images']]
            for variant, image_url in zip(('a', 'b'), image_urls):
                image_id = pid + '-' + variant
                path = '/api/order-reviews-v1/mezan-images/' + image_id
                require(image_url == 'http://127.0.0.1:8001' + path)
                resource = {'path': path, 'sha256': hashlib.sha256(image_bytes).hexdigest()}
                state['catalog_images'][image_url] = resource
                state['catalog_images'][path] = resource
                database[IMAGES].insert_one({'id': image_id, 'user_id': state['owner'],
                    'product_key': 'product:' + pid, 'content_type': 'image/png',
                    'data_base64': base64.b64encode(image_bytes).decode(),
                    'sha256': resource['sha256'], 'size': len(image_bytes)})
            database.salla_products.insert_one({'user_id': state['owner'], 'product_id': pid,
                'sku': product['sku'], 'name': product['name'], 'main_image': product['main_image'],
                'images': copy.deepcopy(product['images']), 'gallery_refreshed_at': a.now().isoformat()})
            state['raw_expected'][(number, iid)] = {'quantity': raw_item['quantity'], 'options': options, 'product_id': pid,
                'catalog_images': tuple(image_urls + [state['catalog_images'][u]['path'] for u in image_urls])}
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

    def deliver_status_event(self, number):
        """Acceptance is conditional on delivery of this synthetic verified event."""
        require(self.state['provider_scenario']=='success' and number in ORDERS)
        other_before=list(self.database.unified_orders.find({'user_id':self.state['outsider']},{'_id':0}))
        generated_before={name:self.database[name].count_documents({}) for name in GENERATED}
        with check('WEBHOOK_EVENT_SOURCE'):
            response=self.a.httpx.get('http://127.0.0.1:8093/__fixture__/status-event/raw-'+number,
                headers={'Authorization':'Bearer '+os.environ['EXIT2D_SIM_TOKEN']},
                follow_redirects=False,trust_env=False,timeout=3)
            require(response.status_code==200)
            event=response.json()
            require(event.get('event')=='order.status.updated' and event.get('merchant')==OWNER_MERCHANT)
            require(event['data']['reference_id']==number and str(event['data']['id'])=='raw-'+number)
        with check('WEBHOOK_DELIVERY'):
            body,headers=signed_body(event,os.environ.get('SALLA_WEBHOOK_SECRET',''))
            result=self.call('POST','/api/salla/webhooks/app',content=body,headers=headers).json()
            require_ingestion(result)
        with check('WEBHOOK_INGESTION'):
            stored=self.database.unified_orders.find_one({'user_id':self.state['owner'],'order_number':number})
            require(stored is not None and stored.get('order_status')==event['data']['status']['name'])
            require(stored['raw_by_source']['salla_direct']['status']==event['data']['status'])
            require(list(self.database.unified_orders.find({'user_id':self.state['outsider']},{'_id':0}))==other_before)
        with check('WEBHOOK_INTERNAL_EFFECTS'):
            require(all(self.database[name].count_documents({})==count for name,count in generated_before.items()))
            require(self.database.mezan_snapchat_capi_outbox_v1.count_documents({})==0)
            require(self.database.mezan_snapchat_capi_scheduler_v1.count_documents({})==0)

    def invariant(self):
        require(all(r.get("experiment_mode") is (self.state["provider_scenario"] != "success") and r.get("salla_status_writes_allowed") is (self.state["provider_scenario"] == "success")
                    for r in self.rows(WORKFLOWS)))
        require(not self.database.backend_startup_leases_v1.find_one({
            "_id": "backend-heavy-initialization:independent-worker:singleton", "status": "running"}))
        require(self.database.salla_connections.count_documents({}) == 0)
        require(self.database.salla_integrations.count_documents({}) == 2)
        require(self.database.salla_integrations.count_documents({'user_id':self.state['owner'],'store_id':OWNER_MERCHANT}) == 1)
        require(self.database.salla_integrations.count_documents({'user_id':self.state['outsider'],'store_id':OTHER_MERCHANT}) == 1)
        from salla_http_simulator import validate_addresses
        validate_addresses(os.environ)

    def record_provider_counts(self, checkpoint):
        from simulator_evidence import validate_counts
        try:
            result = self.a.httpx.get("http://127.0.0.1:8093/__fixture__/counts", follow_redirects=False, trust_env=False, timeout=2)
            require(result.status_code == 200)
            counts = validate_counts(result.json())
        except Exception:
            counts = None
        self.state.setdefault('_review_evidence', []).append((checkpoint, counts))
        return counts

    def provider_counts(self, checkpoint='AFTER_REVIEW'):
        counts = self.record_provider_counts(checkpoint)
        require(counts is not None)
        require(counts["unexpected"] == 0 and counts['auth_rejected'] == 0)
        self.state["simulated_provider_calls"] = counts["simulated_provider_calls"]
        return counts

    def sessions(self):
        for actor in ("owner", "employee", "viewer", "outsider"):
            with check({"owner": "OWNER_SESSION", "employee": "EMPLOYEE_SESSION",
                        "viewer": "VIEWER_SESSION", "outsider": "OUTSIDER_SESSION"}[actor]):
                response = self.call("GET", "/api/auth/me", actor=actor).json()
                require(response["id"] == self.state[actor])

    def login_otp(self, actor):
        a = self.a
        if actor == "outsider":
            from mfa_security import hotp
            response = a.request(0, "POST", "/api/auth/login", expected=202,
                json={"email": login_email(actor), "password": a.EMPLOYEE_PASSWORD, "force_totp": True})
            require(response.json().get("mfa_required") is True)
            device = a.cookies(response)
            verified = a.request(1, "POST", "/api/auth/mfa/verify", cookie=device,
                json={"challenge_token": response.json()["challenge_token"],
                      "code": hotp(a.TOTP, int(time.time() // 30))})
            self.state[actor + "_cookie"] = a.cookies(verified, device)
            return
        a.seed_otp(self.database, self.state[actor])
        response = a.request(0, "POST", "/api/auth/login", expected=202,
            json={"email": login_email(actor), "password": a.EMPLOYEE_PASSWORD})
        device = a.cookies(response)
        verified = a.request(1, "POST", "/api/auth/email-otp/verify", cookie=device,
            json={"challenge_token": response.json()["challenge_token"], "code": a.OTP})
        self.state[actor + "_cookie"] = a.cookies(verified, device)

    def review(self):
        self.record_provider_counts("BEFORE_REVIEW")
        with check("OWNER_LOGIN"):
            self.state["owner_cookie"] = self.a.owner_session()
        for actor in ("employee", "viewer", "outsider"):
            with check({"employee": "EMPLOYEE_LOGIN", "viewer": "VIEWER_LOGIN", "outsider": "OUTSIDER_LOGIN"}[actor]):
                self.login_otp(actor)
        self.sessions()
        self.record_provider_counts("AFTER_LOGIN")
        with check("REVIEW_INVARIANTS"):
            self.invariant()
        with check("TENANT_SNAPSHOT"):
            self.state["other_workflows"] = list(self.database[WORKFLOWS].find({"user_id": self.state["outsider"]}, {"_id": 0}))
        for number in ORDERS:
            path = "/api/order-reviews-v1/" + number
            self.record_provider_counts("BEFORE_IMAGES")
            with check("ORDER_READ"):
                detail = self.call("GET", path).json()
                require(len(detail["items"]) == 2)
            for item in detail["items"]:
                with check("PRODUCT_IDENTITIES"):
                    iid = item["order_item_id"]
                    source_id = item["source"]["source_order_item_id"]
                    require((number, source_id) in self.state["raw_expected"])
                    expected = self.state["raw_expected"][(number, source_id)]
                with check("QUANTITIES_OPTIONS"):
                    require(item["quantity"] == expected["quantity"])
                    require({o["name"]: o["value"] for o in item["options"]} == expected["options"])
                    self.state["expected"][(number, iid)] = expected
                with check("IMAGE_GALLERY_LINK_SET"):
                    verify_gallery_links(item['gallery'], expected['catalog_images'])
                for url in item['gallery']:
                    with check("IMAGE_RESOURCE_KNOWN"):
                        require(url in self.state['catalog_images'])
                        resource = self.state['catalog_images'][url]
                    with check("IMAGE_HTTP_RESPONSE"):
                        response = self.call('GET', resource['path'])
                    with check("IMAGE_CONTENT_MATCH"):
                        require(hashlib.sha256(response.content).hexdigest() == resource['sha256'])
                    with check("IMAGE_TENANT_DENIAL"):
                        self.call('GET', resource['path'], actor='outsider', expected=404)
                with check("IMAGE_UPLOAD"):
                    image = png((40, 120, 60 if number == ORDERS[0] else 190))
                    updated = self.call("POST", path + "/items/" + quote(iid, safe="") + "/mezan-images",
                        json={"filename": "local.png", "content_type": "image/png",
                              "data_base64": base64.b64encode(image).decode()}).json()
                    gallery = next(i for i in updated["items"] if i["order_item_id"] == iid)["gallery"]
                    fresh = [u for u in gallery if u not in item["gallery"]]
                    require(len(fresh) == 1 and fresh[0].startswith("/api/order-reviews-v1/mezan-images/"))
                    image_url = fresh[0]
                    verify_gallery_links(gallery, [*item['gallery'], image_url])
                with check("IMAGE_HTTP_RESPONSE"):
                    uploaded = self.call('GET', image_url)
                with check("IMAGE_CONTENT_MATCH"):
                    require(hashlib.sha256(uploaded.content).digest() == hashlib.sha256(image).digest())
                with check("IMAGE_CHOICE"):
                    detail = self.call("POST", path + "/items/" + quote(iid, safe="") + "/image-choice",
                        json={"expected_revision": updated["revision"], "selected_image_url": image_url,
                              "mode": "order_only"}).json()
                with check("ITEM_NOTE"):
                    detail = self.call("PATCH", path + "/items/" + quote(iid, safe=""),
                        json={"expected_revision": detail["revision"], "preparation_note": "\u0627\u062e\u062a\u0628\u0627\u0631 \u062a\u062c\u0647\u064a\u0632 \u0645\u062d\u0644\u064a"}).json()
                self.state["images"][(number, iid)] = (image_url, hashlib.sha256(image).hexdigest())
                with check("IMAGE_TENANT_DENIAL"):
                    self.call("GET", image_url, actor="outsider", expected=404)
            self.record_provider_counts("AFTER_IMAGES")
            with check("REVIEW_ROLE_DENIAL"):
                self.call("POST", path + "/complete", actor="viewer", expected=403,
                          json={"expected_revision": detail["revision"]})
            self.record_provider_counts("BEFORE_COMPLETE")
            # Actual application route and client. Successful provider replies
            # are fixture-only; the denied fixtures never manufacture sent.
            if self.state["provider_scenario"] != "success":
                with check("REVIEW_COMPLETE"):
                    response = self.call("POST", path + "/complete", expected=502,
                        json={"expected_revision": detail["revision"]}).json()
                with check("REVIEW_ERROR_CODE"):
                    require(response["detail"]["code"] == "salla_review_status_sync_failed")
                with check("REVIEW_STORED_STATE"):
                    workflow = self.database[WORKFLOWS].find_one({"user_id": self.state["owner"], "order_number": number})
                    verify_review_rejected(workflow)
                with check("NO_PREPARATION_ENTITIES"):
                    require(all(self.database[name].count_documents({}) == 0 for name in GENERATED))
            else:
                with check("REVIEW_COMPLETE"):
                    self.call("POST", path + "/complete", json={"expected_revision": detail["revision"]})
                self.deliver_status_event(number)
            self.record_provider_counts("AFTER_COMPLETE")
        with check("PROVIDER_COUNTERS"):
            counts = self.provider_counts()
            verify_provider_scenario(counts, self.state['provider_scenario'], len(ORDERS))
        with check("REVIEW_INVARIANTS"):
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
        with check('SAFE_DRAFT'):
            self.draft(request_id, len(selections))
        payload = {"client_request_id": request_id, "selections": selections}
        with check('FILE_CREATE'):
            response = self.call("POST", "/api/reviewed-preparation-batches-v1/batches", json=payload).json()
        # Match the actual service fallback; never mandate finalize on success.
        if not (response.get("file_registered") is True and response.get("registry_status") == "ready"
                and response.get("piece_registry_status") == "ready"):
            with check('FINALIZE_FALLBACK'):
                response.update(self.call("POST", "/api/preparation-file-registry-v1/finalize/" + request_id).json())
        with check('FILE_CREATE_REPEAT'):
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
        with check('RESUME_INVARIANTS'):
            self.invariant()
        with check('SNAPSHOT_IDENTITY'):
            current = self.snapshot()
            from snapshot_evidence import snapshot_lines
            try:
                self.state['_snapshot_evidence'] = snapshot_lines(self.state['checkpoint'], current)
            except Exception:
                self.state['_snapshot_evidence'] = ['SNAPSHOT unavailable']
            require(current['identity'] == self.state['checkpoint']['identity'])
        with check('SNAPSHOT_MATCH'):
            # Keep full document/list equality, including timestamps and order.
            require(current == self.state["checkpoint"])
        with check('OTHER_TENANT_MATCH'):
            require(list(self.database[WORKFLOWS].find({"user_id": self.state["outsider"]}, {"_id": 0})) == self.state["other_workflows"])
        self.pdf_and_images()

    def pdf_and_images(self):
        import fitz
        with check('IMAGE_PERSISTENCE'):
            for url, digest in self.state["images"].values():
                require(hashlib.sha256(self.call("GET", url).content).hexdigest() == digest)
        with check('FILE_REGISTRY_READ'):
            files = self.call("GET", "/api/preparation-file-registry-v1/files").json()["items"]
        for file in self.state["files"]:
            with check('FILE_REGISTRY_READ'):
                require(any(r["file_number"] == file["file_number"] and r["batch_id"] == file["batch_id"] for r in files))
            with check('PDF_HTTP'):
                pdf = self.call("GET", "/api/reviewed-preparation-batches-v1/batches/" + file["batch_id"] + "/pdf")
            with check('PDF_CONTENT'):
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
            with check('PDF_TENANT_DENIAL'):
                self.call("GET", "/api/reviewed-preparation-batches-v1/batches/" + file["batch_id"] + "/pdf",
                          actor="outsider", expected=404)

    def create(self):
        with check('EMPLOYEE_CATALOG'):
            employees = self.call("GET", "/api/preparation-file-registry-v1/employees").json()["items"]
            require(any(e["id"] == self.state["employee"] for e in employees))
        with check('INITIAL_CATALOG'):
            catalog = self.catalog()
            require(len(catalog) == 8)  # Four source lines, two physical units each.
            selections = [{"group_key": p["group_key"], "revision": p["revision"], "quantity": 1} for p in catalog]
            self.state["old_selection"] = selections[0]
        first = self.build("exit2d-partial-0001", selections[:1])
        with check('EARLY_START_DENIAL'):
            self.call("POST", "/api/preparation-work-v1/files/" + first["file_number"] + "/start",
                      expected=409, json={})
        with check('DRAFT_ROLE_DENIAL'):
            self.call("POST", "/api/preparation-file-safety-v1/drafts", actor="viewer", expected=403,
                      json={"client_request_id": "exit2d-denied-0001", "file_title": "\u0627\u062e\u062a\u0628\u0627\u0631",
                            "responsible_employee_id": self.state["employee"], "expected_quantity": 1,
                            "selected_product_count": 1})
        self.pdf_and_images()
        # Incomplete draft/finalize is produced through HTTP before restart.
        with check('INCOMPLETE_DRAFT'):
            self.draft("exit2d-incomplete-0001", 1)
        with check('INCOMPLETE_FINALIZE_DENIAL'):
            self.call("POST", "/api/preparation-file-registry-v1/finalize/exit2d-incomplete-0001", expected=409)
        with check('CHECKPOINT_CAPTURE'):
            self.state["checkpoint"] = self.snapshot()
        self.record_provider_counts('AFTER_CREATE')

    def recover(self):
        # Real draft + premature finalize, not a fabricated ready batch/failure.
        with check('INCOMPLETE_RELEASE'):
            released = self.call("POST", "/api/preparation-file-safety-v1/requests/exit2d-incomplete-0001/release").json()
            require(released["released"] is True)
            require(not any(r["client_request_id"] == "exit2d-incomplete-0001" for r in self.rows(REGISTRY)))
        for file in self.state["files"]:
            with check('COMPLETED_RELEASE_DENIAL'):
                before = self.identity()
                kept = self.call("POST", "/api/preparation-file-safety-v1/requests/" + file["request_id"] + "/release").json()
                require(kept["released"] is False and self.identity() == before)

    def resume(self):
        self.record_provider_counts('BEFORE_RESUME')
        self.persistence()
        self.record_provider_counts('AFTER_PERSISTENCE')
        self.recover()
        self.record_provider_counts('AFTER_RECOVERY')
        # Old selector must not reclaim its already allocated physical unit.
        with check('REALLOCATION_DRAFT'):
            allocated_before = self.identity()
            self.draft("exit2d-reallocate-0001", 1)
        with check('REALLOCATION_DENIAL'):
            rejection = self.call("POST", "/api/reviewed-preparation-batches-v1/batches", expected=409,
                      json={"client_request_id": "exit2d-reallocate-0001", "selections": [self.state["old_selection"]]}).json()
            require(rejection["detail"]["code"] in {"reviewed_product_not_available", "reviewed_selection_stale", "preparation_quantity_exceeds_remaining"})
        self.state["allocated_unit_rejection"] = rejection["detail"]["code"]
        with check('REALLOCATION_RELEASE'):
            self.call("POST", "/api/preparation-file-safety-v1/requests/exit2d-reallocate-0001/release")
            require(self.identity() == allocated_before)
        self.record_provider_counts('AFTER_REALLOCATION')
        with check('REMAINING_CATALOG'):
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
        with check('ASSIGNMENT_STATES'):
            require(all(w["preparation_assignment_status"] == "assigned" for w in self.rows(WORKFLOWS)))
        with check('ASSIGNMENT_STATES'):
            require(all(w["preparation_progress"]["remaining_quantity"] == 0 for w in self.rows(WORKFLOWS)))
        verify_units(self.state["expected"], self.rows(ALLOCATIONS), self.rows(PIECES), self.state["employee"],
                     self.rows(BATCHES), self.rows(REGISTRY), self.state["files"], state=self.state)
        self.record_provider_counts('AFTER_SECOND_FILE')
        with check('EMPLOYEE_START'):
            first = self.state["files"][0]
            start_path = "/api/preparation-work-v1/files/" + first["file_number"] + "/start"
            started = self.call("POST", start_path, actor="employee", json={}).json()
            require(started["mezan_only"] is True and started["salla_updated"] is False)
        with check('START_REPEAT'):
            events = self.rows(EVENTS)
            self.call("POST", start_path, actor="employee", json={})
            require(self.rows(EVENTS) == events)
        self.record_provider_counts('AFTER_START')
        self.dispatch_receive()
        self.pdf_and_images()
        with check('CHECKPOINT_CAPTURE'):
            self.state["checkpoint"] = self.snapshot()
        self.record_provider_counts('AFTER_RESUME')

    def finish(self):
        self.record_provider_counts('BEFORE_FINISH')
        self.persistence()
        self.record_provider_counts('AFTER_FINAL_PERSISTENCE')
        # Revision reachability remains a separate, explicitly unaccepted gate.
        # Never turn an unavailable-unit rejection into a stale-revision claim.
        with check('FINAL_PROVIDER_COUNTERS'):
            counts = self.provider_counts('AFTER_FINISH')
        with check('FINAL_LABEL_COUNTS'):
            require(counts["shipping_attempted"] == len(ORDERS))
            require(counts["shipping_failed"] == len(ORDERS))

    def dispatch_receive(self):
        for file in self.state["files"]:
            from supplier_workspace_contract import workspace_payload
            payload = workspace_payload(self.call, file, self.rows(PIECES), self.state['employee'], self.state)
            with check('SUPPLIER_DISPATCH'):
                result = self.call("POST", "/api/supplier-dispatch-v1/dispatches", actor="employee", expected=201, json=payload).json()
                dispatch_id = result["dispatch"]["id"]
            with check('SUPPLIER_DISPATCH_REPEAT'):
                event_before = self.rows(EVENTS)
                repeat = self.call("POST", "/api/supplier-dispatch-v1/dispatches", actor="employee", expected=201, json=payload).json()
                require(repeat["dispatch"]["id"] == dispatch_id and self.rows(EVENTS) == event_before)
            with check('SUPPLIER_READY'):
                self.call("POST", "/api/supplier-dispatch-v1/dispatches/" + dispatch_id + "/ready", actor="employee", json={})
        self.record_provider_counts('AFTER_DISPATCH')
        for piece in self.rows(PIECES):
            with check('SUPPLIER_PIECE_IDENTITY'):
                require(piece["supplier_id"] == "exit2d-supplier")
                require(piece["responsible_employee_id"] == self.state["employee"])
            with check('RECEIVING_SEARCH'):
                self.call("GET", "/api/preparation-work-v1/receiving/search", params={"q": piece["piece_id"]})
            with check('PIECE_RECEIVE'):
                path = "/api/preparation-work-v1/receiving/pieces/" + piece["piece_id"] + "/receive"
                payload = {"client_request_id": "receive-" + piece["piece_id"]}
                self.call("POST", path, json=payload)
            with check('RECEIVE_REPEAT'):
                before, events = self.identity(), self.rows(EVENTS)
                self.call("POST", path, json=payload)
                require(self.identity() == before and self.rows(EVENTS) == events)
        self.record_provider_counts('AFTER_RECEIVE')
        with check('FILE_COMPLETED_STATE'):
            require(all(r["execution_status"] == "completed" for r in self.rows(REGISTRY)))
        for piece in self.rows(PIECES):
            with check('ASSEMBLY_SEARCH'):
                self.call("GET", "/api/preparation-work-v1/assembly/search", params={"q": piece["order_number"]})
            # The real route attempts a label. The simulator rejects its
            # first authoritative order lookup; no shipment is fabricated.
            with check('PIECE_ASSEMBLY'):
                result = self.call("POST", "/api/preparation-work-v1/assembly/pieces/" + piece["piece_id"] + "/ready",
                          json={"client_request_id": "assembly-" + piece["piece_id"]}).json()
            if result["progress"].get("order_completed"):
                with check('SIMULATED_LABEL_FAILURE'):
                    require(result["carrier_label"]["ready"] is False)
                    require(result["carrier_label"]["error_code"] == "salla_shipping_unavailable")
        with check('ASSEMBLY_STATES'):
            require(all(w.get("stage") == "completed" and w.get("assembly_status") == "completed" and w.get("carrier_label_ready") is False and w.get("carrier_label_status") == "failed"
                        for w in self.rows(WORKFLOWS)))
        with check('RESUME_PROVIDER_COUNTERS'):
            self.provider_counts('AFTER_ASSEMBLY')
        with check('RESUME_INVARIANTS'):
            self.invariant()


def identity(allocations, pieces, registries):
    def units(rows):
        return sorted((r["order_number"], r["order_item_id"], r["unit_index"], r["batch_id"]) for r in rows)
    return (units(allocations), units(pieces), sorted((r["client_request_id"], r["file_number"], r.get("batch_id")) for r in registries))


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
