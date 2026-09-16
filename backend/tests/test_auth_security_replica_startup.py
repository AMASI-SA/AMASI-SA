from __future__ import annotations

import ast
import asyncio
import json
import sys
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import auth
import email_otp_security as email_otp
import mfa_security as owner_mfa
from fastapi import HTTPException
from starlette.responses import Response


SERVER_PATH = Path("backend/server.py")
AUTH_PATH = Path("backend/auth.py")


@pytest.fixture(autouse=True)
def _isolated_jwt_secret(monkeypatch):
    monkeypatch.setenv(
        "JWT_SECRET",
        "test-only-replica-auth-secret-at-least-32-bytes",
    )


def _async_function(path: Path, name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name
    )


def _call_position(function: ast.AsyncFunctionDef, name: str) -> tuple[int, int]:
    call = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and (
            isinstance(node.func, ast.Name) and node.func.id == name
            or isinstance(node.func, ast.Attribute) and node.func.attr == name
        )
    )
    return call.lineno, call.col_offset


def _ready_assignment_position(function: ast.AsyncFunctionDef) -> tuple[int, int]:
    assignment = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and target.attr == "readiness"
            for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and node.value.value == "ready"
    )
    return assignment.lineno, assignment.col_offset


def test_every_replica_installs_auth_guards_before_advertising_readiness():
    local_startup = _async_function(SERVER_PATH, "_local_startup")

    assert _call_position(
        local_startup,
        "install_process_local_auth_security",
    ) < _ready_assignment_position(local_startup)


def test_auth_guard_install_failure_cannot_be_swallowed_before_readiness():
    local_startup = _async_function(SERVER_PATH, "_local_startup")
    install_line, _ = _call_position(
        local_startup,
        "install_process_local_auth_security",
    )

    protected_by_try = any(
        isinstance(node, ast.Try)
        and node.lineno <= install_line <= max(
            (child.end_lineno or child.lineno) for child in node.body
        )
        for node in ast.walk(local_startup)
    )
    assert protected_by_try is False


def test_release_global_seed_and_replica_local_startup_share_same_installer():
    seed_admin = _async_function(AUTH_PATH, "seed_admin")
    compatibility_alias = _async_function(
        AUTH_PATH,
        "_install_login_security_for_loaded_app",
    )
    local_startup = _async_function(SERVER_PATH, "_local_startup")

    assert _call_position(seed_admin, "_install_login_security_for_loaded_app")
    assert _call_position(
        compatibility_alias,
        "install_process_local_auth_security",
    )
    assert _call_position(local_startup, "install_process_local_auth_security")


@pytest.mark.asyncio
async def test_process_local_installer_attaches_every_auth_layer_in_order(monkeypatch):
    calls = []
    fake_app = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setitem(sys.modules, "server", SimpleNamespace(app=fake_app))

    installers = [
        ("meta_reviewer_bootstrap", "install_meta_reviewer_bootstrap", False),
        ("mobile_session_security", "install_mobile_session_security", True),
        ("progressive_login_security", "install_progressive_login_security", True),
        ("login_security", "install_login_security", True),
        ("passkey_security", "install_passkey_security", True),
        ("mfa_security", "install_mfa_security", True),
        ("email_otp_security", "install_email_otp_security", True),
    ]
    for module_name, function_name, is_async in installers:
        if is_async:
            async def installer(app, db, name=function_name):
                assert app is fake_app
                calls.append(name)
        else:
            def installer(app, db, name=function_name):
                assert app is fake_app
                calls.append(name)
        monkeypatch.setitem(
            sys.modules,
            module_name,
            SimpleNamespace(**{function_name: installer}),
        )

    await auth.install_process_local_auth_security(object())

    assert calls == [function_name for _, function_name, _ in installers]


