#!/usr/bin/env python3
"""Measure the new cost-authority overhead using disposable local Mongo fixtures.

Run from the repository root with PYTHONPATH=backend and an explicit
MZ2_TEST_MONGO_URI=mongodb://127.0.0.1:27561/. There is no environment/database
fallback. The UUID database is deleted in finally, including on assertion failure.
Reported BSON sizes are uncompressed command-response bytes, not packet captures.
This measures the added authority/helpers only, not a complete dashboard request.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import statistics
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from bson import BSON
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener

from bundle_catalog_v2 import BUNDLES
from bundle_fulfillment_v2 import capture_intake, leaf_key
from order_cost_authority import (
    CORRECTIONS, LIFECYCLES, SNAPSHOTS, calculate_catalog_line_cost,
    capture_reporting_costs, ensure_order_cost_indexes, line_fingerprint,
    load_order_cost_authority, resolve_order_line_cost,
)
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES, ensure_indexes as ensure_option_indexes
from product_v2_details_routes import COST_PROFILES, ensure_detail_indexes
from product_v2_routes import PRODUCTS, ensure_product_v2_indexes
from stock_component_consumption_service import (
    PLANS, UNITS, ensure_component_consumption_indexes, selection_tokens,
)

OWNER = "synthetic-cost-benchmark-owner"
DATABASE_PREFIX = "mz2_cost_authority_fixture_"
READ_COMMANDS = {"find", "aggregate", "getMore"}


def local_mongo_uri(value=None):
    value = os.environ.get("MZ2_TEST_MONGO_URI", "") if value is None else value
    parsed = urlsplit(value)
    if (parsed.scheme != "mongodb" or parsed.hostname != "127.0.0.1"
            or not re.fullmatch(r"127\.0\.0\.1:[0-9]+", parsed.netloc)
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.fragment):
        raise ValueError("Explicit credential-free MZ2_TEST_MONGO_URI on 127.0.0.1:port is required")
    return value


class CommandCapture(CommandListener):
    """Keep fixture queries in memory; print only counts/bytes/explain summaries."""

    def __init__(self):
        self.lock = threading.Lock()
        self.enabled = False
        self.counts = Counter()
        self.reply_bytes = 0
        self.queries = []
        self.failures = 0

    def begin(self):
        with self.lock:
            self.counts.clear()
            self.reply_bytes = 0
            self.queries = []
            self.failures = 0
            self.enabled = True

    def finish(self):
        with self.lock:
            self.enabled = False
            return {
                "commands": dict(self.counts),
                "logical_reads": self.counts["find"] + self.counts["aggregate"],
                "read_round_trips": sum(self.counts[name] for name in READ_COMMANDS),
                "reply_bson_bytes": self.reply_bytes,
                "command_failures": self.failures,
            }

    def started(self, event):
        with self.lock:
            if not self.enabled:
                return
            self.counts[event.command_name] += 1
            if event.command_name in {"find", "aggregate"}:
                keep = {"find", "aggregate", "filter", "projection", "sort", "limit", "pipeline", "cursor"}
                self.queries.append({key: deepcopy(value) for key, value in event.command.items() if key in keep})

    def succeeded(self, event):
        with self.lock:
            if self.enabled and event.command_name in READ_COMMANDS:
                self.reply_bytes += len(BSON.encode(event.reply))

    def failed(self, event):
        with self.lock:
            if self.enabled:
                self.failures += 1


def create_client(uri, capture):
    # directConnection prevents replica discovery from leaving the named loopback
    # test endpoint. The URI validator rejects credentials and named databases.
    return AsyncIOMotorClient(
        local_mongo_uri(uri), directConnection=True, serverSelectionTimeoutMS=3000,
        event_listeners=[capture], retryWrites=False,
    )


async def prepare_indexes(db):
    # These are the real production index initializers, outside measured reads.
    await ensure_order_cost_indexes(db)
    await ensure_component_consumption_indexes(db)
    await ensure_product_v2_indexes(db)
    await ensure_detail_indexes(db)
    await ensure_option_indexes(db)
    # Bundle save creates this same catalog index before any recipe is persisted.
    await db[BUNDLES].create_index([("user_id", 1), ("product_id", 1)], unique=True)
    await db[PRODUCT_RESOURCE_BINDINGS].create_index([("user_id", 1), ("salla_product_id", 1)])


def _cost(item):
    return calculate_catalog_line_cost(
        item, product={}, profile={"base_cost": 15},
        product_bindings=[], option_bindings=[], resources={},
    )


def fixture_order(index):
    number = f"fixture-order-{index:05d}"
    items = []
    for suffix, quantity, product in [("bundle-four", 4, "shawl"), ("ordinary", 2, "ordinary"), ("bundle-two", 2, "shawl")]:
        key = number + ":" + suffix
        items.append({
            "order_item_id": key, "order_line_id": key, "source_item_id": key,
            "product_id": product, "quantity": quantity,
            "options_raw": ([{"option_id": "rose", "value_id": "yes"}] if product == "shawl" else []),
            "options_normalized": {},
        })
    return {"order_number": number, "products": items}


def recipe_leaves():
    return [{
        "path": ["shawl", product], "kind": "product", "product_id": product,
        "quantity": "1", "unit_cost": amount, "sourcing_mode": "stock",
        "required": True, "current_product_reference": product == "shawl",
    } for product, amount in [("shawl", "15"), ("bouquet", "40")]]


async def seed_fixtures(db, count=250, metadata_kib=40, *, noise_count=300):
    """Three lines/order; total360;40KiB frozen metadata across intake and plan."""
    await prepare_indexes(db)
    products = [{
        "user_id": OWNER, "salla_product_id": product, "mezan_product_id": "mpv2_" + product,
        "name": "Synthetic " + product, "variants": [],
        "options": ([{"id": "rose", "name": "Rose", "values": [{"id": "yes", "name": "Yes"}]}]
                    if product == "shawl" else []),
    } for product in ["shawl", "bouquet", "ordinary"]]
    await db[PRODUCTS].insert_many(products)
    await db[COST_PROFILES].insert_many([{
        "user_id": OWNER, "salla_product_id": product, "base_cost": cost,
    } for product, cost in [("shawl", 15), ("bouquet", 40), ("ordinary", 15)]])
    await db[BUNDLES].insert_one({
        "user_id": OWNER, "product_id": "shawl", "product_type": "composite", "revision": 1,
        "schema_version": 2, "items": [
            {"kind": "current_product", "quantity": 1, "rule_id": "base"},
            {"kind": "product", "product_id": "bouquet", "quantity": 1, "rule_id": "rose",
             "condition": {"option_id": "rose", "value_id": "yes"}},
        ],
    })
    orders = [fixture_order(index) for index in range(count)]
    lifecycle, plans, units, stored = [], [], [], []
    padding = "x" * (metadata_kib * 1024 // 4)
    for order in orders:
        number = order["order_number"]
        bundles, ordinary = {}, {}
        plan_id = "plan:" + number
        for item in order["products"]:
            key = item["order_item_id"]
            if item["product_id"] == "ordinary":
                cost = _cost(item)
                cost.update(cost_authority="order_snapshot", historical_cost_verified=True)
                record = {"quantity": 2, "product_id": "ordinary", "line_fingerprint": line_fingerprint(item), "cost": cost}
                ordinary[key] = deepcopy(record)
                stored.append({"user_id": OWNER, "order_number": number, "order_item_id": key, **record})
                continue
            leaves = recipe_leaves()
            bundles[key] = {
                "product_id": "shawl", "revision": 1, "sale_quantity": item["quantity"],
                "line_fingerprint": line_fingerprint(item), "estimated_cost": "55", "leaves": leaves,
                "selected_options": [{"option_id": "rose", "value_id": "yes"}],
                "frozen_definitions": {"shawl": {
                    "catalog_metadata": {"synthetic_fixture_padding": padding},
                    "catalog_product": {"salla_product_id": "shawl", "name": "Synthetic shawl"},
                }},
            }
            for index in range(item["quantity"]):
                actual = [{**deepcopy(leaf), "leaf_id": leaf_key(leaf), "state": "consumed",
                           "reserved_quantity": "1", "consumed_quantity": "1", "actual_cost": leaf["unit_cost"]}
                          for leaf in leaves]
                units.append({
                    "user_id": OWNER, "plan_id": plan_id, "order_line_id": key,
                    "unit_index": index, "state": "consumed", "bundle_leaves": actual,
                })
        lifecycle.append({
            "user_id": OWNER, "order_number": number,
            "bundle_intake_snapshots": deepcopy(bundles), "reporting_cost_snapshots": ordinary,
        })
        plans.append({
            "_id": plan_id, "user_id": OWNER, "order_id": number, "bundle_snapshots": bundles,
            "lines": [{**deepcopy(item), "selected_context": selection_tokens(item)} for item in order["products"]],
        })
    for collection, rows in [(LIFECYCLES, lifecycle), (PLANS, plans), (UNITS, units), (SNAPSHOTS, stored)]:
        if rows:
            await db[collection].insert_many(rows)
    # Foreign-tenant distractors make selectivity and the owner predicate real.
    noise = [{"user_id": "synthetic-other-owner", "order_number": f"noise-{i}"} for i in range(noise_count)]
    if noise:
        await db[LIFECYCLES].insert_many([{**row, "bundle_intake_snapshots": {}} for row in noise])
        await db[PLANS].insert_many([{**row, "order_id": row["order_number"], "bundle_snapshots": {}} for row in noise])
        await db[SNAPSHOTS].insert_many([{**row, "order_item_id": "noise", "quantity": 1, "line_cost": 999} for row in noise])
        await db[CORRECTIONS].insert_many([{**row, "order_item_id": "noise", "state": "applied", "quantity": 1} for row in noise])
    return orders


def resolve_cohort(orders, context, *, catalog_costs=None):
    total = 0
    for order in orders:
        for item in order["products"]:
            result = resolve_order_line_cost(
                item, order_number=order["order_number"],
                catalog_cost=(catalog_costs[(order["order_number"], item["order_item_id"])]
                              if catalog_costs is not None else _cost(item)),
                authority=context, product_id=item["product_id"],
            )
            assert result["calculation_cost_available"], result["missing_cost_reasons"]
            total += result["line_total"]
    assert total == len(orders) * 360
    return total


def contains_frozen_definitions(value):
    if isinstance(value, dict):
        return "frozen_definitions" in value or any(contains_frozen_definitions(child) for child in value.values())
    if isinstance(value, list):
        return any(contains_frozen_definitions(child) for child in value)
    return False


async def measured(capture, operation):
    capture.begin()
    started = time.perf_counter()
    try:
        value = await operation()
    finally:
        elapsed = (time.perf_counter() - started) * 1000
        metrics = capture.finish()
    return value, {"elapsed_ms": round(elapsed, 4), **metrics}


def summarize(samples):
    times = sorted(row["elapsed_ms"] for row in samples)
    result = {
        "samples": len(samples), "median_ms": round(statistics.median(times), 3),
        "p95_ms": round(times[math.ceil(len(times) * 0.95) - 1], 3),
        "sample_load_ms": [row["elapsed_ms"] for row in samples],
        "median_reply_bson_bytes": statistics.median(row["reply_bson_bytes"] for row in samples),
        "logical_reads": sorted({row["logical_reads"] for row in samples}),
        "read_round_trips": sorted({row["read_round_trips"] for row in samples}),
        "command_failures": sum(row["command_failures"] for row in samples),
        "commands_per_sample": samples[0]["commands"],
    }
    if all("resolution_ms" in row for row in samples):
        resolution = sorted(row["resolution_ms"] for row in samples)
        combined = sorted(row["elapsed_ms"] + row["resolution_ms"] for row in samples)
        result.update(
            median_line_resolution_ms=round(statistics.median(resolution), 3),
            p95_line_resolution_ms=round(resolution[math.ceil(len(resolution) * 0.95) - 1], 3),
            median_load_and_resolution_ms=round(statistics.median(combined), 3),
            p95_load_and_resolution_ms=round(combined[math.ceil(len(combined) * 0.95) - 1], 3),
        )
    return result


def explain_stages(value):
    stages = set()
    if isinstance(value, dict):
        if isinstance(value.get("stage"), str):
            stages.add(value["stage"])
        for key, child in value.items():
            if key not in {"rejectedPlans", "allPlansExecution"}:
                stages.update(explain_stages(child))
    elif isinstance(value, list):
        for child in value:
            stages.update(explain_stages(child))
    return stages


async def explain_queries(db, queries):
    result = []
    for command in queries:
        collection = command.get("find") or command.get("aggregate")
        if collection not in {LIFECYCLES, PLANS, SNAPSHOTS, UNITS, CORRECTIONS}:
            continue
        explanation = await db.command({"explain": deepcopy(command), "verbosity": "executionStats"})
        stages = explain_stages(explanation)
        result.append({"collection": collection, "stages": sorted(stages), "collection_scan": "COLLSCAN" in stages})
    return result


async def benchmark(args):
    capture = CommandCapture()
    client = create_client(local_mongo_uri(), capture)
    database_name = DATABASE_PREFIX + uuid4().hex
    db = client[database_name]
    report = {"scope": "additional_order_cost_authority_and_ingestion_helpers_only", "production_changed": False}
    try:
        hello = await client.admin.command("hello")
        assert hello.get("isWritablePrimary") and hello.get("setName"), "A local writable replica is required"
        report["mongo_version"] = (await client.server_info())["version"]
        orders = await seed_fixtures(db, max(args.orders), args.metadata_kib)
        report.update(fixture_lines_per_order=3, fixture_units_per_order=8,
                      bundle_units_per_order=6, frozen_metadata_kib_per_order=args.metadata_kib,
                      bson_measurement="uncompressed Mongo command replies; includes cursor envelope; excludes transport framing",
                      p95_method="nearest-rank; with five samples this is the maximum sample",
                      sampling="paired compact/full repetitions with alternating order after warming both modes",
                      authority=[])
        explain_sample = []
        for count in args.orders:
            selected = orders[:count]
            catalog_costs = {(order["order_number"], item["order_item_id"]): _cost(item)
                             for order in selected for item in order["products"]}
            row = {"orders": count, "lines": count * 3, "cost_sar": count * 360}
            for full in (False, True):
                await load_order_cost_authority(db, OWNER, selected, include_frozen_metadata=full)
            samples = {False: [], True: []}
            for repetition in range(args.repeats):
                for full in ((False, True) if repetition % 2 == 0 else (True, False)):
                    context, metrics = await measured(capture, lambda: load_order_cost_authority(
                        db, OWNER, selected, include_frozen_metadata=full,
                    ))
                    resolution_started = time.perf_counter()
                    resolve_cohort(selected, context, catalog_costs=catalog_costs)
                    metrics["resolution_ms"] = (time.perf_counter() - resolution_started) * 1000
                    assert contains_frozen_definitions(context) is full
                    samples[full].append(metrics)
                    if not full:
                        explain_sample = deepcopy(capture.queries)
            for full in (False, True):
                row["full_metadata" if full else "compact"] = summarize(samples[full])
            report["authority"].append(row)
        report["scoped_query_explain"] = await explain_queries(db, explain_sample)
        report["all_scoped_queries_indexed"] = all(not row["collection_scan"] for row in report["scoped_query_explain"])

        lines = fixture_order(100000)["products"]
        first, replay = [], []

        async def capture_helpers(previous_bundle=None, previous_reporting=None):
            bundle = await capture_intake(db, OWNER, lines, previous_bundle or {})
            reporting = await capture_reporting_costs(db, OWNER, lines, previous_reporting or {}, bundle)
            assert len(reporting) == 1 and sum(row["cost"]["line_total"] for row in reporting.values()) == 30
            assert sum(float((bundle.get(item["order_item_id"]) or {}).get("estimated_cost") or 0) * item["quantity"]
                       for item in lines) == 330
            return bundle, reporting

        await capture_helpers()
        for _ in range(args.repeats):
            previous, metrics = await measured(capture, capture_helpers)
            first.append(metrics)
            again, metrics = await measured(capture, lambda: capture_helpers(*previous))
            assert again == previous
            replay.append(metrics)
        report["ingestion_helpers_one_order"] = {"first_capture": summarize(first), "replay": summarize(replay)}
        reporting_first, reporting_replay = [], []
        bundle = previous[0]
        for _ in range(args.repeats):
            reporting, metrics = await measured(capture, lambda: capture_reporting_costs(db, OWNER, lines, {}, bundle))
            reporting_first.append(metrics)
            again, metrics = await measured(capture, lambda: capture_reporting_costs(db, OWNER, lines, reporting, bundle))
            assert again == reporting
            reporting_replay.append(metrics)
        report["new_reporting_capture_only_one_order"] = {
            "first_capture": summarize(reporting_first), "replay": summarize(reporting_replay),
        }
        report["limitations"] = [
            "Local fixture replica, warm connections and warmed collections; production latency and load differ.",
            "Authority timings exclude existing dashboard, HTTP, cache and other report work; add them to a measured baseline.",
            "Load and line-resolution timings are separate; resolver timing uses catalog costs computed before measurement.",
            "Ingestion timings cover capture_intake plus capture_reporting_costs; exclude lifecycle writes, stock reservation and provider transport.",
            "No production orders, journals or live application state were read or changed.",
        ]
    finally:
        capture.enabled = False
        assert re.fullmatch(re.escape(DATABASE_PREFIX) + r"[0-9a-f]{32}", database_name)
        await client.drop_database(database_name)
        report["fixture_database_cleaned"] = database_name not in await client.list_database_names()
        client.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", nargs="+", type=int, default=[1, 80, 250], choices=[1, 80, 250])
    parser.add_argument("--repeats", type=int, default=5, choices=range(3, 10))
    parser.add_argument("--metadata-kib", type=int, default=40, choices=range(20, 51))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(benchmark(args))
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
