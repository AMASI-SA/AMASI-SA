"""Offline evidence assessment only; never resumes Review or connects to services.

Input: {"cases": [{"operation": {...}, "request": {...}, "source": {...},
"order": {...}, "acceptance": {...}, "workflow": {...}, "component": {...}}]}.
The earlier stored-hash-only case-analysis format is accepted as insufficient
evidence. A SAFE_TO_RESUME result is a point-in-time assessment, not permission
to execute recovery; live guards must be checked again by the completion path.
"""
import argparse
import json
from pathlib import Path
import re
import sys


ALLOWED_ORDERS = frozenset({
    "291715477", "291703306", "291967952", "291717149", "292127659",
    "291717456", "291702542", "291914588", "291507628",
})
EVIDENCE_FIELDS = ("operation", "request", "source", "order", "acceptance", "workflow", "component")


def _identity(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_:.\-]{1,160}", value) else None


def analyze(document):
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, list) or len(cases) > 100:
        raise ValueError("invalid_case_envelope")
    rows = []
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("invalid_case_envelope")
        operation = case.get("operation") if isinstance(case.get("operation"), dict) else {}
        number = str(operation.get("order_number") or case.get("order_number") or "")
        if number in seen:
            raise ValueError("duplicate_order_evidence")
        seen.add(number)
        row = {
            "order_number": number if number in ALLOWED_ORDERS else None,
            "operation_id": _identity(operation.get("_id")),
            "decision": "REQUIRES_REVIEW",
            "evidence_status": "INSUFFICIENT_EVIDENCE",
            "reason": "approval_source_and_dto_evidence_missing",
        }
        if number not in ALLOWED_ORDERS:
            row["reason"] = "order_outside_explicit_allowlist"
        elif (all(isinstance(case.get(key), dict) for key in EVIDENCE_FIELDS if key != "workflow")
              and "workflow" in case and (case["workflow"] is None or isinstance(case["workflow"], dict))):
            # Import only the pure assessor, never a database client or route.
            backend = str(Path(__file__).resolve().parents[1] / "backend")
            if backend not in sys.path:
                sys.path.insert(0, backend)
            from order_review_recovery_guard import assess_recovery
            try:
                assessment = assess_recovery(
                    op=operation, request=case["request"], source=case["source"],
                    order=case["order"], acceptance=case["acceptance"],
                    workflow=case["workflow"], component=case["component"],
                )
                decision = assessment.get("decision")
                row["decision"] = decision if decision in {"SAFE_TO_RESUME", "REQUIRES_REVIEW"} else "REQUIRES_REVIEW"
                row["reason"] = _identity(assessment.get("reason")) or "assessment_refused"
                row["evidence_status"] = _identity(assessment.get("evidence_status")) or "INSUFFICIENT_EVIDENCE"
                if row["evidence_status"] == "INSUFFICIENT_EVIDENCE":
                    row["decision"] = "REQUIRES_REVIEW"
            except Exception:
                # Exceptions can contain source values. Never log their text.
                row.update(decision="REQUIRES_REVIEW", evidence_status="INSUFFICIENT_EVIDENCE",
                           reason="invalid_or_unverifiable_evidence")
        rows.append(row)
    return {
        "mode": "OFFLINE_ASSESSMENT_ONLY", "production_replay": False,
        "cases": rows,
        "counts": {
            "total": len(rows),
            "safe_to_resume": sum(row["decision"] == "SAFE_TO_RESUME" for row in rows),
            "requires_review": sum(row["decision"] == "REQUIRES_REVIEW" for row in rows),
            "insufficient_evidence": sum(row["evidence_status"] == "INSUFFICIENT_EVIDENCE" for row in rows),
        },
        "note": "No Review was executed. Missing original evidence cannot be reconstructed from stored hashes.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Existing local JSON evidence file; no URL or database URI")
    args = parser.parse_args()
    try:
        with args.input.open(encoding="utf-8-sig") as stream:
            report = analyze(json.load(stream))
    except Exception:
        # Fail without echoing paths, payload values, credentials or tracebacks.
        print(json.dumps({"error": "invalid_or_unreadable_offline_evidence"}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
