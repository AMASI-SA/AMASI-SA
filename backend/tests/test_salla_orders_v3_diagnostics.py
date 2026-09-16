from copy import deepcopy
from datetime import datetime, timedelta, timezone
import inspect
import uuid

import pytest

import salla_orders_v3.diagnostics as diagnostics_module

from salla_orders_v3.diagnostics import (
    build_parity_report,
    read_fulfillment_shadow_comparison,
    scope_diagnostic,
)
from salla_orders_v3.parity import (
    compare_fulfillment_parity,
    compare_qoyod_parity,
)


UTC = timezone.utc
EVIDENCE_HEAD = "a" * 40
EVIDENCE_RUN = "parity-run-20260913-001"
EVIDENCE_TIME = datetime(2026, 9, 13, 10, tzinfo=UTC)
REQUIRED_REGRESSIONS = {
    "order_review": True,
    "fulfillment": True,
    "qoyod": True,
    "snapchat_attribution": True,
    "dashboard_order_totals": True,
}


class _WriteResult:
    def __init__(self, *, upserted_id=None, matched_count=0):
        self.upserted_id = upserted_id
        self.matched_count = matched_count


class _Records:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or {})

    async def find_one(self, query, projection=None):
        return deepcopy(self.rows.get(query.get("_id")))

    async def update_one(self, query, update, upsert=False):
        key = query.get("_id")
        existing = self.rows.get(key)
        if existing is not None:
            return _WriteResult(matched_count=1)
        if not upsert:
            return _WriteResult()
        row = deepcopy(update.get("$setOnInsert") or {})
        self.rows[key] = row
        return _WriteResult(upserted_id=key)


class _ParityDB:
    def __init__(self, *, audits=None):
        self.salla_orders_v3_parity_runs = _Records()
        self.salla_orders_v3_parity_audits = _Records(audits)
        self.salla_orders_v3_parity_evidence = _Records()


async def _persisted_run(monkeypatch, db=None):
    db = db or _ParityDB()
    creator = getattr(diagnostics_module, "create_parity_run_context", None)
    assert callable(creator)
    monkeypatch.setattr(
        diagnostics_module,
        "_utcnow",
        lambda: EVIDENCE_TIME + timedelta(minutes=5),
    )
    monkeypatch.setattr(
        diagnostics_module,
        "_new_run_id",
        lambda: EVIDENCE_RUN,
    )
    monkeypatch.setattr(
        diagnostics_module,
        "_trusted_candidate_head_sha",
        lambda: EVIDENCE_HEAD,
    )
    run = await creator(
        db,
        authenticated_user_id="owner-1",
        user_id="owner-1",
        owner_authorized=True,
    )
    return db, run


def _strict_artifact():
    order = {
        "order_identity": {
            "user_id": "owner-1",
            "store_id": "store-1",
            "order_number": "3001",
        },
        "products": [],
        "items_authoritative": True,
        "items_payload_valid": True,
        "needs_items_enrichment": False,
        "signal_revision": 2,
        "items_success_signal_revision": 2,
    }
    dry_run = {
        "eligible": True,
        "payload": {"invoice": {"total": 10}},
        "idempotency_key": "qoyod:owner-1:store-1:3001",
        "provider_write_reached": False,
        "order_identity": {
            "user_id": "owner-1",
            "store_id": "store-1",
            "order_number": "3001",
        },
    }
    attribution = [{
        "order_number": "3001",
        "campaign_id": "campaign-1",
        "revenue": 10.0,
        "evidence_count": 1,
    }]
    return {
        "legacy_order": dict(order),
        "v3_order": dict(order),
        "legacy_qoyod_dry_run": dict(dry_run),
        "v3_qoyod_dry_run": dict(dry_run),
        "legacy_attribution_rows": [dict(attribution[0])],
        "v3_attribution_rows": [dict(attribution[0])],
        "regression_results": dict(REQUIRED_REGRESSIONS),
    }


