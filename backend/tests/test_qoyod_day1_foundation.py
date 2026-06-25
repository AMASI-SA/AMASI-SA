"""Day 1 lock-in tests for Qoyod Invoice MVP.

Scope: Foundation only — schemas, indexes, crypto, credentials, API client.
NO pipeline logic yet (that's Day 3+).
"""
import os
import uuid
import asyncio
import pytest
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from integrations.qoyod.crypto import encrypt_secret, decrypt_secret
from integrations.qoyod.credentials import (
    save_api_key, get_api_key, get_fingerprint, delete_api_key,
)
from integrations.qoyod.models import (
    ensure_qoyod_indexes,
    QoyodSettings, QoyodInbox, QoyodInvoiceRecord,
    PIPELINE_STAGES, INVOICE_STATUSES, PRODUCT_TYPE_MODES,
)
from integrations.qoyod.api_client import (
    QoyodAPIClient, QoyodAPIError, _classify,
)


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


# ─────────────────────────────────────────────────────────────────────
# A) crypto round-trip
# ─────────────────────────────────────────────────────────────────────
def test_crypto_round_trip():
    plain = "qoyod_sk_live_abc123XYZ"
    cipher = encrypt_secret(plain)
    assert isinstance(cipher, bytes)
    assert cipher != plain.encode()
    assert decrypt_secret(cipher) == plain


def test_crypto_rejects_tampered_bytes():
    cipher = encrypt_secret("anything")
    tampered = cipher[:-2] + b"XY"
    with pytest.raises(ValueError):
        decrypt_secret(tampered)


def test_crypto_handles_empty_and_none():
    assert decrypt_secret(b"") == ""
    assert decrypt_secret(None) == ""   # noqa
    assert encrypt_secret("") == b""


# ─────────────────────────────────────────────────────────────────────
# B) credentials store (ADR-001 #14)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_save_and_retrieve_api_key(db):
    user_id = f"test_day1_{uuid.uuid4().hex[:8]}"
    try:
        result = await save_api_key(db, user_id, "sk_test_abc123XYZ")
        # Result MUST NOT contain plaintext or ciphertext.
        assert "api_key_enc" not in result
        assert "api_key" not in result
        assert result["fingerprint"]
        # Plaintext readable only through get_api_key.
        assert await get_api_key(db, user_id) == "sk_test_abc123XYZ"
        # Fingerprint is short and stable.
        fp1 = await get_fingerprint(db, user_id)
        assert fp1 and len(fp1) < 20
    finally:
        await delete_api_key(db, user_id)


@pytest.mark.asyncio
async def test_save_api_key_upserts_on_rotation(db):
    user_id = f"test_day1_{uuid.uuid4().hex[:8]}"
    try:
        await save_api_key(db, user_id, "first_key")
        fp1 = await get_fingerprint(db, user_id)
        await save_api_key(db, user_id, "second_rotated_key")
        fp2 = await get_fingerprint(db, user_id)
        assert fp1 != fp2
        # Only one row exists (unique index).
        count = await db.qoyod_credentials.count_documents({"user_id": user_id})
        assert count == 1
        assert await get_api_key(db, user_id) == "second_rotated_key"
    finally:
        await delete_api_key(db, user_id)


@pytest.mark.asyncio
async def test_save_rejects_empty(db):
    with pytest.raises(ValueError):
        await save_api_key(db, "x", "")
    with pytest.raises(ValueError):
        await save_api_key(db, "x", "   ")


# ─────────────────────────────────────────────────────────────────────
# C) Models — vocabulary + version + defaults
# ─────────────────────────────────────────────────────────────────────
def test_settings_defaults_are_safe():
    s = QoyodSettings()
    assert s.enabled is False                  # ADR-001 #3 feature flag default OFF
    assert s.send_only_completed is True       # user spec
    assert s.default_product_type == "service" # user spec: Amasi default
    assert s.user_id == "main"                 # single-tenant
    assert s.schema_version == 1


def test_settings_supports_three_product_modes():
    for mode in PRODUCT_TYPE_MODES:
        s = QoyodSettings(default_product_type=mode)
        assert s.default_product_type == mode


def test_settings_rejects_unknown_product_mode():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        QoyodSettings(default_product_type="legacy_widget_mode")


def test_pipeline_stages_vocabulary_locked():
    # Lock-in: any future re-ordering or rename MUST update the test.
    assert PIPELINE_STAGES == (
        "received", "normalized", "rules_applied",
        "customer_ready", "products_ready",
        "invoiced", "receipted", "completed",
        "skipped", "failed",
    )


def test_invoice_statuses_include_split_state():
    # Per user spec: invoice may succeed but receipt fail.
    assert "invoice_sent_receipt_failed" in INVOICE_STATUSES
    assert "sent" in INVOICE_STATUSES
    assert "failed" in INVOICE_STATUSES


