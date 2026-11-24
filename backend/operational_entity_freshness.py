"""Compact financial coverage, produced only inside canonical reconciliation.

ADR-001: additive metadata (1,7,13), canonical evidence (4,6,9), deterministic
identities (10), owner binding (11), and no credentials/provider I/O (14).
This module never admits or writes money. Its optional aggregate field is the
feature boundary: absent/invalid evidence is unknown, never an invented zero.
Reads use only the bounded saved projection, not the archived financial state.
"""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from itertools import chain
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from operational_demand_refresh import calculation_checkpoint


FIELD = "automatic_source_coverage"
MAX_ENTITIES = 256
MAX_MISSING_DAYS = 31
MAX_CALENDAR_DAYS = 3660
MAX_AGE_SECONDS = 7200
KINDS = frozenset({"employee", "ad_account", "courier", "store_driver", "provider", "supplier", "recurring", "bank"})
PLATFORMS = {"snapchat": "Snapchat", "meta": "Meta", "tiktok": "TikTok", "google_ads": "Google Ads"}
PROVIDERS = {"salla": "سلة", "tabby": "تابي", "tamara": "تمارا", "emkan": "إمكان", "paypal": "باي بال"}
MESSAGES = {
    "current": "اكتملت مطابقة الأدلة المالية المطلوبة لهذا الحساب حتى الفترة المبينة.",
    "missing_days": "توجد أيام مستحقة لم يثبت تسجيل أدلتها المالية كاملة.",
    "stale": "انقضت مدة التحديث؛ آخر نتيجة مالية محفوظة تحتاج إلى مطابقة جديدة.",
    "source_incomplete": "إعداد المصدر أو أدلته غير مكتملة؛ لا يمكن تأكيد اكتمال الأرصدة.",
    "source_issue": "توجد أدلة مالية معلقة أو متعارضة لهذه الجهة؛ لم يُثبت اكتمالها.",
    "no_financial_evidence": "لم يثبت وجود دليل مالي لهذه الجهة؛ لا يُعد ذلك رصيدًا صفريًا مؤكدًا.",
    "metadata_unavailable": "لم تُحفظ بعد مطابقة مالية مفصلة لكل جهة؛ لا يمكن تأكيد الأيام المكتملة.",
    "metadata_invalid": "تعذر التحقق من ارتباط أدلة الجهات بآخر احتساب محفوظ.",
    "metadata_limit": "تجاوزت الجهات أو الأيام حد العرض؛ توجد بيانات لم يُثبت اكتمالها ويجب مراجعتها.",
    "unconfigured": "لا يوجد مصدر تلقائي مفعّل لهذه الجهة؛ لم يُفترض وجود حركة أو رصيد صفري.",
}


def _checked(values):
    for index, value in enumerate(values):
        if index % 128 == 0:
            calculation_checkpoint()
        yield value


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _text(value, limit=200):
    return value if type(value) is str and 0 < len(value) <= limit else None


def _instant(value):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _day(value):
    try:
        parsed = date.fromisoformat(value)
        return parsed if parsed.isoformat() == value else None
    except (TypeError, ValueError):
        return None


def _money(value):
    try:
        parsed = Decimal(value) if type(value) is str else None
        return parsed if parsed is not None and parsed.is_finite() and parsed >= 0 else None
    except (InvalidOperation, TypeError):
        return None


def _usable(row):
    return type(row) is dict and not any(row.get(k) for k in ("archived", "deleted", "is_archived", "is_deleted"))


def _descriptor(row):
    if type(row) is not dict or not _text(row.get('entity_type')) or row.get("entity_type") not in KINDS:
        return None
    kind, identity, currency = row["entity_type"], _text(row.get("entity_id")), row.get("currency")
    if (identity is None or type(currency) is not str or len(currency) != 3
            or not currency.isascii() or not currency.isalpha() or not currency.isupper()):
        return None
    zone = row.get("timezone") or "Asia/Riyadh"
    try:
        if not _text(zone, 80):
            return None
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return {"key": _hash([kind, identity, currency])[:32], "entity_type": kind, "entity_id": identity,
        "label": _text(row.get("label"), 120) or identity[:120], "currency": currency,
        "timezone": zone, "platform": row.get("platform") if _text(row.get('platform')) and row.get("platform") in PLATFORMS else None,
        "source_available": row.get("source_available") is True,
        "configured": row.get("configured") is not False}