async def _persisted_evidence(db, parity_run, artifact=None):
    canonical_artifact = diagnostics_module._canonical_artifact(
        _strict_artifact() if artifact is None else artifact
    )
    observed_at = diagnostics_module._bson_utc_datetime(
        diagnostics_module._utcnow()
    )
    evidence_id = str(uuid.uuid4())
    digest = diagnostics_module._artifact_digest(
        canonical_artifact,
        candidate_head_sha=parity_run.candidate_head_sha,
        run_id=parity_run.run_id,
        owner_id="owner-1",
        observed_at=observed_at,
    )
    document = {
        "_id": f"parity-evidence:{evidence_id}",
        "record_type": "salla_orders_v3_parity_evidence",
        "status": "sealed",
        "trusted_context": True,
        "artifact_id": evidence_id,
        "artifact_digest": digest,
        "artifact": canonical_artifact,
        "candidate_head_sha": parity_run.candidate_head_sha,
        "run_id": parity_run.run_id,
        "owner_id": "owner-1",
        "created_by": "owner-1",
        "observed_at": observed_at,
        "expires_at": min(
            parity_run.expires_at,
            observed_at + timedelta(
                seconds=diagnostics_module.PARITY_EVIDENCE_MAX_AGE_SECONDS
            ),
        ),
        "shadow_only": True,
    }
    collection = db.salla_orders_v3_parity_evidence
    if isinstance(collection, _Records):
        collection.rows[document["_id"]] = deepcopy(document)
    else:
        await collection.insert_one(document)
    return await diagnostics_module.read_persisted_parity_evidence(
        db,
        authenticated_user_id="owner-1",
        user_id="owner-1",
        evidence_id=evidence_id,
        owner_authorized=True,
        parity_run=parity_run,
    )


def _strict_report_kwargs(parity_run, parity_evidence):
    return {
        "parity_run": parity_run,
        "parity_evidence": parity_evidence,
    }


def test_scope_diagnostic_uses_stored_scope_and_never_returns_tokens():
    result = scope_diagnostic({
        "status": "connected",
        "scope": "offline_access orders.read products.read",
        "access_token_encrypted": b"secret",
        "refresh_token_encrypted": b"secret",
    })

    assert result["required_scope_present"] is True
    assert result["token_fields_returned"] is False
    assert "access_token_encrypted" not in result
    assert "refresh_token_encrypted" not in result


def test_scope_diagnostic_accepts_current_salla_read_write_scope():
    result = scope_diagnostic({
        "status": "connected",
        "scope": "settings.read orders.read_write offline_access",
    })

    assert result["required_scope_present"] is True
    assert result["effective_order_read_scopes"] == ["orders.read_write"]


def test_semantic_option_values_keep_bool_and_int_types_distinct():
    common = {
        "items_authoritative": True,
        "items_payload_valid": True,
        "needs_items_enrichment": False,
        "signal_revision": 3,
        "items_success_signal_revision": 3,
    }
    legacy = {
        **common,
        "products": [{
            "order_item_id": "7",
            "options": [{"name": "gift", "value": True, "raw": "legacy"}],
        }],
    }
    v3 = {
        **common,
        "products": [{
            "order_item_id": "7",
            "options": [{"name": "gift", "value": 1, "source": "v3"}],
        }],
    }

    result = compare_fulfillment_parity(legacy, v3)

    assert result["passed"] is False


def test_malformed_semantic_option_rows_are_rejected_not_dropped():
    malformed = {
        "products": [{
            "order_item_id": "7",
            "options": [{"name": "gift", "raw": "missing-value"}],
        }],
        "items_authoritative": True,
        "items_payload_valid": True,
        "needs_items_enrichment": False,
        "signal_revision": 3,
        "items_success_signal_revision": 3,
    }

    with pytest.raises(ValueError, match="malformed"):
        compare_fulfillment_parity(malformed, {**malformed, "products": []})


def test_qoyod_payload_comparison_is_type_strict():
    common = {
        "eligible": True,
        "idempotency_key": "qoyod:owner-1:store-1:3001",
        "provider_write_reached": False,
    }

    result = compare_qoyod_parity(
        {**common, "payload": {"tax_inclusive": True}},
        {**common, "payload": {"tax_inclusive": 1}},
    )

    assert result["passed"] is False


def test_report_has_no_caller_controlled_head_run_owner_or_clock_parameters():
    parameters = inspect.signature(build_parity_report).parameters
    creator_parameters = inspect.signature(
        diagnostics_module.create_parity_run_context
    ).parameters
    assert "parity_run" in parameters
    assert not {
        "evidence_head_sha",
        "evidence_run_id",
        "evidence_owner_id",
        "evaluated_at",
    } & set(parameters)
    assert not {"run_id", "created_at", "expires_at", "evaluated_at"} & set(
        creator_parameters
    )
    assert "candidate_head_sha" not in creator_parameters