@pytest.mark.asyncio
async def test_process_local_installer_propagates_layer_failure(monkeypatch):
    fake_app = SimpleNamespace(state=SimpleNamespace())
    monkeypatch.setitem(sys.modules, "server", SimpleNamespace(app=fake_app))

    def install_meta_reviewer_bootstrap(app, db):
        pass

    async def install_mobile_session_security(app, db):
        raise RuntimeError("index setup failed")

    monkeypatch.setitem(
        sys.modules,
        "meta_reviewer_bootstrap",
        SimpleNamespace(install_meta_reviewer_bootstrap=install_meta_reviewer_bootstrap),
    )
    monkeypatch.setitem(
        sys.modules,
        "mobile_session_security",
        SimpleNamespace(install_mobile_session_security=install_mobile_session_security),
    )
    for module_name, function_name in (
        ("progressive_login_security", "install_progressive_login_security"),
        ("login_security", "install_login_security"),
        ("passkey_security", "install_passkey_security"),
        ("mfa_security", "install_mfa_security"),
        ("email_otp_security", "install_email_otp_security"),
    ):
        async def unused_installer(app, db):
            raise AssertionError("installer after the failure must not run")
        monkeypatch.setitem(
            sys.modules,
            module_name,
            SimpleNamespace(**{function_name: unused_installer}),
        )

    with pytest.raises(RuntimeError, match="index setup failed"):
        await auth.install_process_local_auth_security(object())


class _Users:
    def __init__(self, user):
        self.user = user

    async def find_one(self, query):
        return dict(self.user) if query.get("id") == self.user["id"] else None


class _Db:
    def __init__(self, user):
        self.users = _Users(user)


class _Request:
    def __init__(self, token):
        self.cookies = {"access_token": token}
        self.headers = {}
        self.url = SimpleNamespace(path="/api/auth/me")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["viewer", "owner"])
async def test_password_only_login_token_is_rejected_by_auth_me_policy(role):
    user = {"id": f"{role}-1", "email": f"{role}@example.test", "role": role}
    token = auth.create_access_token(user["id"], user["email"], mfa_verified=False)

    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user_from_db(_Request(token), _Db(user))

    assert exc.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["viewer", "owner"])
async def test_second_factor_login_token_is_accepted_by_auth_me_policy(role):
    user = {"id": f"{role}-1", "email": f"{role}@example.test", "role": role}
    token = auth.create_access_token(user["id"], user["email"], mfa_verified=True)

    result = await auth.get_current_user_from_db(_Request(token), _Db(user))

    assert result["id"] == user["id"]


async def _asgi_request(app, path, payload):
    body = json.dumps(payload).encode()
    delivered = False
    sent = []

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(
        {"type": "http", "method": "POST", "path": path, "headers": []},
        receive,
        send,
    )
    start = next(item for item in sent if item["type"] == "http.response.start")
    response_body = json.loads(
        next(item for item in sent if item["type"] == "http.response.body")["body"]
    )
    return start, response_body


class _AuthStackUsers:
    def __init__(self, user):
        self.user = user

    async def find_one(self, query):
        key = "email" if "email" in query else "id"
        return dict(self.user) if query.get(key) == self.user.get(key) else None

    async def update_one(self, query, update):
        if query.get("id") != self.user.get("id"):
            return SimpleNamespace(modified_count=0)
        expected_less_than = (
            query.get("$or", [{}, {}, {}])[-1]
            .get("mfa_last_totp_counter", {})
            .get("$lt")
        )
        current = self.user.get("mfa_last_totp_counter")
        if expected_less_than is not None and current is not None and current >= expected_less_than:
            return SimpleNamespace(modified_count=0)
        self.user.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1)


class _AuthStackDb:
    def __init__(self, user):
        self.users = _AuthStackUsers(user)
        self.auth_email_otp_challenges = object()
        self.auth_mfa_challenges = object()
        self.auth_security_events = object()


