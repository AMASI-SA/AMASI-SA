from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "backend/integrations_control_center/__init__.py"
GUARD = ROOT / "backend/integrations_control_center/tiktok_native_guard.py"
SCHEDULER = ROOT / "backend/integrations_control_center/ads_auto_sync_scheduler.py"
CARD = ROOT / "frontend/src/components/integrationsV2/IntegrationCardV2.jsx"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return source.replace(old, new, 1)


# 1) Register TikTok native reporting routes in the production V2 router.
source = INIT.read_text(encoding="utf-8")
source = replace_once(
    source,
    '''from .tiktok_catalog_native import install_tiktok_native_catalog
from .tiktok_connections import attach_tiktok_connection_routes
from .models import (
''',
    '''from .tiktok_catalog_native import install_tiktok_native_catalog
from .tiktok_connections import attach_tiktok_connection_routes
from .tiktok_native_reporting_routes import (
    attach_tiktok_native_reporting_routes,
)
from .models import (
''',
    "TikTok reporting route import",
)
source = replace_once(
    source,
    '''    attach_dashboard_authoritative_summary_routes(
        router, db, current_user, _require_owner
    )
    attach_tiktok_connection_routes(router, db, current_user, _require_owner)

    # Lazy import keeps focused V2 modules importable in lightweight test
''',
    '''    attach_dashboard_authoritative_summary_routes(
        router, db, current_user, _require_owner
    )
    attach_tiktok_connection_routes(router, db, current_user, _require_owner)
    attach_tiktok_native_reporting_routes(
        router, db, current_user, _require_owner
    )

    # Lazy import keeps focused V2 modules importable in lightweight test
''',
    "TikTok reporting route registration",
)
INIT.write_text(source, encoding="utf-8")


# 2) Extend the existing native/legacy isolation guard to cover reporting.
source = GUARD.read_text(encoding="utf-8")
source = replace_once(
    source,
    '''        base / "tiktok_projection.py",
        base / "tiktok_connections.py",
    )
''',
    '''        base / "tiktok_projection.py",
        base / "tiktok_connections.py",
        base / "tiktok_native_reporting.py",
        base / "tiktok_native_reporting_routes.py",
    )
''',
    "TikTok reporting legacy guard paths",
)
GUARD.write_text(source, encoding="utf-8")


# 3) Add TikTok to the same server-side five-minute scheduler used by Meta/Snap.
source = SCHEDULER.read_text(encoding="utf-8")
source = replace_once(
    source,
    '''from .meta_oauth_security import META_PROVIDER_ID, meta_oauth_configured
from .snapchat_account_hourly_refresh import (
''',
    '''from .meta_oauth_security import META_PROVIDER_ID, meta_oauth_configured
from .tiktok_native_reporting import (
    TIKTOK_REPORTING_SOURCE_MODE,
    TikTokReportingError,
    TikTokReportingSyncInput,
    run_tiktok_reporting_sync,
    tiktok_reporting_enabled,
)
from .tiktok_oauth_security import TIKTOK_PROVIDER_ID, tiktok_oauth_configured
from .snapchat_account_hourly_refresh import (
''',
    "TikTok scheduler imports",
)
source = replace_once(
    source,
    '''META_RUN_TYPE = "meta_reporting_async"
SNAP_RUN_TYPE = "analytics_refresh"
ACTIVE_STATUSES = ("queued", "running")
''',
    '''META_RUN_TYPE = "meta_reporting_async"
SNAP_RUN_TYPE = "analytics_refresh"
TIKTOK_RUN_TYPE = "tiktok_reporting_async"
ACTIVE_STATUSES = ("queued", "running")
''',
    "TikTok scheduler run type",
)
source = replace_once(
    source,
    '''def riyadh_date_range(now: datetime, days: int) -> tuple[date, date]:
    current = now.astimezone(_timezone(BUSINESS_TIMEZONE)).date()
    return current - timedelta(days=days - 1), current


def _worker_id() -> str:
''',
    '''def riyadh_date_range(now: datetime, days: int) -> tuple[date, date]:
    current = now.astimezone(_timezone(BUSINESS_TIMEZONE)).date()
    return current - timedelta(days=days - 1), current


def _tiktok_scheduler_state() -> dict[str, Any]:
    configured = tiktok_oauth_configured()
    enabled = configured and tiktok_reporting_enabled()
    if enabled:
        return {
            "mode": "native_polling",
            "status": "native_polling",
            "native_polling": True,
            "reason": None,
        }
    return {
        "mode": "automatic_webhook_feed",
        "status": "automatic_webhook_feed",
        "native_polling": False,
        "reason": (
            "native_reporting_disabled"
            if configured
            else "awaiting_tiktok_oauth_approval"
        ),
    }


def _worker_id() -> str:
''',
    "TikTok scheduler state helper",
)
source = replace_once(
    source,
    '''            "provider": {"$in": [META_PROVIDER_ID, SNAPCHAT_PROVIDER_ID]},
''',
    '''            "provider": {"$in": [
                META_PROVIDER_ID,
                SNAPCHAT_PROVIDER_ID,
                TIKTOK_PROVIDER_ID,
            ]},
''',
    "TikTok scheduler target query",
)
source = replace_once(
    source,
    '''        if user_id and provider in {META_PROVIDER_ID, SNAPCHAT_PROVIDER_ID}:
''',
    '''        if user_id and provider in {
            META_PROVIDER_ID,
            SNAPCHAT_PROVIDER_ID,
            TIKTOK_PROVIDER_ID,
        }:
''',
    "TikTok scheduler target allowlist",
)