def test_report_accepts_only_persisted_sealed_parity_evidence():
    parameters = inspect.signature(build_parity_report).parameters

    assert "parity_evidence" in parameters
    assert not {
        "legacy_order",
        "v3_order",
        "legacy_qoyod_dry_run",
        "v3_qoyod_dry_run",
        "legacy_attribution_rows",
        "v3_attribution_rows",
        "regression_results",
        "regression_evidence",
    } & set(parameters)
    assert not hasattr(diagnostics_module, "persist_parity_evidence")


@pytest.mark.asyncio
async def test_default_mongo_bson_naive_run_timestamp_decodes_as_utc(monkeypatch):
    import mongomock_motor

    client = mongomock_motor.AsyncMongoMockClient()
    db = client.salla_v3_bson_run
    monkeypatch.setattr(
        diagnostics_module,
        "_utcnow",
        lambda: EVIDENCE_TIME + timedelta(minutes=5),
    )
    monkeypatch.setattr(
        diagnostics_module,
        "_new_run_id",
        lambda: EVIDENCE_RUN,
    )
    monkeypatch.setattr(
        diagnostics_module,
        "_trusted_candidate_head_sha",
        lambda: EVIDENCE_HEAD,
    )

    parity_run = await diagnostics_module.create_parity_run_context(
        db,
        authenticated_user_id="owner-1",
        user_id="owner-1",
        owner_authorized=True,
    )

    assert parity_run.created_at.tzinfo is UTC
    assert diagnostics_module._timestamp(
        datetime(2026, 9, 13, 10, 5)
    ) is None


@pytest.mark.asyncio
async def test_mongo_evidence_roundtrip_normalizes_bson_milliseconds(monkeypatch):
    import mongomock_motor

    client = mongomock_motor.AsyncMongoMockClient()
    db = client.salla_v3_bson_evidence
    observed = EVIDENCE_TIME + timedelta(
        minutes=5,
        microseconds=123456,
    )
    monkeypatch.setattr(diagnostics_module, "_utcnow", lambda: observed)
    monkeypatch.setattr(
        diagnostics_module,
        "_new_run_id",
        lambda: EVIDENCE_RUN,
    )
    monkeypatch.setattr(
        diagnostics_module,
        "_trusted_candidate_head_sha",
        lambda: EVIDENCE_HEAD,
    )
    parity_run = await diagnostics_module.create_parity_run_context(
        db,
        authenticated_user_id="owner-1",
        user_id="owner-1",
        owner_authorized=True,
    )

    evidence = await _persisted_evidence(db, parity_run)
    report = build_parity_report(
        **_strict_report_kwargs(parity_run, evidence)
    )

    assert parity_run.created_at.microsecond == 123000
    assert evidence.observed_at.microsecond == 123000
    assert evidence.observed_at.tzinfo is UTC
    assert report["persisted_evidence_valid"] is True
    assert report["parity_ready"] is True


@pytest.mark.asyncio
async def test_report_rejects_forged_or_mutated_evidence(monkeypatch):
    db, parity_run = await _persisted_run(monkeypatch)
    evidence = await _persisted_evidence(db, parity_run)

    with pytest.raises(TypeError, match="persisted evidence"):
        build_parity_report(
            parity_run=parity_run,
            parity_evidence={"artifact": _strict_artifact()},
        )

    evidence.artifact["regression_results"]["qoyod"] = False
    report = build_parity_report(
        **_strict_report_kwargs(parity_run, evidence)
    )
    assert report["persisted_evidence_valid"] is False
    assert report["parity_ready"] is False


@pytest.mark.asyncio
async def test_digest_cannot_confuse_iso_string_with_datetime_mutation(
    monkeypatch,
):
    db, parity_run = await _persisted_run(monkeypatch)
    artifact = _strict_artifact()
    artifact["legacy_order"]["ignored_observed_at"] = (
        "2026-09-13T10:00:00+00:00"
    )
    evidence = await _persisted_evidence(db, parity_run, artifact)
    evidence.artifact["legacy_order"]["ignored_observed_at"] = EVIDENCE_TIME

    report = build_parity_report(
        **_strict_report_kwargs(parity_run, evidence)
    )

    assert report["persisted_evidence_valid"] is False
    assert report["parity_ready"] is False