def source_entities(data, state, normalized=None):
    """Reuse already owner-validated collection rows; do not query for a catalog."""
    owner = state.get("owner_id") if type(state) is dict else None
    entities, complete = {}, bool(_text(owner))

    def rows(collection):
        nonlocal complete
        values = data.get(collection, [])
        if type(values) is not list:
            complete = False
            return []
        result = []
        for row in _checked(values):
            if type(row) is not dict or row.get("user_id") != owner:
                complete = False
            elif _usable(row):
                result.append(row)
        return result

    def add(kind, identity, label, currency="SAR", *, zone="Asia/Riyadh", platform=None, available=True, configured=True):
        nonlocal complete
        value = _descriptor({"entity_type": kind, "entity_id": identity, "label": label,
            "currency": currency, "timezone": zone, "platform": platform, "source_available": available,
            "configured": configured})
        if value is None:
            complete = False
            return
        prior = entities.get(value["key"])
        if prior is not None and prior != value:
            value["source_available"] = False
        entities[value["key"]] = value

    contracts = {}
    for contract in _checked(rows("mezan_employee_salary_contracts_v2")):
        identity = _text(contract.get("employee_id"))
        if identity:
            contracts.setdefault(identity, []).append(contract)
        else:
            complete = False
    for employee in _checked(rows("mezan_employees_v2")):
        matches = contracts.get(_text(employee.get("id")), [])
        add("employee", employee.get("id"), employee.get("name"),
            matches[0].get("currency") or "SAR" if len(matches) == 1 else "SAR", available=len(matches) == 1)
    for kind, collection in (("store_driver", "store_drivers"), ("supplier", "mezan_suppliers_v2")):
        for row in _checked(rows(collection)):
            add(kind, row.get("id"), row.get("name"), row.get("currency") or "SAR")
    accounts = rows("mezan_integration_accounts_v2")
    account_index, linked = {}, set()
    platforms = {"snapchat_ads": "snapchat", "meta_ads": "meta", "tiktok_ads": "tiktok", "google_ads": "google_ads"}
    for account in _checked(accounts):
        provider = _text(account.get('provider'))
        key = (account.get("mezan_integration_account_id"), account.get("external_account_id"), platforms.get(provider))
        if all(_text(value) for value in key):
            account_index.setdefault(key, []).append(account)
    for binding in _checked(rows("mz2_ad_account_bindings_v2")):
        key = (binding.get("integration_account_id"), binding.get("platform_account_id"), binding.get("platform"))
        matches = account_index.get(key, []) if all(_text(value) for value in key) else []
        if all(_text(value) for value in key):
            linked.add(key)
        account = matches[0] if len(matches) == 1 else {}
        platform = _text(binding.get("platform"))
        label = account.get("display_name") or account.get("name") or account.get("account_name") or PLATFORMS.get(platform, "حساب إعلاني")
        add("ad_account", str(binding.get("_id") or binding.get("id") or ""), label,
            account.get("currency") or binding.get("currency") or "SAR",
            zone=account.get("timezone") or binding.get("timezone") or "Asia/Riyadh", platform=platform,
            available=bool(len(matches) == 1 and _text(account.get('currency')) and _text(account.get('timezone'))
                and binding.get("confirmed_by") and binding.get("confirmed_at")
                and account.get("connection_status") == "connected" and binding.get("active") is not False
                and binding.get("is_active") is not False and binding.get("status") not in {"inactive", "suspended"}))
    for key, matches in _checked(account_index.items()):
        if key in linked:
            continue
        account = matches[0]
        add("ad_account", "unbound:"+_hash(key)[:40], account.get("display_name") or PLATFORMS[key[2]],
            account.get("currency") or "SAR", zone=account.get("timezone") or "Asia/Riyadh",
            platform=key[2], available=False)
    from operational_balance_sources import external_courier
    from operational_courier_tariffs import saved_identity, native_matches
    native = []
    for setup in _checked(rows("mz2_shipping_setup_v2")):
        for courier in setup.get("couriers", []) if type(setup.get("couriers", [])) is list else []:
            if not _usable(courier) or not external_courier(courier):
                continue
            native.append(courier)
            add("courier", courier.get("courier_key"), courier.get("name") or courier.get("company_name"),
                available=bool(courier.get("confirmed_by") and courier.get("confirmed_at")))
    settings = rows("settings")
    if len(settings) > 1:
        complete = False
    enabled_providers = set()
    from operational_payment_evidence import provider_for
    for setting in _checked(settings):
        configured = setting.get("shipping_companies") or []
        if type(configured) is not list or len(configured) > 1000:
            complete = False
            configured = []
        for courier in _checked(configured):
            if not _usable(courier) or not external_courier(courier):
                continue
            identity = saved_identity(courier)
            if not identity or identity.get("identity_error"):
                complete = False
                continue
            if not native_matches({"couriers": native}, identity):
                add("courier", identity["id"], identity["name"], available=False)
        methods = setting.get("payment_methods") or []
        methods = methods.values() if type(methods) is dict else methods if type(methods) is list else []
        for method in _checked(methods):
            if type(method) is dict and method.get("enabled") is False:
                continue
            name = method.get('name') if type(method) is dict else method
            provider = provider_for(method) or provider_for(name)
            if not provider:
                from operational_fee_methods import fee_rail
                provider = 'salla' if fee_rail(name) else None
            if provider:
                enabled_providers.add(provider)
    for document in _checked(rows("mz2_provider_fee_policies_v2")):
        for policy in _checked(document.get("policies") or []):
            if type(policy) is dict and policy.get("provider") in PROVIDERS and policy.get("user_id") in (None, owner):
                enabled_providers.add(policy["provider"])
    for bank in _checked(rows("mz2_financial_accounts")):
        if bank.get("account_type") == "bank":
            add("bank", bank.get("id"), bank.get("name") or bank.get("display_name"), bank.get("currency") or "SAR",
                available=bank.get("status") == "active")
    for recurring in _checked(rows("operating_recurring_obligations_v2")):
        if recurring.get("status") in {"active", "stopped"}:
            add("recurring", recurring.get("id"), recurring.get("name") or recurring.get("title") or "مصروف دوري",
                recurring.get("currency") or "SAR")
    # Existing financial identities remain visible when their configuration is
    # absent. They are explicitly unknown, not silently removed or re-created.
    for obligation in _checked((state.get("engine", {}).get("obligations", {}) or {}).values()):
        if type(obligation) is not dict or obligation.get("party_type") not in KINDS:
            continue
        kind, identity = obligation["party_type"], obligation.get("party_id")
        currency = obligation.get("currency")
        key = _hash([kind, identity, currency])[:32]
        if key not in entities:
            add(kind, identity, obligation.get("name"), currency, available=kind in {"provider", "bank"})
        if kind == "provider" and identity in PROVIDERS:
            enabled_providers.add(identity)
    # Namespaced bank identities also live in the already-resolved order source
    # and aggregate. A pending original-date proof must remain visible by bank.
    candidates = []
    for order in _checked((normalized or {}).get('orders', [])):
        payment = order.get('payment') if type(order) is dict else None
        if type(payment) is dict:
            provider_ids = {payment['provider']} if type(payment.get('provider')) is str else set()
            split = payment.get('split')
            if type(split) is dict and type(split.get('tenders')) is list:
                provider_ids.update(leg['provider'] for leg in split['tenders']
                    if type(leg) is dict and type(leg.get('provider')) is str)
            for provider in provider_ids & PROVIDERS.keys():
                if order.get('currency') == 'SAR':
                    enabled_providers.add(provider)
                add('provider', provider, PROVIDERS[provider], order.get('currency'), available=True)
        credit = order.get('bank_credit') if type(order) is dict else None
        if type(credit) is dict:
            candidates.append({'party_type': 'bank', 'party_id': credit.get('bank_id'),
                'name': credit.get('bank_name'), 'currency': credit.get('currency') or 'SAR'})
    for problem in _checked((normalized or {}).get('issues', [])):
        if (type(problem) is dict and problem.get('code') == 'provider_capture_acquisition_pending'
                and type(problem.get('provider')) is str and problem['provider'] in PROVIDERS
                and type(problem.get('currency')) is str):
            provider, currency = problem['provider'], problem['currency']
            if currency == 'SAR':
                enabled_providers.add(provider)
            add('provider', provider, PROVIDERS[provider], currency, available=False)
    for value in _checked(chain((state.get('openings') or {}).values(), state.get('movements') or [])):
        if type(value) is dict and value.get('party_type') == 'bank':
            candidates.append(value)
    for value in _checked(candidates):
        identity, currency = value.get('party_id'), value.get('currency') or 'SAR'
        if _hash(['bank', identity, currency])[:32] not in entities:
            add('bank', identity, value.get('name'), currency)
    for provider, label in PROVIDERS.items():
        key = _hash(["provider", provider, "SAR"])[:32]
        if key not in entities:
            add("provider", provider, label, configured=provider in enabled_providers,
                available=provider in enabled_providers)
    ordered = sorted(entities.values(), key=lambda r: (r["entity_type"], r["entity_id"], r["currency"]))
    return {"schema_version": 1, "owner_id": owner, "complete": complete,
            "total_entities": len(ordered), "rows": ordered[:MAX_ENTITIES]}


