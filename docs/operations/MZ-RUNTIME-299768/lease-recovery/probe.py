"""Standalone A/B against a private MongoDB and spawned Python workers. No provider I/O."""
import asyncio, json, os, signal, socket, subprocess, sys, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["MEZAN_ADS_AUTO_SYNC_ENABLED"] = "true"
from motor.motor_asyncio import AsyncIOMotorClient
from integrations_control_center import ads_auto_sync_scheduler as scheduler

TTL = timedelta(seconds=2.5)
POLL = 0.1
scheduler.LEASE_TTL = TTL
scheduler.HEARTBEAT_SECONDS = POLL
scheduler.interval_seconds = lambda: 1
scheduler.startup_delay_seconds = lambda: 0


def original_scheduler():
    import types
    mod = types.ModuleType("integrations_control_center.original_candidate")
    mod.__package__ = "integrations_control_center"
    source = subprocess.check_output(["git", "show", "d5531f3745e17dcb81dd50ba70239e5bc54ff5fe:backend/integrations_control_center/ads_auto_sync_scheduler.py"])
    exec(compile(source, "<baseline>", "exec"), mod.__dict__)
    mod.LEASE_TTL = TTL
    mod.interval_seconds = lambda: 1
    return mod


def client(port):
    return AsyncIOMotorClient(f"mongodb://127.0.0.1:{port}/?directConnection=true", tz_aware=True, serverSelectionTimeoutMS=10000)


async def holder(port, db_name, skew):
    c = client(port)
    native_release = scheduler._release_lease
    result_saved = asyncio.Event()
    async def hold(_db, *, on_progress):
        print("entered", flush=True)
        command = (await asyncio.to_thread(sys.stdin.readline)).strip()
        if command != "finish":
            raise AssertionError(command)
        return {"status": "complete", "probe": "holder"}
    async def release(*args, **kwargs):
        await native_release(*args, **kwargs)
        result_saved.set()
    scheduler._utcnow = lambda: datetime.now(timezone.utc) + timedelta(days=int(skew))
    try:
        with patch.object(scheduler, "run_auto_sync_cycle", hold), patch.object(scheduler, "_release_lease", release):
            task = asyncio.create_task(scheduler.auto_sync_loop(c[db_name]))
            await result_saved.wait()
            print("released", flush=True)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    finally:
        c.close()