@pytest.mark.asyncio
async def test_reader_rejects_digest_tamper_and_deleted_artifact(monkeypatch):
    db, parity_run = await _persisted_run(monkeypatch)
    evidence = await _persisted_evidence(db, parity_run)
    key = f"parity-evidence:{evidence.artifact_id}"
    row = db.salla_orders_v3_parity_evidence.rows[key]
    row["artifact"]["regression_results"]["qoyod"] = False

    with pytest.raises(RuntimeError, match="evidence is invalid"):
        await diagnostics_module.read_persisted_parity_evidence(
            db,
            authenticated_user_id="owner-1",
            user_id="owner-1",
            evidence_id=evidence.artifact_id,
            owner_authorized=True,
            parity_run=parity_run,
        )

    row.pop("artifact")
    with pytest.raises(RuntimeError, match="evidence is invalid"):
        await diagnostics_module.read_persisted_parity_evidence(
            db,
            authenticated_user_id="owner-1",
            user_id="owner-1",
            evidence_id=evidence.artifact_id,
            owner_authorized=True,
            parity_run=parity_run,
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("candidate_head_sha", "b" * 40),
        ("run_id", "different-run"),
        ("owner_id", "owner-2"),
        ("created_by", "owner-2"),
    ],
)
@pytest.mark.asyncio
async def test_reader_rejects_cross_context_persisted_evidence(
    field,
    replacement,
    monkeypatch,
):
    db, parity_run = await _persisted_run(monkeypatch)
    evidence = await _persisted_evidence(db, parity_run)
    row = db.salla_orders_v3_parity_evidence.rows[
        f"parity-evidence:{evidence.artifact_id}"
    ]
    row[field] = replacement

    with pytest.raises(RuntimeError, match="evidence is invalid"):
        await diagnostics_module.read_persisted_parity_evidence(
            db,
            authenticated_user_id="owner-1",
            user_id="owner-1",
            evidence_id=evidence.artifact_id,
            owner_authorized=True,
            parity_run=parity_run,
        )


@pytest.mark.parametrize("offset_seconds", [-1, 120])
@pytest.mark.asyncio
async def test_reader_rejects_pre_run_or_future_evidence_even_with_valid_digest(
    offset_seconds,
    monkeypatch,
):
    db, parity_run = await _persisted_run(monkeypatch)
    evidence = await _persisted_evidence(db, parity_run)
    row = db.salla_orders_v3_parity_evidence.rows[
        f"parity-evidence:{evidence.artifact_id}"
    ]
    observed_at = parity_run.created_at + timedelta(seconds=offset_seconds)
    row["observed_at"] = observed_at
    row["expires_at"] = min(
        parity_run.expires_at,
        observed_at + timedelta(
            seconds=diagnostics_module.PARITY_EVIDENCE_MAX_AGE_SECONDS
        ),
    )
    row["artifact_digest"] = diagnostics_module._artifact_digest(
        row["artifact"],
        candidate_head_sha=row["candidate_head_sha"],
        run_id=row["run_id"],
        owner_id=row["owner_id"],
        observed_at=observed_at,
    )

    with pytest.raises(RuntimeError, match="evidence is invalid"):
        await diagnostics_module.read_persisted_parity_evidence(
            db,
            authenticated_user_id="owner-1",
            user_id="owner-1",
            evidence_id=evidence.artifact_id,
            owner_authorized=True,
            parity_run=parity_run,
        )


@pytest.mark.asyncio
async def test_default_mongo_bson_naive_na_timestamp_decodes_as_utc(monkeypatch):
    import mongomock_motor

    _fake_db, parity_run = await _persisted_run(monkeypatch)
    client = mongomock_motor.AsyncMongoMockClient()
    db = client.salla_v3_bson_audit
    audited_at = EVIDENCE_TIME + timedelta(minutes=6)
    await db.salla_orders_v3_parity_audits.insert_one({
        "_id": "parity-audit:qoyod-bson",
        "record_type": "salla_orders_v3_parity_not_applicable",
        "status": "not_applicable",
        "gate": "qoyod",
        "owner_id": "owner-1",
        "recorded_by": "owner-1",
        "reason": "store has no Qoyod integration",
        "audit_id": "qoyod-bson",
        "audited_at": audited_at,
        "evidence_head_sha": EVIDENCE_HEAD,
        "evidence_run_id": EVIDENCE_RUN,
        "order_identity": {
            "user_id": "owner-1",
            "store_id": "store-1",
            "order_number": "3001",
        },
    })
    monkeypatch.setattr(
        diagnostics_module,
        "_utcnow",
        lambda: EVIDENCE_TIME + timedelta(minutes=7),
    )

    audit = await diagnostics_module.read_audited_not_applicable(
        db,
        authenticated_user_id="owner-1",
        user_id="owner-1",
        audit_id="qoyod-bson",
        gate="qoyod",
        owner_authorized=True,
        parity_run=parity_run,
    )

    assert audit.audited_at.tzinfo is UTC


