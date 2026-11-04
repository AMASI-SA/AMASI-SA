"""Disconnect after durable supplier dispatch; exercise failover to a separate real HTTP process."""
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from benchmark_dashboard_contention import limit_network, make_app, seed

PATH = "/api/supplier-dispatch-v1/dispatches"
BODY = json.dumps({"client_request_id": "synthetic-lost-reply", "supplier_id": "supplier",
                   "file_number": "PF-0", "selections": [{"group_key": "piece:0-0", "quantity": 1}]}).encode()
HEADERS = {"x-test-actor": "writer", "content-type": "application/json"}

def mongo(port):
    return AsyncIOMotorClient(f"mongodb://127.0.0.1:{port}/?directConnection=true", serverSelectionTimeoutMS=8000)

def bind():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    return sock

async def serve(role, mongo_port, fd, marker):
    limit_network(mongo_port)
    import uvicorn
    client = mongo(mongo_port)
    app = make_app(client.synthetic_replay, order_read=True)
    async def withhold(scope, receive, send):
        async def checked(message):
            if scope.get("path") == PATH and message["type"] == "http.response.start" and message["status"] == 201:
                marker.write_text("201")  # Handler finished. No response bytes were forwarded.
                await asyncio.Future()   # The parent kills this process after inspecting B and Mongo.
            await send(message)
        await app(scope, receive, checked)
    try:
        server = uvicorn.Server(uvicorn.Config(withhold if role == "A" else app, lifespan="off",
                                                access_log=False, log_level="error", http="h11"))
        await server.serve(sockets=[socket.socket(fileno=fd)])
    finally:
        client.close()

