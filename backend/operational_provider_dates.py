"""Admit newly confirmed provider money only on its proved capture day.

A page's paid total proves an amount, not when it was paid.  Existing claims
retain their recorded date; an aggregate cannot move earlier money to the day
of a later capture.  No order, transaction-creation or refresh timestamp is a
substitute for the normalized source capture timestamp.
"""
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from operational_payment_evidence import _amount, _instant


UNPROVEN = "provider_original_capture_date_unproven"
MULTIPLE_DAYS = "provider_capture_multiple_business_dates"
DATE_ISSUES = frozenset({UNPROVEN, MULTIPLE_DAYS})
RIYADH = ZoneInfo("Asia/Riyadh")


def recognition_day(previous, captures, *, currency, confirmed, captured, created_at, as_of):
    """Return the original day, or withhold an unrepresentable confirmed increase.

    `captures` comes from the canonical payment normalizer, including the
    authenticated, explicitly order-bound native transaction bridge.  Require
    complete coverage of the captured principal, not merely one dated sample.
    Multiple references on the same business day are valid and deduplicated.
    """
    previous = previous or {}
    known = _amount(previous.get("confirmed", 0), currency)
    confirmed = _amount(confirmed, currency)
    created, now = _instant(created_at), _instant(as_of)
    if previous and confirmed <= known:
        try:
            return date.fromisoformat(previous["business_date"])
        except (ValueError, KeyError, TypeError):
            raise ValueError(UNPROVEN) from None
    if confirmed == 0:
        return created.astimezone(RIYADH).date()
    if not isinstance(captures, list) or not 0 < len(captures) <= 1024:
        raise ValueError(UNPROVEN)
    evidence = {}
    try:
        from operational_demand_refresh import calculation_checkpoint
        for index, capture in enumerate(captures):
            if index % 128 == 0:
                calculation_checkpoint()
            ref = capture["id"]
            if not isinstance(ref, str) or not ref:
                raise ValueError(UNPROVEN)
            value, at = _amount(capture["amount"], currency), _instant(capture["captured_at"])
            if value <= 0 or not created <= at <= now:
                raise ValueError(UNPROVEN)
            observation = (value, at)
            if ref in evidence and evidence[ref] != observation:
                raise ValueError(UNPROVEN)
            evidence[ref] = observation
        if sum((value for value, _ in evidence.values()), Decimal(0)) != _amount(captured, currency):
            raise ValueError(UNPROVEN)
        days = {at.astimezone(RIYADH).date() for _, at in evidence.values()}
        if len(days) != 1:
            raise ValueError(MULTIPLE_DAYS)
        effective = next(iter(days))
        if known > 0 and previous.get("business_date") != effective.isoformat():
            # Re-dating one aggregate would also re-date its already posted
            # principal.  A separate approved identity is required instead.
            raise ValueError(MULTIPLE_DAYS)
        return effective
    except (KeyError, TypeError, ArithmeticError):
        raise ValueError(UNPROVEN) from None
    except ValueError as exc:
        if str(exc) in DATE_ISSUES:
            raise
        raise ValueError(UNPROVEN) from None


def verified_original_obligation(state, order, identity, row, as_of):
    """Match a confirmed claim to actual canonical captures and saved evidence.

    This proof deliberately has no previous-claim shortcut.  A forecast or a
    net refund aggregate remains unknown until its own dated evidence can be
    represented.  Only referenced facts are read, never a whole-ledger scan.
    """
    try:
        payment, currency = order["payment"], order["currency"]
        oid, party = str(order["id"]), row["party_id"]
        if (row.get("id") != identity or row.get("kind") != "provider" or row.get("party_type") != "provider"
                or row.get("direction") != "receivable" or row.get("currency") != currency
                or payment.get("currency", currency) != currency or row.get("derived_credit")
                or row.get("superseded") or payment.get("refunds") or payment.get("cancellations")
                or payment.get("refund_allocation_unresolved") or _amount(payment.get("cancelled", 0), currency)
                or _amount(row.get("expected"), currency) != 0):
            return False
        confirmed = _amount(row.get("confirmed"), currency)
        if confirmed <= 0:
            return False
        facts = state["engine"]["facts"]
        split = payment.get("split")
        if split is not None:
            if (identity != "provider:" + oid + ":" + party or split.get("owner_id") != state.get("owner_id")
                    or split.get("order_id") != oid or split.get("order_number") != order.get("order_number")
                    or split.get("currency") != currency):
                return False
            saved = facts.get("provider_split:" + oid, {})
            if saved.get("evidence") != split or party not in saved.get("booked_providers", []):
                return False
            legs = {leg["reference"]: leg for leg in split["tenders"] if leg.get("provider") == party}
            raw_captures = payment.get("split_captures")
            if not isinstance(raw_captures, list) or len(raw_captures) > 1024 or not legs:
                return False
            captures = []
            for capture in raw_captures:
                if capture.get("provider") != party:
                    continue
                leg = legs.get(capture.get("id"))
                if (not leg or capture.get("currency") != currency
                        or _amount(leg["amount"], currency) != _amount(capture.get("amount"), currency)):
                    return False
                captures.append(capture)
            if ({capture["id"] for capture in captures} != set(legs)
                    or sum((_amount(leg["amount"], currency) for leg in legs.values()), Decimal(0)) != confirmed):
                return False
        else:
            if identity != "provider:" + oid or payment.get("provider") != party:
                return False
            captures = payment.get("captures")
            if not isinstance(captures, list) or not 0 < len(captures) <= 1024:
                return False
            for capture in captures:
                saved = facts.get("capture:" + capture["id"], {})
                if ((saved.get("order_id"), saved.get("provider")) != (oid, party)
                        or _amount(saved.get("amount"), currency) != _amount(capture.get("amount"), currency)):
                    return False
            saved_page = facts.get("provider_order_balance:" + oid)
            if saved_page is not None and (
                    (saved_page.get("owner_id"), saved_page.get("order_id"), saved_page.get("provider"), saved_page.get("currency"))
                    != (state.get("owner_id"), oid, party, currency)
                    or _amount(saved_page.get("paid"), currency) != confirmed):
                return False
        effective = recognition_day(None, captures, currency=currency, confirmed=confirmed, captured=confirmed,
            created_at=order["created_at"], as_of=as_of)
        return effective.isoformat() == row.get("business_date")
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        return False
