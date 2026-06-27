"""Iter-280 — Duplicate Attempt Detection + Merge.

Even after the idempotency-key fix, production already has orphan
duplicate rows from BEFORE the fix (e.g. order 268632361 with traces
`eac68e664dee48738005a52b15e50a60` + `33c07a10a2994f6796a44fa386a33c00`).
These tests cover the cleanup endpoint + helper functions.

Safety contract
───────────────
1. NEVER touches Qoyod itself (local-only archive op).
2. NEVER touches rows of other tenants.
3. Confirm token "MERGE" required.
4. Archive insert BEFORE delete (recoverable).
5. `keep_trace_id` must exist in the group, else refuse.
6. Stamps `duplicate_attempts_archive[]` on the KEPT row so audit
   trail is preserved on the kept side too.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from integrations.qoyod.first_sync_monitor import (
    find_duplicate_groups, archive_duplicate_attempts,
    DuplicateMergeRefused, DUPLICATE_CONFIRM_TOKEN,
    _extract_event_and_status_from_row, _suggest_keep_trace,
)


pytestmark = pytest.mark.asyncio


# ─── Fakes ──────────────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
    def sort(self, *args, **kwargs):
        return self
    def __aiter__(self):
        return self._gen()
    async def _gen(self):
        for r in self._rows:
            yield r


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.insert_many_called_with = None
        self.deleted_count = 0
        self.updates = []

    def find(self, q, *, sort=None, limit=None):
        # Apply tenant + order_number filter only (good enough for the
        # group-archive paths the tests exercise).
        def match(r):
            if "user_id" in q and r.get("user_id") != q["user_id"]:
                return False
            if "salla_order_number" in q:
                spec = q["salla_order_number"]
                if isinstance(spec, dict):
                    if "$ne" in spec and r.get("salla_order_number") == spec["$ne"]:
                        return False
                else:
                    if r.get("salla_order_number") != spec:
                        return False
            if "trace_id" in q:
                spec = q["trace_id"]
                if isinstance(spec, dict) and "$in" in spec:
                    if r.get("trace_id") not in spec["$in"]:
                        return False
            return True
        return _FakeCursor([r for r in self.rows if match(r)])

    async def insert_many(self, docs):
        self.insert_many_called_with = list(docs)
        class _Result:
            inserted_ids = [f"_id_{i}" for i in range(len(docs))]
        return _Result()

    async def delete_many(self, q):
        before = len(self.rows)
        def match(r):
            if r.get("user_id") != q.get("user_id"):
                return False
            if q.get("salla_order_number") and \
               r.get("salla_order_number") != q.get("salla_order_number"):
                return False
            trace_spec = q.get("trace_id")
            if isinstance(trace_spec, dict) and "$in" in trace_spec:
                if r.get("trace_id") not in trace_spec["$in"]:
                    return False
            return True
        self.rows = [r for r in self.rows if not match(r)]
        deleted = before - len(self.rows)
        class _Result:
            pass
        _Result.deleted_count = deleted
        return _Result()

    async def update_one(self, q, update):
        self.updates.append((q, update))
        for r in self.rows:
            if r.get("trace_id") == q.get("trace_id"):
                # Apply $set + $push roughly
                for k, v in (update.get("$set") or {}).items():
                    r[k] = v
                for k, push_spec in (update.get("$push") or {}).items():
                    each = (push_spec or {}).get("$each", [])
                    r.setdefault(k, []).extend(each)
                break


class _FakeDB:
    def __init__(self, inbox_rows=None):
        self.integration_inbox = _FakeCollection(inbox_rows or [])
        self.integration_inbox_archive = _FakeCollection()


def _row(*, trace_id, order_number="268632361", event="order_completed",
         status_slug="completed", stage="DEAD_LETTER",
         received_at=None, user_id="main", dry_run=False,
         shape="legacy"):
    """Build a fake inbox row that has the fields _extract_* relies on.
    shape='legacy' → status on raw root; shape='canonical' → on data.status."""
    r = {
        "trace_id": trace_id,
        "id": trace_id,
        "user_id": user_id,
        "salla_order_number": order_number,
        "received_at": received_at or datetime.now(timezone.utc).isoformat(),
        "pipeline_stage": stage,
        "dry_run": dry_run,
        "idempotency_key": f"salla:order:{order_number}:{event}:{status_slug}",
    }
    if shape == "legacy":
        r["raw_payload"] = {
            "event_type": event,
            "order_status_slug": status_slug,
            "order_number": order_number,
        }
    else:
        r["raw_payload"] = {
            "event": event,
            "data": {"reference_id": order_number,
                     "status": {"slug": status_slug}},
        }
    return r


# ─── _extract_event_and_status_from_row ─────────────────────────────
def test_extract_event_and_status_from_legacy_row():
    row = _row(trace_id="t1", shape="legacy")
    ev, st = _extract_event_and_status_from_row(row)
    assert ev == "order_completed"
    assert st == "completed"


def test_extract_event_and_status_from_canonical_row():
    row = _row(trace_id="t1", shape="canonical")
    # canonical_payload absent → falls back to raw.event
    ev, st = _extract_event_and_status_from_row(row)
    assert ev == "order_completed"
    assert st == "completed"


def test_extract_uses_canonical_metadata_when_present():
    row = _row(trace_id="t1", shape="legacy")
    row["canonical_payload"] = {
        "metadata": {"source_event": "order.updated"},
        "order_status": "completed",
    }
    ev, st = _extract_event_and_status_from_row(row)
    assert ev == "order.updated"
    assert st == "completed"


# ─── find_duplicate_groups ──────────────────────────────────────────
async def test_find_duplicate_groups_groups_identical_order_event_status():
    """The user's exact scenario: order 268632361, two trace_ids,
    both DEAD_LETTER, both for the SAME event+status."""
    db = _FakeDB([
        _row(trace_id="eac68e66", received_at="2026-02-26T08:00:00Z"),
        _row(trace_id="33c07a10", received_at="2026-02-27T10:00:00Z"),
    ])
    groups = await find_duplicate_groups(db, user_id="main")
    assert len(groups) == 1
    g = groups[0]
    assert g["order_number"] == "268632361"
    assert g["event"]        == "order_completed"
    assert g["status_slug"]  == "completed"
    assert g["attempt_count"] == 2
    # newest first
    assert g["latest_trace"] == "33c07a10"
    assert g["oldest_trace"] == "eac68e66"


async def test_find_duplicate_groups_does_not_group_different_statuses():
    """Two legitimate transitions: under_review then completed. Must
    NOT be merged."""
    db = _FakeDB([
        _row(trace_id="t_review", status_slug="under_review"),
        _row(trace_id="t_completed", status_slug="completed"),
    ])
    groups = await find_duplicate_groups(db, user_id="main")
    assert groups == []


async def test_find_duplicate_groups_isolates_tenants():
    db = _FakeDB([
        _row(trace_id="t1", user_id="tenant_a"),
        _row(trace_id="t2", user_id="tenant_a"),
        _row(trace_id="t3", user_id="tenant_b"),
    ])
    groups_a = await find_duplicate_groups(db, user_id="tenant_a")
    groups_b = await find_duplicate_groups(db, user_id="tenant_b")
    assert len(groups_a) == 1
    assert groups_b == []   # only one row → not a duplicate


async def test_find_duplicate_groups_only_failed_excludes_completed_groups():
    db = _FakeDB([
        _row(trace_id="t1", stage="COMPLETED"),
        _row(trace_id="t2", stage="COMPLETED"),
    ])
    groups = await find_duplicate_groups(db, user_id="main", only_failed=True)
    assert groups == []
    groups_all = await find_duplicate_groups(
        db, user_id="main", only_failed=False)
    assert len(groups_all) == 1


async def test_find_duplicate_groups_includes_group_with_mixed_stages():
    db = _FakeDB([
        _row(trace_id="t1", stage="COMPLETED"),
        _row(trace_id="t2", stage="DEAD_LETTER"),
    ])
    groups = await find_duplicate_groups(db, user_id="main", only_failed=True)
    assert len(groups) == 1   # contains ≥1 failed → surfaced


# ─── _suggest_keep_trace ────────────────────────────────────────────
def test_suggest_keep_trace_prefers_completed():
    attempts = [
        {"trace_id": "t1", "pipeline_stage": "DEAD_LETTER",
         "received_at": "2026-02-27T10:00:00Z"},
        {"trace_id": "t2", "pipeline_stage": "COMPLETED",
         "received_at": "2026-02-25T10:00:00Z"},
    ]
    assert _suggest_keep_trace(attempts) == "t2"


def test_suggest_keep_trace_prefers_newest_when_all_failed():
    attempts = [
        {"trace_id": "t_old", "pipeline_stage": "DEAD_LETTER",
         "received_at": "2026-02-26T08:00:00Z"},
        {"trace_id": "t_new", "pipeline_stage": "DEAD_LETTER",
         "received_at": "2026-02-27T10:00:00Z"},
    ]
    assert _suggest_keep_trace(attempts) == "t_new"


# ─── archive_duplicate_attempts — safety + correctness ──────────────
async def test_archive_refuses_without_confirm_token():
    db = _FakeDB([_row(trace_id="t1"), _row(trace_id="t2")])
    with pytest.raises(DuplicateMergeRefused) as e:
        await archive_duplicate_attempts(
            db, user_id="main", order_number="268632361",
            event="order_completed", status_slug="completed",
            keep_trace_id="t1", confirm_token="", actor="op")
    assert "MERGE" in str(e.value)


async def test_archive_refuses_unknown_keep_trace():
    db = _FakeDB([_row(trace_id="t1"), _row(trace_id="t2")])
    with pytest.raises(DuplicateMergeRefused) as e:
        await archive_duplicate_attempts(
            db, user_id="main", order_number="268632361",
            event="order_completed", status_slug="completed",
            keep_trace_id="UNKNOWN", confirm_token="MERGE", actor="op")
    assert "not in group" in str(e.value)


async def test_archive_refuses_single_row_group():
    db = _FakeDB([_row(trace_id="t1")])
    with pytest.raises(DuplicateMergeRefused) as e:
        await archive_duplicate_attempts(
            db, user_id="main", order_number="268632361",
            event="order_completed", status_slug="completed",
            keep_trace_id="t1", confirm_token="MERGE", actor="op")
    assert "nothing to merge" in str(e.value)


async def test_archive_moves_losers_to_archive_collection():
    """End-to-end: 2 rows for order 268632361. Operator keeps t2 (newest).
    t1 must be inserted into archive collection AND removed from inbox.
    Inbox now has only t2."""
    db = _FakeDB([
        _row(trace_id="t1", received_at="2026-02-26T08:00:00Z"),
        _row(trace_id="t2", received_at="2026-02-27T10:00:00Z"),
    ])
    result = await archive_duplicate_attempts(
        db, user_id="main", order_number="268632361",
        event="order_completed", status_slug="completed",
        keep_trace_id="t2", confirm_token="MERGE", actor="op")
    assert result["archived"] == 1
    assert result["deleted"]  == 1
    assert result["kept_trace"] == "t2"
    assert result["merged_traces"] == ["t1"]
    # archive collection got t1
    assert db.integration_inbox_archive.insert_many_called_with is not None
    archived = db.integration_inbox_archive.insert_many_called_with
    assert len(archived) == 1
    assert archived[0]["trace_id"] == "t1"
    assert archived[0]["archive_reason"] == "duplicate_attempt_merged"
    assert archived[0]["duplicate_group"]["kept_trace"] == "t2"
    # inbox has t2 only
    assert len(db.integration_inbox.rows) == 1
    assert db.integration_inbox.rows[0]["trace_id"] == "t2"


async def test_archive_stamps_kept_row_with_attempt_history():
    db = _FakeDB([
        _row(trace_id="t1", received_at="2026-02-26T08:00:00Z"),
        _row(trace_id="t2", received_at="2026-02-27T10:00:00Z"),
    ])
    await archive_duplicate_attempts(
        db, user_id="main", order_number="268632361",
        event="order_completed", status_slug="completed",
        keep_trace_id="t2", confirm_token="MERGE", actor="amasi")
    kept = next(r for r in db.integration_inbox.rows if r["trace_id"] == "t2")
    assert kept.get("duplicate_attempts_merged_by") == "amasi"
    assert kept.get("duplicate_attempts_merged_at")
    history = kept.get("duplicate_attempts_archive") or []
    assert len(history) == 1
    assert history[0]["trace_id"] == "t1"


async def test_archive_never_touches_other_orders():
    """Strict scope — only the (order, event, status) group is affected."""
    db = _FakeDB([
        _row(trace_id="t1_268632361", order_number="268632361"),
        _row(trace_id="t2_268632361", order_number="268632361"),
        _row(trace_id="other_order", order_number="999999"),
    ])
    await archive_duplicate_attempts(
        db, user_id="main", order_number="268632361",
        event="order_completed", status_slug="completed",
        keep_trace_id="t2_268632361", confirm_token="MERGE", actor="op")
    # other_order is untouched in the inbox
    surviving = [r["trace_id"] for r in db.integration_inbox.rows]
    assert "other_order" in surviving
    assert "t2_268632361" in surviving
    assert "t1_268632361" not in surviving
