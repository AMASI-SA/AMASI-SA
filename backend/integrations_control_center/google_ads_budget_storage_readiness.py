"""Owner-requested storage diagnostic; never grants execution authority."""
import asyncio
import uuid

from fastapi import Depends
from pymongo import timeout
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .google_ads_automation_policy import _owner
from .google_ads_management import problem

PROBE_COLLECTION = "google_ads_budget_storage_readiness_probes"
TIMEOUT_SECONDS = 8
# One strong reference bounds both active probes and delayed session cleanup.
# A timed-out task retains this slot until its original task exits CSOT/session.
_active_probe = None


def _result(verified):
    return {"scope": "budget_storage_transaction_only", "storage_verified": verified,
        "snapshot_majority_write_aborted": verified, "execution_authorized": False,
        "funding_ready": False, "policy_changed": False, "provider_writes": 0}


async def verify_budget_storage(db):
    """Write one unique probe inside our own transaction, then explicitly abort.

    Never commit, delete, reuse a caller session, or issue a credential/provider
    request. No capability object escapes this diagnostic. A successful abort
    acknowledgement is mandatory for success; any uncertainty fails closed.
    """
    global _active_probe
    if _active_probe is not None and not _active_probe.done():
        raise problem("google_budget_storage_probe_busy",
            "يوجد تحقق تخزين لم ينتهِ تنظيف جلسته بعد؛ لم يبدأ تحقق جديد.", 503)

    async def probe():
        session = await db.client.start_session()
        try:
            hello = await db.command("hello", session=session)
            if (not isinstance(hello, dict)
                or not (hello.get("setName") or hello.get("msg") == "isdbgrid")
                or type(hello.get("logicalSessionTimeoutMinutes")) is not int):
                raise ValueError("unsupported topology")
            session.start_transaction(read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"), max_commit_time_ms=4000)
            result = await db[PROBE_COLLECTION].insert_one(
                {"_id": "storage_probe_" + uuid.uuid4().hex, "probe_only": True}, session=session)
            if result.acknowledged is not True:
                raise ValueError("unacknowledged probe")
            # Generic command preserves the server response instead of the
            # driver's abort helper, which intentionally suppresses some errors.
            # Public session machinery adds the same lsid/txnNumber/autocommit.
            reply = await db.client.admin.command(
                {"abortTransaction": 1, "writeConcern": {"w": "majority", "wtimeout": 4000}},
                session=session)
            if (not isinstance(reply, dict) or type(reply.get("ok")) not in (int, float)
                or reply["ok"] != 1 or "writeConcernError" in reply or "writeErrors" in reply):
                raise ValueError("abort acknowledgement unproved")
        finally:
            # Motor's async context __aexit__ delegates synchronously. Its public
            # async end_session instead runs driver cleanup off the event loop.
            # Cleanup may safely abort our own transaction again; it never turns
            # a failed/unknown explicit abort response into diagnostic success.
            cleanup = asyncio.ensure_future(session.end_session())
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    # Cancelling a Motor Future does not stop its executor
                    # thread. Keep that Future alive/awaitable and retain our
                    # per-process slot until the actual cleanup completes.
                    cancelled = True
            cleanup.result()
            if cancelled:
                raise asyncio.CancelledError()
        return _result(True)
    async def bounded_probe():
        with timeout(TIMEOUT_SECONDS):
            return await probe()

    task = asyncio.create_task(bounded_probe())
    _active_probe = task
    # wait_for waits for cancellation cleanup; a stuck abort would then exceed
    # the HTTP deadline. Bound the response independently while the driver-owned
    # session finishes only its own work/cleanup. Do not cancel driver Futures
    # on the HTTP deadline: cancellation cannot stop executor threads. Never retry.
    def consume(done):
        global _active_probe
        if not done.cancelled():
            done.exception()
        if _active_probe is done:
            _active_probe = None
    task.add_done_callback(consume)
    try:
        done, _ = await asyncio.wait({task}, timeout=TIMEOUT_SECONDS)
        if not done:
            raise TimeoutError()
        return task.result()
    except asyncio.CancelledError:
        # Request cancellation must not cancel executor-backed driver Futures.
        # The retained probe owns its session until its CSOT work/cleanup ends.
        raise
    except Exception:
        # Do not expose driver messages, connection strings, hosts or DB names.
        raise problem("google_budget_storage_unverified",
            "تعذر إثبات معاملة التخزين وإلغائها. لم يُمنح أي تفويض تشغيل؛ يلزم تحقق تقني من التخزين.", 503) from None


def attach_google_ads_budget_storage_readiness_routes(router, db, current_user):
    @router.post("/google_ads/automation/storage-readiness")
    async def verify(user=Depends(current_user)):
        await _owner(db, user)
        return await verify_budget_storage(db)
