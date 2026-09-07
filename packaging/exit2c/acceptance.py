"""Synthetic, real HTTP/Mongo acceptance; never imports the application server.

One controller keeps sessions in memory across setup, HTTP, Mongo outage and
web restart. Its stdin/stdout protocol contains only fixed phase names/status.
No access token is fabricated: sessions come from the real password + second
factor HTTP handlers. OTP transport and WebAuthn hardware are fixture boundaries.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie

import httpx
from pymongo import MongoClient

sys.path.insert(0, "/opt/mezan/backend")

JWT = "exit2c-public-synthetic-key-not-for-production"
OWNER_PASSWORD = "Exit2C-synthetic-owner-password!"
EMPLOYEE_PASSWORD = "Exit2C-synthetic-employee-password!"
TOTP = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
OTP = "419263"
ORIGIN = "https://mezansalla.com"  # Origin header only; all requests use loopback.
URLS = ("http://127.0.0.1:8001", "http://127.0.0.1:8002")
REGISTRY = "mezan_preparation_file_registry_v2"
BATCHES = "mezan_preparation_batches_v2"
PIECES = "mezan_preparation_pieces_v1"
EVENTS = "mezan_preparation_piece_events_v1"


def boundary():
    assert os.environ.get("JWT_SECRET") == JWT
    assert os.environ.get("DB_NAME") == "mezan_exit2c"
    assert os.environ.get("MONGO_URL") == "mongodb://127.0.0.1:27017"
    assert {name for _, name in socket.if_nameindex()} == {"lo"}
    # Documentation-only address: proves network namespace has no external path.
    try:
        socket.create_connection(("198.51.100.1", 443), timeout=0.3).close()
    except OSError:
        pass
    else:
        raise AssertionError("External network path unexpectedly available")


def db():
    return MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=1500)["mezan_exit2c"]


def now():
    return datetime.now(timezone.utc)


def request(port, method, path, *, cookie="", expected=200, **kwargs):
    headers = {"Origin": ORIGIN, "X-Forwarded-Proto": "https"}
    if cookie:
        headers["Cookie"] = cookie
    headers.update(kwargs.pop("headers", {}))
    # Fresh transport every call; do not silently retain cookies across users.
    with httpx.Client(trust_env=False, timeout=12) as client:
        response = client.request(method, URLS[port] + path, headers=headers, **kwargs)
    if response.status_code != expected:
        try:
            detail = response.json().get("detail")
            hint = [{"loc": e.get("loc"), "type": e.get("type")} for e in detail] if isinstance(detail, list) else (detail.get("code") if isinstance(detail, dict) else None)
        except Exception:
            hint = None
        raise AssertionError((method, path, response.status_code, expected, hint))
    return response


def cookies(response, previous=""):
    parsed = SimpleCookie()
    parsed.load(previous)
    for value in response.headers.get_list("set-cookie"):
        parsed.load(value)
    return "; ".join(f"{key}={value.value}" for key, value in parsed.items() if value.value)


def seed_otp(database, employee):
    from email_otp_security import otp_digest
    current = now()
    database.auth_email_otp_challenges.replace_one({"jti": "exit2c-otp"}, {
        "jti": "exit2c-otp", "user_id": employee,
        "otp_hash": otp_digest("exit2c-otp", OTP), "failures": 0,
        "resend_count": 0, "created_at": current, "sent_at": current,
        "expires_at": current + timedelta(minutes=5),
    }, upsert=True)


def setup(state):
    from auth import hash_password
    from mfa_security import encrypt_totp_secret
    database = db()
    owner = database.users.find_one({"email": "admin@hesab.app"})
    assert owner and owner["role"] == "owner", "Separate migration must create owner"
    database.users.update_one({"id": owner["id"]}, {"$set": {
        "mfa_enabled": True, "mfa_totp_secret_enc": encrypt_totp_secret(TOTP),
    }, "$unset": {"mfa_last_totp_counter": "", "password_updated_at": ""}})
    database.qoyod_settings.insert_one({"user_id": "main", "enabled": True,
        "auto_send": True, "dry_run_mode": False, "legacy_pipeline_frozen": True})
    employee = "exit2c-employee"
    database.users.insert_one({"id": employee, "email": "employee@example.com",
        "name": "Synthetic employee", "password_hash": hash_password(EMPLOYEE_PASSWORD),
        "role": "viewer", "created_by": owner["id"], "is_active": True})
    line = {"order_number": "EXIT2C-ORDER", "order_date": "2026-09-06",
        "product_name": "منتج اصطناعي", "customer_name": "عميل اصطناعي",
        "quantity": 1, "total_products_in_order": 1, "item_index": 0,
        "sku": "EXIT2C-SKU", "product_id": "exit2c-product", "note": "اختبار محلي"}
    database.preparation_uploads.insert_one({"user_id": owner["id"],
        "upload_id": "exit2c-upload", "filename": "synthetic.pdf", "lines": [line],
        "uploaded_at": now().isoformat()})
    database[REGISTRY].insert_one({"user_id": owner["id"], "id": "exit2c-registry",
        "client_request_id": "exit2c-request", "file_number": "PF-EXIT2C-0001",
        "batch_id": "exit2c-batch", "status": "ready", "execution_status": "assigned",
        "responsible_employee_id": owner["id"], "file_title": "تجهيز اصطناعي",
        "file_date": "2026-09-06", "allocated_quantity": 1})
    database[BATCHES].insert_one({"user_id": owner["id"], "id": "exit2c-batch",
        "execution_status": "assigned", "status": "ready", "lines": [line]})
    database[PIECES].insert_one({"user_id": owner["id"], "piece_id": "exit2c-piece",
        "batch_id": "exit2c-batch", "order_number": "EXIT2C-ORDER",
        "order_item_id": "exit2c-item", "status": "assigned"})
    database.order_review_workflows.insert_one({"user_id": owner["id"],
        "order_number": "EXIT2C-ORDER", "stage": "reviewed", "revision": 1})
    state.update({"owner": owner["id"], "employee": employee})
    print("PASS synthetic fixture setup after separate migration")


def owner_session():
    from mfa_security import hotp
    response = request(0, "POST", "/api/auth/login", expected=202,
        json={"email": "admin@hesab.app", "password": OWNER_PASSWORD, "force_totp": True})
    assert response.json().get("mfa_required") is True
    device = cookies(response)
    request(1, "GET", "/api/auth/me", cookie=device, expected=401)
    token = response.json()["challenge_token"]
    request(0, "POST", "/api/auth/mfa/verify", cookie=device, expected=401,
        json={"challenge_token": token, "code": "not-a-code"})
    code = hotp(TOTP, int(time.time() // 30))
    verified = request(1, "POST", "/api/auth/mfa/verify", cookie=device,
        json={"challenge_token": token, "code": code})
    request(0, "POST", "/api/auth/mfa/verify", cookie=device, expected=401,
        json={"challenge_token": token, "code": code})
    result = cookies(verified, device)
    assert "access_token=" in result and "refresh_token=" in result
    for port in range(2):
        me = request(port, "GET", "/api/auth/me", cookie=result)
        assert me.json()["role"] == "owner"
        assert me.headers["x-content-type-options"] == "nosniff"
    print("PASS password -> owner MFA across two processes; invalid/replay rejected")
    refreshed = request(1, "POST", "/api/auth/refresh", cookie=result)
    for name in ("access_token=", "refresh_token="):
        header = next(h.lower() for h in refreshed.headers.get_list("set-cookie") if h.startswith(name))
        assert "httponly" in header and "secure" in header and "samesite=none" in header
    result = cookies(refreshed, result)
    request(0, "GET", "/api/auth/me", cookie=result)
    return result


def employee_session(state):
    database = db()
    # Recent challenge is exactly the normal resend cooldown boundary; no SMTP
    # function is patched. Password login still signs the challenge itself.
    seed_otp(database, state["employee"])
    response = request(0, "POST", "/api/auth/login", expected=202,
        json={"email": "employee@example.com", "password": EMPLOYEE_PASSWORD})
    token = response.json()["challenge_token"]
    device = cookies(response)
    request(0, "GET", "/api/auth/me", cookie=device, expected=401)
    request(0, "POST", "/api/auth/email-otp/verify", cookie=device, expected=401,
        json={"challenge_token": token, "code": "000000"})
    response = request(1, "POST", "/api/auth/email-otp/verify", cookie=device,
        json={"challenge_token": token, "code": OTP})
    request(0, "POST", "/api/auth/email-otp/verify", cookie=device, expected=401,
        json={"challenge_token": token, "code": OTP})
    assert database.auth_email_otp_challenges.count_documents({"jti": "exit2c-otp"}) == 0
    print("PASS employee password + real OTP verifier; invalid and replay rejected")
    return cookies(response, device)


def passkey_session(state):
    import cbor2
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from passkey_security import _configured_origin, _rp_id
    b64 = lambda data: base64.urlsafe_b64encode(data).decode().rstrip("=")
    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.public_key().public_numbers()
    public = cbor2.dumps({1: 2, 3: -7, -1: 1,
        -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
    device_token = "exit2c-synthetic-passkey-device"
    device_hash = hmac.new(JWT.encode(), f"device:{device_token}".encode(), hashlib.sha256).hexdigest()
    cookie = f"mezan_device_id={device_token}.{device_hash}"
    credential_id = b64(b"exit2c-synthetic-credential")
    database = db()
    database.auth_passkey_credentials.insert_one({"user_id": state["owner"],
        "device_hash": device_hash, "credential_id_b64": credential_id,
        "credential_public_key_b64": b64(public), "sign_count": 0,
        "trust_expires_at": now() + timedelta(days=1)})
    response = request(0, "POST", "/api/auth/login", cookie=cookie, expected=202,
        json={"email": "admin@hesab.app", "password": OWNER_PASSWORD})
    data = response.json()
    assert data.get("passkey_required") is True
    client_data = json.dumps({"type": "webauthn.get",
        "challenge": data["webauthn_options"]["challenge"],
        "origin": _configured_origin(), "crossOrigin": False}, separators=(",", ":")).encode()
    auth_data = hashlib.sha256(_rp_id().encode()).digest() + b"\x05" + (1).to_bytes(4, "big")
    signature = key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
    credential = {"id": credential_id, "rawId": credential_id, "type": "public-key",
        "response": {"authenticatorData": b64(auth_data), "clientDataJSON": b64(client_data),
            "signature": b64(b"invalid-signature"), "userHandle": None}}
    payload = {"challenge_id": data["challenge_id"], "credential": credential}
    request(0, "POST", "/api/auth/passkey/authenticate/verify", cookie=cookie,
        expected=401, json=payload)
    credential["response"]["signature"] = b64(signature)
    response = request(1, "POST", "/api/auth/passkey/authenticate/verify", cookie=cookie, json=payload)
    request(0, "POST", "/api/auth/passkey/authenticate/verify", cookie=cookie,
        expected=401, json=payload)
    assert database.auth_passkey_credentials.find_one({"credential_id_b64": credential_id})["sign_count"] == 1
    request(0, "GET", "/api/auth/me", cookie=cookies(response, cookie))
    print("PASS real WebAuthn cryptographic verifier; invalid signature, cross-process success, replay denial (synthetic authenticator)")


def http(state):
    from PIL import Image
    import fitz
    for port in range(2):
        request(port, "GET", "/api/ready")
        request(port, "GET", "/api/auth/me", expected=401)
        allowed = request(port, "OPTIONS", "/api/auth/me", headers={
            "Access-Control-Request-Method": "GET"})
        assert allowed.headers["access-control-allow-origin"] == ORIGIN
        denied = request(port, "OPTIONS", "/api/auth/me", expected=400, headers={
            "Origin": "https://evil.example.test", "Access-Control-Request-Method": "GET"})
        assert "access-control-allow-origin" not in denied.headers
    assert db().qoyod_settings.find_one({"user_id": "main"})["auto_send"] is True
    state["owner_cookie"] = owner_session()
    state["employee_cookie"] = employee_session(state)
    passkey_session(state)
    owner, employee = state["owner_cookie"], state["employee_cookie"]
    request(0, "PUT", "/api/auth/profile/name", cookie=owner, expected=403,
        headers={"Origin": "https://evil.example.test", "Sec-Fetch-Site": "cross-site"},
        json={"name": "must not persist"})
    request(1, "GET", "/api/preparation-work-v1/manager/summary", cookie=employee, expected=403)
    request(1, "GET", "/api/preparation/preview/exit2c-upload", cookie=employee, expected=404)
    image = io.BytesIO()
    Image.new("RGB", (32, 32), (20, 110, 190)).save(image, format="PNG")
    request(0, "PUT", "/api/preparation/image/exit2c-upload/0?scope=line", cookie=owner,
        files={"file": ("synthetic.png", image.getvalue(), "image/png")})
    attached = request(1, "GET", "/api/preparation/image/exit2c-upload/0", cookie=owner)
    state["image_sha256"] = hashlib.sha256(attached.content).hexdigest()
    request(0, "POST", "/api/preparation-work-v1/files/PF-EXIT2C-0001/start",
        cookie=owner, expected=409, json={"note": "Incomplete allocation must block"})
    db().order_review_workflows.update_one({"user_id": state["owner"], "order_number": "EXIT2C-ORDER"},
        {"$set": {"preparation_assignment_status": "assigned", "preparation_progress": {
            "required_quantity": 1, "allocated_quantity": 1, "remaining_quantity": 0}}})
    started = request(0, "POST", "/api/preparation-work-v1/files/PF-EXIT2C-0001/start",
        cookie=owner, json={"note": "Synthetic execution start"})
    assert started.json()["mezan_only"] and not started.json()["salla_updated"]
    request(1, "POST", "/api/preparation-work-v1/files/PF-EXIT2C-0001/start",
        cookie=owner, json={"note": "Duplicate must not add history"})
    assert db()[EVENTS].count_documents({"file_number": "PF-EXIT2C-0001"}) == 1
    pdf = request(1, "POST", "/api/preparation/generate/exit2c-upload", cookie=owner, json={})
    assert pdf.content.startswith(b"%PDF")
    with fitz.open(stream=pdf.content, filetype="pdf") as document:
        assert document.page_count >= 1
    # This legacy route streams PDF; it persists inputs and export history,
    # not PDF bytes. Verify its true contract rather than inventing blob storage.
    assert db().exported_items.count_documents({"upload_id": "exit2c-upload"}) == 1
    batch_pdf = request(0, "GET", "/api/reviewed-preparation-batches-v1/batches/exit2c-batch/pdf", cookie=owner)
    with fitz.open(stream=batch_pdf.content, filetype="pdf") as document:
        state["batch_pdf_pages"] = document.page_count
        assert document.page_count >= 1
    print("PASS CSRF, RBAC, user isolation, real attachment upload and PDF generation, preparation transition and idempotent start")


def after_restart(state):
    import fitz
    owner = state["owner_cookie"]
    database = db()
    for port in range(2):
        request(port, "GET", "/api/ready")
        request(port, "GET", "/api/auth/me", cookie=owner)
        request(port, "GET", "/api/auth/me", cookie=state["employee_cookie"])
        attachment = request(port, "GET", "/api/preparation/image/exit2c-upload/0", cookie=owner)
        assert hashlib.sha256(attachment.content).hexdigest() == state["image_sha256"]
        preview = request(port, "GET", "/api/preparation/preview/exit2c-upload", cookie=owner)
        assert preview.json()["excluded_items_count"] == 1
        request(port, "POST", "/api/preparation/generate/exit2c-upload", cookie=owner,
            expected=400, json={})
        batch_pdf = request(port, "GET", "/api/reviewed-preparation-batches-v1/batches/exit2c-batch/pdf", cookie=owner)
        with fitz.open(stream=batch_pdf.content, filetype="pdf") as document:
            assert document.page_count == state["batch_pdf_pages"]
    assert database[REGISTRY].find_one({"file_number": "PF-EXIT2C-0001"})["execution_status"] == "in_progress"
    assert database[PIECES].find_one({"piece_id": "exit2c-piece"})["status"] == "in_progress"
    assert database[EVENTS].count_documents({"file_number": "PF-EXIT2C-0001"}) == 1
    # Negative-only expired copies of real session payloads: never accepted
    # tokens or fake guard success. Genuine JWT expiry is checked over HTTP.
    import jwt
    parsed = SimpleCookie()
    parsed.load(state["employee_cookie"])
    expired = []
    for name in ("access_token", "refresh_token"):
        payload = jwt.decode(parsed[name].value, JWT, algorithms=["HS256"])
        payload["exp"] = int(time.time()) - 60
        expired.append(f"{name}=" + jwt.encode(payload, JWT, algorithm="HS256"))
    for port in range(2):
        request(port, "GET", "/api/auth/me", cookie="; ".join(expired), expected=401)
        request(port, "POST", "/api/auth/refresh", cookie="; ".join(expired), expected=401)
    # Password-update revocation is checked from shared DB by both web processes.
    database.users.update_one({"id": state["employee"]}, {"$set": {
        "password_updated_at": (now() + timedelta(seconds=2)).isoformat()}})
    for port in range(2):
        request(port, "GET", "/api/auth/me", cookie=state["employee_cookie"], expected=401)
        request(port, "POST", "/api/auth/refresh", cookie=state["employee_cookie"], expected=401)
    print("PASS restart preserves sessions, attachment bytes, preparation state/history and export deduplication; shared session revocation")


def mongo_down(state):
    for port in range(2):
        request(port, "GET", "/api/live")
        request(port, "GET", "/api/ready", expected=503)
        response = request(port, "GET", "/api/auth/me", cookie=state["owner_cookie"], expected=503)
        assert not response.headers.get_list("set-cookie"), "Mongo outage must not erase session"
    print("PASS unavailable Mongo yields bounded readiness/auth 503 without deleting browser session")


if __name__ == "__main__":
    boundary()
    phases = {"setup": setup, "http": http, "after-restart": after_restart, "mongo-down": mongo_down}
    from acceptance_controller import run
    raise SystemExit(run(phases))
