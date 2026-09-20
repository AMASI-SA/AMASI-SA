"""Durable control plane for the reviewed 199-order recovery.

No prepare/read operation sends money. Activation is separate, fingerprint
bound, disabled by default, and never invoked by deployment or migration.
"""
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import uuid

from .recovery_404 import Scope, Outcome, recover_one, audit_one

CAMPAIGN = "main:product-404-20260919"
COHORT_DIGEST = "1d15938ee89692b1d14f4ef7cfb6c57fb3205f84d92383ad4b0613af4a694011"
COMPLETED_DIGESTS = frozenset({
    "2db319e48b27b8290c8c3ba3be38a229f79f52576671b1c9dcb1724db56802ec",
    "ed876815d85b1d99ee8b65621b63769fc9d9ddfc6e599acefb3da59ac79301e3",
})
VERIFIED = {"verified_sent", "verified_existing", "verified_audit", "excluded_completed"}


def now():
    return datetime.now(timezone.utc)


def scope_from(doc):
    return Scope(tuple(doc["references"]), tuple(doc["excluded"]),
                 date.fromisoformat(doc["from_date"]), date.fromisoformat(doc["to_date"]),
                 doc["release_identity"])


def validate_closed_campaign(campaign, rows):
    refs = sorted(campaign["references"])
    excluded = sorted(x for x in refs if hashlib.sha256(x.encode()).hexdigest() in COMPLETED_DIGESTS)
    if (len(refs) != 199 or len(set(refs)) != 199
            or hashlib.sha256("\n".join(refs).encode()).hexdigest() != COHORT_DIGEST
            or len(excluded) != 2 or sorted(campaign["excluded"]) != excluded
            or campaign["from_date"] != "2026-07-01" or campaign["to_date"] != "2026-09-19"
            or scope_from(campaign).fingerprint != campaign["fingerprint"]
            or sorted(r["reference"] for r in rows) != refs
            or any(r["state"] != "excluded_completed" for r in rows if r["reference"] in excluded)):
        raise ValueError("closed_cohort_mismatch")


def unresolved_attempt(row):
    return (row["state"] in {"running", "unknown", "review"}
            or (row["state"] == "blocked" and row.get("reason") not in {
                "outside_recovery_scope", "salla_evidence_unverified", "outside_date_scope",
                "ineligible_status", "cod_deferred", "missing_sku_deferred", "payment_ineligible",
                "not_proven_old_product_404", "unsupported_currency", "nonpositive_total",
                "preflight_amount_mismatch"}))


async def prepare(db, references, owner, actor, release_identity):
    refs = sorted(str(x).strip() for x in references)
    digest = hashlib.sha256("\n".join(refs).encode()).hexdigest()
    if len(refs) != 199 or len(set(refs)) != 199 or digest != COHORT_DIGEST:
        raise ValueError("closed_cohort_mismatch")
    excluded = [x for x in refs if hashlib.sha256(x.encode()).hexdigest() in COMPLETED_DIGESTS]
    if len(excluded) != 2:
        raise ValueError("completed_exclusions_missing")
    scope = Scope(tuple(refs), tuple(excluded), date(2026, 7, 1), date(2026, 9, 19), release_identity)
    doc = {"_id": CAMPAIGN, "references": refs, "excluded": excluded,
           "from_date": scope.from_date.isoformat(), "to_date": scope.to_date.isoformat(),
           "release_identity": release_identity, "fingerprint": scope.fingerprint,
           "orders_owner": owner, "prepared_by": actor, "prepared_at": now(),
           "state": "prepared", "busy": False, "cursor": None}
    # Idempotent prepare must never overwrite an active/previous campaign or outcomes.
    await db.qoyod_404_campaigns.update_one({"_id": CAMPAIGN}, {"$setOnInsert": doc}, upsert=True)
    existing = await db.qoyod_404_campaigns.find_one({"_id": CAMPAIGN})
    if existing["fingerprint"] != scope.fingerprint or existing["orders_owner"] != owner:
        raise ValueError("existing_campaign_requires_review")
    for ref in refs:
        await db.qoyod_404_outcomes.update_one(
            {"_id": f"{CAMPAIGN}:{ref}"}, {"$setOnInsert": {
                "campaign": CAMPAIGN, "reference": ref,
                "state": "excluded_completed" if ref in excluded else "pending",
                "reason": "previously_verified_do_not_resend" if ref in excluded else "awaiting_activation",
            }}, upsert=True)
    return await report(db)


def audit_eligibility(campaign):
    """Scheduling eligibility only; routes still authorize and audit acquires CAS."""
    if not campaign:
        return {"can_audit": False, "audit_block_reason": "campaign_not_prepared"}
    if campaign["state"] == "active":
        return {"can_audit": False, "audit_block_reason": "campaign_active"}
    lease = campaign.get("lease_until")
    if lease is not None and lease.tzinfo is None:
        lease = lease.replace(tzinfo=timezone.utc)
    if campaign.get("busy") and (lease is None or lease >= now()):
        return {"can_audit": False, "audit_block_reason": "operation_in_progress"}
    return {"can_audit": True, "audit_block_reason": None}