@pytest.mark.asyncio
async def test_read_and_report_reject_persisted_run_from_other_runtime_head(
    monkeypatch,
):
    db, parity_run = await _persisted_run(monkeypatch)
    parity_evidence = await _persisted_evidence(db, parity_run)
    monkeypatch.setattr(
        diagnostics_module,
        "_trusted_candidate_head_sha",
        lambda: "b" * 40,
    )

    with pytest.raises(RuntimeError, match="HEAD"):
        await diagnostics_module.read_parity_run_context(
            db,
            authenticated_user_id="owner-1",
            user_id="owner-1",
            run_id=EVIDENCE_RUN,
            owner_authorized=True,
        )

    report = build_parity_report(
        **_strict_report_kwargs(parity_run, parity_evidence)
    )
    assert report["parity_ready"] is False
    assert report["runtime_head_matches"] is False


@pytest.mark.asyncio
async def test_qoyod_identity_must_equal_fulfillment_identity(monkeypatch):
    db, parity_run = await _persisted_run(monkeypatch)
    artifact = _strict_artifact()
    for row in (
        artifact["legacy_qoyod_dry_run"],
        artifact["v3_qoyod_dry_run"],
    ):
        row["order_identity"] = {
            "user_id": "owner-1",
            "store_id": "other-store",
            "order_number": "9999",
        }

    evidence = await _persisted_evidence(db, parity_run, artifact)
    report = build_parity_report(
        **_strict_report_kwargs(parity_run, evidence)
    )

    assert report["parity_ready"] is False
    assert report["qoyod_identity_matches_fulfillment"] is False


@pytest.mark.asyncio
async def test_parity_run_context_is_internally_timed_and_persisted(monkeypatch):
    db, parity_run = await _persisted_run(monkeypatch)
    parity_evidence = await _persisted_evidence(db, parity_run)
    row = db.salla_orders_v3_parity_runs.rows[
        f"parity-run:{EVIDENCE_RUN}"
    ]

    assert row["candidate_head_sha"] == EVIDENCE_HEAD
    assert row["run_id"] == EVIDENCE_RUN
    assert row["owner_id"] == "owner-1"
    assert row["created_at"] == EVIDENCE_TIME + timedelta(minutes=5)
    assert row["expires_at"] > row["created_at"]

    values = _strict_report_kwargs(parity_run, parity_evidence)
    values["parity_run"] = {
        "candidate_head_sha": EVIDENCE_HEAD,
        "run_id": EVIDENCE_RUN,
        "owner_id": "owner-1",
    }
    with pytest.raises(TypeError, match="persisted parity run"):
        build_parity_report(**values)

    monkeypatch.setattr(
        diagnostics_module,
        "_utcnow",
        lambda: EVIDENCE_TIME + timedelta(hours=2),
    )
    stale = build_parity_report(
        **_strict_report_kwargs(parity_run, parity_evidence)
    )
    assert stale["parity_ready"] is False
    assert stale["parity_run_valid"] is False


@pytest.mark.asyncio
async def test_cutover_remains_closed_until_all_parity_and_regressions_pass(
    monkeypatch,
):
    db, parity_run = await _persisted_run(monkeypatch)
    blocked_artifact = _strict_artifact()
    blocked_artifact["regression_results"]["fulfillment"] = False
    blocked_evidence = await _persisted_evidence(
        db, parity_run, blocked_artifact
    )
    allowed_evidence = await _persisted_evidence(db, parity_run)
    blocked = build_parity_report(
        **_strict_report_kwargs(parity_run, blocked_evidence)
    )
    allowed = build_parity_report(
        **_strict_report_kwargs(parity_run, allowed_evidence)
    )

    assert blocked["cutover_allowed"] is False
    assert allowed["parity_ready"] is True
    assert allowed["cutover_allowed"] is False
    assert allowed["provider_write_reached"] is False

    write_artifact = _strict_artifact()
    write_artifact["v3_qoyod_dry_run"]["provider_write_reached"] = True
    write_evidence = await _persisted_evidence(db, parity_run, write_artifact)
    write_observed = build_parity_report(
        **_strict_report_kwargs(parity_run, write_evidence)
    )
    assert write_observed["parity_ready"] is False
    assert write_observed["provider_write_reached"] is True