def _required_days(first, last):
    if first is None or last is None or first > last:
        return []
    if (last - first).days >= MAX_CALENDAR_DAYS:
        raise ValueError("calendar_limit")
    return [first + timedelta(days=offset) for offset in range((last-first).days + 1)]


def _expected_ad_day(now, zone):
    return (now.astimezone(ZoneInfo(zone)) - timedelta(hours=2)).date() - timedelta(days=1)


def _has_issue(row, issues):
    tokens = {"employee": ("salary", "employee"), "ad_account": ("ad_", "advertising"),
        "courier": ("shipping", "courier", "delivery"), "store_driver": ("driver", "delivery", "cod_"),
        "provider": ("provider", "payment", "tender"), "supplier": ("supplier", "receipt", "product_cost"),
        "recurring": ("recurring",), "bank": ("bank",)}[row["entity_type"]]
    for issue in _checked(issues):
        if type(issue) is not dict:
            return True
        code = issue.get("code")
        if type(code) is not str:
            return True
        if (any(token in code for token in tokens)
                or code == "operational_shipping_original_date_unproven"
                and issue.get("party_type") == row["entity_type"] == "store_driver"):
            # Source errors often carry order identities, not party identities.
            # Retain the family warning unless an explicit different party is
            # proven. A missing mapping cannot turn a failed source green.
            if issue.get("party_type") == row["entity_type"] and issue.get("party_id") not in (None, row["entity_id"]):
                continue
            if row["entity_type"] in {"employee", "ad_account", "recurring"}:
                identity = issue.get("source_id")
                if type(identity) is str and identity and identity != row["entity_id"]:
                    if row["entity_type"] == "employee":
                        if identity.startswith("salary:") and not identity.startswith("salary:"+row["entity_id"]+":"):
                            continue
                        if code.startswith("employee_salary_"):
                            continue
            return True
    return False