async def report(db):
    campaign = await db.qoyod_404_campaigns.find_one({"_id": CAMPAIGN}, {"_id": 0})
    if not campaign:
        return {"state": "not_prepared", "total": 199, "verified": 2, "remaining": 197,
                "results": [], **audit_eligibility(None)}
    rows = [row async for row in db.qoyod_404_outcomes.find({"campaign": CAMPAIGN}, {"_id": 0})]
    counts = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    verified = sum(counts.get(key, 0) for key in VERIFIED)
    can_activate = (campaign["state"] in {"prepared", "paused"}
                    and not campaign.get("busy") and not campaign.get("cursor")
                    and not any(unresolved_attempt(row) for row in rows))
    try:
        validate_closed_campaign(campaign, rows)
    except (ValueError, KeyError):
        can_activate = False
    return {**campaign, **audit_eligibility(campaign), "results": sorted(rows, key=lambda x: x["reference"]),
            "can_activate": can_activate, "rounding_unsettled": counts.get("rounding_review", 0),
            "counts": counts, "total": len(campaign["references"]),
            "verified": verified, "remaining": len(campaign["references"]) - verified}


async def activate(db, fingerprint, owner, actor, release_identity):
    campaign = await db.qoyod_404_campaigns.find_one({"_id": CAMPAIGN})
    if (not campaign or campaign["orders_owner"] != owner
            or campaign["fingerprint"] != fingerprint
            or campaign["release_identity"] != release_identity):
        raise ValueError("activation_scope_or_release_mismatch")
    rows = [row async for row in db.qoyod_404_outcomes.find({"campaign": CAMPAIGN})]
    validate_closed_campaign(campaign, rows)
    if any(unresolved_attempt(r) for r in rows):
        raise ValueError("unresolved_attempt_requires_read_only_audit")
    result = await db.qoyod_404_campaigns.update_one(
        {"_id": CAMPAIGN, "fingerprint": fingerprint, "busy": False, "cursor": None,
         "lease_token": campaign.get("lease_token"),
         "state": {"$in": ["prepared", "paused"]}},
        {"$set": {"state": "active", "activated_by": actor, "activated_at": now()}})
    if not result.modified_count:
        raise ValueError("campaign_not_activatable")
    return await report(db)


async def review_release(db, fingerprint, owner, actor, release_identity):
    """Explicit local-only rebind after deploy; never activates or clears claims."""
    campaign = await db.qoyod_404_campaigns.find_one({"_id": CAMPAIGN})
    if (not campaign or campaign["orders_owner"] != owner
            or campaign["fingerprint"] != fingerprint):
        raise ValueError("activation_scope_or_release_mismatch")
    rows = [row async for row in db.qoyod_404_outcomes.find({"campaign": CAMPAIGN})]
    validate_closed_campaign(campaign, rows)
    if not audit_eligibility(campaign)["can_audit"] or campaign.get("busy") or campaign.get("cursor"):
        raise ValueError("read_only_audit_required_before_release_review")
    new_scope = scope_from({**campaign, "release_identity": release_identity})
    result = await db.qoyod_404_campaigns.update_one(
        {"_id": CAMPAIGN, "fingerprint": fingerprint, "orders_owner": owner,
         "lease_token": campaign.get("lease_token"),
         "state": {"$in": ["paused", "prepared", "review_complete"]}, "busy": False, "cursor": None},
        {"$set": {"release_identity": release_identity, "fingerprint": new_scope.fingerprint,
                  "state": "paused", "release_reviewed_by": actor, "release_reviewed_at": now()},
         "$push": {"release_reviews": {"previous_identity": campaign["release_identity"],
                   "release_identity": release_identity, "actor": actor, "at": now()}}})
    if not result.modified_count:
        raise ValueError("campaign_not_reviewable")
    return await report(db)


async def pause(db, reason="operator_pause"):
    await db.qoyod_404_campaigns.update_one({"_id": CAMPAIGN},
        {"$set": {"state": "paused", "pause_reason": reason}})


class DurablePorts:
    """Persistence boundary plus a narrow provider adapter; claims never expire."""
    def __init__(self, db, campaign, external):
        self.db, self.campaign, self.external = db, campaign, external

    async def authorized(self, fingerprint):
        row = await self.db.qoyod_404_campaigns.find_one({"_id": CAMPAIGN})
        return bool(row and row["state"] == "active" and row["fingerprint"] == fingerprint
                    and await self.external.authorized(row["release_identity"]))

    async def facts(self, reference):
        return await self.external.facts(reference)

    async def observe(self, reference):
        return await self.external.observe(reference)

    async def claim_once(self, reference, fingerprint):
        try:
            result = await self.db.qoyod_404_attempts.update_one(
                {"_id": f"main:{reference}"}, {"$setOnInsert": {
                    "reference": reference, "fingerprint": fingerprint, "claimed_at": now(),
                }}, upsert=True)
        except Exception as exc:
            if getattr(exc, "code", None) == 11000:
                return False
            raise
        return result.upserted_id is not None

    async def send_guarded(self, reference):
        await self.external.send_guarded(reference)

    async def reconcile_marker(self, reference, invoice_id):
        await self.external.reconcile_marker(reference, invoice_id)

    async def finish(self, outcome):
        await self.db.qoyod_404_outcomes.update_one(
            {"_id": f"{CAMPAIGN}:{outcome.reference}"},
            {"$set": {**asdict(outcome), "updated_at": now()}}, upsert=False)
        if outcome.state in VERIFIED:
            await self.db.qoyod_manual_auto_quarantines.update_one(
                {"_id": f"main:{outcome.reference}", "status": "open"},
                {"$set": {"status": "resolved", "resolution": "404_recovery_verified",
                          "resolved_invoice_id": outcome.invoice_id, "resolved_at": now()}})

    async def pause(self, reason):
        await pause(self.db, reason)


