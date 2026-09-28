"""Immutable merchant-private evidence and explicit manual carrier attestation."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from .binding import require_bound, transaction
from .contracts import Evidence, FxSnapshot, Recipient
from .domain import DomainError, digest

COLLECTION = "mezan_special_order_evidence_v1"
MAX_BYTES = 8 * 1024 * 1024
MIMES = frozenset({"application/pdf", "image/jpeg", "image/png", "image/webp"})


def recipient_digest(recipient):
    parsed = recipient if isinstance(recipient, Recipient) else Recipient.model_validate(recipient)
    return digest(parsed.model_dump(mode="json"))


async def inspect_document(data: bytes, content_type: str) -> dict:
    if content_type not in MIMES or not data or len(data) > MAX_BYTES:
        raise DomainError("evidence_type_or_size_invalid", 422)
    package_root = str(Path(__file__).resolve().parent.parent)
    import pypdf
    dependency_root = str(Path(pypdf.__file__).resolve().parent.parent)
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.pathsep.join((package_root,dependency_root)),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "mezan_special_orders.document_validator",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, env=env,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(data), timeout=8)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode != 0 or len(stdout) > 1024:
        raise DomainError("evidence_document_invalid", 422)
    try:
        result = json.loads(stdout)
    except (UnicodeError, ValueError):
        raise DomainError("evidence_document_invalid", 422) from None
    if result.get("ok") is not True or result.get("content_type") != content_type:
        raise DomainError("evidence_mime_mismatch", 422)
    return result


class EvidenceStore:
    def __init__(self, db):
        self.db = db

    async def ensure_indexes(self):
        await self.db[COLLECTION].create_index([("tenant_id", 1), ("object_id", 1)], unique=True)
        await self.db[COLLECTION].create_index([("tenant_id", 1), ("sha256", 1)])

    async def upload(self, tenant_id, actor_id, *, kind, content_type, data):
        require_bound(self.db, write=True, tenant_id=tenant_id)
        if kind not in {"bank_receipt", "carrier_label", "cost_document"}:
            raise DomainError("evidence_kind_invalid", 422)
        inspected = await inspect_document(data, content_type)
        if kind == "carrier_label" and inspected.get("pages", 1) > 4:
            raise DomainError("carrier_label_page_limit", 422)
        from bson import Binary
        identity = str(uuid4())
        fingerprint = hashlib.sha256(data).hexdigest()
        async def save(scoped):
            await scoped[COLLECTION].insert_one({
                "tenant_id": str(tenant_id), "object_id": identity, "kind": kind,
                "sha256": fingerprint, "content_type": content_type, "size": len(data),
                "content": Binary(data), "inspection": inspected, "status": "available",
                "created_by": str(actor_id), "created_at": datetime.now(timezone.utc).isoformat(),
            })
        await transaction(self.db, save, tenant_id=str(tenant_id), scopes=frozenset({"evidence"}))
        return Evidence(object_id=identity, kind=kind, sha256=fingerprint)

    async def get(self, tenant_id, object_id):
        require_bound(self.db, tenant_id=tenant_id)
        row = await self.db[COLLECTION].find_one({
            "tenant_id": str(tenant_id), "object_id": str(object_id), "status": "available",
        }, {"_id": 0})
        if not row:
            raise DomainError("evidence_not_found", 404)
        data = bytes(row.get("content", b""))
        if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise DomainError("evidence_content_integrity_failed")
        return row

    async def verify(self, tenant_id, evidence: Evidence, *, carrier_key=None, tracking_number=None):
        row = await self.get(tenant_id, evidence.object_id)
        if (row["kind"], row["sha256"]) != (evidence.kind, evidence.sha256):
            raise DomainError("evidence_identity_mismatch")
        if evidence.kind == "carrier_label":
            attestation = row.get("carrier_attestation") or {}
            if (attestation.get("carrier_key"), attestation.get("tracking_number")) != (carrier_key, tracking_number):
                raise DomainError("carrier_label_attestation_required")
        return True

    async def attest_label(self, tenant_id, actor_id, **values):
        async def apply(scoped):
            return await EvidenceStore(scoped)._attest_label(tenant_id, actor_id, **values)
        return await transaction(self.db, apply, tenant_id=str(tenant_id), scopes=frozenset({"evidence"}))

    async def _attest_label(self, tenant_id, actor_id, *, evidence, carrier_key, tracking_number,
                           recipient, currency, cod_minor, reason):
        require_bound(self.db, write=True, tenant_id=tenant_id)
        row = await self.get(tenant_id, evidence.object_id)
        if evidence.kind != "carrier_label" or row["kind"] != evidence.kind or row["sha256"] != evidence.sha256:
            raise DomainError("carrier_label_required", 422)
        # Endpoint checks the authenticated owner's explicit attestation permission.
        # This is not a claim that a carrier API or OCR authenticated the label.
        attestation = {"mode": "manual_owner_attestation", "carrier_key": carrier_key,
            "tracking_number": tracking_number, "recipient_digest": recipient_digest(recipient),
            "currency": currency, "cod_minor": cod_minor, "reason": reason,
            "actor_id": str(actor_id), "attested_at": datetime.now(timezone.utc).isoformat()}
        old = row.get("carrier_attestation")
        if old:
            if any(old[k] != attestation[k] for k in ("carrier_key", "tracking_number", "recipient_digest", "currency", "cod_minor")):
                raise DomainError("label_attestation_immutable_upload_new_document")
            return {"object_id": evidence.object_id, "attestation": old}
        result = await self.db[COLLECTION].update_one({
            "tenant_id": str(tenant_id), "object_id": evidence.object_id,
            "carrier_attestation": {"$exists": False},
        }, {"$set": {"carrier_attestation": attestation}})
        if result.matched_count != 1:
            raise DomainError("label_attestation_conflict")
        return {"object_id": evidence.object_id, "attestation": attestation}

    async def verify_delivery(self, document, *, planned=False):
        from .domain import effective_delivery
        delivery = effective_delivery(document)
        if delivery["method"] != "carrier":
            return
        evidence = Evidence.model_validate(delivery["label"])
        await self.verify(document["tenant_id"], evidence, carrier_key=delivery["carrier_key"], tracking_number=delivery["tracking_number"])
        attestation = (await self.get(document["tenant_id"], evidence.object_id))["carrier_attestation"]
        if (attestation["recipient_digest"], attestation["currency"]) != (
                recipient_digest(document["recipient"]), document["fx"]["currency"]):
            raise DomainError("label_recipient_or_currency_mismatch")
        from .domain import balances
        expected_cod = document["collection"]["cod_minor"] if planned else balances(document)["cod_to_collect_minor"]
        if attestation["cod_minor"] != expected_cod:
            raise DomainError("label_cod_changed_new_label_required")