def _canonical_login_route(user):
    login_function = copy.deepcopy(_async_function(SERVER_PATH, "login"))
    login_function.decorator_list = []
    login_function.returns = None
    for argument in login_function.args.args:
        argument.annotation = None
    module = ast.Module(body=[login_function], type_ignores=[])
    ast.fix_missing_locations(module)
    canonical_user = {
        "name": "Test User",
        "password_hash": "test-boundary-hash",
        **user,
    }
    db = _AuthStackDb(canonical_user)

    async def ensure_user_settings(db, user_id):
        return None

    namespace = {
        "db": db,
        "account_is_disabled": auth.account_is_disabled,
        "verify_password": lambda supplied, password_hash: supplied == "valid",
        "ensure_user_settings": ensure_user_settings,
        "create_access_token": auth.create_access_token,
        "create_refresh_token": auth.create_refresh_token,
        "set_auth_cookies": auth.set_auth_cookies,
        "HTTPException": HTTPException,
    }
    exec(compile(module, str(SERVER_PATH), "exec"), namespace)
    canonical_login = namespace["login"]

    async def route(scope, receive, send):
        request = await receive()
        payload = SimpleNamespace(**json.loads(request["body"]))
        response = Response()
        result = await canonical_login(payload, response)
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": list(response.raw_headers) + [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": json.dumps(result).encode(),
        })
    return route


@pytest.mark.asyncio
async def test_unprotected_replica_login_200_then_auth_me_401():
    user = {"id": "employee-1", "email": "employee@example.test", "role": "viewer"}
    start, body = await _asgi_request(
        _canonical_login_route(user),
        "/api/auth/login",
        {"email": user["email"], "password": "valid"},
    )

    assert start["status"] == 200
    with pytest.raises(HTTPException) as exc:
        await auth.get_current_user_from_db(_Request(body["access_token"]), _Db(user))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_protected_employee_login_202_then_verify_200_and_auth_me_200(monkeypatch):
    user = {"id": "employee-1", "email": "employee@example.test", "role": "viewer"}
    db = _AuthStackDb(user)
    middleware = email_otp.EmailOtpSecurityMiddleware(
        _canonical_login_route(user), db=db,
    )
    now = datetime.now(timezone.utc)
    code = "123456"
    challenge = {"sub": user["id"], "jti": "otp-1"}
    document = {
        "jti": "otp-1",
        "expires_at": now + timedelta(minutes=5),
        "sent_at": now,
        "otp_hash": email_otp.otp_digest("otp-1", code),
    }
    middleware.store.issue_for_login = lambda _: asyncio.sleep(
        0, result=(document, code),
    )
    middleware.store.resolve = lambda _: asyncio.sleep(
        0, result=(challenge, document),
    )
    middleware.store.consume_matching = lambda **_: asyncio.sleep(0, result=True)
    middleware.store.safe_event = lambda *a, **k: asyncio.sleep(0)
    monkeypatch.setattr(email_otp, "send_otp_email", lambda *a, **k: asyncio.sleep(0))

    login_start, login_body = await _asgi_request(
        middleware,
        "/api/auth/login",
        {"email": user["email"], "password": "valid"},
    )
    assert login_start["status"] == 202
    assert login_body["mfa_channel"] == "email"
    login_cookies = [
        value.decode()
        for key, value in login_start["headers"]
        if key.lower() == b"set-cookie"
    ]
    assert len(login_cookies) == 2
    assert all('Max-Age=0' in cookie for cookie in login_cookies)

    verify_start, verify_body = await _asgi_request(
        middleware,
        "/api/auth/email-otp/verify",
        {"challenge_token": login_body["challenge_token"], "code": code},
    )
    assert verify_start["status"] == 200
    verify_cookies = [
        value.decode()
        for key, value in verify_start["headers"]
        if key.lower() == b"set-cookie"
    ]
    assert any("access_token=" in cookie and "Max-Age=43200" in cookie for cookie in verify_cookies)
    assert any("refresh_token=" in cookie and "Max-Age=2592000" in cookie for cookie in verify_cookies)
    current = await auth.get_current_user_from_db(
        _Request(verify_body["access_token"]),
        _Db(user),
    )
    assert current["id"] == user["id"]


@pytest.mark.asyncio
async def test_protected_owner_login_returns_mfa_challenge(monkeypatch):
    user = {
        "id": "owner-1",
        "email": "owner@example.test",
        "role": "owner",
        "mfa_enabled": False,
    }
    db = _AuthStackDb(user)
    email_layer = email_otp.EmailOtpSecurityMiddleware(
        _canonical_login_route(user), db=db,
    )
    middleware = owner_mfa.MfaSecurityMiddleware(email_layer, db=db)
    middleware.store.safe_event = lambda *a, **k: asyncio.sleep(0)
    monkeypatch.setenv("MFA_BOOTSTRAP_CODE", "Bootstrap-Secret-2026!")

    start, body = await _asgi_request(
        middleware,
        "/api/auth/login",
        {"email": user["email"], "password": "valid"},
    )

    assert start["status"] == 202
    assert body["mfa_bootstrap_required"] is True