@pytest.mark.asyncio
async def test_parity_rejects_empty_evidence_without_authenticated_exemption(
    monkeypatch,
):
    db, parity_run = await _persisted_run(monkeypatch)
    artifact = _strict_artifact()
    artifact.update({
        "legacy_qoyod_dry_run": {},
        "v3_qoyod_dry_run": {},
        "legacy_attribution_rows": [],
        "v3_attribution_rows": [],
    })
    evidence = await _persisted_evidence(db, parity_run, artifact)
    report = build_parity_report(
        **_strict_report_kwargs(parity_run, evidence)
    )

    assert report["parity_ready"] is False
    assert report["qoyod_evidence_accepted"] is False
    assert report["attribution_evidence_accepted"] is False


@pytest.mark.asyncio
async def test_caller_made_not_applicable_mapping_is_rejected(monkeypatch):
    db, parity_run = await _persisted_run(monkeypatch)
    parity_evidence = await _persisted_evidence(db, parity_run)
    values = _strict_report_kwargs(parity_run, parity_evidence)
    values["qoyod_not_applicable"] = {
        "status": "not_applicable",
        "owner_id": "owner-1",
    }

    with pytest.raises(TypeError, match="persisted audit"):
        build_parity_report(**values)


@pytest.mark.asyncio
async def test_shadow_comparison_requires_exact_owner_authorization():
    class _DB:
        pass

    with pytest.raises(PermissionError, match="owner-only"):
        await read_fulfillment_shadow_comparison(
            _DB(),
            authenticated_user_id="owner-2",
            user_id="owner-1",
            store_id="store-1",
            order_number="3001",
            owner_authorized=True,
        )

    with pytest.raises(PermissionError, match="owner-only"):
        await read_fulfillment_shadow_comparison(
            _DB(),
            authenticated_user_id="owner-1",
            user_id="owner-1",
            store_id="store-1",
            order_number="3001",
            owner_authorized=False,
        )

    with pytest.raises(PermissionError, match="owner-only"):
        await read_fulfillment_shadow_comparison(
            _DB(),
            authenticated_user_id="",
            user_id="",
            store_id="store-1",
            order_number="3001",
            owner_authorized=True,
        )


@pytest.mark.asyncio
async def test_shadow_comparison_uses_job_snapshot_and_latest_queue_signal():
    class _Collection:
        def __init__(self, row):
            self.row = row

        async def find_one(self, query, projection=None):
            return self.row

    class _DB:
        unified_orders = _Collection({"products": []})
        salla_orders_v3_jobs = _Collection({
            "signal_revision": 2,
            "compatibility_order": {
                "products": [],
                "items_authoritative": True,
                "items_payload_valid": True,
                "needs_items_enrichment": False,
                "signal_revision": 1,
                "items_success_signal_revision": 1,
            },
        })

    result = await read_fulfillment_shadow_comparison(
        _DB(),
        authenticated_user_id="owner-1",
        user_id="owner-1",
        store_id="store-1",
        order_number="3001",
        owner_authorized=True,
    )

    assert result["available"] is True
    assert result["passed"] is False
    assert result["signal_revision"] == 2
    assert result["items_success_signal_revision"] == 1


@pytest.mark.asyncio
async def test_parity_report_requires_typed_fresh_same_head_and_run_evidence(
    monkeypatch,
):
    db, parity_run = await _persisted_run(monkeypatch)
    evidence = await _persisted_evidence(db, parity_run)
    report = build_parity_report(
        **_strict_report_kwargs(parity_run, evidence)
    )

    assert report["parity_ready"] is True
    assert report["evidence_context_valid"] is True
    assert report["regressions_passed"] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda values: [
            row.update({"idempotency_key": 123})
            for row in (
                values["legacy_qoyod_dry_run"],
                values["v3_qoyod_dry_run"],
            )
        ],
        lambda values: [
            row.update({"payload": {}})
            for row in (
                values["legacy_qoyod_dry_run"],
                values["v3_qoyod_dry_run"],
            )
        ],
        lambda values: [
            row.update({"eligible": False, "payload": {}})
            for row in (
                values["legacy_qoyod_dry_run"],
                values["v3_qoyod_dry_run"],
            )
        ],
    ],
)
@pytest.mark.asyncio
async def test_qoyod_evidence_requires_typed_idempotency_payload_or_reason(
    mutate,
    monkeypatch,
):
    db, parity_run = await _persisted_run(monkeypatch)
    artifact = _strict_artifact()
    mutate(artifact)
    evidence = await _persisted_evidence(db, parity_run, artifact)

    report = build_parity_report(
        **_strict_report_kwargs(parity_run, evidence)
    )

    assert report["parity_ready"] is False
    assert report["qoyod_evidence_accepted"] is False