def build_coverage(state, sources, as_of, *, entity_names=None):
    """Build metadata alongside the financial result, before its existing CAS."""
    clock, start = _instant(as_of), _instant(state.get("started_at"))
    owner, engine = state.get("owner_id"), state.get("engine") or {}
    catalog = sources.get("automatic_source_entities") or {}
    available = bool(clock and start and clock >= start and catalog.get("schema_version") == 1
                     and catalog.get("owner_id") == owner and catalog.get("complete") is True)
    raw = catalog.get("rows", [])
    raw = raw if type(raw) is list else []
    total = catalog.get("total_entities")
    total = total if type(total) is int and total >= len(raw) else len(raw)
    result = {"schema_version": 1, "owner_id": owner, "as_of": as_of,
              "calculation_revision": state.get("revision", 0)+1, "complete": available,
              "total_entities": total, "rows": []}
    if not clock or not start:
        return result
    obligations = engine.get("obligations") or {}
    grouped = {}
    for key, obligation in _checked(obligations.items()):
        if type(obligation) is dict and not obligation.get("derived_credit") and not obligation.get("superseded"):
            identity = (obligation.get("party_type"), obligation.get("party_id"), obligation.get("currency"))
            day, amount = _day(obligation.get('business_date')), _money(obligation.get('confirmed'))
            if day and amount is not None and amount > 0:
                grouped[identity] = max(grouped.get(identity, day), day)
    for movement in _checked(state.get('movements') or []):
        if type(movement) is not dict or movement.get('automatic_order_bank') is not True:
            continue
        occurred, amount = _instant(movement.get('occurred_at')), _money(movement.get('amount'))
        if occurred and amount is not None and amount > 0 and start <= occurred <= clock:
            identity = ('bank', movement.get('party_id'), movement.get('currency'))
            day = occurred.astimezone(ZoneInfo('Asia/Riyadh')).date()
            grouped[identity] = max(grouped.get(identity, day), day)
    issues = engine.get("issues", [])
    previous = engine.get(FIELD)
    prior_rows = coverage_rows(previous, owner, previous.get('as_of') if type(previous) is dict else None,
        now=clock, revision=state.get('revision', 0))['rows']
    prior_success = {row['key']: row['last_success_at'] for row in prior_rows}
    seen = set()
    for item in _checked(raw[:MAX_ENTITIES]):
        row = _descriptor(item)
        if row is None or row["key"] in seen:
            result["complete"] = False
            continue
        seen.add(row["key"])
        names = (entity_names or {}).get(row["entity_type"], {})
        row["label"] = _text(names.get(row["entity_id"]), 120) or row["label"]
        latest = grouped.get((row["entity_type"], row["entity_id"], row["currency"]))
        financial_days = [latest] if latest else []
        row.update(calendar="events", calendar_start=None, calendar_end=None, expected_through=None,
            missing_days=[], missing_days_count=None, missing_days_truncated=False,
            first_missing_day=None, last_missing_day=None, last_financial_day=str(financial_days[-1]) if financial_days else None,
            last_success_at=prior_success.get(row['key']), assessed_at=as_of, reason="no_financial_evidence")
        known, required, confirmed = False, [], set()
        try:
            if row["entity_type"] == "ad_account":
                row["calendar"] = "ad_closed_days"
                first, last = start.astimezone(ZoneInfo(row["timezone"])).date(), _expected_ad_day(clock, row["timezone"])
                required = _required_days(first, last)
                for day in _checked(required):
                    key = "advertising:" + row["entity_id"] + ":" + day.isoformat()
                    marker = engine.get("ad_days", {}).get(key) or {}
                    amount, observed = _money(marker.get("amount")), _instant(marker.get("observed_at"))
                    parts = [obligations[oid] for oid in (key, key+':wallet', key+':credit') if oid in obligations
                             and not obligations[oid].get('superseded') and not obligations[oid].get('derived_credit')]
                    valid = [value for value in parts if value.get("business_date") == day.isoformat()
                             and _money(value.get("confirmed")) is not None and _money(value.get("expected")) == 0
                             and value.get('party_type') == 'ad_account' and value.get('party_id') == row['entity_id']
                             and value.get('currency') == row['currency']]
                    due = datetime.combine(day+timedelta(days=1), time.min, ZoneInfo(row["timezone"]))
                    if (marker.get("closed") is True and amount is not None and observed and due <= observed <= clock
                            and marker.get("timezone") == row["timezone"] and parts and len(parts) == len(valid)
                            and sum((_money(value["confirmed"]) for value in valid), Decimal(0)) == amount):
                        confirmed.add(day)
                known = row["source_available"]
            elif row["entity_type"] == "employee":
                row["calendar"] = "salary_days"
                matches = [value for value in sources.get("employees", []) if value.get("id") == row["entity_id"]]
                if len(matches) != 1:
                    raise ValueError("employee_source_missing")
                employee = matches[0]
                from operational_salary_start import select_first_day
                first = select_first_day(state, clock)
                for field in ("hire_date", "accrual_start_date"):
                    if employee.get(field):
                        first = max(first, date.fromisoformat(employee[field][:10]))
                last = clock.astimezone(ZoneInfo("Asia/Riyadh")).date()
                end = _day(employee.get("effective_to"))
                if end:
                    row["calendar_end"] = end.isoformat()
                    last = min(last, end)
                required = _required_days(first, last)
                for day in _checked(required):
                    key = "salary:"+row["entity_id"]+":"+day.isoformat()
                    marker, obligation = engine.get("salary_days", {}).get(key) or {}, obligations.get(key) or {}
                    amount = _money(marker.get("amount"))
                    if (amount is not None and _money(obligation.get("confirmed")) == amount
                            and marker.get("currency", obligation.get('currency')) == row["currency"] == obligation.get("currency")
                            and _money(obligation.get('expected')) == 0
                            and obligation.get("business_date") == day.isoformat()
                            and key not in engine.get("salary_conflicts", {})):
                        confirmed.add(day)
                known = row["source_available"] and employee.get("currency") == row["currency"]
            else:
                first = last = None
                known = row["source_available"] and bool(financial_days)
                if row["entity_type"] == "recurring":
                    expected = [value for value in sources.get("recurring", []) if value.get("obligation_id") == row["entity_id"]]
                    actual = [obligations.get("recurring:"+value["id"]) for value in expected]
                    known = row["source_available"] and bool(expected) and all(
                        type(value) is dict and value.get("currency") == row["currency"]
                        and value.get("business_date") == source.get("due_at")
                        and _money(value.get("confirmed")) is not None and _money(value.get("expected")) is not None
                        and _money(value["confirmed"])+_money(value["expected"]) == _money(source.get("amount"))
                        for source, value in zip(expected, actual))
                    financial_days = sorted({_day(value.get("business_date")) for value in actual
                                             if type(value) is dict and _day(value.get("business_date"))})
                    row["last_financial_day"] = str(financial_days[-1]) if financial_days else None
            if row["calendar"] != "events":
                row["calendar_start"] = first.isoformat()
                row["expected_through"] = last.isoformat()
                missing = [value.isoformat() for value in required if value not in confirmed]
                row.update(missing_days=missing[:MAX_MISSING_DAYS], missing_days_count=len(missing),
                    missing_days_truncated=len(missing)>MAX_MISSING_DAYS,
                    first_missing_day=missing[0] if missing else None, last_missing_day=missing[-1] if missing else None)
                row["last_financial_day"] = str(max(confirmed)) if confirmed else None
            problem = _has_issue(row, issues)
            row["reason"] = ("unconfigured" if not row['configured'] and not financial_days else
                             "source_incomplete" if not known else "source_issue" if problem else
                             "missing_days" if row["missing_days_count"] else "current")
            if row["reason"] == "current":
                row["last_success_at"] = as_of
            elif row["reason"] == "source_incomplete" and row["calendar"] == "events" and not financial_days:
                row["reason"] = "no_financial_evidence"
        except (ValueError, KeyError, TypeError, OverflowError, AttributeError):
            row["reason"] = "source_incomplete"
            row.update(missing_days=[], missing_days_count=None, missing_days_truncated=False)
        result["rows"].append(row)
    return result


