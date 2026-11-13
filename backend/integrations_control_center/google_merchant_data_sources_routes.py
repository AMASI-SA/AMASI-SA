"""Owner-requested source provenance; deliberately unattached pending review."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Query, Request

from .google_ads_client import problem
from .google_ads_management import principal
from .google_ads_reporting import GoogleBoundReadError, google_bound_read_session
from .google_merchant_data_sources_proof import DataSourceProofError
from .google_merchant_data_sources_reader import read_merchant_data_sources
from .google_merchant_observation_routes import (
    AUTH_FIELDS, ROUTE_SECONDS, _encoded, _id, _membership, _native_evidence, _same_membership,
)


def _refuse(code="google_merchant_data_sources_read_unproved", status=409):
    raise problem(code, "تعذر إثبات مصادر بيانات الحساب الحالي. أعد القراءة بعد التحقق من الاتصال.", status) from None


async def read_owner_merchant_data_sources(db, user, *, account_id, merchant_id):
    """Reuse the accepted authenticated-owner, membership and native-link gates.

    Explicit saved Ads and Merchant accounts are both required by the existing
    bound session. No default account, manager fallback or relaxed scope gate.
    """
    actor = await principal(db, user, write=False)
    if actor.get("owner") is not True or actor.get("actor_id") != actor.get("user_id"):
        _refuse("google_ads_owner_required", 403)
    owner_id = actor["user_id"]
    account_id, merchant_id = _id(account_id), _id(merchant_id)
    try:
        async with asyncio.timeout(ROUTE_SECONDS):
            before = await _membership(db, owner_id, account_id, merchant_id)
            if before.authenticated_owner != _encoded([{key: user[key] for key in AUTH_FIELDS if key in user}]):
                _refuse("google_merchant_owner_changed", 403)
            async with google_bound_read_session(db, owner_id, ads_account=before.ads_account, merchant_id=merchant_id) as bound:
                await _same_membership(db, before, owner_id, account_id, merchant_id)
                native_before = await _native_evidence(bound, account_id, merchant_id)
                result = await read_merchant_data_sources(bound, merchant_id=merchant_id)
                native_after = await _native_evidence(bound, account_id, merchant_id)
                if native_before != native_after:
                    _refuse("google_merchant_native_link_changed")
                await _same_membership(db, before, owner_id, account_id, merchant_id)
                await bound.check_current()
                result["scope"] = {"owner_id": owner_id, "ads_customer_id": account_id, "merchant_id": merchant_id}
                result["access_observation"] = {
                    "credential_evidence_id": bound.evidence_id,
                    "native_link_resource": next(row[0] for row in native_after[1] if row[2] == merchant_id),
                    "local_membership": "matching_bracketing_snapshots",
                    "native_link_set": "matching_complete_bracketing_reads",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "continuous_authorization_proved": False,
                }
            # The bound context's final credential fence must succeed first.
            return result
    except HTTPException:
        raise
    except (GoogleBoundReadError, DataSourceProofError):
        _refuse()
    except Exception:
        _refuse("google_merchant_data_sources_unavailable", 503)


def attach_google_merchant_data_sources_routes(router, db, current_user):
    """Unattached: root must separately approve a parent router integration."""
    @router.get("/google_ads/merchant-products/data-sources")
    async def sources(request: Request, account_id: str = Query(min_length=1, max_length=19),
            merchant_id: str = Query(min_length=1, max_length=19), user=Depends(current_user)):
        if (set(request.query_params) != {"account_id", "merchant_id"}
            or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params)):
            _refuse("google_merchant_data_sources_query_invalid", 422)
        return await read_owner_merchant_data_sources(db, user, account_id=account_id, merchant_id=merchant_id)
