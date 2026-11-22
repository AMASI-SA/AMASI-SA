"""Demand-only display projections of the saved operational ledger.

The web boundary reads identity, permissions and a small snapshot descriptor.
Heavy builders run only in the dedicated demand-read child process. None of
these values is an authorization or an input to a money mutation.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
import re
from types import SimpleNamespace
from typing import get_args
import unicodedata
from zoneinfo import ZoneInfo

from operational_balance_access import OPERATION_LABELS, load_access
from operational_balance_store import STATES, digest, fail, initial, now
from operational_financial_activity_runtime import checked_rows, prepare_in_thread
from operational_web_page_policy import bind_page_request, request_modes

FAMILIES = frozenset({
    "operational_home", "operational_directory", "operational_options",
    "operational_selection", "operational_resource", "operational_ledger", "operational_register",
    "operational_statement", "operational_history", "operational_position",
    "operational_report",
})
ASSET_KINDS = ("bank", "cash", "provider", "pos_receivable", "ad_account")
PARTY_KINDS = ("supplier", "employee", "employee_custody", "courier", "store_driver",
               "external_person", "owner_withdrawal", "operating_expense")
PROJECTION_VERSION = "workspace-v1"
HEADER_FIELDS = {"_id": 1, "owner_id": 1, "revision": 1, "status": 1,
                 "started_at": 1, "engine.as_of": 1}
_PATHS = {"operational_home": "home", "operational_directory": "directory",
    "operational_options": "options", "operational_selection": "selection",
    "operational_resource": "resource", "operational_ledger": "ledger",
    "operational_register": "movements", "operational_statement": "statements",
    "operational_history": "history", "operational_position": "position",
    "operational_report": "report"}
_FILTERS = {
    "operational_home": set(), "operational_selection": set(),
    "operational_position": set(), "operational_history": set(),
    "operational_directory": {"group", "kind", "q"},
    "operational_options": {"operation", "role", "kind", "selected_kind", "selected_id", "q"},
    "operational_resource": {"ad_bank_fx"},
    "operational_ledger": {"ad_bank_fx", "provider_orders", "q", "date_from", "date_to", "courier_history"},
    "operational_register": {"q", "date_from", "date_to", "direction", "status", "courier_history"},
    "operational_statement": {"date_from", "date_to", "purpose"},
    "operational_report": {"view", "expected_revision", "expected_as_of", "expected_started_at"},
}


def history_month():
    return datetime.fromisoformat(now().replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Riyadh")).strftime("%Y-%m")


def validate_spec(spec):
    """Bind the stored builder to its exact authorized HTTP route and filters.

    The queue never accepts a client-supplied descriptor. Still fail closed if a
    future caller or stored job mismatches an authorized page and a builder.
    """
    from operational_balance_access import RESOURCE_KINDS
    from operational_workspace_ledger_filters import check_period
    from operational_financial_position_views import FinancialPositionView
    family, filters = spec.get("family"), spec.get("filters")
    if (family not in FAMILIES or spec.get("schema") != 1
            or spec.get("projection_version") != PROJECTION_VERSION
            or spec.get("client_kind") not in {"web", "mobile"}
            or spec.get("cache_seconds") != 300
            or not isinstance(filters, dict) or not set(filters) <= _FILTERS[family]):
        raise ValueError("demand_read_request_invalid")
    target = spec.get("resource")
    required = family in {"operational_selection", "operational_resource", "operational_ledger",
                           "operational_statement", "operational_history"}
    if target is not None:
        if (family not in {"operational_register"} and not required
                or not isinstance(target, (list, tuple)) or len(target) != 2
                or not all(isinstance(v, str) and v and "/" not in v for v in target)
                or target[0] not in RESOURCE_KINDS or len(target[1]) > 200):
            raise ValueError("demand_read_request_invalid")
    elif required:
        raise ValueError("demand_read_request_invalid")
    path = "/api/operational-balances/workspace/" + _PATHS[family]
    if required:
        path += "/" + "/".join(target)
    if spec.get("request") != {"path": path, "method": "GET", "query": filters}:
        raise ValueError("demand_read_request_invalid")
    if (spec.get("period") != {k: filters.get(k) for k in ("date_from", "date_to")}
            or spec.get("currency") is not None):
        raise ValueError("demand_read_request_invalid")
    for key in ("ad_bank_fx", "provider_orders", "courier_history"):
        if key in filters and type(filters[key]) is not bool:
            raise ValueError("demand_read_request_invalid")
    if "q" in filters and (not isinstance(filters["q"], str) or len(filters["q"]) > 160):
        raise ValueError("demand_read_request_invalid")
    check_period(filters.get("date_from"), filters.get("date_to"))
    if family == "operational_directory":
        if filters.get("group") not in {"assets", "parties"}:
            raise ValueError("demand_read_request_invalid")
        selected_kinds(filters, groups=True)
    if family == "operational_options":
        if (filters.get("operation") not in {"in", "out", "move", "ads", "wallet", "movement", "filter", *OPERATION_LABELS}
                or filters.get("role", "recipient") not in {"recipient", "source", "filter"}
                or (filters.get("selected_kind") is None) != (filters.get("selected_id") is None)):
            raise ValueError("demand_read_request_invalid")
        if filters.get("selected_kind") is not None and (filters["selected_kind"] not in RESOURCE_KINDS
                or not isinstance(filters["selected_id"], str) or not 1 <= len(filters["selected_id"]) <= 200):
            raise ValueError("demand_read_request_invalid")
        selected_kinds(filters)
    if family == "operational_register" and (filters.get("direction") not in {None, "in", "out", "transfer"}
            or filters.get("status") not in {None, "done", "info"}):
        raise ValueError("demand_read_request_invalid")
    if family == "operational_statement" and (not filters.get("date_from")
            or filters.get("purpose", "view") not in {"view", "print"}):
        raise ValueError("demand_read_request_invalid")
    if family == "operational_report":
        revision = filters.get("expected_revision")
        if (filters.get("view", "all") not in get_args(FinancialPositionView)
                or revision is not None and (type(revision) is not int or revision < 0)):
            raise ValueError("demand_read_request_invalid")
        for key in ("expected_as_of", "expected_started_at"):
            if filters.get(key) is not None and (not isinstance(filters[key], str) or not 1 <= len(filters[key]) <= 64):
                raise ValueError("demand_read_request_invalid")
    month = spec.get("calendar_month")
    if (family == "operational_history" and (not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month))
            or family != "operational_history" and month is not None):
        raise ValueError("demand_read_request_invalid")


async def snapshot_header(db, owner):
    value = await db[STATES].find_one({"_id": owner, "owner_id": owner}, HEADER_FIELDS)
    value = value if value is not None else initial(owner)
    if (value.get("_id") != owner or value.get("owner_id") != owner
            or type(value.get("revision")) is not int or value["revision"] < 0):
        fail("operational_financial_snapshot_invalid", "تعذر التحقق من لقطة التقرير المالي المحفوظة", 503)
    return value


async def directory_input(db, owner):
    fields = {**HEADER_FIELDS, **dict.fromkeys(("pos_receivable_binding", "ad_funding",
        "courier_tariffs.payment_mode", "courier_tariffs.cash_account.id", "engine.ad_balance_sar_basis"), 1)}
    return await db[STATES].find_one({"_id": owner, "owner_id": owner}, fields) or initial(owner)


def source_revision(header):
    return {"revision": header["revision"], "status": header["status"],
            "started_at": header.get("started_at"), "as_of": header.get("engine", {}).get("as_of")}


def permission_fingerprint(actor, policy):
    # A version number alone does not include browser-page narrowing or changes
    # to malformed/legacy grants. Bind the effective normalized policy as well.
    return digest({"actor_id": actor["id"], "owner_id": policy.owner_id,
        "name": actor.get("name"), "role": actor.get("role"), "created_by": actor.get("created_by"),
        "web_page_access": actor.get("web_page_access"), "enabled": policy.enabled,
        "owner_override": policy.owner_override, "version": policy.version,
        "operations": sorted(policy.operations),
        "resources": sorted(policy.resources, key=lambda r: (r["kind"], r["id"]))})


async def load_context(db, principal, *, expected_owner=None):
    # The canonical access loader already refreshes actor and owner membership.
    # Use that exact actor for both page checks and the returned policy, avoiding
    # a second identity read inside one authorization pass. Callers still repeat
    # this pass after acquiring data to detect revoked or changed permissions.
    policy = await load_access(db, principal, expected_owner)
    actor, owner = policy.actor, policy.owner_id
    request_modes(actor)  # Explicit page check even for the owner override.
    policy.require_enabled()
    return actor, owner, policy


def context_metadata(actor, owner, policy, header):
    from operational_balance_workspace import KIND_OPERATIONS, _actor_snapshot, _business_day
    def permitted_kind(kind, *, use=False, movements=False, balance=False):
        return policy.owner_override or any(r["kind"] == kind and
            (not use or r["use"]) and (not movements or r["movements"] != "none") and
            (not balance or r["balance"]) for r in policy.resources)
    can_create = any(policy.can_operation(op) and permitted_kind(kind, use=True)
                     for kind, operations in KIND_OPERATIONS.items() for op in operations)
    return {"actor": {**_actor_snapshot(actor), "is_owner": policy.owner_override},
        "session_scope": digest([owner, actor["id"]]), "permission_version": policy.version,
        "owner_override": policy.owner_override, "status": header["status"], "resources": [],
        "ledger_start": _business_day({"occurred_at": header["started_at"]}) if header.get("started_at") else None,
        "capabilities": {"manage_access": policy.owner_override,
            "create_entries": header["status"] == "active" and can_create,
            "read_movements": policy.owner_override or any(r["movements"] != "none" for r in policy.resources),
            "review_collections": policy.can_operation("courier.review") and (policy.owner_override or any(
                r["kind"] == "store_driver" and r["movements"] == "all" for r in policy.resources)),
            "review_orders": policy.owner_override or any(r["kind"] in {"bank", "provider", "courier", "store_driver"}
                and r["movements"] == "all" for r in policy.resources),
            "purchase_suppliers": policy.can_operation("supplier.accrual") and permitted_kind("supplier", use=True)},
        "operations": [{"key": key, "label": label} for key, label in OPERATION_LABELS.items()
                       if policy.can_operation(key)]}


async def bootstrap(db, principal):
    actor, owner, policy = await load_context(db, principal)
    header = await snapshot_header(db, owner)
    latest, latest_owner, current = await load_context(db, principal, expected_owner=owner)
    if permission_fingerprint(actor, policy) != permission_fingerprint(latest, current):
        fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
    return context_metadata(latest, latest_owner, current, header)


@contextmanager
def spec_request(spec):
    validate_spec(spec)
    descriptor = spec["request"]
    if descriptor.get("method") != "GET" or not descriptor.get("path", "").startswith(
            "/api/operational-balances/workspace/"):
        raise ValueError("demand_read_request_invalid")
    principal = {"id": spec["actor_id"], "_session_client":
                 "amasi_mobile" if spec["client_kind"] == "mobile" else "web"}
    request = SimpleNamespace(url=SimpleNamespace(path=descriptor["path"]), method="GET",
                              query_params=descriptor.get("query", {}))
    with bind_page_request(principal, request):
        yield principal


async def make_spec(db, principal, request, family, *, filters=None, resource=None):
    if family not in FAMILIES:
        raise ValueError("demand_read_family_invalid")
    actor, owner, policy = await load_context(db, principal)
    if family == "operational_report" and not policy.owner_override:
        fail("operational_workspace_route_required", "استخدم صفحة الأرصدة المخصصة لصلاحياتك", 403)
    filters = filters or {}
    if resource:
        access = policy.resource(*resource)
        allowed = (policy.can_reference(*resource) if family == "operational_selection" else
                   access["balance"] if family == "operational_resource" else access["movements"] != "none")
        if not allowed:
            fail("operational_workspace_not_found", "الحساب غير متاح", 404)
    header = await snapshot_header(db, owner)
    if family == "operational_report" and (filters.get("view") == "forecast" or any(
            filters.get(key) is not None for key in ("expected_revision", "expected_as_of", "expected_started_at"))):
        from operational_financial_position_state import require_snapshot_binding
        require_snapshot_binding(header, revision=filters.get("expected_revision"),
            as_of=filters.get("expected_as_of"), started_at=filters.get("expected_started_at"))
    spec = {"schema": 1, "family": family, "owner_id": owner, "actor_id": actor["id"],
        "client_kind": "mobile" if principal.get("_session_client") == "amasi_mobile" or principal.get("_mobile_actor_id") else "web",
        "request": {"path": request.url.path, "method": "GET", "query": dict(filters)},
        "permission_fingerprint": permission_fingerprint(actor, policy),
        "source_revision": source_revision(header), "resource": list(resource) if resource else None,
        "currency": filters.get("currency"), "period": {k: filters.get(k) for k in ("date_from", "date_to")},
        "filters": filters, "projection_version": PROJECTION_VERSION, "cache_seconds": 300}
    if family == "operational_history":
        spec["calendar_month"] = history_month()
    validate_spec(spec)
    return spec


async def authorize_spec(db, spec):
    if spec.get("family") not in FAMILIES:
        raise ValueError("demand_read_family_invalid")
    with spec_request(spec) as principal:
        actor, owner, policy = await load_context(db, principal, expected_owner=spec["owner_id"])
        header = await snapshot_header(db, owner)
        if (permission_fingerprint(actor, policy) != spec["permission_fingerprint"]
                or source_revision(header) != spec["source_revision"]
                or spec["family"] == "operational_history" and spec["calendar_month"] != history_month()):
            fail("operational_read_source_changed", "تغيرت البيانات أو الصلاحيات؛ أعد تحميل الصفحة", 409)
    return True


def _signed(resource):
    balance = resource.get("balance")
    if not resource["access"]["balance"] or not balance or balance.get("visible") is False:
        return None
    amount = Decimal(balance["amount"])
    cash = resource["kind"] in {"bank", "cash", "employee_custody", "ad_account"}
    negative = balance["side"] == ("for_party" if cash else "for_us")
    return -amount if negative else amount


def _home_summary(resources):
    from operational_balance_service import fmt
    cash = [r for r in resources if r["kind"] in {"bank", "cash"}
            and not r.get("prepaid_shipping_cash") and r["currency"] == "SAR"]
    totals = {}
    for kind in ("bank", "cash"):
        values = [_signed(r) for r in cash if r["kind"] == kind]
        values = [v for v in values if v is not None]
        totals[kind] = sum(values, Decimal(0)) if values else None
    known = [value for value in totals.values() if value is not None]
    drawers = [r for r in cash if r["kind"] == "cash"]
    return {"currency": "SAR", **{key: fmt(value) if value is not None else None for key, value in totals.items()},
        "total": fmt(sum(known, Decimal(0))) if known else None, "cash_count": len(drawers),
        "cash_balance_visible": any(r["access"]["balance"] for r in drawers)}


async def _recent_without_directories(db, owner, policy, state, checkpoint):
    """Resolve only the identities appearing in the four canonical recent rows.

    Identity/lifecycle is still checked against the current catalog. If a
    candidate is unavailable, remove it and let the unchanged recent-posting
    reducer find the next authorized event (including its other cash leg).
    """
    from operational_balance_workspace import _recent_postings, resource_catalog
    candidates = {}
    for item in checked_rows(state.get("movements", ())):
        for kind, identity, currency in (
            (item.get("party_type"), item.get("party_id"), item.get("currency")),
            (item.get("bank_kind"), item.get("bank_id"), item.get("bank_currency") or item.get("currency")),
        ):
            if not all(isinstance(v, str) and v for v in (kind, identity, currency)):
                continue
            access = policy.resource(kind, identity)
            if access["movements"] != "none":
                candidates[(kind, identity, currency)] = {
                    "kind": kind, "id": identity, "currency": currency, "name": identity,
                    "access": access, "operations": [], "balance": None}
    resolved = set()
    while candidates:
        recent = await prepare_in_thread(_recent_postings, state, list(candidates.values()), policy)
        missing = {(row["party_type"], row["party_id"]) for row in recent} - resolved
        if not missing:
            return recent
        selected = defaultdict(list)
        for kind, identity in sorted(missing):
            selected[kind].append(identity)
        await checkpoint()
        resources, _ = await resource_catalog(db, owner, policy, state, directory_only=True,
            selected_kinds=set(selected), identities_by_kind=selected)
        for key in list(candidates):
            if key[:2] in missing:
                del candidates[key]
        for resource in resources:
            candidates[(resource["kind"], resource["id"], resource["currency"])] = resource
        resolved.update(missing)
    return []


async def _home(db, owner, policy, state, checkpoint):
    from operational_balance_workspace import resource_catalog
    # Summary cash and the preferred bank/cash/provider cards are the only
    # catalog groups opened by home. Report arithmetic remains canonical.
    resources, computed = await resource_catalog(db, owner, policy, state,
        ad_bank_fx=True, provider_positions=True, selected_kinds={"bank", "cash", "provider"})
    preferred = [next((r for r in resources if r["kind"] == kind and not r.get("prepaid_shipping_cash")), None)
                 for kind in ("bank", "cash", "provider")]
    preferred = [r for r in preferred if r is not None]
    cards = (preferred + [r for r in resources if r not in preferred])[:3]
    if len(cards) < 3:
        # These identities are requested only when an actual visible card needs
        # them, preserving the old fallback ordering and exact card values.
        extra, _ = await resource_catalog(db, owner, policy, state, ad_bank_fx=True,
            provider_positions=True, selected_kinds={"pos_receivable", "ad_account"}, computed_override=computed)
        ordering = {kind: i for i, kind in enumerate(("provider", "ad_account", "bank", "cash", "pos_receivable"))}
        all_assets = sorted(resources + extra, key=lambda r: ordering[r["kind"]])
        cards = (preferred + [r for r in all_assets if r not in preferred])[:3]
    await checkpoint()
    recent = await _recent_without_directories(db, owner, policy, state, checkpoint)
    has_issues = any(r.get("order_issues_count", 0) is None or r.get("order_issues_count", 0) > 0 for r in resources)
    if not has_issues and (computed.get("issues") or not computed.get("as_of")):
        # The notice reveals no closed shipping directory or order detail.
        has_issues = policy.owner_override or any(r["kind"] in {"courier", "store_driver"}
            and r["movements"] == "all" for r in policy.resources)
    return {"header": {"summary": _home_summary(resources), "resources": cards,
                        "recent_movements": recent, "has_order_issues": has_issues}, "rows": None}


def directory_search(value):
    # Exactly the previous React searchable() normalization. In particular it
    # does not silently broaden Persian digits or casefold ß into ss.
    value = unicodedata.normalize("NFKC", str(value or ""))
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = value.translate(str.maketrans("أإآٱى٠١٢٣٤٥٦٧٨٩", "ااااي0123456789"))
    return value.strip().lower()


def selected_kinds(filters, *, groups=False):
    from operational_balance_workspace import RESOURCE_KINDS
    group = filters.get("group")
    allowed = ASSET_KINDS if group == "assets" else PARTY_KINDS if group == "parties" else RESOURCE_KINDS
    requested = tuple(part for part in str(filters.get("kind") or "").split(",") if part)
    if len(requested) != len(set(requested)) or any(kind not in allowed for kind in requested):
        fail("operational_workspace_kind", "اختر مجموعة حسابات صحيحة", 422)
    return tuple(kind for kind in allowed if not requested or kind in requested)


async def _directory(db, owner, policy, state, filters):
    from operational_balance_workspace import resource_catalog
    kinds = selected_kinds(filters, groups=True)
    resources, _ = await resource_catalog(db, owner, policy, state, selected_kinds=set(kinds),
        selected_kind=kinds[0] if len(kinds) == 1 else None, ad_bank_fx=True,
        provider_positions=bool(set(kinds) & {"provider"}))
    term = directory_search(filters.get("q"))
    # Filter once on the server before persisting the ordered rows. HTTP pages
    # subsequently acquire only the indexed page, never this complete list.
    matches = [r for kind in kinds for r in resources if r["kind"] == kind
               and (not term or term in directory_search(r["name"]))]
    return {"header": {"group": filters.get("group"), "kinds": [kind for kind in kinds
        if any(r["kind"] == kind for r in resources)], "has_cash": any(r["kind"] == "cash" for r in resources)}, "rows": matches}


def _target_operation(kind, mode):
    from operational_balance_workspace import KIND_OPERATIONS
    if mode in OPERATION_LABELS:
        return mode if mode in KIND_OPERATIONS.get(kind, ()) else None
    if mode in {"move", "wallet"}:
        return "accounts.transfer" if kind in {"bank", "cash"} else None
    if mode == "supplier.accrual":
        return "supplier.accrual" if kind == "supplier" else None
    mapping = {
        "in": {"employee": "employee.collection", "supplier": "supplier.collection",
            "external_person": "external.receive", "employee_custody": "custody.return",
            "owner_withdrawal": "owner.return", "provider": "provider.settle",
            "pos_receivable": "provider.settle", "courier": "courier.receive", "store_driver": "courier.receive"},
        "out": {"employee": "employee.payment", "supplier": "supplier.payment", "external_person": "external.pay",
            "employee_custody": "custody.fund", "operating_expense": "expense.pay",
            "owner_withdrawal": "owner.withdraw", "ad_account": "ads.fund", "courier": "courier.pay",
            "store_driver": "courier.pay"},
        "ads": {"ad_account": "ads.fund"},
    }
    return mapping.get(mode, {}).get(kind)


def _source_mode(operation):
    if operation in {"accounts.transfer"}:
        return "move"
    if (operation in {"external.receive", "provider.settle", "courier.receive", "owner.return", "custody.return"}
            or operation.endswith(".collection")):
        return "in"
    if operation in {"ads.fund"}:
        return "ads"
    if operation in OPERATION_LABELS:
        return "out"
    return operation


def _option_eligible(resource, policy, filters, selected):
    mode, role = filters.get("operation"), filters.get("role", "recipient")
    kind, access = resource["kind"], resource["access"]
    if mode == "filter":
        return access["movements"] != "none"
    if mode == "courier.review":
        return (policy.can_operation("courier.review") and ((kind == "store_driver" and access["movements"] == "all")
                or kind == "bank" and policy.can_reference(kind, resource["id"])))
    if not access["use"]:
        return False
    if role == "source":
        mode = _source_mode(mode)
        if resource["currency"] != "SAR" or selected and (kind, resource["id"]) == (selected["kind"], selected["id"]):
            return False
        if selected and (selected.get("ad_bank_funding") or (selected["kind"], selected["id"]) == ("provider", "paypal")
                         or selected["kind"] == "pos_receivable"):
            return kind == "bank"
        if kind in {"bank", "cash"}:
            return mode != "out" or not resource.get("prepaid_shipping_cash")
        return (mode == "out" and selected and selected["kind"] in {"supplier", "external_person", "operating_expense"}
                and kind == "employee_custody" and policy.can_operation("custody.spend")
                and resource.get("can_fund_payment") is True)
    if mode == "movement":
        return bool(resource["operations"])
    beneficiary = {"external.salary": "employee", "external.charity_company": "operating_expense",
        "external.charity_owner": "owner_withdrawal", "external.household": "owner_withdrawal",
        "external.personal": "owner_withdrawal"}.get(mode)
    if beneficiary and selected and selected["kind"] == "external_person":
        return kind == beneficiary and policy.can_operation(mode) and resource["currency"] == "SAR"
    operation = _target_operation(kind, mode)
    return bool(operation and operation in resource["operations"] and
        (mode != "wallet" or resource.get("prepaid_shipping_cash")) and
        (resource["currency"] == "SAR" or resource.get("ad_bank_funding")))


async def _options(db, owner, policy, state, filters):
    from operational_balance_workspace import resource_catalog
    mode, role = filters.get("operation"), filters.get("role", "recipient")
    kinds = selected_kinds(filters)
    if mode == "courier.review":
        policy.require_operation("courier.review")
        kinds = tuple(kind for kind in kinds if kind in {"store_driver", "bank"})
    elif mode == "filter":
        pass
    elif role == "source":
        kinds = tuple(kind for kind in kinds if kind in {"bank", "cash", "employee_custody"})
    elif mode in {"external.salary", "external.charity_company", "external.charity_owner", "external.household", "external.personal"}:
        policy.require_operation(mode)
        beneficiary = {"external.salary": "employee", "external.charity_company": "operating_expense"}.get(mode, "owner_withdrawal")
        kinds = tuple(kind for kind in kinds if kind == beneficiary)
    elif mode != "movement":
        kinds = tuple(kind for kind in kinds if _target_operation(kind, mode) and policy.can_operation(_target_operation(kind, mode)))
    selected = None
    if filters.get("selected_kind") and filters.get("selected_id"):
        target = (filters["selected_kind"], filters["selected_id"])
        policy.require_use(*target)
        choices, _ = await resource_catalog(db, owner, policy, state, selected_resource=target,
            selected_kinds={target[0]}, identities_by_kind={target[0]: [target[1]]}, directory_only=True, ad_bank_fx=True)
        selected = next(iter(choices), None)
        if selected is None:
            fail("operational_workspace_not_found", "الحساب غير متاح", 404)
    if mode in OPERATION_LABELS:
        policy.require_operation(mode)
    resources, _ = await resource_catalog(db, owner, policy, state, selected_kinds=set(kinds),
        selected_kind=kinds[0] if len(kinds) == 1 else None, ad_bank_fx=True, directory_only=role != "source")
    term = directory_search(filters.get("q"))
    rows = [r for r in resources if _option_eligible(r, policy, filters, selected)
            and (not term or term in directory_search(r["name"]))]
    return {"header": {}, "rows": rows}


async def _selection(db, owner, policy, state, target, *, balance_only=False, ad_bank_fx=True):
    from operational_balance_workspace import resource_catalog
    if balance_only and not policy.resource(*target)["balance"]:
        fail("operational_workspace_not_found", "الحساب غير متاح", 404)
    resources, _ = await resource_catalog(db, owner, policy, state, selected_resource=target,
        selected_kinds={target[0]}, identities_by_kind={target[0]: [target[1]]}, ad_bank_fx=ad_bank_fx,
        provider_positions=target[0] == "provider", directory_only=not policy.resource(*target)["balance"])
    if len(resources) != 1:
        fail("operational_workspace_not_found", "الحساب غير متاح", 404)
    return {"header": resources[0], "rows": None}


async def _ledger(db, actor, owner, policy, state, target, filters, family):
    from operational_balance_workspace import (resource_catalog, ledger_items, _actor_snapshot,
        _pos_summary, expected_collections, courier_summary, automatic_history)
    from operational_workspace_projection import balance_note, compact_rows, projected_balance_status, statement as period_statement
    from operational_workspace_ledger_filters import filter_activity
    from operational_workspace_supplier_invoices import fill_historical_headers
    kind, identity = target
    provider_orders = filters.get("provider_orders", False)
    resources, computed = await resource_catalog(db, owner, policy, state, selected_resource=target,
        selected_kinds={kind}, identities_by_kind={kind: [identity]}, ad_bank_fx=filters.get("ad_bank_fx", False),
        provider_positions=kind == "provider", provider_orders=provider_orders)
    resource = next((r for r in resources if (r["kind"], r["id"]) == target), None)
    if not resource or resource["access"]["movements"] == "none":
        fail("operational_workspace_not_found", "الكشف غير متاح", 404)
    if not provider_orders:
        await fill_historical_headers(db, owner, resources, computed, state, policy)
    first, last, q = filters.get("date_from"), filters.get("date_to"), filters.get("q", "")
    if provider_orders:
        from operational_workspace_provider_orders import filtered_orders
        rows, undated = filtered_orders(computed["details"]["workspace_provider_orders"], q, first, last)
        groups = computed["details"]["provider_balances"]
        amounts = [{"currency": currency, **{k: item.get(k) for k in ("opening", "settled", "gross", "known_gross",
            "fees", "known_fees", "refunded", "net", "known_net", "complete",
            "uncomputed_principal", "uncomputed_principal_complete", "uncomputed_orders")}}
            for currency, accounts in groups.items() for item in accounts if item["provider"] == identity]
        return {"header": {"resource": resource, "provider_orders": True, "amounts": amounts, "undated": undated}, "rows": rows}
    rows = await prepare_in_thread(ledger_items, state, computed, resource, policy)
    if family == "operational_statement":
        result = await prepare_in_thread(period_statement, resource, rows, date_from=first, date_to=last,
            actor=_actor_snapshot(actor), generated_at=now(), purpose=filters.get("purpose", "view"),
            started_at=state.get("started_at"), already_projected=True)
        # Store the complete period as bounded immutable rows. The explicit
        # statement reader reassembles them; summary balances remain period-wide.
        return {"header": {key: value for key, value in result.items() if key != "items"}, "rows": result["items"]}
    if family == "operational_history":
        if kind not in {"employee", "store_driver", "courier"}:
            fail("operational_workspace_not_found", "الكشف غير متاح", 404)
        # History grouping is independent of client page size. Persist the
        # complete canonical grouped history and page the immutable rows.
        result = await prepare_in_thread(automatic_history, resource, rows, page=1,
                                        page_size=max(1, len(rows)), generated_at=now())
        items = result.pop("items")
        for field in ("page", "page_size", "total_pages", "total"):
            result.pop(field, None)
        return {"header": result, "rows": items}
    summary = await prepare_in_thread(_pos_summary, state, resource)
    balance_status = projected_balance_status(resource, rows)
    if first and resource["access"].get("report"):
        period = await prepare_in_thread(period_statement, resource, rows, date_from=first, date_to=last,
            actor=_actor_snapshot(actor), generated_at=now(), purpose="view", started_at=state.get("started_at"),
            already_projected=True)
        entries = period.pop("items")
        summary["period"] = {key: period[key] for key in ("date_from", "date_to", "currency", "scope",
            "can_print", "opening_balance", "closing_balance", "totals")}
        summary["period"]["total"] = len(entries)
        display = list(filter_activity(reversed(entries), q=q))
    elif q or first:
        display = list(filter_activity(rows, q=q, date_from=first, date_to=last))
    else:
        display = compact_rows(rows)
    return {"header": {"resource": resource, **summary, "balance_status": balance_status,
        "balance_note": balance_note(balance_status), "courier_summary": courier_summary(state, computed, resource),
        "expected_collections": expected_collections(state, computed, resource, policy)}, "rows": display}


async def _register(db, owner, policy, state, target, filters):
    from operational_balance_workspace import resource_catalog, _index_ledger_sources, ledger_items, _courier_history
    from operational_workspace_projection import compact_rows, row_order
    from operational_workspace_ledger_filters import filter_activity
    from operational_workspace_supplier_invoices import fill_historical_headers
    resources, computed = await resource_catalog(db, owner, policy, state, selected_resource=target,
        **({"selected_kinds": {target[0]}, "identities_by_kind": {target[0]: [target[1]]}} if target else {}))
    if target and not any(r["access"]["movements"] != "none" for r in resources):
        fail("operational_workspace_not_found", "الكشف غير متاح", 404)
    await fill_historical_headers(db, owner, resources, computed, state, policy)
    indexed = await prepare_in_thread(_index_ledger_sources, state, computed, resources)
    unique = {}
    for resource in checked_rows(resources):
        if resource["access"]["movements"] == "none":
            continue
        sources = indexed[(resource["kind"], resource["id"], resource["currency"])]
        rows = await prepare_in_thread(ledger_items, state, computed, resource, policy, sources=sources)
        if any(filters.get(k) for k in ("q", "date_from", "direction", "status")):
            display = filter_activity(rows, **{k: filters.get(k) for k in ("q", "date_from", "date_to", "direction", "status")})
        else:
            display = compact_rows(rows)
        # Preserve first-authorized-resource precedence and search BEFORE
        # de-duplication, exactly as the canonical register route.
        for row in checked_rows(display):
            unique.setdefault(row["event_id"], row)
    items = sorted(unique.values(), key=row_order, reverse=True)
    if filters.get("courier_history"):
        history = {}
        for resource in resources:
            if (resource["kind"] == "courier" and resource["access"]["balance"] and resource["access"]["movements"] == "all"
                    and any(row["party_type"] == "courier" and row["party_id"] == resource["id"] and not row.get("summary") for row in items)):
                history[(resource["id"], resource["currency"])] = await prepare_in_thread(_courier_history, state, resource, policy)
        for row in items:
            if row["party_type"] == "courier" and not row.get("summary"):
                row["balance_after"] = history.get((row["party_id"], row["currency"]), {}).get(row["id"])
    return {"header": {}, "rows": items}


async def _position(db, actor, owner, policy, state):
    from operational_balance_workspace import resource_catalog
    from operational_financial_position import build_financial_position
    from operational_balance_service import accounting_inactive, fmt
    from fastapi import HTTPException
    resources, _ = await resource_catalog(db, owner, policy, state, ad_bank_fx=True, provider_positions=True)
    order = {kind: i for i, kind in enumerate(ASSET_KINDS + PARTY_KINDS)}
    resources = sorted((r for r in resources if r["access"]["balance"]), key=lambda r: order[r["kind"]])
    report = None
    if policy.owner_override and state["status"] == "active":
        try:
            await accounting_inactive(db, owner)
        except HTTPException as exc:
            if not isinstance(exc.detail, dict) or exc.detail.get("code") != "operational_accounting_active":
                raise
            state = {**state, "status": "accounting_active"}
        report = await prepare_in_thread(build_financial_position, state, view="summary", generated_at=now())
        report.update(session_scope=digest([owner, actor["id"]]), permissions={"reports": True,
            "confirm_delivery_after_start": state["status"] == "active"})
    currencies = set((report or {}).get("currencies", {})) | {r["currency"] for r in resources}
    summary = {"available_cash": {}, "providers": {}}
    providers = [r for r in resources if r["kind"] == "provider"]
    for currency in currencies:
        values = [_signed(r) for r in resources if r["kind"] in {"bank", "cash"}
                  and not r.get("prepaid_shipping_cash") and r["currency"] == currency]
        summary["available_cash"][currency] = fmt(sum(values, Decimal(0))) if all(v is not None for v in values) else None
        figures = (report or {}).get("currencies", {}).get(currency, {})
        if currency == "SAR" and providers and all(r.get("provider_position") for r in providers):
            positions = [r["provider_position"] for r in providers]
            values = [p.get("net") if p.get("complete") else p.get("known_net") for p in positions]
            known = [max(Decimal(v), Decimal(0)) for v in values if v is not None]
            value = fmt(sum(known, Decimal(0))) if known else None
            partial = any(not p.get("complete") for p in positions)
        else:
            value = next((r.get("receivable") for r in figures.get("debt_rows", ()) if r.get("id") == "providers"), None)
            partial = figures.get("completeness", {}).get("actual") is False
        summary["providers"][currency] = {"amount": value, "partial": partial}
    return {"header": {"report": report, "summary": summary}, "rows": resources}


async def build_projection(db, spec, checkpoint):
    from operational_financial_position_state import read_financial_position_state
    with spec_request(spec) as principal:
        actor, owner, policy = await load_context(db, principal, expected_owner=spec["owner_id"])
        if permission_fingerprint(actor, policy) != spec["permission_fingerprint"]:
            fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
        family, filters = spec["family"], spec.get("filters", {})
        target = tuple(spec["resource"]) if spec.get("resource") else None
        await checkpoint()
        if family == "operational_report":
            if not policy.owner_override:
                fail("operational_workspace_route_required", "استخدم صفحة الأرصدة المخصصة لصلاحياتك", 403)
            from operational_financial_report_read import read_financial_position
            from operational_balance_routes import scope
            result = await read_financial_position(db, principal, scope=scope, **filters)
            await checkpoint()
            result.update(permission_version=policy.version, owner_override=policy.owner_override)
            return {"header": result, "rows": None}
        metadata_only = (family == "operational_options" and filters.get("role") != "source"
                         or family == "operational_selection" and not policy.resource(*target)["balance"])
        state = await directory_input(db, owner) if metadata_only else await read_financial_position_state(db, owner)
        if source_revision(state) != spec["source_revision"]:
            fail("operational_read_source_changed", "تغيرت البيانات؛ أعد تحميل الصفحة", 409)
        await checkpoint()
        if family == "operational_home":
            result = await _home(db, owner, policy, state, checkpoint)
        elif family == "operational_directory":
            result = await _directory(db, owner, policy, state, filters)
        elif family == "operational_options":
            result = await _options(db, owner, policy, state, filters)
        elif family in {"operational_selection", "operational_resource"}:
            result = await _selection(db, owner, policy, state, target, balance_only=family == "operational_resource",
                ad_bank_fx=True if family == "operational_selection" else filters.get("ad_bank_fx", False))
        elif family in {"operational_ledger", "operational_statement", "operational_history"}:
            result = await _ledger(db, actor, owner, policy, state, target, filters, family)
        elif family == "operational_register":
            result = await _register(db, owner, policy, state, target, filters)
        elif family == "operational_position":
            result = await _position(db, actor, owner, policy, state)
        else:
            raise ValueError("demand_read_family_invalid")
        await checkpoint()
        result["header"].update(session_scope=digest([owner, actor["id"]]), permission_version=policy.version,
                                owner_override=policy.owner_override)
        return result