@pytest.mark.asyncio
async def test_qoyod_ineligible_evidence_requires_and_accepts_explicit_reason(
    monkeypatch,
):
    db, parity_run = await _persisted_run(monkeypatch)
    artifact = _strict_artifact()
    for row in (
        artifact["legacy_qoyod_dry_run"],
        artifact["v3_qoyod_dry_run"],
    ):
        row.update({
            "eligible": False,
            "payload": {},
            "ineligible_reason": "order is outside Qoyod policy",
        })

    evidence = await _persisted_evidence(db, parity_run, artifact)
    report = build_parity_report(
        **_strict_report_kwargs(parity_run, evidence)
    )

    assert report["qoyod_evidence_accepted"] is True
    assert report["parity_ready"] is True


@pytest.mark.parametrize("identity_failure", ["missing", "wrong_owner", "bool_id"])
@pytest.mark.asyncio
async def test_fulfillment_evidence_requires_matching_typed_order_identity(
    identity_failure,
    monkeypatch,
):
    db, parity_run = await _persisted_run(monkeypatch)
    artifact = _strict_artifact()
    if identity_failure == "missing":
        artifact["legacy_order"].pop("order_identity")
    elif identity_failure == "wrong_owner":
        artifact["v3_order"]["order_identity"]["user_id"] = "owner-2"
    else:
        artifact["v3_order"]["order_identity"]["order_number"] = True

    evidence = await _persisted_evidence(db, parity_run, artifact)
    report = build_parity_report(
        **_strict_report_kwargs(parity_run, evidence)
    )

    assert report["parity_ready"] is False
    assert report["evidence_context_valid"] is False


@pytest.mark.parametrize(
    ("mutate", "expected_flag"),
    [
        (
            lambda values: values["legacy_qoyod_dry_run"].update(
                {"eligible": 1}
            ),
            "qoyod_evidence_accepted",
        ),
        (
            lambda values: values["legacy_attribution_rows"].append(
                dict(values["legacy_attribution_rows"][0])
            ),
            "attribution_evidence_accepted",
        ),
        (
            lambda values: values["v3_attribution_rows"][0].update(
                {"evidence_count": 2}
            ),
            "attribution_evidence_accepted",
        ),
        (
            lambda values: values.update(
                {
                    "regression_results": {
                        **REQUIRED_REGRESSIONS,
                        "extra": True,
                    }
                }
            ),
            "regressions_passed",
        ),
        (
            lambda values: values["regression_results"].update(
                {"qoyod": 1}
            ),
            "regressions_passed",
        ),
    ],
)
@pytest.mark.asyncio
async def test_malformed_or_cross_run_parity_evidence_fails_closed(
    mutate,
    expected_flag,
    monkeypatch,
):
    db, parity_run = await _persisted_run(monkeypatch)
    artifact = _strict_artifact()
    mutate(artifact)
    evidence = await _persisted_evidence(db, parity_run, artifact)

    report = build_parity_report(
        **_strict_report_kwargs(parity_run, evidence)
    )

    assert report["parity_ready"] is False
    assert report[expected_flag] is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda artifact: artifact["v3_qoyod_dry_run"].update(
            {"evidence_head_sha": "b" * 40}
        ),
        lambda artifact: artifact["legacy_order"].update(
            {"evidence_observed_at": "2026-09-12T10:00:00+00:00"}
        ),
        lambda artifact: artifact["v3_attribution_rows"][0].update(
            {"revenue": float("nan")}
        ),
    ],
)
@pytest.mark.asyncio
async def test_caller_context_and_nonfinite_values_cannot_be_sealed(
    mutate,
    monkeypatch,
):
    db, parity_run = await _persisted_run(monkeypatch)
    artifact = _strict_artifact()
    mutate(artifact)

    with pytest.raises(ValueError, match="parity"):
        await _persisted_evidence(db, parity_run, artifact)