async def run(temp, mongod, sha):
    ws = [bind(), bind()]
    ports = [s.getsockname()[1] for s in ws]
    with bind() as dbsock:
        dbport = dbsock.getsockname()[1]
    def private_net(event, args):
        if event == "socket.connect" and (args[1][0] != "127.0.0.1" or args[1][1] not in {dbport, *ports}):
            raise RuntimeError("Only this experiment's loopback sockets are permitted")
    sys.addaudithook(private_net)
    dbpath = temp / "mongo"
    dbpath.mkdir()
    dbproc = subprocess.Popen([mongod, "--dbpath", str(dbpath), "--bind_ip", "127.0.0.1",
                               "--port", str(dbport), "--nounixsocket", "--wiredTigerCacheSizeGB", "0.25",
                               "--logpath", str(temp / "mongo.log")], stdout=subprocess.DEVNULL)
    children = []
    client = mongo(dbport)
    try:
        import preparation_supplier_dispatch as dispatch
        db = client.synthetic_replay
        assert await db.list_collection_names() == []
        await seed(db, count=1, orders=1)
        marker = temp / "prepared-201"
        for sock, role in zip(ws, "AB"):
            children.append(subprocess.Popen([sys.executable, __file__, "serve", role, str(dbport),
                                              str(sock.fileno()), str(marker)], pass_fds=(sock.fileno(),)))
            sock.close()
        a, b = children
        assert a.pid != b.pid
        base_a, base_b = [f"http://127.0.0.1:{p}" for p in ports]
        async with httpx.AsyncClient(trust_env=False, timeout=20) as http:
            for child, url in zip(children, [base_a, base_b]):
                async with asyncio.timeout(20):
                    while True:
                        assert child.poll() is None, child.returncode
                        try:
                            if (await http.get(url + "/bench/health")).status_code == 200:
                                break
                        except httpx.ConnectError:
                            pass
                        await asyncio.sleep(.03)
            async def read_b():
                order, work = await asyncio.gather(http.get(base_b + "/api/orders-v2/A"),
                    http.get(base_b + "/api/supplier-dispatch-v1/workspace?grain=piece", headers=HEADERS))
                assert order.status_code == 200 and order.json()["order_number"] == "A", order.text[:250]
                assert work.status_code == 200 and work.json()["ok"], work.text[:250]
                assert work.json()["qoyod_updated"] is False and work.json()["salla_updated"] is False
                return {"order_http": 200, "workspace_http": 200, "stored_dispatch_ids": [
                    item["id"] for acct in work.json()["supplier_accounts"] for item in acct["dispatches"]]}
            async def persisted():
                counts = [await db[col].count_documents({}) for col in
                          (dispatch.DISPATCHES, dispatch.DISPATCH_EVENTS, dispatch.PIECE_EVENTS)]
                assert counts == [1, 1, 1], counts
                row = await db[dispatch.DISPATCHES].find_one({"client_request_id": "synthetic-lost-reply"})
                assert row["status"] == "sent" and row["piece_ids"] == ["0-0"]
                event = await db[dispatch.DISPATCH_EVENTS].find_one({})
                assert event["event_type"] == "preparation_pieces_sent_to_supplier" and event["dispatch_id"] == row["id"]
                return row["id"]
            phases = {"before_submit": await read_b()}
            assert phases["before_submit"]["stored_dispatch_ids"] == []
            background_probes = []
            stopped = asyncio.Event()
            async def poll():
                while not stopped.is_set():
                    background_probes.append(await read_b())
                    await asyncio.sleep(.005)
            poll_task = asyncio.create_task(poll())
            original = asyncio.create_task(http.post(base_a + PATH, content=BODY, headers=HEADERS))
            try:
                async with asyncio.timeout(12):
                    while not marker.exists():
                        if original.done():
                            reply = await original
                            raise AssertionError(f"Unexpected original HTTP response: {reply.status_code} {reply.text[:250]}")
                        await asyncio.sleep(.005)
                assert marker.read_text() == "201"
                stored = await persisted()
                phases["A_withholding_201"] = await read_b()
                a.kill()
                assert await asyncio.to_thread(a.wait, 5) == -signal.SIGKILL
                try:
                    await original
                except (httpx.RemoteProtocolError, httpx.ReadError) as error:
                    lost = type(error).__name__
                else:
                    raise AssertionError("First HTTP client unexpectedly received confirmation")
                phases["A_terminated_before_retry"] = await read_b()
                replay = await http.post(base_b + PATH, content=BODY, headers=HEADERS)
                assert replay.status_code == 201 and replay.json()["ok"] and replay.json()["dispatch"]["id"] == stored
                assert await persisted() == stored
                phases["after_B_replay"] = await read_b()
                for label in ("A_withholding_201", "A_terminated_before_retry", "after_B_replay"):
                    assert phases[label]["stored_dispatch_ids"] == [stored]
            finally:
                stopped.set()
                await poll_task
            assert b.poll() is None and background_probes
        return {"source_sha": sha, "database": "private loopback MongoDB " + (await client.server_info())["version"],
                "distinct_web_processes": True, "A_exit_signal": "SIGKILL", "A_withheld_http": 201,
                "A_client_error": lost, "B_replay_http": 201, "identical_body_bytes": True,
                "same_dispatch_id": True, "dispatches": 1, "dispatch_events": 1, "piece_events": 1,
                "B_continuous_order_and_workspace_pairs": len(background_probes),
                "phases": {k: {"order_http": v["order_http"], "workspace_http": v["workspace_http"],
                               "visible_dispatches": len(v["stored_dispatch_ids"])} for k, v in phases.items()}}
    finally:
        for s in ws: s.close()
        for proc in reversed(children):
            if proc.poll() is None: proc.terminate()
            try: proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        client.close()
        dbproc.terminate()
        dbproc.wait(timeout=15)

if __name__ == "__main__":
    if sys.argv[1] == "serve":
        asyncio.run(serve(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), Path(sys.argv[5])))
    else:
        with tempfile.TemporaryDirectory(prefix="mz-replay-") as temp:
            print(json.dumps(asyncio.run(run(Path(temp), *sys.argv[1:])), indent=2))