def coverage_rows(saved, owner, as_of, *, now, revision):
    """Read only compact metadata; newly due days expire without a refresh."""
    def unavailable(code):
        return {"state": "unknown", "rows": [], "omitted_entities": 0, "message": MESSAGES[code]}
    if saved is None:
        return unavailable("metadata_unavailable")
    stamp = _instant(as_of)
    now = _instant(now)
    if (type(saved) is not dict or saved.get("schema_version") != 1 or saved.get("owner_id") != owner
            or saved.get("as_of") != as_of or stamp is None or now is None or stamp > now
            or type(revision) is not int or revision < 0
            or type(saved.get("calculation_revision")) is not int or not 0 < saved["calculation_revision"] <= revision
            or type(saved.get("rows")) is not list or len(saved["rows"]) > MAX_ENTITIES
            or type(saved.get("total_entities")) is not int or saved["total_entities"] < len(saved["rows"])):
        return unavailable("metadata_invalid")
    rows, keys = [], set()
    for value in saved["rows"]:
        row = _descriptor(value)
        if (row is None or row["key"] in keys or not _text(value.get('reason')) or value.get("reason") not in MESSAGES
                or value.get("assessed_at") != as_of or not _text(value.get('calendar'))
                or value.get("calendar") not in {"events", "ad_closed_days", "salary_days"}):
            return unavailable("metadata_invalid")
        keys.add(row["key"])
        count, missing = value.get("missing_days_count"), value.get("missing_days")
        if (type(missing) is not list or len(missing)>MAX_MISSING_DAYS or any(_day(day) is None for day in missing)
                or len(missing) != len(set(missing)) or count is not None and (type(count) is not int or count<len(missing))):
            return unavailable("metadata_invalid")
        for field in ('last_financial_day', 'calendar_start', 'calendar_end', 'expected_through', 'first_missing_day', 'last_missing_day'):
            if value.get(field) is not None and _day(value[field]) is None:
                return unavailable('metadata_invalid')
        expected = _day(value.get("expected_through"))
        reason = value["reason"]
        additional = []
        if value["calendar"] != "events" and count is not None and expected is not None:
            due = (_expected_ad_day(now, row["timezone"]) if value["calendar"] == "ad_closed_days"
                   else now.astimezone(ZoneInfo("Asia/Riyadh")).date())
            end = _day(value.get("calendar_end"))
            due = min(due, end) if end else due
            if due > expected:
                additional_count = (due-expected).days
                additional = [(expected+timedelta(days=i+1)).isoformat() for i in range(min(additional_count, MAX_MISSING_DAYS))]
                count += additional_count
                expected = due
        missing = sorted(set(missing+additional))[:MAX_MISSING_DAYS]
        if reason == "current" and count:
            reason = "missing_days"
        last = _instant(value.get("last_success_at"))
        if ((value.get('last_success_at') is not None and last is None) or last is not None and last > stamp
                or value['reason'] == 'current' and (last != stamp or value['calendar'] != 'events' and count is None)):
            return unavailable("metadata_invalid")
        age = max(0, int((now-(last or stamp)).total_seconds()))
        if reason == "current" and age >= MAX_AGE_SECONDS:
            reason = "stale"
        row.update(state="inactive" if reason == 'unconfigured' else "current" if reason == "current" else "stale" if reason in {"stale", "missing_days"} else "unknown",
            last_success_at=last.isoformat() if last else None, last_financial_day=value.get("last_financial_day"),
            data_through=value.get("last_financial_day"), expected_through=expected.isoformat() if expected else None,
            missing_days=missing, missing_days_count=count, missing_days_truncated=count is not None and count>len(missing),
            first_missing_day=value.get('first_missing_day') or (missing[0] if missing else None),
            last_missing_day=additional[-1] if additional and len(additional) < MAX_MISSING_DAYS else
                expected.isoformat() if additional and expected else value.get('last_missing_day'),
            stale_for_seconds=age if last else None, assessed_at=as_of, calendar=value["calendar"], message=MESSAGES[reason])
        rows.append(row)
    omitted = saved["total_entities"] - len(rows)
    state = ("unknown" if omitted or saved.get("complete") is not True or any(row["state"] == "unknown" for row in rows)
             else "stale" if any(row["state"] == "stale" for row in rows) else "current")
    return {"state": state, "rows": rows, "omitted_entities": omitted,
            "message": MESSAGES["metadata_limit"] if omitted else MESSAGES["source_incomplete"] if saved.get("complete") is not True else ""}