refresh_tiktok = '''async def _refresh_tiktok(
    db: Any,
    *,
    user_id: str,
    start_date: date,
    end_date: date,
    now: datetime,
) -> dict[str, Any]:
    if not tiktok_oauth_configured() or not tiktok_reporting_enabled():
        return {
            "provider": TIKTOK_PROVIDER_ID,
            "status": "skipped",
            "reason": "disabled",
        }
    active = await _active_run(
        db, user_id=user_id, provider=TIKTOK_PROVIDER_ID, now=now
    )
    if active:
        return {
            "provider": TIKTOK_PROVIDER_ID,
            "status": "skipped",
            "reason": "sync_in_progress",
            "run_id": active.get("run_id"),
        }
    run_id = await _start_run(
        db,
        user_id=user_id,
        provider=TIKTOK_PROVIDER_ID,
        run_type=TIKTOK_RUN_TYPE,
        source_mode=TIKTOK_REPORTING_SOURCE_MODE,
        start_date=start_date,
        end_date=end_date,
        now=now,
    )
    try:
        result = await run_tiktok_reporting_sync(
            db,
            user_id,
            TikTokReportingSyncInput(
                days=(end_date - start_date).days + 1,
                from_date=start_date.isoformat(),
                to_date=end_date.isoformat(),
            ),
        )
        status = str(result.get("status") or "complete")
        if status not in {"complete", "partial"}:
            status = "complete"
        await _finish_run(
            db, user_id=user_id, run_id=run_id, status=status, result=result
        )
        return {
            "provider": TIKTOK_PROVIDER_ID,
            "run_id": run_id,
            "status": status,
            **_safe_summary(result),
        }
    except TikTokReportingError as exc:
        error_id = await _record_error(
            db,
            user_id=user_id,
            provider=TIKTOK_PROVIDER_ID,
            run_id=run_id,
            source_mode=TIKTOK_REPORTING_SOURCE_MODE,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
        )
        await _finish_run(
            db,
            user_id=user_id,
            run_id=run_id,
            status="failed",
            result=exc.result,
            error={
                "error_id": error_id,
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            },
        )
        if exc.code == "tiktok_needs_reauth":
            await _mark_needs_reauth(db, user_id, TIKTOK_PROVIDER_ID)
        return {
            "provider": TIKTOK_PROVIDER_ID,
            "run_id": run_id,
            "status": "failed",
            "code": exc.code,
        }


'''
source = replace_once(
    source,
    '''async def _refresh_snapchat(
''',
    refresh_tiktok + '''async def _refresh_snapchat(
''',
    "TikTok scheduled refresh function",
)
source = replace_once(
    source,
    '''            if provider == META_PROVIDER_ID:
                return await _refresh_meta(
                    db,
                    user_id=user_id,
                    start_date=start_date,
                    end_date=end_date,
                    now=started,
                )
            return await _refresh_snapchat(
''',
    '''            if provider == META_PROVIDER_ID:
                return await _refresh_meta(
                    db,
                    user_id=user_id,
                    start_date=start_date,
                    end_date=end_date,
                    now=started,
                )
            if provider == TIKTOK_PROVIDER_ID:
                return await _refresh_tiktok(
                    db,
                    user_id=user_id,
                    start_date=start_date,
                    end_date=end_date,
                    now=started,
                )
            return await _refresh_snapchat(
''',
    "TikTok scheduler dispatch",
)
source = replace_once(
    source,
    '''        "tiktok": {
            "status": "automatic_webhook_feed",
            "native_polling": False,
            "reason": "awaiting_tiktok_oauth_approval",
        },
''',
    '''        "tiktok": _tiktok_scheduler_state(),
''',
    "TikTok cycle status",
)
source = replace_once(
    source,
    '''            "provider": {"$in": [META_PROVIDER_ID, SNAPCHAT_PROVIDER_ID]},
''',
    '''            "provider": {"$in": [
                META_PROVIDER_ID,
                SNAPCHAT_PROVIDER_ID,
                TIKTOK_PROVIDER_ID,
            ]},
''',
    "TikTok scheduler status query",
)
source = replace_once(
    source,
    '''        "tiktok": {
            "mode": "automatic_webhook_feed",
            "native_polling": False,
            "reason": "awaiting_tiktok_oauth_approval",
        },
''',
    '''        "tiktok": _tiktok_scheduler_state(),
''',
    "TikTok scheduler status payload",
)