async def main(mongod):
    with tempfile.TemporaryDirectory(prefix="mz1724-fixed-lease-") as td, open(os.devnull, "wb") as log:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        mongo = subprocess.Popen([mongod,"--dbpath",td,"--bind_ip","127.0.0.1","--port",str(port),"--nounixsocket","--wiredTigerCacheSizeGB","0.25"],stdout=log,stderr=log)
        c, spawned = client(port), []
        try:
            await c.admin.command("ping")
            assert mongo.poll() is None and (await c.admin.command("getCmdLineOpts"))["parsed"]["storage"]["dbPath"] == td
            result = {"mongo": (await c.admin.command("buildInfo"))["version"], "test_ttl_seconds": TTL.total_seconds(), "test_heartbeat_seconds": POLL}
            async def server_now(): return (await c.admin.command("hello"))["localTime"].replace(tzinfo=timezone.utc)
            async def pause_until(when):
                while await server_now() < when:
                    await asyncio.sleep(0.04)
            async def row(db):
                return await db[scheduler.SCHEDULER_COLLECTION].find_one({"_id":scheduler.SCHEDULER_ID})
            async def start(db, skew=0):
                proc = await asyncio.create_subprocess_exec(sys.executable, "-B", __file__, "--holder", str(port), db.name, str(skew), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
                spawned.append(proc)
                assert (await asyncio.wait_for(proc.stdout.readline(), 12)).strip() == b"entered"
                return proc

            old = original_scheduler()
            db = c.baseline_skew
            clock = await server_now()
            assert await old._acquire_lease(db, worker_id="original", now=clock)
            assert await old._acquire_lease(db, worker_id="fast-client", now=clock+timedelta(days=1))
            result["original_fast_client_stole_live_lease"] = True

            # Initial simultaneous upserts race on a brand-new Mongo singleton.
            db = c.empty_singleton
            wins = await asyncio.gather(*(scheduler._acquire_lease(db, worker_id=f"initial-{i}") for i in range(15)))
            assert sum(wins) == 1 and await db[scheduler.SCHEDULER_COLLECTION].count_documents({}) == 1
            result["first_acquisition_winners_of_15"] = sum(wins)

            malformed = [
                {"lease_expires_at":"1970-01-01T00:00:00Z"},
                {"next_due_at":"1970-01-01T00:00:00Z"}, {"next_due_at":None},
            ]
            for i, fields in enumerate(malformed):
                db = c[f"invalid_date_{i}"]
                await db[scheduler.SCHEDULER_COLLECTION].insert_one({"_id":scheduler.SCHEDULER_ID, **fields})
                assert not await scheduler._acquire_lease(db, worker_id="malformed")
                assert await row(db) == {"_id":scheduler.SCHEDULER_ID, **fields}
            db = c.valid_null_expiry
            await db[scheduler.SCHEDULER_COLLECTION].insert_one({"_id":scheduler.SCHEDULER_ID,"lease_expires_at":None})
            assert await scheduler._acquire_lease(db, worker_id="legacy-null")
            result["date_shape_parity"] = "rejected ISO strings and explicit null next_due; accepted legacy null expiry and missing next_due"

            # Two days of client clock disagreement cannot alter Mongo lease eligibility.
            db = c.long_with_skew
            proc = await start(db, skew=1)
            first = await row(db)
            old_owner = first["lease_owner"]
            assert abs(((await server_now()) - first["last_started_at"]).total_seconds()) < 2
            fast_clock = lambda: datetime.now(timezone.utc)+timedelta(days=1)
            slow_clock = lambda: datetime.now(timezone.utc)-timedelta(days=1)
            with patch.object(scheduler, "_utcnow", fast_clock):
                assert not await scheduler._acquire_lease(db, worker_id="fast-rival-1")
            await pause_until(first["lease_expires_at"]+timedelta(seconds=0.2))
            active = await row(db)
            assert active["lease_owner"] == old_owner and active["lease_expires_at"] > first["lease_expires_at"] + timedelta(seconds=1)
            assert active["lease_expires_at"] > await server_now()
            assert not await scheduler._acquire_lease(db, worker_id="fast-rival-2")
            assert proc.returncode is None
            result["healthy_long_cycle"] = "renewed past original deadline; rival denied"
            result["new_clock_skew"] = "first acquisition uses server time; fast client denied"
            # Real abrupt process death, then verify expiry and late CAS.
            proc.kill()
            assert await proc.wait() == -signal.SIGKILL
            at_crash = await row(db)
            assert not await scheduler._acquire_lease(db, worker_id="early")
            await pause_until(at_crash["lease_expires_at"]+timedelta(milliseconds=120))
            with patch.object(scheduler,"_utcnow",slow_clock):
                try: await scheduler._renew_lease(db, old_owner)
                except scheduler._SchedulerLeaseUnconfirmed: pass
                else: raise AssertionError("expired old owner revived lease")
            successor = await start(db, skew=-1)
            owned = await row(db)
            assert owned["lease_owner"] != old_owner and owned["status"] == "running"
            try: await scheduler._release_lease(db, worker_id=old_owner, result={"status":"complete","probe":"stale"})
            except scheduler._SchedulerLeaseUnconfirmed: pass
            else: raise AssertionError("stale write accepted")
            assert await row(db) == owned
            successor.stdin.write(b"finish\n")
            await successor.stdin.drain()
            assert (await asyncio.wait_for(successor.stdout.readline(), 8)).strip() == b"released"
            assert await successor.wait() == 0
            complete = await row(db)
            assert complete["lease_owner"] == owned["lease_owner"] and complete["last_result"] == {"status":"complete","probe":"holder"}
            result["crash_recovery"] = "SIGKILL, denied before expiry; newly spawned slow-clock process acquired after expiry and completed"
            result["stale_renewal_and_release"] = "expired owner denied; successor document preserved"
            db = c.long_normal_finish
            live = await start(db)
            await pause_until((await row(db))["lease_expires_at"]+timedelta(milliseconds=200))
            live.stdin.write(b"finish\n")
            await live.stdin.drain()
            assert (await asyncio.wait_for(live.stdout.readline(), 8)).strip() == b"released" and await live.wait() == 0
            assert (await row(db))["last_result"] == {"status":"complete","probe":"holder"}
            result["actual_long_process_completion"] = "original owner recorded own result after original TTL"
            print(json.dumps(result,indent=2))
        finally:
            for proc in spawned:
                if proc.returncode is None:
                    proc.kill(); await proc.wait()
            c.close()
            if mongo.poll() is None:
                mongo.terminate()
                try: mongo.wait(timeout=15)
                except subprocess.TimeoutExpired: mongo.kill(); mongo.wait()

if __name__=="__main__":
    if sys.argv[1]=="--holder": asyncio.run(holder(*sys.argv[2:]))
    else: asyncio.run(main(sys.argv[1]))