@pytest.mark.asyncio
async def test_not_applicable_requires_same_persisted_parity_run(monkeypatch):
    loader = getattr(diagnostics_module, "read_audited_not_applicable", None)
    assert callable(loader)
    audit_time = (EVIDENCE_TIME + timedelta(minutes=5)).isoformat()
    order_identity = {
        "user_id": "owner-1",
        "store_id": "store-1",
        "order_number": "3001",
    }
    db = _ParityDB(audits={
        "parity-audit:qoyod-1": {
            "_id": "parity-audit:qoyod-1",
            "record_type": "salla_orders_v3_parity_not_applicable",
            "status": "not_applicable",
            "gate": "qoyod",
            "owner_id": "owner-1",
            "recorded_by": "owner-1",
            "reason": "store has no Qoyod integration",
            "audit_id": "qoyod-1",
            "audited_at": audit_time,
            "evidence_head_sha": EVIDENCE_HEAD,
            "evidence_run_id": EVIDENCE_RUN,
            "order_identity": order_identity,
        },
        "parity-audit:attribution-1": {
            "_id": "parity-audit:attribution-1",
            "record_type": "salla_orders_v3_parity_not_applicable",
            "status": "not_applicable",
            "gate": "attribution",
            "owner_id": "owner-1",
            "recorded_by": "owner-1",
            "reason": "store has no advertising attribution",
            "audit_id": "attribution-1",
            "audited_at": audit_time,
            "evidence_head_sha": EVIDENCE_HEAD,
            "evidence_run_id": EVIDENCE_RUN,
            "order_identity": order_identity,
        },
        "parity-audit:wrong-run": {
            "_id": "parity-audit:wrong-run",
            "record_type": "salla_orders_v3_parity_not_applicable",
            "status": "not_applicable",
            "gate": "qoyod",
            "owner_id": "owner-1",
            "recorded_by": "owner-1",
            "reason": "wrong run must fail",
            "audit_id": "wrong-run",
            "audited_at": audit_time,
            "evidence_head_sha": EVIDENCE_HEAD,
            "evidence_run_id": "different-run",
            "order_identity": order_identity,
        },
        "parity-audit:qoyod-other-store": {
            "_id": "parity-audit:qoyod-other-store",
            "record_type": "salla_orders_v3_parity_not_applicable",
            "status": "not_applicable",
            "gate": "qoyod",
            "owner_id": "owner-1",
            "recorded_by": "owner-1",
            "reason": "different store exemption",
            "audit_id": "qoyod-other-store",
            "audited_at": audit_time,
            "evidence_head_sha": EVIDENCE_HEAD,
            "evidence_run_id": EVIDENCE_RUN,
            "order_identity": {
                **order_identity,
                "store_id": "store-2",
            },
        },
    })
    db, parity_run = await _persisted_run(monkeypatch, db)

    common = {
        "authenticated_user_id": "owner-1",
        "user_id": "owner-1",
        "owner_authorized": True,
        "parity_run": parity_run,
    }
    qoyod_audit = await loader(
        db,
        audit_id="qoyod-1",
        gate="qoyod",
        **common,
    )
    attribution_audit = await loader(
        db,
        audit_id="attribution-1",
        gate="attribution",
        **common,
    )
    other_store_audit = await loader(
        db,
        audit_id="qoyod-other-store",
        gate="qoyod",
        **common,
    )
    with pytest.raises(RuntimeError, match="invalid"):
        await loader(
            db,
            audit_id="wrong-run",
            gate="qoyod",
            **common,
        )

    artifact = _strict_artifact()
    artifact.update({
        "legacy_qoyod_dry_run": {},
        "v3_qoyod_dry_run": {},
        "legacy_attribution_rows": [],
        "v3_attribution_rows": [],
    })
    parity_evidence = await _persisted_evidence(db, parity_run, artifact)
    values = _strict_report_kwargs(parity_run, parity_evidence)
    values.update({
        "qoyod_not_applicable": qoyod_audit,
        "attribution_not_applicable": attribution_audit,
    })

    assert build_parity_report(**values)["parity_ready"] is True

    values["qoyod_not_applicable"] = other_store_audit
    assert build_parity_report(**values)["parity_ready"] is False

    values["qoyod_not_applicable"] = {
        "status": "not_applicable",
        "owner_id": "owner-1",
    }
    with pytest.raises(TypeError, match="persisted audit"):
        build_parity_report(**values)
