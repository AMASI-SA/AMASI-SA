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
    async def probe():
        async with await db.client.start_session() as session:
            hello = await db.command("hello", session=session)
            if (not isinstance(hello, dict)
                or not (hello.get("setName") or hello.get("msg") == "isdbgrid")
                or type(hello.get("logicalSessionTimeoutMinutes")) is not int):
                raise ValueError("unsupported topology")
            session.start_transaction(read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"), max_commit_time_ms=4000)
            try:
                result = await db[PROBE_COLLECTION].insert_one(
                    {"_id": "storage_probe_" + uuid.uuid4().hex, "probe_only": True}, session=session)
                if result.acknowledged is not True:
                    raise ValueError("unacknowledged probe")
            finally:
                await session.abort_transaction()
        return _result(True)
    async def bounded_probe():
        with timeout(TIMEOUT_SECONDS):
            return await probe()

    task = asyncio.create_task(bounded_probe())
    # wait_for waits for cancellation cleanup; a stuck abort would then exceed
    # the HTTP deadline. Bound the response independently while the driver-owned
    # session cleans up only its own transaction. Never retry this probe.
    def consume(done):
        if not done.cancelled():
            done.exception()
    try:
        done, _ = await asyncio.wait({task}, timeout=TIMEOUT_SECONDS)
        if not done:
            task.cancel()
            task.add_done_callback(consume)
            raise TimeoutError()
        return task.result()
    except asyncio.CancelledError:
        task.cancel()
        task.add_done_callback(consume)
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