@pytest.mark.asyncio
async def test_enabled_owner_totp_rejects_wrong_code_then_issues_accepted_session(monkeypatch):
    fixed_time = 1_800_000_000.0
    monkeypatch.setattr(owner_mfa.time, "time", lambda: fixed_time)
    secret = owner_mfa.generate_totp_secret()
    user = {
        "id": "owner-1",
        "email": "owner@example.test",
        "role": "owner",
        "mfa_enabled": True,
        "mfa_totp_secret_enc": owner_mfa.encrypt_totp_secret(secret),
    }
    db = _AuthStackDb(user)
    email_layer = email_otp.EmailOtpSecurityMiddleware(
        _canonical_login_route(user), db=db,
    )
    middleware = owner_mfa.MfaSecurityMiddleware(email_layer, db=db)
    challenge = {"sub": user["id"], "jti": "mfa-1", "purpose": "login"}
    challenge_token = owner_mfa._challenge_token(
        user_id=user["id"], purpose="login", jti="mfa-1",
    )
    middleware.store.create = lambda **_: asyncio.sleep(0, result=challenge_token)
    middleware.store.resolve = lambda _: asyncio.sleep(
        0, result=(challenge, {"jti": "mfa-1"}),
    )
    middleware.store.fail = lambda _: asyncio.sleep(0, result=4)
    middleware.store.consume = lambda _: asyncio.sleep(0)
    middleware.store.safe_event = lambda *a, **k: asyncio.sleep(0)

    login_start, login_body = await _asgi_request(
        middleware,
        "/api/auth/login",
        {"email": user["email"], "password": "valid"},
    )
    assert login_start["status"] == 202
    assert login_body["mfa_required"] is True

    counter = int(fixed_time // 30)
    accepted_codes = {
        owner_mfa.hotp(secret, candidate)
        for candidate in (counter - 1, counter, counter + 1)
    }
    wrong_code = next(
        f"{candidate:06d}"
        for candidate in range(1_000_000)
        if f"{candidate:06d}" not in accepted_codes
    )
    wrong_start, wrong_body = await _asgi_request(
        middleware,
        "/api/auth/mfa/verify",
        {"challenge_token": challenge_token, "code": wrong_code},
    )
    assert wrong_start["status"] == 401
    assert wrong_body["attempts_remaining"] == 4
    assert not any(
        key.lower() == b"set-cookie" for key, _ in wrong_start.get("headers", [])
    )

    valid_code = owner_mfa.hotp(secret, counter)
    verify_start, verify_body = await _asgi_request(
        middleware,
        "/api/auth/mfa/verify",
        {"challenge_token": challenge_token, "code": valid_code},
    )
    assert verify_start["status"] == 200
    assert verify_body["mfa_verified"] is True
    assert any(
        key.lower() == b"set-cookie" and b"access_token=" in value
        for key, value in verify_start["headers"]
    )
    current = await auth.get_current_user_from_db(
        _Request(verify_body["access_token"]), db,
    )
    assert current["id"] == user["id"]


@pytest.mark.asyncio
async def test_extracted_local_startup_stays_unready_when_auth_install_fails():
    function = _async_function(SERVER_PATH, "_local_startup")
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    app = SimpleNamespace(state=SimpleNamespace(readiness="starting"))

    async def fail_install(db):
        raise RuntimeError("auth middleware install failed")

    namespace = {
        "app": app,
        "db": object(),
        "install_process_local_auth_security": fail_install,
    }
    exec(compile(module, str(SERVER_PATH), "exec"), namespace)

    with pytest.raises(RuntimeError, match="auth middleware install failed"):
        await namespace["_local_startup"]()
    assert app.state.readiness == "starting"
