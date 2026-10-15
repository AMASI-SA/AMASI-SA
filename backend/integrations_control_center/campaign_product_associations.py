"""Append-only campaign-to-product identity graph.

This module gives advertising decisions an explicit product scope before any
performance window exists.  It intentionally stores *associations*, not
conversion attribution: a verified link means that an entity is intended to
advertise a product, while orders and revenue still need their own evidence.

Rows are immutable events.  Corrections and removals append a successor row,
and ``expected_latest_event_id`` provides optimistic concurrency control for
callers that edit an existing association.  Every query is tenant scoped.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CAMPAIGN_PRODUCT_LINK_COLLECTION = "mezan_campaign_product_links_v2"
CAMPAIGN_PRODUCT_LINK_SOURCE_MODE = "campaign_product_association_events_v1"

VerificationStatus = Literal[
    "verified",
    "inferred",
    "user_suggestion",
    "unverified",
]
EvidenceSource = Literal[
    "management_proposal",
    "campaign_creation",
    "provider_adoption",
    "provider_observed",
    "catalog_item",
    "landing_page",
    "manual",
    "historical_inference",
]


class CampaignProductAssociationError(ValueError):
    """Base error for invalid or conflicting association writes."""


class CampaignProductAssociationConflict(CampaignProductAssociationError):
    """Raised when idempotency or optimistic version checks fail."""


class CampaignProductAssociationNotFound(CampaignProductAssociationError):
    """Raised when a requested association has no history."""


def _text(value: Any, *, field: str, maximum: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        raise CampaignProductAssociationError(f"{field} is required")
    if len(text) > maximum:
        raise CampaignProductAssociationError(
            f"{field} must be at most {maximum} characters"
        )
    return text


def _optional_text(
    value: Any,
    *,
    field: str,
    maximum: int = 240,
) -> str | None:
    if value is None or not str(value).strip():
        return None
    return _text(value, field=field, maximum=maximum)


def _provider(value: Any) -> str:
    provider = _text(value, field="provider", maximum=80).lower()
    if provider in {"snapchat", "snap", "snapchat_ads"}:
        return "snapchat_ads"
    return provider


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _datetime(value: Any, *, field: str, default_now: bool = False) -> datetime:
    if value is None:
        if not default_now:
            raise CampaignProductAssociationError(f"{field} is required")
        parsed = _utcnow()
    elif isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise CampaignProductAssociationError(
                f"{field} must be an ISO-8601 datetime"
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any = None, *, field: str = "datetime") -> str:
    return _datetime(value, field=field, default_now=value is None).isoformat()


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) != "_id"
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, datetime):
        return _iso(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _digest(value: Any) -> str:
    payload = json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _collection(db: Any) -> Any:
    try:
        return db[CAMPAIGN_PRODUCT_LINK_COLLECTION]
    except (KeyError, TypeError, AttributeError):
        return getattr(db, CAMPAIGN_PRODUCT_LINK_COLLECTION)


async def _cursor_rows(cursor: Any, *, limit: int = 10_000) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=limit)
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


class CampaignProductEvidence(BaseModel):
    """Bounded provenance for a product association.

    Only ``verified`` evidence is eligible to be consumed as a confirmed fact.
    Suggestions and inferences remain visible, but cannot silently become fact.
    """

    model_config = ConfigDict(extra="forbid")

    source: EvidenceSource
    verification_status: VerificationStatus
    observed_at: datetime | str
    source_ref: str | None = Field(default=None, max_length=240)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("source_ref", "note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split()).strip() or None

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at(cls, value: datetime | str) -> str:
        return _iso(value, field="evidence.observed_at")

    @model_validator(mode="after")
    def constrain_confidence(self) -> "CampaignProductEvidence":
        if self.verification_status != "verified" and self.confidence >= 1.0:
            # A non-verified source may be strong, but it is not certainty.
            self.confidence = 0.99
        return self


class CampaignProductAssociationInput(BaseModel):
    """Identity and validity of one product association."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "snapchat_ads"
    account_id: str = Field(min_length=1, max_length=160)
    mezan_integration_account_id: str | None = Field(default=None, max_length=160)
    campaign_id: str | None = Field(default=None, max_length=160)
    ad_squad_id: str | None = Field(default=None, max_length=160)
    ad_id: str | None = Field(default=None, max_length=160)
    management_proposal_id: str | None = Field(default=None, max_length=160)
    product_id: str = Field(min_length=1, max_length=160)
    product_variant_id: str | None = Field(default=None, max_length=160)
    product_name: str | None = Field(default=None, max_length=300)
    valid_from: datetime | str | None = None
    valid_to: datetime | str | None = None
    evidence: CampaignProductEvidence
    origin_event_id: str | None = Field(default=None, max_length=160)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return _provider(value)

    @field_validator(
        "account_id",
        "mezan_integration_account_id",
        "campaign_id",
        "ad_squad_id",
        "ad_id",
        "management_proposal_id",
        "product_id",
        "product_variant_id",
        "product_name",
        "origin_event_id",
    )
    @classmethod
    def normalize_identifiers(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        maximum = 300 if info.field_name == "product_name" else 160
        return _optional_text(value, field=info.field_name, maximum=maximum)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def normalize_validity(cls, value: datetime | str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _iso(value, field=info.field_name)

    @model_validator(mode="after")
    def validate_hierarchy_and_validity(self) -> "CampaignProductAssociationInput":
        if not self.campaign_id and not self.management_proposal_id:
            raise ValueError("campaign_id or management_proposal_id is required")
        if self.ad_squad_id and not self.campaign_id:
            raise ValueError("ad_squad_id requires campaign_id")
        if self.ad_id and not self.ad_squad_id:
            raise ValueError("ad_id requires ad_squad_id")
        if self.valid_from and self.valid_to:
            start = _datetime(self.valid_from, field="valid_from")
            end = _datetime(self.valid_to, field="valid_to")
            if end <= start:
                raise ValueError("valid_to must be after valid_from")
        return self


def _identity_payload(
    user_id: str, value: CampaignProductAssociationInput
) -> dict[str, Any]:
    # A proposal is the primary scope only until a provider campaign exists.
    # Once campaign_id exists, management_proposal_id remains provenance and is
    # deliberately excluded from the stable association identity.
    proposal_scope = value.management_proposal_id if not value.campaign_id else None
    return {
        "user_id": user_id,
        "provider": value.provider,
        "account_id": value.account_id,
        "campaign_id": value.campaign_id,
        "ad_squad_id": value.ad_squad_id,
        "ad_id": value.ad_id,
        "management_proposal_id": proposal_scope,
        "product_id": value.product_id,
        "product_variant_id": value.product_variant_id,
    }


def _association_key(user_id: str, value: CampaignProductAssociationInput) -> str:
    return _digest(_identity_payload(user_id, value))


def _scope_type(value: CampaignProductAssociationInput) -> str:
    if value.ad_id:
        return "ad"
    if value.ad_squad_id:
        return "ad_squad"
    if value.campaign_id:
        return "campaign"
    return "management_proposal"


def _public(row: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: _canonical(value)
        for key, value in row.items()
        if key not in {"_id", "user_id", "request_fingerprint", "predecessor_key"}
    }
    evidence = dict(public.get("evidence") or {})
    public["confirmed"] = evidence.get("verification_status") == "verified"
    public["decision_fact_eligible"] = public["confirmed"]
    return public


async def ensure_campaign_product_association_indexes(db: Any) -> None:
    """Install indexes needed for tenant isolation and a linear event chain."""

    collection = _collection(db)
    await collection.create_index(
        [("user_id", 1), ("event_id", 1)],
        unique=True,
        name="mezan_campaign_product_links_v2_event_unique",
    )
    await collection.create_index(
        [("user_id", 1), ("idempotency_key", 1)],
        unique=True,
        name="mezan_campaign_product_links_v2_idempotency_unique",
    )
    await collection.create_index(
        [("user_id", 1), ("association_key", 1), ("predecessor_key", 1)],
        unique=True,
        partialFilterExpression={"predecessor_key": {"$type": "string"}},
        name="mezan_campaign_product_links_v2_linear_history",
    )
    await collection.create_index(
        [
            ("user_id", 1),
            ("provider", 1),
            ("account_id", 1),
            ("campaign_id", 1),
            ("ad_squad_id", 1),
            ("ad_id", 1),
            ("recorded_at", -1),
        ],
        name="mezan_campaign_product_links_v2_hierarchy_history",
    )
    await collection.create_index(
        [
            ("user_id", 1),
            ("management_proposal_id", 1),
            ("recorded_at", -1),
        ],
        name="mezan_campaign_product_links_v2_proposal_history",
    )


async def _rows_for_association(
    db: Any,
    user_id: str,
    association_key: str,
) -> list[dict[str, Any]]:
    cursor = _collection(db).find(
        {"user_id": user_id, "association_key": association_key},
        {"_id": 0},
    )
    rows = await _cursor_rows(cursor)
    rows.sort(
        key=lambda row: (
            str(row.get("recorded_at") or ""),
            str(row.get("event_id") or ""),
        )
    )
    return rows


async def _latest_for_association(
    db: Any,
    user_id: str,
    association_key: str,
) -> dict[str, Any] | None:
    rows = await _rows_for_association(db, user_id, association_key)
    return rows[-1] if rows else None


def _request_fingerprint(
    *,
    state: str,
    value: CampaignProductAssociationInput,
) -> str:
    return _digest({"state": state, "association": value.model_dump(mode="json")})


async def _idempotent_existing(
    db: Any,
    user_id: str,
    idempotency_key: str,
    request_fingerprint: str,
) -> dict[str, Any] | None:
    row = await _collection(db).find_one(
        {"user_id": user_id, "idempotency_key": idempotency_key},
        {"_id": 0},
    )
    if not row:
        return None
    if row.get("request_fingerprint") != request_fingerprint:
        raise CampaignProductAssociationConflict(
            "idempotency_key was already used for a different association event"
        )
    return _public(row)


async def _append_event(
    db: Any,
    user_id: str,
    value: CampaignProductAssociationInput,
    *,
    state: Literal["active", "inactive"],
    idempotency_key: str,
    expected_latest_event_id: str | None = None,
    actor_id: str | None = None,
    event_reason: str | None = None,
) -> dict[str, Any]:
    tenant = _text(user_id, field="user_id", maximum=160)
    idempotency = _text(idempotency_key, field="idempotency_key", maximum=128)
    if len(idempotency) < 8:
        raise CampaignProductAssociationError(
            "idempotency_key must be at least 8 characters"
        )
    key = _association_key(tenant, value)
    request_fingerprint = _request_fingerprint(state=state, value=value)
    existing = await _idempotent_existing(db, tenant, idempotency, request_fingerprint)
    if existing:
        return existing

    latest = await _latest_for_association(db, tenant, key)
    latest_event_id = str(latest.get("event_id") or "") if latest else None
    if expected_latest_event_id is not None and latest_event_id != str(
        expected_latest_event_id
    ):
        raise CampaignProductAssociationConflict(
            "association changed since it was read"
        )
    if state == "inactive" and latest is None:
        raise CampaignProductAssociationNotFound("association has no history")

    now = _iso()
    valid_from = value.valid_from or now
    event_id = str(uuid.uuid4())
    evidence = value.evidence.model_dump(mode="json")
    scope_type = _scope_type(value)
    event_type = (
        "detached" if state == "inactive" else "restated" if latest else "attached"
    )
    row: dict[str, Any] = {
        "event_id": event_id,
        # link_id remains compatible with the dormant Phase-1 record shape.
        "link_id": key,
        "association_key": key,
        "idempotency_key": idempotency,
        "request_fingerprint": request_fingerprint,
        "predecessor_key": latest_event_id or "__root__",
        "supersedes_event_id": latest_event_id,
        "revision": int(latest.get("revision") or 0) + 1 if latest else 1,
        "user_id": tenant,
        "provider": value.provider,
        "account_id": value.account_id,
        "mezan_integration_account_id": (
            value.mezan_integration_account_id or value.account_id
        ),
        "campaign_id": value.campaign_id,
        "ad_squad_id": value.ad_squad_id,
        # Alias retained for older consumers that used Meta terminology.
        "ad_group_id": value.ad_squad_id,
        "ad_id": value.ad_id,
        "management_proposal_id": value.management_proposal_id,
        "scope_type": scope_type,
        "product_id": value.product_id,
        "product_variant_id": value.product_variant_id,
        "product_name": value.product_name,
        "state": state,
        "status": state,
        "event_type": event_type,
        "valid_from": valid_from,
        "valid_to": value.valid_to,
        "evidence": evidence,
        "origin_event_id": value.origin_event_id,
        "event_reason": _optional_text(event_reason, field="event_reason", maximum=500),
        "actor_id": _optional_text(actor_id, field="actor_id", maximum=160),
        "source_mode": CAMPAIGN_PRODUCT_LINK_SOURCE_MODE,
        "recorded_at": now,
        "created_at": now,
        "updated_at": now,
    }
    row["content_hash"] = _digest(
        {
            key_name: value_item
            for key_name, value_item in row.items()
            if key_name not in {"content_hash", "request_fingerprint"}
        }
    )

    try:
        await _collection(db).insert_one(row)
    except Exception as exc:
        # A duplicate idempotency key is a successful retry only when the
        # request body matches.  A duplicate predecessor means a concurrent
        # writer won, so the caller must reread instead of forking history.
        retry = await _idempotent_existing(db, tenant, idempotency, request_fingerprint)
        if retry:
            return retry
        refreshed = await _latest_for_association(db, tenant, key)
        refreshed_id = str(refreshed.get("event_id") or "") if refreshed else None
        if refreshed_id != latest_event_id:
            raise CampaignProductAssociationConflict(
                "association changed concurrently; reread before retrying"
            ) from exc
        raise
    return _public(row)


async def attach_campaign_product(
    db: Any,
    user_id: str,
    association: CampaignProductAssociationInput | dict[str, Any],
    *,
    idempotency_key: str,
    expected_latest_event_id: str | None = None,
    actor_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Append a verified, inferred, or suggested product association."""

    value = (
        association
        if isinstance(association, CampaignProductAssociationInput)
        else CampaignProductAssociationInput.model_validate(association)
    )
    return await _append_event(
        db,
        user_id,
        value,
        state="active",
        idempotency_key=idempotency_key,
        expected_latest_event_id=expected_latest_event_id,
        actor_id=actor_id,
        event_reason=reason,
    )


async def detach_campaign_product(
    db: Any,
    user_id: str,
    association: CampaignProductAssociationInput | dict[str, Any],
    *,
    idempotency_key: str,
    expected_latest_event_id: str | None = None,
    actor_id: str | None = None,
    reason: str,
) -> dict[str, Any]:
    """Append a removal; previous association evidence stays immutable."""

    value = (
        association
        if isinstance(association, CampaignProductAssociationInput)
        else CampaignProductAssociationInput.model_validate(association)
    )
    return await _append_event(
        db,
        user_id,
        value,
        state="inactive",
        idempotency_key=idempotency_key,
        expected_latest_event_id=expected_latest_event_id,
        actor_id=actor_id,
        event_reason=_text(reason, field="reason", maximum=500),
    )


def _event_effective(row: dict[str, Any], as_of: datetime) -> bool:
    try:
        start = _datetime(row.get("valid_from"), field="valid_from")
    except CampaignProductAssociationError:
        return False
    if start > as_of:
        return False
    valid_to = row.get("valid_to")
    if valid_to is not None:
        try:
            if _datetime(valid_to, field="valid_to") <= as_of:
                return False
        except CampaignProductAssociationError:
            return False
    return row.get("state") == "active"


def _matches_requested_hierarchy(
    row: dict[str, Any],
    *,
    campaign_id: str | None,
    ad_squad_id: str | None,
    ad_id: str | None,
    management_proposal_id: str | None,
) -> bool:
    if campaign_id:
        if str(row.get("campaign_id") or "") != campaign_id:
            return False
    else:
        if row.get("campaign_id"):
            return False
        if str(row.get("management_proposal_id") or "") != str(
            management_proposal_id or ""
        ):
            return False

    row_squad = str(row.get("ad_squad_id") or "")
    row_ad = str(row.get("ad_id") or "")
    if row_squad and row_squad != str(ad_squad_id or ""):
        return False
    if row_ad and row_ad != str(ad_id or ""):
        return False
    return True


async def list_effective_campaign_products(
    db: Any,
    user_id: str,
    *,
    provider: str,
    account_id: str,
    campaign_id: str | None = None,
    ad_squad_id: str | None = None,
    ad_id: str | None = None,
    management_proposal_id: str | None = None,
    as_of: datetime | str | None = None,
    include_unverified: bool = False,
) -> list[dict[str, Any]]:
    """Resolve active links, including inherited campaign/squad scope.

    A campaign-level link applies to all of its descendants.  A squad-level
    link only applies when that squad is requested, and an ad-level link only
    applies to that ad.  By default only verified facts are returned.
    """

    tenant = _text(user_id, field="user_id", maximum=160)
    provider_id = _provider(provider)
    account = _text(account_id, field="account_id", maximum=160)
    campaign = _optional_text(campaign_id, field="campaign_id", maximum=160)
    squad = _optional_text(ad_squad_id, field="ad_squad_id", maximum=160)
    ad = _optional_text(ad_id, field="ad_id", maximum=160)
    proposal = _optional_text(
        management_proposal_id,
        field="management_proposal_id",
        maximum=160,
    )
    if not campaign and not proposal:
        raise CampaignProductAssociationError(
            "campaign_id or management_proposal_id is required"
        )
    if ad and not squad:
        raise CampaignProductAssociationError("ad_id requires ad_squad_id")

    query: dict[str, Any] = {
        "user_id": tenant,
        "provider": provider_id,
        "account_id": account,
    }
    if campaign:
        query["campaign_id"] = campaign
    else:
        query["management_proposal_id"] = proposal
    rows = await _cursor_rows(_collection(db).find(query, {"_id": 0}))
    rows = [
        row
        for row in rows
        if _matches_requested_hierarchy(
            row,
            campaign_id=campaign,
            ad_squad_id=squad,
            ad_id=ad,
            management_proposal_id=proposal,
        )
    ]
    at = _datetime(as_of, field="as_of", default_now=as_of is None)

    # Select the latest event whose validity has started.  A future successor
    # must not hide the currently effective predecessor.
    latest_by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            starts = _datetime(row.get("valid_from"), field="valid_from")
        except CampaignProductAssociationError:
            continue
        if starts > at:
            continue
        key = str(row.get("association_key") or "")
        rank = (
            starts.isoformat(),
            str(row.get("recorded_at") or ""),
            str(row.get("event_id") or ""),
        )
        previous = latest_by_key.get(key)
        previous_rank = (
            (
                str(previous.get("valid_from") or ""),
                str(previous.get("recorded_at") or ""),
                str(previous.get("event_id") or ""),
            )
            if previous
            else None
        )
        if previous is None or rank > previous_rank:
            latest_by_key[key] = row

    effective = [row for row in latest_by_key.values() if _event_effective(row, at)]
    if not include_unverified:
        effective = [
            row
            for row in effective
            if (row.get("evidence") or {}).get("verification_status") == "verified"
        ]
    effective.sort(
        key=lambda row: (
            {"campaign": 0, "ad_squad": 1, "ad": 2}.get(
                str(row.get("scope_type") or ""), 3
            ),
            str(row.get("product_name") or ""),
            str(row.get("product_id") or ""),
        )
    )
    return [_public(row) for row in effective]


async def list_campaign_product_ids(
    db: Any,
    user_id: str,
    **kwargs: Any,
) -> list[str]:
    """Return unique confirmed product IDs for decision-metric consumers."""

    kwargs.pop("include_unverified", None)
    links = await list_effective_campaign_products(
        db, user_id, include_unverified=False, **kwargs
    )
    return sorted({str(row.get("product_id")) for row in links})


async def get_campaign_product_history(
    db: Any,
    user_id: str,
    association_key: str,
) -> list[dict[str, Any]]:
    """Return the full immutable history for one tenant-owned association."""

    tenant = _text(user_id, field="user_id", maximum=160)
    key = _text(association_key, field="association_key", maximum=128)
    rows = await _rows_for_association(db, tenant, key)
    return [_public(row) for row in rows]


def _product_item(raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, str):
        return {"product_id": raw}
    if not isinstance(raw, dict):
        raise CampaignProductAssociationError(
            "products must contain product IDs or product objects"
        )
    return {
        "product_id": raw.get("product_id") or raw.get("id"),
        "product_variant_id": raw.get("product_variant_id") or raw.get("variant_id"),
        "product_name": raw.get("product_name") or raw.get("name"),
    }


async def attach_products_to_management_proposal(
    db: Any,
    user_id: str,
    *,
    proposal_id: str,
    provider: str,
    account_id: str,
    products: Iterable[str | dict[str, Any]],
    actor_id: str | None,
    observed_at: datetime | str,
    idempotency_prefix: str,
) -> list[dict[str, Any]]:
    """Persist explicit proposal product intent before provider execution."""

    proposal = _text(proposal_id, field="proposal_id", maximum=160)
    prefix = _text(idempotency_prefix, field="idempotency_prefix", maximum=80)
    results: list[dict[str, Any]] = []
    for raw in products:
        product = _product_item(raw)
        identity_hash = _digest(product)[:20]
        value = CampaignProductAssociationInput(
            provider=provider,
            account_id=account_id,
            management_proposal_id=proposal,
            valid_from=observed_at,
            **product,
            evidence=CampaignProductEvidence(
                source="management_proposal",
                verification_status="verified",
                observed_at=observed_at,
                source_ref=proposal,
                confidence=1.0,
                note="Explicit product selected for the management proposal.",
            ),
        )
        results.append(
            await attach_campaign_product(
                db,
                user_id,
                value,
                idempotency_key=f"{prefix}:{identity_hash}",
                actor_id=actor_id,
                reason="Product selected when the management proposal was created.",
            )
        )
    return results


async def adopt_management_proposal_products(
    db: Any,
    user_id: str,
    *,
    proposal_id: str,
    provider: str,
    account_id: str,
    campaign_id: str,
    ad_squad_id: str | None = None,
    ad_id: str | None = None,
    actor_id: str | None,
    provider_verified_at: datetime | str,
    provider_entity_verified: bool,
    idempotency_prefix: str,
) -> list[dict[str, Any]]:
    """Carry proposal products onto the real provider campaign after create.

    The proposal rows remain as historical intent.  New campaign-scoped rows
    point back to the originating event, so creation/adoption is auditable.
    """

    proposal = _text(proposal_id, field="proposal_id", maximum=160)
    campaign = _text(campaign_id, field="campaign_id", maximum=160)
    prefix = _text(idempotency_prefix, field="idempotency_prefix", maximum=80)
    proposal_links = await list_effective_campaign_products(
        db,
        user_id,
        provider=provider,
        account_id=account_id,
        management_proposal_id=proposal,
        include_unverified=True,
    )
    verification: VerificationStatus = (
        "verified" if provider_entity_verified else "unverified"
    )
    source: EvidenceSource = (
        "campaign_creation" if provider_entity_verified else "provider_adoption"
    )
    results: list[dict[str, Any]] = []
    for link in proposal_links:
        product = {
            "product_id": link.get("product_id"),
            "product_variant_id": link.get("product_variant_id"),
            "product_name": link.get("product_name"),
        }
        identity_hash = _digest(product)[:20]
        value = CampaignProductAssociationInput(
            provider=provider,
            account_id=account_id,
            campaign_id=campaign,
            ad_squad_id=ad_squad_id,
            ad_id=ad_id,
            management_proposal_id=proposal,
            origin_event_id=str(link.get("event_id") or ""),
            valid_from=provider_verified_at,
            **product,
            evidence=CampaignProductEvidence(
                source=source,
                verification_status=verification,
                observed_at=provider_verified_at,
                source_ref=f"{proposal}:{link.get('event_id')}",
                confidence=1.0 if provider_entity_verified else 0.5,
                note=(
                    "Provider campaign was read back after creation."
                    if provider_entity_verified
                    else "Campaign identity was adopted but provider read-back is pending."
                ),
            ),
        )
        results.append(
            await attach_campaign_product(
                db,
                user_id,
                value,
                idempotency_key=f"{prefix}:{identity_hash}",
                actor_id=actor_id,
                reason="Carried explicit proposal product onto provider campaign.",
            )
        )
    return results
