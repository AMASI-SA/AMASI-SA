#!/usr/bin/env python3
"""Read one saved snapshot and diagnose Tabby fee predicates on exact M.

Run only after independent review, outside /app. No refresh, writer, server,
startup hook, original-file mutation, or fee reimplementation is used. Output
omits owner/order/account identifiers and source references. Amounts are opt-in.
"""
import argparse
import asyncio
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from types import CodeType

ROOT = Path("/app")
BACKEND = ROOT / "backend"
SOURCE_M = "236a428364bc8163a684795ed1be87e237476f18"
READ_SECONDS = 25
PROJECTION_SECONDS = 60
MAX_SAMPLES = 10
PROVIDERS = {"tabby", "tamara", "salla", "emkan"}
OPERATION_BASES = {"capture_facts", "one_provider_order_estimate", "unavailable"}
FIXED_BASES = {"configured", "statement_sale_mean"}
VAT_BASES = {"configured", "statement", "unavailable", "conflicting_policy"}


class DiagnosticError(Exception):
    pass


class Discard(io.TextIOBase):
    def write(self, value):
        return len(value)

    def flush(self):
        pass


def require(value, code):
    if not value:
        raise DiagnosticError(code)


def pin_source():
    require(ROOT.resolve(strict=True) == ROOT, "source_root_not_canonical")
    require(ROOT not in Path(__file__).resolve().parents, "helper_must_be_outside_source")
    prefix = ["git", "--no-optional-locks", "-C", str(ROOT)]
    head = subprocess.run(prefix + ["rev-parse", "HEAD"], capture_output=True, timeout=10)
    require(head.returncode == 0 and head.stdout.decode("ascii").strip() == SOURCE_M,
            "source_head_differs")
    for suffix in (["diff", "--quiet"], ["diff", "--cached", "--quiet"]):
        result = subprocess.run(prefix + suffix, capture_output=True, timeout=10)
        require(result.returncode == 0, "tracked_source_not_clean")


def count(value):
    return value if type(value) is int and value >= 0 else None


def currency(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) else "other"


def enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else "other"


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        if result.is_finite() and abs(result) <= Decimal("999999999999999999"):
            return result
    except (InvalidOperation, ValueError, TypeError):
        pass
    return None


def amount(value):
    parsed = number(value)
    return format(parsed, "f") if parsed is not None else None


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).isoformat()
    except (AttributeError, ValueError, TypeError):
        pass
    return None


