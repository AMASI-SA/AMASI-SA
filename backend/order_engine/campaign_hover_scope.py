"""Validated executive reporting context shared by campaign hover readers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

STORE_TIMEZONE = "Asia/Riyadh"


@dataclass(frozen=True)
class OperatingScope:
    date_from: date
    date_to: date
    payment_methods: tuple[str, ...] = ()
    shipping_companies: tuple[str, ...] = ()
    report_statuses: tuple[str, ...] | None = None
    source: str = "executive_selected_period"

    def metadata(self) -> dict:
        return {
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
            "timezone": STORE_TIMEZONE,
            "payment_methods": list(self.payment_methods),
            "shipping_companies": list(self.shipping_companies),
            "report_statuses": list(self.report_statuses) if self.report_statuses is not None else None,
            "source": self.source,
            **({"date": self.date_from.isoformat()} if self.date_from == self.date_to else {}),
        }


def resolve_operating_scope(
    *, from_date: str | None = None, to_date: str | None = None,
    payment_methods: str | None = None, shipping_companies: str | None = None,
    report_statuses: str | None = None, now: datetime | None = None,
) -> OperatingScope:
    """Resolve legacy 'today' once; an explicit scope never follows the clock."""
    explicit = any(value is not None for value in (
        from_date, to_date, payment_methods, shipping_companies, report_statuses,
    ))
    if explicit:
        try:
            if not from_date or not to_date:
                raise ValueError("operating_dates_required")
            start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
            if end < start:
                raise ValueError("reversed_operating_date_range")
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="invalid_operating_date_range") from exc
    else:
        instant = now or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        start = end = instant.astimezone(ZoneInfo(STORE_TIMEZONE)).date()

    def filters(value: str | None) -> tuple[str, ...]:
        return tuple(sorted({part.strip().casefold() for part in (value or "").split(",") if part.strip()}))

    selected = None
    if report_statuses is not None:
        from sold_financial_report import validate_report_statuses
        selected = tuple(sorted(set(validate_report_statuses(report_statuses).split(","))))
    return OperatingScope(
        start, end, filters(payment_methods), filters(shipping_companies), selected,
        "executive_selected_period" if explicit else "store_day",
    )