def test_inbox_requires_idempotency_key():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        QoyodInbox(id="x", trace_id="t", raw_payload={})  # missing idem


# ─────────────────────────────────────────────────────────────────────
# D) Indexes — idempotent + correct uniqueness (ADR-001 #10)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_indexes_created_and_idempotent(db):
    # Run twice — second run must not raise.
    await ensure_qoyod_indexes(db)
    await ensure_qoyod_indexes(db)

    # Verify each unique index exists with expected name.
    expected = {
        "qoyod_settings":            "qoyod_settings_user_unique",
        "qoyod_credentials":         "qoyod_credentials_user_unique",
        "qoyod_inbox":               "qoyod_inbox_idem_unique",
        "qoyod_invoices":            "qoyod_invoices_order_unique",
        "qoyod_products_mapping":    "qoyod_products_sku_unique",
        "qoyod_customers_mapping":   "qoyod_customers_lookup_unique",
    }
    for coll, idx in expected.items():
        info = await db[coll].index_information()
        assert idx in info, f"index {idx} missing on {coll}"
        assert info[idx].get("unique") is True


@pytest.mark.asyncio
async def test_inbox_idempotency_enforced_by_index(db):
    """Two inbox rows with the same (user_id, idempotency_key) must
    fail at the DB layer — protects against duplicate webhook delivery
    even if application logic regresses (ADR-001 #10)."""
    await ensure_qoyod_indexes(db)
    user_id = f"test_day1_{uuid.uuid4().hex[:8]}"
    key = f"salla:order:{uuid.uuid4().hex[:6]}"
    try:
        await db.qoyod_inbox.insert_one({
            "id": uuid.uuid4().hex, "user_id": user_id,
            "trace_id": uuid.uuid4().hex,
            "idempotency_key": key,
            "raw_payload": {"a": 1}, "pipeline_stage": "received",
        })
        from pymongo.errors import DuplicateKeyError
        with pytest.raises(DuplicateKeyError):
            await db.qoyod_inbox.insert_one({
                "id": uuid.uuid4().hex, "user_id": user_id,
                "trace_id": uuid.uuid4().hex,
                "idempotency_key": key,
                "raw_payload": {"a": 2}, "pipeline_stage": "received",
            })
    finally:
        await db.qoyod_inbox.delete_many({"user_id": user_id})


# ─────────────────────────────────────────────────────────────────────
# E) API Client — error classification + secret redaction
# ─────────────────────────────────────────────────────────────────────
def test_classify_maps_status_codes():
    code, msg = _classify(401, None)
    assert code == "qoyod_unauthorized"
    code, msg = _classify(403, None)
    assert code == "qoyod_forbidden"
    code, msg = _classify(404, None)
    assert code == "qoyod_not_found"
    code, msg = _classify(422, {"message": "invalid sku"})
    assert code == "qoyod_validation_error"
    assert "invalid sku" in msg
    code, msg = _classify(429, None)
    assert code == "qoyod_rate_limited"
    code, msg = _classify(503, None)
    assert code == "qoyod_server_error"


def test_api_client_repr_redacts_secret():
    c = QoyodAPIClient("super_secret_key_DO_NOT_LEAK")
    r = repr(c)
    assert "super_secret_key" not in r
    assert "api_key=***" in r


def test_api_client_requires_api_key():
    with pytest.raises(ValueError):
        QoyodAPIClient("")


def test_qoyod_api_error_log_dict_omits_secrets():
    err = QoyodAPIError(
        status_code=422, code="qoyod_validation_error",
        message="bad", response_excerpt="…",
        endpoint="POST /invoices",
    )
    log = err.to_log_dict()
    # Only documented fields are present; never api_key, headers, auth.
    assert set(log.keys()) == {
        "code", "message", "status_code", "endpoint",
        "qoyod_response_excerpt"}


# ─────────────────────────────────────────────────────────────────────
# F) ADR-001 compliance evidence
# ─────────────────────────────────────────────────────────────────────
def test_adr_001_exists_and_lists_14_principles():
    path = "/app/docs/adr/ADR-001-architecture-principles.md"
    assert os.path.exists(path), "ADR-001 must be committed"
    with open(path) as f:
        body = f.read()
    # Each numbered principle has a heading
    for n in range(1, 15):
        assert f"### {n}." in body, f"principle #{n} missing in ADR-001"


def test_qoyod_module_documents_compliance():
    path = "/app/backend/integrations/qoyod/README.md"
    assert os.path.exists(path)
    with open(path) as f:
        body = f.read()
    # Must explicitly reference ADR-001 principle numbers used.
    for principle in ("#1", "#3", "#4", "#5", "#8", "#10", "#11", "#13", "#14"):
        assert principle in body, f"README should map principle {principle}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