class PredicateObserver:
    def __init__(self, provider, include_amounts, target_code, parent_code):
        self.provider = provider
        self.include_amounts = include_amounts
        self.target_code = target_code
        self.parent_code = parent_code
        self.key = secrets.token_bytes(32)  # Never printed or persisted.
        self.counts = Counter()
        self.bases = Counter()
        self.predicates = Counter()
        self.seen = set()
        self.sample_patterns = set()
        self.samples = []
        self.errors = 0

    def callback(self, frame, event, result):
        if event != "return" or frame.f_code is not self.target_code:
            return
        if frame.f_back is None or frame.f_back.f_code is not self.parent_code:
            self.errors += 1
            return
        try:
            self.observe(frame.f_locals, frame.f_back.f_locals)
        except Exception:
            self.errors += 1  # No exception text or raw locals leave the process.

    def observe(self, local, caller):
        row, profile, components = (local.get(key) for key in ("row", "profile", "components"))
        if not isinstance(row, dict) or row.get("provider") != self.provider:
            return
        self.counts["callback_returns"] += 1
        if not isinstance(profile, dict) or not isinstance(components, dict):
            self.counts["callbacks_without_components"] += 1
            return
        context = caller.get("statement_context") or {}
        pair = (row.get("order_id"), self.provider)
        captures = (context.get("captures") or {}).get(pair, {})
        capture_values = [number(value) for value in captures.values()]
        capture_valid = all(value is not None for value in capture_values)
        capture_sum = sum(capture_values, Decimal(0)) if capture_valid else None
        native_gross = number(row.get("native_gross"))
        pages = context.get("pages") or {}
        page = number(pages.get(pair))
        payment = row.get("payment") or {}
        claim = caller.get("claim")
        applied = number(caller.get("applied"))
        token_input = json.dumps([self.provider, currency(row.get("native_currency")),
                                  str(row.get("order_id") or "")], separators=(",", ":"))
        token = hmac.new(self.key, token_input.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
        identity = (token, currency(row.get("currency")))
        if identity in self.seen:
            self.counts["duplicate_callbacks"] += 1
            return
        self.seen.add(identity)
        sample = {
            "token": token, "report_currency": currency(row.get("currency")),
            "native_currency": currency(row.get("native_currency")),
            "missing_fixed": components.get("fixed") is None,
            "missing_vat": components.get("vat") is None,
            "missing_total": components.get("fee") is None,
            "operations": count(components.get("operations")),
            "operations_basis": enum(components.get("operations_basis"), OPERATION_BASES),
            "fixed_basis": enum(components.get("fixed_basis"), FIXED_BASES),
            "vat_basis": enum(components.get("vat_basis"), VAT_BASES),
            "vat_rate_present": components.get("vat_rate") is not None,
            "profile_vat_rate_present": profile.get("vat_rate") is not None,
            "capture_fact_count": len(captures),
            "capture_amounts_valid": capture_valid,
            "capture_sum_equals_native_gross": capture_valid and native_gross is not None and capture_sum == native_gross,
            "capture_sum_less_than_native_gross": capture_valid and native_gross is not None and capture_sum < native_gross,
            "page_exists": pair in pages,
            "page_equals_native_gross": page is not None and native_gross is not None and page == native_gross,
            "native_gross_positive": native_gross is not None and native_gross > 0,
            "payment_provider_present": bool(payment.get("provider")),
            "payment_provider_equals": payment.get("provider") == self.provider,
            "split_payment": bool(row.get("split_payment")),
            "payout_allocation_exists": applied is not None and applied > 0,
            "accepted_event_count": len((context.get("events") or {}).get(pair, [])),
            "held": bool(caller.get("held")),
            "claim_exists": isinstance(claim, dict),
            "claim_incomplete": bool(claim.get("incomplete")) if isinstance(claim, dict) else None,
        }
        self.counts["unique_average_rows"] += 1
        self.bases[sample["operations_basis"]] += 1
        for key in ("missing_fixed", "missing_vat", "missing_total"):
            self.counts[key] += int(sample[key])
        if not sample["missing_total"]:
            self.counts["complete_average_rows"] += 1
            return
        for key, value in sample.items():
            if type(value) is bool:
                self.predicates[key + ("_true" if value else "_false")] += 1
        if sample["missing_fixed"] and sample["operations"] is None:
            self.counts["missing_fixed_with_unknown_operations"] += 1
        if sample["missing_vat"] and not sample["vat_rate_present"]:
            self.counts["missing_vat_without_usable_rate"] += 1
        if sample["missing_vat"] and sample["missing_fixed"] and sample["vat_rate_present"]:
            self.counts["missing_vat_waiting_for_fixed_component"] += 1
        pattern = json.dumps({key: value for key, value in sample.items() if key != "token"}, sort_keys=True)
        if pattern in self.sample_patterns or len(self.samples) >= MAX_SAMPLES:
            return
        self.sample_patterns.add(pattern)
        if self.include_amounts:
            sample["amounts"] = {key: amount(components.get(key)) for key in ("variable", "fixed", "vat", "fee")}
            sample["amounts"].update(native_gross=amount(native_gross), capture_sum=amount(capture_sum),
                                     page_paid=amount(page), allocated_gross=amount(applied))
        self.samples.append(sample)

    def output(self):
        return {"counts": dict(self.counts), "operation_bases": dict(self.bases),
                "incomplete_row_predicate_counts": dict(self.predicates),
                "offending_samples": self.samples, "sample_limit": MAX_SAMPLES,
                "sampling": "first incomplete row per predicate pattern, at most ten",
                "tokens": "per-run HMAC; raw identities and HMAC key are not output"}


def select_summary(row, report_currency, include_amounts, allowed_reasons):
    result = {"report_currency": currency(report_currency)}
    for key in ("paid_orders", "confirmed_orders", "net_confirmed_orders", "held_orders",
                "average_estimated_orders", "plan_estimated_orders", "pending_orders",
                "unidentified_pending_orders"):
        result[key] = count(row.get(key))
    for key in ("complete", "fees_complete", "pending_details_complete"):
        result[key] = row.get(key) is True
    result["fee_status"] = enum(row.get("fee_status"), {"review", "mixed", "estimated", "confirmed", "unavailable"})
    result["source_average_status"] = enum(row.get("source_average_status"), {"available", "partial", "unavailable"})
    result["missing_aggregates"] = {key: row.get(key) is None for key in (
        "estimated_fees", "average_fixed_fees", "average_vat_fees")}
    result["pending_reasons"] = [{"code": enum(reason.get("code"), allowed_reasons),
                                  "orders": count(reason.get("orders"))}
                                 for reason in row.get("pending_reasons", []) if isinstance(reason, dict)]
    result["source_scope_issues"] = [{"native_currency": currency(issue.get("native_currency")),
        "reason": enum(issue.get("reason_code"), {"source_profile_ambiguous", "source_profile_invalid",
            "source_average_unavailable", "source_scope_unproven"}), "scopes": count(issue.get("scopes"))}
        for issue in row.get("source_average_issues", []) if isinstance(issue, dict)]
    if include_amounts:
        result["amounts"] = {key: amount(row.get(key)) for key in (
            "known_gross", "known_fees", "actual_statement_fees", "actual_settlement_fees",
            "estimated_fees", "average_estimated_fees", "average_variable_fees",
            "average_fixed_fees", "average_vat_fees", "known_net", "opening", "settled", "refunded")}
    return result


def project_saved(state, args):
    from operational_balance_service import report
    import operational_provider_summary as provider_module
    parent_code = provider_module.provider_balances.__code__
    targets = [value for value in parent_code.co_consts
               if isinstance(value, CodeType) and value.co_name == "average_components"]
    require(len(targets) == 1, "diagnostic_target_not_unique")
    observer = PredicateObserver(args.provider, args.include_amounts, targets[0], parent_code)
    previous = sys.getprofile()
    try:
        sys.setprofile(observer.callback)
        projection = report(state, provider_balances_sar=True, ad_balances_sar=True, provider_balances_net=True)
    finally:
        sys.setprofile(previous)
    require(observer.errors == 0, "predicate_observation_failed")
    allowed_reasons = set(provider_module.PENDING_MESSAGES) | set(provider_module.DIAGNOSTICS)
    selected = [select_summary(row, ccy, args.include_amounts, allowed_reasons)
        for ccy, rows in projection.get("details", {}).get("provider_balances", {}).items()
        for row in rows if row.get("provider") == args.provider]
    inputs = state.get("engine", {}).get("provider_fee_inputs", {})
    profiles = [row for row in inputs.get("average_profiles", [])
                if isinstance(row, dict) and row.get("provider") == args.provider]
    events = [row for row in inputs.get("statement_events", [])
              if isinstance(row, dict) and row.get("provider") == args.provider]
    return {"provider_summaries": selected, "average_components": observer.output(),
        "saved_fee_input_counts": {
            "profiles": len(profiles), "accepted_events": len(events),
            "profiles_by_native_currency": dict(Counter(currency(row.get("native_currency")) for row in profiles)),
            "accepted_events_by_native_currency": dict(Counter(currency(row.get("native_currency")) for row in events))}}


async def diagnose(args):
    from motor.motor_asyncio import AsyncIOMotorClient
    from review_acceptance_config_guard import AcceptanceConfigDatabase
    from operational_financial_position_state import read_financial_position_state
    from operational_financial_activity_runtime import prepare_in_thread
    require(bool(os.environ.get("MONGO_URL")) and bool(os.environ.get("DB_NAME")), "database_configuration_missing")
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], connect=False, retryReads=False,
        maxPoolSize=1, minPoolSize=0, serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000, socketTimeoutMS=15000, waitQueueTimeoutMS=5000)
    try:
        db = AcceptanceConfigDatabase(client[os.environ["DB_NAME"]])
        state = await asyncio.wait_for(read_financial_position_state(db, args.owner_id), timeout=READ_SECONDS)
    finally:
        client.close()
    require(state.get("status") == "active", "saved_snapshot_not_active")
    result = await asyncio.wait_for(prepare_in_thread(project_saved, state, args), timeout=PROJECTION_SECONDS)
    result.update(revision=count(state.get("revision")), as_of=timestamp(state.get("engine", {}).get("as_of")),
                  started_at=timestamp(state.get("started_at")))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", required=True, help="Explicit saved-state owner; never included in output")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="tabby")
    parser.add_argument("--load-dotenv", action="store_true",
                        help="Load /app/backend/.env internally without overriding existing environment values")
    parser.add_argument("--include-amounts", action="store_true", help="Include selected financial amounts in sanitized output")
    args = parser.parse_args()
    phase = "source_validation"
    try:
        require(bool(args.owner_id) and args.owner_id == args.owner_id.strip() and len(args.owner_id) <= 200,
                "owner_argument_invalid")
        sys.dont_write_bytecode = True
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        pin_source()
        logging.disable(logging.CRITICAL)
        with redirect_stdout(Discard()), redirect_stderr(Discard()):
            sys.path.insert(0, str(BACKEND))
            if args.load_dotenv:
                phase = "configuration_loading"
                from dotenv import load_dotenv
                load_dotenv(BACKEND / ".env", override=False)
            phase = "saved_snapshot_diagnostic"
            result = asyncio.run(diagnose(args))
        phase = "final_source_validation"
        pin_source()
        result.update(ok=True, source_commit=SOURCE_M, provider=args.provider,
            amount_output_enabled=args.include_amounts, saved_financial_state_reads=1,
            historical_audit_page_reads=0, database_writes=0, source_refreshes=0,
            projection="existing report() on a saved snapshot; no pricing values changed",
            read_budget_seconds=READ_SECONDS, projection_budget_seconds=PROJECTION_SECONDS)
        print(json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2))
        return 0
    except DiagnosticError as error:
        print(json.dumps({"ok": False, "phase": phase, "code": str(error)}, sort_keys=True))
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "phase": phase, "code": "interrupted"}, sort_keys=True))
        return 130
    except Exception as error:
        # Mongo and parsing exceptions can contain credentials or merchant data.
        # Deliberately omit messages, traceback, arguments, locals and environment.
        print(json.dumps({"ok": False, "phase": phase, "code": "diagnostic_failed",
                          "error_type": type(error).__name__}, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