async def tick(db, external_factory):
    """One order per worker tick; stale in-flight work is audited, never resent.

    A ten-minute lock is scheduling only. Financial claims are permanent.
    If a process dies with a cursor, another process only audits that cursor.
    """
    campaign = await db.qoyod_404_campaigns.find_one({"_id": CAMPAIGN})
    if not campaign or campaign["state"] != "active":
        return False
    token = uuid.uuid4().hex
    acquired = await db.qoyod_404_campaigns.update_one(
        {"_id": CAMPAIGN, "state": "active", "$or": [
            {"busy": False}, {"lease_until": {"$lt": now()}}]},
        {"$set": {"busy": True, "lease_token": token,
                  "lease_until": now() + timedelta(minutes=10)}})
    if not acquired.modified_count:
        return True
    try:
        campaign = await db.qoyod_404_campaigns.find_one({"_id": CAMPAIGN})
        scope = scope_from(campaign)
        external = external_factory(db, campaign)
        ports = DurablePorts(db, campaign, external)
        ref = campaign.get("cursor")
        audit = bool(ref)
        if not ref:
            row = await db.qoyod_404_outcomes.find_one(
                {"campaign": CAMPAIGN, "state": "pending"}, sort=[("reference", 1)])
            if not row:
                await db.qoyod_404_campaigns.update_one({"_id": CAMPAIGN, "lease_token": token},
                    {"$set": {"state": "review_complete"}})
                return True
            ref = row["reference"]
            await db.qoyod_404_campaigns.update_one({"_id": CAMPAIGN, "lease_token": token},
                {"$set": {"cursor": ref}})
            await db.qoyod_404_outcomes.update_one({"_id": f"{CAMPAIGN}:{ref}"},
                {"$set": {"state": "running", "reason": "refreshing_salla"}})
        outcome = await (audit_one(scope, ref, ports) if audit else recover_one(scope, ref, ports))
        if outcome.state == "disabled":
            # Authorization was refused before a claim/write. Do not invent an
            # ambiguous financial attempt or strand this row behind audit.
            await ports.finish(Outcome(ref, "pending", outcome.reason))
            await pause(db, outcome.reason)
            await db.qoyod_404_campaigns.update_one({"_id": CAMPAIGN, "lease_token": token},
                {"$set": {"cursor": None}})
        elif outcome.state in {"unknown", "review"}:
            await pause(db, outcome.reason)
        else:
            await db.qoyod_404_campaigns.update_one({"_id": CAMPAIGN, "lease_token": token},
                {"$set": {"cursor": None}})
    except Exception:
        await pause(db, "worker_interrupted_requires_audit")
        raise
    finally:
        await db.qoyod_404_campaigns.update_one({"_id": CAMPAIGN, "lease_token": token},
            {"$set": {"busy": False}})
    return True


async def audit_pending(db, external_factory):
    campaign = await db.qoyod_404_campaigns.find_one({"_id": CAMPAIGN})
    if not audit_eligibility(campaign)["can_audit"]:
        raise ValueError("pause_campaign_before_audit")
    token = uuid.uuid4().hex
    acquired = await db.qoyod_404_campaigns.update_one(
        {"_id": CAMPAIGN, "state": {"$ne": "active"}, "$or": [
            {"busy": False}, {"lease_until": {"$lt": now()}}]},
        {"$set": {"busy": True, "lease_token": token,
                  "lease_until": now() + timedelta(minutes=10)}})
    if not acquired.modified_count:
        raise ValueError("audit_already_running")
    scope = scope_from(campaign)
    ports = DurablePorts(db, campaign, external_factory(db, campaign))
    try:
        # Use the same unresolved predicate that blocks activation. A worker
        # read failure before send is `blocked`, but still requires audit.
        rows = [row async for row in db.qoyod_404_outcomes.find({"campaign": CAMPAIGN})
                if unresolved_attempt(row) or row["state"] == "rounding_review"]
        for row in rows:
            await audit_one(scope, row["reference"], ports)
    finally:
        await db.qoyod_404_campaigns.update_one({"_id": CAMPAIGN, "lease_token": token},
            {"$set": {"cursor": None, "busy": False}})
    return await report(db)
