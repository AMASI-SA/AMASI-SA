"""Real isolated Mongo arbitration for request-owned refresh leases."""
import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from operational_demand_refresh import DemandRefreshContext, LEASES
from test_operational_balance_demand_refresh import Request, allowed
from test_operational_balance_integration import run


def test_real_mongo_competing_refreshes_and_expired_token_takeover():
    async def scenario(db):
        stamp = datetime(2026, 11, 12, tzinfo=timezone.utc)
        # Independent context/DB handles share no process mutex or token.
        contexts = [DemandRefreshContext(Request(), db.client[db.name], "owner", actor, allowed,
                                         clock=lambda: stamp) for actor in ("owner", "staff")]
        outcomes = await asyncio.gather(*(context.acquire() for context in contexts), return_exceptions=True)
        winners = [context for context, outcome in zip(contexts, outcomes) if outcome is None]
        errors = [outcome for outcome in outcomes if isinstance(outcome, HTTPException)]
        assert len(winners) == len(errors) == 1
        assert errors[0].detail["code"] == "operational_refresh_in_progress"
        winner = winners[0]
        assert await db[LEASES].count_documents({}) == 1
        for context in contexts:
            if context is not winner:
                await context.release()
        assert (await db[LEASES].find_one({"_id": "owner"}))["token"] == winner.token

        successor = DemandRefreshContext(Request(), db.client[db.name], "owner", "owner", allowed,
                                         clock=lambda: stamp + timedelta(minutes=3))
        await successor.acquire()
        try:
            await winner.checkpoint()
        except HTTPException as error:
            assert error.detail["code"] == "operational_refresh_lease_lost"
        else:
            raise AssertionError("old request renewed a successor's lease")
        await winner.release()
        assert (await db[LEASES].find_one({"_id": "owner"}))["token"] == successor.token
        await successor.release()
        assert await db[LEASES].count_documents({}) == 0
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)