def recovery_snapshot(state, first_day, last_day):
    """Read-only before/after totals; never emit raw customer references or rows."""
    first, last = _day(first_day), _day(last_day)
    if first is None or last is None or not 0 <= (last-first).days < MAX_CALENDAR_DAYS:
        raise ValueError("invalid_recovery_window")
    groups, identities = {}, set()
    for identity, value in _checked((state.get("engine", {}).get("obligations") or {}).items()):
        if type(value) is not dict:
            raise ValueError("invalid_financial_evidence")
        day = _day(value.get("business_date"))
        if day is None or not first <= day <= last:
            continue
        confirmed, expected = _money(value.get("confirmed")), _money(value.get("expected"))
        if confirmed is None or expected is None:
            raise ValueError("invalid_financial_evidence")
        key = (value.get("party_type"), value.get("party_id"), value.get("currency"), value.get("direction"), day.isoformat())
        row = groups.setdefault(key, {"count": 0, "confirmed": Decimal(0), "expected": Decimal(0), "ids": []})
        row["count"] += 1
        row["confirmed"] += confirmed
        row["expected"] += expected
        row["ids"].append(_hash(identity))
        identities.add(_hash(identity))
    movements = state.get("movements") or []
    if type(movements) is not list or any(type(value) is not dict for value in movements):
        raise ValueError('invalid_financial_evidence')
    manual = [value for value in movements if not any(value.get(flag) is True for flag in
        ("automatic_order_bank", "automatic_prepaid_shipping"))]
    automatic_groups, duplicate_keys = {}, {'request_id': {}, 'order_number': {}, 'bank_transaction_reference': {}}
    unknown_automatic_dates = 0
    for value in _checked(movements):
        automatic = any(value.get(flag) is True for flag in ('automatic_order_bank', 'automatic_prepaid_shipping'))
        if not automatic:
            continue
        for field, counts in duplicate_keys.items():
            raw = value.get(field)
            if _text(raw) and (field == 'request_id' or value.get('automatic_order_bank') is True):
                identity = _hash(raw)
                counts[identity] = counts.get(identity, 0) + 1
        stamp, amount = _instant(value.get('occurred_at')), _money(value.get('amount'))
        if stamp is None or amount is None:
            unknown_automatic_dates += 1
            continue
        day = stamp.astimezone(ZoneInfo('Asia/Riyadh')).date()
        if first <= day <= last:
            key = (value.get('party_type'), value.get('party_id'), value.get('currency'), value.get('direction'), day.isoformat())
            group = automatic_groups.setdefault(key, {'count': 0, 'amount': Decimal(0)})
            group['count'] += 1
            group['amount'] += amount
    output = []
    for key, value in sorted(groups.items(), key=lambda pair: str(pair[0])):
        output.append(dict(zip(("entity_type", "entity_id", "currency", "direction", "business_date"), key),
            count=value["count"], confirmed=format(value["confirmed"], ".2f"), expected=format(value["expected"], ".2f"),
            identity_digest=_hash(sorted(value["ids"]))))
    return {"schema_version": 1, "as_of": state.get("engine", {}).get("as_of"), "revision": state.get("revision"),
        "from": first_day, "through": last_day, "obligations": output, "obligation_identity_digest": _hash(sorted(identities)),
        "automatic_movements": [dict(zip(('entity_type', 'entity_id', 'currency', 'direction', 'business_date'), key),
            count=value['count'], amount=format(value['amount'], '.2f'))
            for key, value in sorted(automatic_groups.items(), key=lambda pair: str(pair[0]))],
        "automatic_movements_unverified_date_or_amount": unknown_automatic_dates,
        "duplicate_automatic_keys": {field: sum(count-1 for count in counts.values()) for field, counts in duplicate_keys.items()},
        "manual_movement_count": len(manual), "manual_movements_digest": _hash(sorted(_hash(value) for value in manual)),
        "movement_count": len(movements), "movement_id_count": len({value.get("id") for value in movements})}