required_scheduler = (
    "TIKTOK_PROVIDER_ID",
    "TIKTOK_RUN_TYPE",
    "async def _refresh_tiktok(",
    "run_tiktok_reporting_sync(",
    '"tiktok": _tiktok_scheduler_state()',
)
for marker in required_scheduler:
    if marker not in source:
        raise SystemExit(f"Scheduler marker missing after patch: {marker}")
SCHEDULER.write_text(source, encoding="utf-8")


# 4) Host the bounded TikTok reporting control in the current Production card.
source = CARD.read_text(encoding="utf-8")
source = replace_once(
    source,
    '''import MetaReportingControl from "./MetaReportingControl";
''',
    '''import MetaReportingControl from "./MetaReportingControl";
import TikTokReportingSyncControl from "./TikTokReportingSyncControl";
''',
    "TikTok reporting control import",
)
source = replace_once(
    source,
    '''    const showMetaReporting = integration.provider === "meta_ads";
''',
    '''    const showMetaReporting = integration.provider === "meta_ads";
    const showTikTokReporting = integration.provider === "tiktok_ads";
''',
    "TikTok reporting control flag",
)
source = replace_once(
    source,
    '''            {showMetaReporting && (
                <div className="mt-4" data-testid="meta-reporting-control-host">
                    <MetaReportingControl integration={integration} />
                </div>
            )}

            <div className={`mt-auto grid grid-cols-2 gap-2 border-t border-slate-100 pt-4 ${
''',
    '''            {showMetaReporting && (
                <div className="mt-4" data-testid="meta-reporting-control-host">
                    <MetaReportingControl integration={integration} />
                </div>
            )}

            {showTikTokReporting && (
                <div className="mt-4" data-testid="tiktok-reporting-control-host">
                    <TikTokReportingSyncControl integration={integration} />
                </div>
            )}

            <div className={`mt-auto grid grid-cols-2 gap-2 border-t border-slate-100 pt-4 ${
''',
    "TikTok reporting control host",
)
CARD.write_text(source, encoding="utf-8")

print("PROD_TIKTOK_NATIVE_5M_PATCHED")
