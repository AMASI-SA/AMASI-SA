"""Isolated, synthetic A/B evidence for received supplier-cost reporting.

This measures the three report cost computations, not HTTP, provider latency or
production capacity. Both arms read their own identically seeded disposable
loopback Mongo database. The baseline is the immutable pre-performance helper
and index set; the candidate uses the working tree. No provider or live data is
accessible through the read-only report database wrapper.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import threading
import time
import tracemalloc
from types import ModuleType
from urllib.parse import urlparse
from uuid import uuid4
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import monitoring
from pymongo.uri_parser import parse_uri

import accounting_ledger_v2 as ledger
import dashboard_v2_routes as dashboard
import operational_balance_sources
from preparation_piece_operations import ensure_piece_operation_indexes
import sold_products_report_v2 as sold
import supplier_invoice_reporting_costs as candidate
import supplier_native_invoice_v2 as native
from supplier_receiving_routes import ensure_supplier_receiving_indexes

BASE = "bcdd6e501e629ee715667c39d1b43aab5b263914"
OWNER = "synthetic-supplier-report-benchmark"
APPROVED = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
AS_OF = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
PRODUCT_COUNT = 12
NEW_INDEXES = {
    candidate.PIECES: ["ix_preparation_piece_order_cost_v1"],
    candidate.INVOICES: ["ix_supplier_invoice_identity_v2"],
    ledger.GROUPS_COLLECTION: ["ix_accounting_v2_group_identity", "ix_accounting_v2_group_reversal_proof"],
}


def fixture(order_count=6000, *, history_count=6000):
    """Two units/sale; 0, 1, 2, 2 received units in alternating product blocks.

    Invoice product price includes the selected direct option. Half the
    products have two service units costing 3 each (current estimate 3.50).
    Internal packaging is independently 2/unit and is outside invoice costs.
    Invoices group 200 physical units/product, crossing many order read batches.
    """
    data = defaultdict(list)
    data[candidate.SUPPLIERS].append({"user_id": OWNER, "id": "supplier"})
    data[dashboard.RESOURCES] = [
        {"user_id": OWNER, "id": "bag", "name": "Packaging", "kind": "component", "unit_cost": 2},
        {"user_id": OWNER, "id": "service", "name": "Customization", "kind": "service", "unit_cost": 3.5},
    ]
    for product_index in range(PRODUCT_COUNT):
        pid = f"product-{product_index:02}"
        options = [{"id": "direct", "name": "Finish", "type": "radio", "values": [{"id": "yes", "name": "Selected"}]}]
        if product_index % 2:
            options.append({"id": "service-option", "name": "Customize", "type": "radio", "values": [{"id": "yes", "name": "Selected"}]})
        data[candidate.PRODUCTS].append({"user_id": OWNER, "id": pid, "salla_product_id": pid,
            "mezan_product_id": pid, "name": "Synthetic " + pid, "sku": "SYN-" + pid,
            "options": options, "cost_price_from_salla": 100})
        data[dashboard.COST_PROFILES].append({"user_id": OWNER, "salla_product_id": pid, "base_cost": 20 + product_index})
        data[dashboard.PRODUCT_RESOURCE_BINDINGS].append({"user_id": OWNER, "salla_product_id": pid,
            "id": "bag-" + pid, "resource_id": "bag", "quantity": 1})
        data[dashboard.BINDINGS].append({"user_id": OWNER, "salla_product_id": pid,
            "id": "direct-" + pid, "option_id": "direct", "value_id": "yes", "mode": "direct", "direct_amount": 3, "quantity": 1})
        if product_index % 2:
            data[dashboard.BINDINGS].append({"user_id": OWNER, "salla_product_id": pid,
                "id": "service-" + pid, "option_id": "service-option", "value_id": "yes",
                "mode": "resource", "resource_id": "service", "quantity": 2})
    pieces_by_product = defaultdict(list)
    orders, expected = [], []
    for index in range(order_count):
        product_index = index % PRODUCT_COUNT
        pid = f"product-{product_index:02}"
        number = f"sale-{index:06}"
        received = (0, 1, 2, 2)[(index // PRODUCT_COUNT) % 4]
        choices = [{"option_id": "direct", "value_id": "yes"}]
        if product_index % 2:
            choices.append({"option_id": "service-option", "value_id": "yes"})
        item = {"order_item_id": "line", "product_id": pid, "quantity": 2, "total": 200,
            "sku": "SYN-" + pid, "name": "Synthetic " + pid, "options_raw": choices}
        order = {"user_id": OWNER, "order_number": number, "order_id": number,
            "order_date": f"2026-{'10' if index < order_count // 2 else '11'}-{index % 18 + 1:02}",
            "order_status": "تم التنفيذ", "currency": "SAR", "total_amount": 200, "products": [item]}
        orders.append(order)
        actual_unit = 19 + product_index + (6 if product_index % 2 else 0) + 2
        estimated_unit = 20 + product_index + 3 + (7 if product_index % 2 else 0) + 2
        expected.append({"product_id": pid, "cost": received * actual_unit + (2 - received) * estimated_unit,
            "received_units": received, "estimated_units": 2 - received, "sales": 200})
        for unit in range(1, received + 1):
            piece_id = f"{number}:unit:{unit}"
            piece = {"user_id": OWNER, "id": piece_id, "piece_id": piece_id, "batch_id": "synthetic-batch",
                "order_number": number, "order_item_id": "line", "unit_index": unit, "product_id": pid,
                "options_raw": deepcopy(choices), "services": [], "received_at": APPROVED,
                "service_plan_status": "completed", "remaining_service_count": 0,
                "supplier_dispatch_status": "received"}
            pieces_by_product[product_index].append(piece)
            data[candidate.PIECES].append(piece)
    for product_index, pieces in pieces_by_product.items():
        for offset in range(0, len(pieces), 200):
            selected = pieces[offset:offset + 200]
            invoice_id = f"invoice-{product_index:02}-{offset:05}"
            pid = f"product-{product_index:02}"
            services = [{"service_id": "service", "service_name": "Customization",
                "quantity_per_piece": 2, "unit_price_halalas": 300, "total_halalas": 600 * len(selected)}] if product_index % 2 else []
            product_total = (1900 + product_index * 100) * len(selected)
            service_total = 600 * len(selected) if services else 0
            inv = {"user_id": OWNER, "id": invoice_id, "invoice_number": "SYN-" + invoice_id,
                "session_id": "session-" + invoice_id, "supplier_id": "supplier", "approved_at": APPROVED,
                "status": "payable_posted", "currency": "SAR", "experiment_mode": False,
                "financial_integrity_verified": True, "mz2_financial_contract": "mz2_supplier_invoice_v1",
                "mz2_txn_group_id": "group-" + invoice_id,
                "subtotal_halalas": product_total + service_total, "total_halalas": product_total + service_total,
                "line_count": 1, "piece_count": len(selected),
                "lines": [{"product_id": pid, "piece_ids": [p["piece_id"] for p in selected], "quantity": len(selected),
                    "product_price_authority": "mezan_v2", "product_charge_eligible": True,
                    "product_unit_price_halalas": 1900 + product_index * 100,
                    "product_total_halalas": product_total, "services_total_halalas": service_total,
                    "total_halalas": product_total + service_total, "services": services}]}
            data[candidate.INVOICES].append(inv)
            data[ledger.GROUPS_COLLECTION].append({"user_id": OWNER, "txn_group_id": inv["mz2_txn_group_id"],
                "operation_id": "synthetic-operation", "idempotency_key": invoice_id,
                "status": "posted", "txn_type": "supplier_invoice",
                "metadata": {"supplier_invoice_id": invoice_id, "invoice_digest": native.invoice_digest(inv)}})
            for piece in selected:
                piece["supplier_receiving_history"] = [{"invoice_id": invoice_id}]
                if services:
                    piece["services"] = [{"service_id": "service", "status": "completed", "required_quantity": 2,
                        "completed_quantity": 2, "supplier_invoice_id": invoice_id}]
                data[candidate.EVENTS].append({"user_id": OWNER, "id": "event-" + piece["piece_id"],
                    "event_type": "supplier_piece_service_recorded", "session_id": inv["session_id"],
                    **{key: piece[key] for key in ("piece_id", "order_number", "order_item_id", "unit_index", "product_id", "options_raw")},
                    "supplier_invoice_id": invoice_id, "supplier_context": {"id": "supplier"},
                    "finalized_at": APPROVED, "financial_invoice_created": True, "liability_created": True})
    # Same-owner unrelated historic records reveal broad owner-prefix scans.
    # All identifiers are disjoint; no selected order can use their evidence.
    for index in range(history_count):
        identity = f"unselected-history-{index:06}"
        data[candidate.PIECES].append({"user_id": OWNER, "batch_id": identity, "order_number": identity,
            "order_item_id": "line", "unit_index": 1, "id": identity, "piece_id": identity})
        data[candidate.INVOICES].append({"user_id": OWNER, "id": identity, "session_id": identity,
            "invoice_number": identity, "supplier_id": "supplier", "lines": [{"piece_ids": [identity]}]})
        data[ledger.GROUPS_COLLECTION].append({"user_id": OWNER, "operation_id": "historic-operation",
            "idempotency_key": identity, "txn_group_id": identity, "status": "posted"})
    return orders, dict(data), expected


def frozen_helper():
    source = subprocess.check_output(["git", "show", BASE + ":backend/supplier_invoice_reporting_costs.py"], cwd=ROOT)
    module = ModuleType("frozen_supplier_cost_benchmark")
    module.__file__ = BASE + ":backend/supplier_invoice_reporting_costs.py"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module, hashlib.sha256(source).hexdigest()


class WireCommands(monitoring.CommandListener):
    def __init__(self):
        self.active = None
        self.lock = threading.Lock()

    def started(self, event):
        with self.lock:
            if self.active is not None:
                self.active[event.command_name] += 1
                if event.command_name in {"insert", "update", "delete", "findAndModify", "createIndexes", "dropIndexes"}:
                    self.active["unexpected_write"] += 1

    def succeeded(self, event):
        with self.lock:
            if self.active is not None and event.command_name in {"find", "aggregate", "getMore"}:
                cursor = event.reply.get("cursor", {})
                self.active["wire_documents"] += len(cursor.get("firstBatch", cursor.get("nextBatch", [])))

    def failed(self, event):
        with self.lock:
            if self.active is not None:
                self.active["failed_commands"] += 1


class ReadCursor:
    def __init__(self, cursor, metrics, collection):
        self.cursor, self.metrics, self.collection = cursor, metrics, collection

    async def to_list(self, length):
        rows = await self.cursor.to_list(length=length)
        self.metrics["documents:" + self.collection] += len(rows)
        return rows

    def __aiter__(self):
        return self

    async def __anext__(self):
        row = await self.cursor.__anext__()
        self.metrics["documents:" + self.collection] += 1
        return row

    def __getattr__(self, name):
        raise AssertionError("Unexpected report cursor operation: " + name)


class ReadCollection:
    def __init__(self, collection, metrics, name):
        self.collection, self.metrics, self.name = collection, metrics, name

    def find(self, query, projection=None, **kwargs):
        assert query.get("user_id") == OWNER, (self.name, query)
        self.metrics["find:" + self.name] += 1
        return ReadCursor(self.collection.find(query, projection, **kwargs), self.metrics, self.name)

    async def find_one(self, query, projection=None, **kwargs):
        assert query.get("user_id") == OWNER, (self.name, query)
        self.metrics["find_one:" + self.name] += 1
        row = await self.collection.find_one(query, projection, **kwargs)
        self.metrics["documents:" + self.name] += int(row is not None)
        return row

    def aggregate(self, pipeline, **kwargs):
        assert pipeline[0].get("$match", {}).get("user_id") == OWNER
        assert all("$out" not in stage and "$merge" not in stage for stage in pipeline)
        self.metrics["aggregate:" + self.name] += 1
        return ReadCursor(self.collection.aggregate(pipeline, **kwargs), self.metrics, self.name)

    def __getattr__(self, name):
        raise AssertionError("Reporting must not perform " + name)


class ReadDB:
    def __init__(self, db):
        self.db, self.metrics = db, Counter()

    def __getitem__(self, name):
        return ReadCollection(self.db[name], self.metrics, name)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


@contextmanager
def helper_arm(module, metrics):
    allocate, digest = operational_balance_sources.invoice_component_amounts, native.invoice_digest

    def counted_allocate(*args, **kwargs):
        metrics["invoice_allocations"] += 1
        return allocate(*args, **kwargs)

    def counted_digest(*args, **kwargs):
        metrics["invoice_digests"] += 1
        return digest(*args, **kwargs)

    with patch.object(module, "_now", lambda: AS_OF), \
            patch.object(dashboard, "load_supplier_invoice_reporting_costs", module.load_supplier_invoice_reporting_costs), \
            patch.object(dashboard, "resolve_supplier_invoice_reporting_cost", module.resolve_supplier_invoice_reporting_cost), \
            patch.object(sold, "load_supplier_invoice_reporting_costs", module.load_supplier_invoice_reporting_costs), \
            patch.object(sold, "resolve_supplier_invoice_reporting_cost", module.resolve_supplier_invoice_reporting_cost), \
            patch.object(operational_balance_sources, "invoice_component_amounts", counted_allocate), \
            patch.object(native, "invoice_digest", counted_digest):
        yield


def output_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def oracle(value, surface, expected):
    total = sum(row["cost"] for row in expected)
    units = len(expected) * 2
    if surface == "sold":
        assert len(value) == PRODUCT_COUNT
        assert all(row["total_cost"] is not None for row in value), value
        assert sum(row["total_cost"] for row in value) == total
        assert sum(row["units_sold"] for row in value) == units
        for row in value:
            pid = row.get("product_id") or row.get("salla_product_id")
            assert row["total_cost"] == sum(x["cost"] for x in expected if x["product_id"] == pid), row
    else:
        assert value["total"] == total, (surface, value["total"], total)
        assert value["missing_product_cost_count"] == value["incomplete_orders_count"] == 0
        if surface == "top-products":
            assert len(value["product_rows"]) == PRODUCT_COUNT
            assert value["product_profit_summary"]["total_cost"] == total


async def build(db, orders, surface):
    if surface == "sold":
        return await sold.load_sold_products(db, OWNER, orders)
    return await dashboard.build_mezan_v2_product_cost_cooperative(
        db, OWNER, orders, include_product_rows=surface == "top-products")


def rss_bytes():
    # Linux runner resident pages, not virtual address space.
    return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")


async def measured_build(db, orders, surface, expected):
    readonly = ReadDB(db)
    peak_rss, max_loop_delay = rss_bytes(), 0.0
    finished = False

    async def sampler():
        nonlocal peak_rss, max_loop_delay
        previous = time.perf_counter()
        while not finished:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            max_loop_delay = max(max_loop_delay, now - previous - 0.01)
            peak_rss = max(peak_rss, rss_bytes())
            previous = now

    task = asyncio.create_task(sampler())
    start = time.perf_counter()
    try:
        value = await build(readonly, orders, surface)
        elapsed = time.perf_counter() - start
    finally:
        finished = True
        await task
    oracle(value, surface, expected)
    return {"elapsed_seconds": elapsed, "sampled_rss_bytes": peak_rss,
        "max_event_loop_delay_seconds": max_loop_delay, "logical_reads": dict(readonly.metrics),
        "output_sha256": output_hash(value)}, value


async def seed(db, data, *, baseline=False):
    assert db.name.startswith("supplier_report_perf_")
    for name, rows in data.items():
        if rows:
            await db[name].insert_many(deepcopy(rows))
    await ensure_piece_operation_indexes(db)
    await ensure_supplier_receiving_indexes(db)
    await ledger.ensure_accounting_ledger_v2_indexes(db)
    if baseline:
        for collection, indexes in NEW_INDEXES.items():
            for index in indexes:
                await db[collection].drop_index(index)


def plan_summary(explain):
    stages, indexes = set(), set()

    def visit(value):
        if isinstance(value, dict):
            if "stage" in value:
                stages.add(value["stage"])
            if "indexName" in value:
                indexes.add(value["indexName"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(explain["queryPlanner"]["winningPlan"])
    stats = explain["executionStats"]
    return {"stages": sorted(stages), "indexes": sorted(indexes), "returned": stats["nReturned"],
        "keys_examined": stats["totalKeysExamined"], "documents_examined": stats["totalDocsExamined"]}


async def explain_reads(db, data, orders):
    numbers = [order["order_number"] for order in orders[:250]]
    selected_numbers = set(numbers)
    pieces = [row["piece_id"] for row in data[candidate.PIECES] if row.get("order_number") in selected_numbers]
    selected_pieces = set(pieces)
    invoices = [row["id"] for row in data[candidate.INVOICES]
        if any(set(line.get("piece_ids", [])) & selected_pieces for line in row.get("lines", []))]
    groups = ["group-" + identity for identity in invoices]
    queries = {
        "pieces": (candidate.PIECES, {"user_id": OWNER, "order_number": {"$in": numbers}}, candidate.PIECE_FIELDS),
        "events": (candidate.EVENTS, {"user_id": OWNER, "order_number": {"$in": numbers},
            "event_type": "supplier_piece_service_recorded"}, candidate.EVENT_FIELDS),
        "invoices": (candidate.INVOICES, {"user_id": OWNER, "$or": [
            {"id": {"$in": invoices}}, {"lines.piece_ids": {"$in": pieces}}]}, candidate.INVOICE_FIELDS),
        "journals": (ledger.GROUPS_COLLECTION, {"user_id": OWNER, "$or": [
            {"txn_group_id": {"$in": groups}}, {"reversal_of_txn_group_id": {"$in": groups}}]},
            {"_id": 0, "txn_group_id": 1, "reversal_of_txn_group_id": 1, "status": 1,
                "metadata.supplier_invoice_id": 1, "metadata.invoice_digest": 1}),
    }
    results = {}
    for name, (collection, query, projection) in queries.items():
        result = await db.command("explain", {"find": collection, "filter": query, "projection": projection}, verbosity="executionStats")
        results[name] = plan_summary(result)
    return results


def summarize_samples(samples):
    durations = [sample["elapsed_seconds"] for sample in samples]
    return {"n": len(samples), "median_seconds": statistics.median(durations),
        "min_seconds": min(durations), "max_seconds": max(durations),
        "p95_seconds": sorted(durations)[math.ceil(0.95 * len(durations)) - 1],
        "stdev_seconds": statistics.pstdev(durations), "samples": samples}


async def run(uri, *, output, count=6000, repeats=5, history_count=6000):
    # Reject SRV before parsing: its parser may perform DNS discovery. Validate
    # the whole seed list, and forbid replica discovery beyond the one local
    # socket even when a hostile local server advertises remote members.
    assert urlparse(uri).scheme == "mongodb", "Disposable direct loopback Mongo only"
    parsed = parse_uri(uri)
    assert len(parsed["nodelist"]) == 1 and parsed["nodelist"][0][0] in {"localhost", "127.0.0.1"}, "Disposable direct loopback Mongo only"
    assert parsed["options"].get("directConnection") is True, "Disposable direct loopback Mongo only"
    assert count >= 48 and count % 2 == 0 and repeats >= 2
    orders, data, expected = fixture(count, history_count=history_count)
    baseline, baseline_sha = frozen_helper()
    wire = WireCommands()
    client = AsyncIOMotorClient(uri, directConnection=True, tz_aware=True, serverSelectionTimeoutMS=5000, event_listeners=[wire])
    identity = uuid4().hex
    databases = {arm: client[f"supplier_report_perf_{identity}_{arm}"] for arm in ("baseline", "candidate")}
    report = {"contract": "synthetic-supplier-report-performance-v1", "baseline_commit": BASE,
        "baseline_helper_sha256": baseline_sha, "candidate_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "scope": "Report cost computation only; cold means uncached application computation, not cold OS/Mongo buffers.",
        "fixture": {"orders": count, "products": PRODUCT_COUNT, "units": count * 2,
            "received_units": sum(x["received_units"] for x in expected),
            "estimated_units": sum(x["estimated_units"] for x in expected), "unselected_history_rows_per_collection": history_count,
            "invoice_group_max_pieces": 200, "native_evidence": "Synthetic approved invoice and posted journal with production digest; native writer correctness is gated separately."},
        "p95_method": "Nearest rank of the stated small repeat count; not a production p95 or capacity claim.",
        "results": {}, "explain": {}}
    try:
        await client.admin.command("ping")
        for arm, db in databases.items():
            await seed(db, data, baseline=arm == "baseline")
            report["explain"][arm] = await explain_reads(db, data, orders)
        for selector in ("pieces", "invoices", "journals"):
            old, new = report["explain"]["baseline"][selector], report["explain"]["candidate"][selector]
            assert old["returned"] == new["returned"] > 0
            assert "COLLSCAN" not in new["stages"], (selector, new)
            assert new["documents_examined"] <= old["documents_examined"], (selector, old, new)
            if history_count >= 1000:
                # A baseline COLLSCAN has zero keys; compare total examined
                # index/document records rather than rewarding that zero.
                assert new["keys_examined"] + new["documents_examined"] < old["keys_examined"] + old["documents_examined"], (selector, old, new)
                required = {
                    "pieces": {"ix_preparation_piece_order_cost_v1"},
                    "invoices": {"ix_supplier_invoice_identity_v2", "ix_supplier_invoice_piece_v2"},
                    "journals": {"ix_accounting_v2_group_identity", "ix_accounting_v2_group_reversal_proof"},
                }[selector]
                assert required.issubset(new["indexes"]), (selector, new)
        for size, label in ((count // 2, "one-month"), (count, "two-months")):
            cohort, truth = orders[:size], expected[:size]
            report["results"][label] = {}
            reference = {}
            for arm, module in (("baseline", baseline), ("candidate", candidate)):
                db, runs, work = databases[arm], {}, Counter()
                with helper_arm(module, work):
                    for surface in ("executive", "top-products", "sold"):
                        samples = []
                        for _ in range(repeats):
                            commands = Counter()
                            wire.active = commands
                            before = work.copy()
                            try:
                                sample, value = await measured_build(db, cohort, surface, truth)
                            finally:
                                wire.active = None
                            sample["wire_commands"] = dict(commands)
                            sample["pure_invoice_work"] = dict(work - before)
                            assert commands["unexpected_write"] == commands["failed_commands"] == 0
                            if arm == "baseline":
                                if surface in reference:
                                    assert value == reference[surface]
                                else:
                                    reference[surface] = value
                            else:
                                assert value == reference[surface], (label, surface, "financial output changed")
                            samples.append(sample)
                        runs[surface] = summarize_samples(samples)
                    concurrent_commands = Counter()
                    wire.active = concurrent_commands
                    started = time.perf_counter()
                    try:
                        concurrent = await asyncio.gather(*(
                            measured_build(db, cohort, surface, truth)
                            for surface in ("executive", "top-products", "sold")))
                    finally:
                        wire.active = None
                    for surface, (_, value) in zip(("executive", "top-products", "sold"), concurrent):
                        assert value == reference[surface]
                    assert concurrent_commands["unexpected_write"] == concurrent_commands["failed_commands"] == 0
                    runs["concurrent_three"] = {"wall_seconds": time.perf_counter() - started,
                        "requests": [sample for sample, _ in concurrent], "wire_commands": dict(concurrent_commands)}
                    if size == count:
                        tracemalloc.start()
                        try:
                            sample, value = await measured_build(db, cohort, "top-products", truth)
                            _, peak = tracemalloc.get_traced_memory()
                        finally:
                            tracemalloc.stop()
                        assert value == reference["top-products"]
                        runs["separate_memory_profile"] = {"python_peak_bytes": peak,
                            "sampled_rss_bytes": sample["sampled_rss_bytes"],
                            "note": "Tracemalloc run is excluded from timing samples."}
                        assert peak < 256 * 1024 * 1024, (arm, "bounded Python working memory", peak)
                report["results"][label][arm] = runs
            before, after = report["results"][label]["baseline"], report["results"][label]["candidate"]
            for surface in ("executive", "top-products", "sold"):
                old, new = before[surface]["samples"][0], after[surface]["samples"][0]
                assert old["logical_reads"] == new["logical_reads"], (surface, "fresh evidence read contract changed")
                if size > candidate.CHUNK_SIZE:
                    assert new["pure_invoice_work"]["invoice_allocations"] < old["pure_invoice_work"]["invoice_allocations"]
                    assert new["pure_invoice_work"]["invoice_digests"] < old["pure_invoice_work"]["invoice_digests"]
                # Generous shared-runner guard; timings and variance are retained
                # for review rather than pretending one noisy sample is an SLA.
                assert after[surface]["median_seconds"] <= max(2.0, before[surface]["median_seconds"] * 1.25)
                assert after[surface]["max_seconds"] < 30
            assert after["concurrent_three"]["wall_seconds"] < 45
        report["process_high_water_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        report["all_financial_outputs_equal"] = True
        report["status"] = "passed"
    except BaseException as exc:
        report["status"] = "failed"
        report["failure_type"] = type(exc).__name__
        raise
    finally:
        wire.active = None
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        for db in databases.values():
            await client.drop_database(db.name)
        client.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--orders", type=int, default=6000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--history", type=int, default=6000)
    args = parser.parse_args()
    uri = os.environ.get("SUPPLIER_REPORT_PERF_MONGO_URI", "")
    if not uri:
        parser.error("SUPPLIER_REPORT_PERF_MONGO_URI is required; no skip/fallback")
    result = asyncio.run(run(uri, output=args.output, count=args.orders, repeats=args.repeats, history_count=args.history))
    for period, arms in result["results"].items():
        for arm, surfaces in arms.items():
            for surface in ("executive", "top-products", "sold"):
                row = surfaces[surface]
                print(json.dumps({"period": period, "arm": arm, "surface": surface,
                    "median_seconds": row["median_seconds"], "p95_seconds": row["p95_seconds"],
                    "invoice_work": row["samples"][0]["pure_invoice_work"]}, sort_keys=True))
    print("Synthetic monthly supplier report benchmark passed; full evidence:", args.output)


if __name__ == "__main__":
    main()
