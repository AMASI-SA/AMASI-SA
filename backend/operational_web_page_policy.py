"""Browser-page boundaries layered on the existing per-account financial scopes."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import re

from fastapi import HTTPException

BASE = "/operational-balances"
API = "/api" + BASE
ASSETS = frozenset({"bank", "cash", "provider", "pos_receivable", "ad_account"})
PROVIDERS = frozenset({"tabby", "tamara", "salla"})
WRITE_ENTRY = "تسجيل الحركات الممنوحة على الحسابات والجهات المحددة"


def _page(key, label, url=None, *, write=None, owner=False):
    return {"id": "balances." + key, "label": label, "section": "balances", "section_label": "الأرصدة التشغيلية",
            "to": url or f"{BASE}?view={key}", **({"owner_only": True} if owner else {}),
            **({"write_label": write} if write else {})}


OPERATIONAL_WEB_PAGES = (
    _page("home", "رئيسية الأرصدة التشغيلية", BASE),
    _page("assets", "الأصول والحسابات"), _page("parties", "الأشخاص والجهات"),
    _page("log", "سجل الحركات المالية", BASE + "/daily-movements"),
    _page("entry", "حركة مالية جديدة", write=WRITE_ENTRY),
    _page("refund", "رد مبلغ لعميل برقم الطلب", write="تسجيل ومتابعة رد المبلغ حسب صلاحيات الحساب"),
    _page("review", "الطلبات التي تحتاج مراجعة", BASE + "?view=reviews"),
    _page("collections", "مراجعة تحصيلات الموصلين", write="اعتماد أو رفض الإيصالات المخصصة"),
    _page("position", "المركز المالي للحسابات المخصصة"),
    _page("tools", "العمليات والكشوف"),
    _page("statements", "كشوف منصات الدفع ومطابقتها", BASE + "/provider-statements",
          write="رفع كشف المنصة المخصصة واعتماد مطابقته"),
    _page("fees", "إعداد عمولات المنصات", BASE + "/provider-fees"),
    _page("reports", "المركز المالي الشامل والتقارير التفصيلية", BASE + "/financial-position", owner=True),
    _page("returns", "مرتجعات الأصناف", BASE + "/customer-returns"),
    _page("exchanges", "استبدال الأصناف", BASE + "/customer-exchanges"),
    _page("specialized", "الأرصدة الافتتاحية والإعدادات المتخصصة", owner=True),
    _page("access", "تحديد حسابات وعمليات الموظفين", BASE + "/access", owner=True),
)
WORKSPACE = frozenset(p["id"] for p in OPERATIONAL_WEB_PAGES if not p.get("owner_only") and p["id"] != "balances.statements")


def _deny():
    raise HTTPException(403, detail={"code": "web_page_permission_required"})


def _allow(modes, *keys, write=False):
    if not any(modes.get("balances." + key) in (("write",) if write else ("read", "write")) for key in keys):
        _deny()


def enforce_operational_web_pages(modes, path, method, query=None):
    if modes is None or not path.startswith(API + "/"):
        return
    path = path[len(API):].rstrip("/")
    query = query or {}
    if path == "/inventory" or path.startswith(("/inventory-", "/standalone-inventory/")):
        # The product workspace catalogue applies its own exact route and method checks.
        return
    if path == "/workspace/context" and method == "GET":
        if not modes.keys() & (WORKSPACE - {"balances.returns", "balances.exchanges", "balances.fees"}):
            _deny()
        if str(query.get("home_preview", "")).lower() in {"true", "1", "on", "yes", "y", "t"}:
            _allow(modes, "home")
        return
    if path == "/workspace/parties" and method == "GET":
        kind = query.get("kind")
        return _allow(modes, "assets" if kind in ASSETS else "parties") if kind else _allow(modes, "assets", "parties")
    if path == "/workspace/movements" and method == "GET":
        return _allow(modes, "log")
    match = re.fullmatch(r"/workspace/specialist/(returns|exchanges|fees)", path)
    if match and method == "GET":
        return _allow(modes, match[1])
    if path == "/workspace/order-issues" and method == "GET":
        return _allow(modes, "review")
    match = re.fullmatch(r"/workspace/(?:ledger|statements|history)/([^/]+)/[^/]+", path)
    if match and method == "GET":
        return _allow(modes, "assets" if match[1] in ASSETS else "parties")
    if path in {"/workspace/entries", "/workspace/ad-funding-quote"} and method == "POST":
        return _allow(modes, "entry", write=True)
    if re.fullmatch(r"/workspace/pending-commands/[^/]+", path) and method in {"GET", "POST", "DELETE"}:
        # Exact action is checked against the actor-bound stored command inside the handler.
        return _allow(modes, "entry", "refund", write=True)
    if path == "/workspace/refunds" or path.startswith("/workspace/refunds/"):
        return _allow(modes, "refund", write=method != "GET")
    if path == "/workspace/reviews" or path.startswith("/workspace/reviews/"):
        return _allow(modes, "collections", write=method != "GET")
    if path == "/provider-statements" or path.startswith("/provider-statements/"):
        return _allow(modes, "statements", write=method != "GET")
    _deny()


@dataclass
class BrowserRequest:
    path: str
    method: str
    query: dict
    provider: str | None = None
    web_revision: tuple | None = None
    managed: bool = False


_CURRENT = ContextVar("operational_web_page_request", default=None)


@contextmanager
def bind_page_request(user, request):
    web = user.get("_session_client") != "amasi_mobile" and not user.get("_mobile_actor_id")
    token = _CURRENT.set(BrowserRequest(request.url.path, request.method, dict(request.query_params)) if web else None)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def request_modes(actor):
    request = _CURRENT.get()
    if request is None:
        return None
    if "web_page_access" not in actor and not request.managed:
        return None
    from web_page_access import configured_page_modes
    modes = configured_page_modes(actor)
    request.managed = modes is not None
    grant = actor.get("web_page_access")
    revision = (actor.get("id"), actor.get("created_by"), isinstance(grant, dict),
                grant.get("revision") if isinstance(grant, dict) else None)
    if request.web_revision is None:
        request.web_revision = revision
    elif request.web_revision != revision:
        raise HTTPException(409, detail={"code": "operational_access_changed"})
    enforce_operational_web_pages(modes, request.path, request.method, request.query)
    return modes


def limit_workspace_policy(policy):
    """Apply the union of granted page capabilities to the already-scoped account policy."""
    from dataclasses import replace
    request = _CURRENT.get()
    if request is None or not request.path.startswith(API + "/workspace/") or policy.owner_override:
        return policy
    modes = request_modes(policy.actor)
    if modes is None:
        return policy
    pages = {p.split(".", 1)[1] for p in modes if p in WORKSPACE}
    if pages & {"home", "log", "position", "entry"}:
        kinds = None
    else:
        from operational_balance_access import RESOURCE_KINDS
        kinds = set()
        if "assets" in pages:
            kinds.update(ASSETS)
        if "parties" in pages:
            kinds.update(RESOURCE_KINDS - ASSETS)
        if "returns" in pages:
            kinds.update(("bank", "provider", "courier", "store_driver"))
        if "exchanges" in pages:
            kinds.update(("bank", "supplier", "courier", "store_driver"))
        if "fees" in pages:
            kinds.add("provider")
        if "refund" in pages:
            kinds.update(("bank", "provider"))
        if "collections" in pages:
            kinds.update(("bank", "store_driver", "pos_receivable"))
        if "review" in pages:
            kinds.update(("provider", "courier", "store_driver"))
    if request.path.endswith("/workspace/parties"):
        from operational_balance_access import RESOURCE_KINDS
        kinds = (ASSETS if "assets" in pages else frozenset()) | (RESOURCE_KINDS - ASSETS if "parties" in pages else frozenset())
    resources = [r for r in policy.resources if kinds is None or r["kind"] in kinds]
    if request.path.rstrip("/").endswith("/workspace/context"):
        # Entry pickers and case review need account names, not the directory balances.
        whole = bool(pages & {"home", "position", "log"})
        resources = [row if whole or ("assets" if row["kind"] in ASSETS else "parties") in pages else
                     {**row, "balance": False, "report": False, "print": False} for row in resources]
    # Page grants for view/entry are further intersected with native operation permissions.
    operations = tuple(op for op in policy.operations if (
        (op.startswith("customer.refund") and "refund" in pages) or
        (op == "courier.review" and "collections" in pages) or
        (not op.startswith(("customer.refund", "provider.statement_")) and op != "courier.review" and modes.get("balances.entry") == "write")))
    if resources == policy.resources and operations == policy.operations:
        return policy
    return replace(policy, resources=resources, operations=operations)


def enforce_pending_action(actor, action):
    modes = request_modes(actor)
    if modes is not None:
        _allow(modes, "entry" if action == "entry" else "refund", write=True)


def bind_statement_provider(provider):
    request = _CURRENT.get()
    if request is not None and request.managed:
        if provider not in PROVIDERS or (request.provider is not None and request.provider != provider):
            _deny()
        request.provider = provider


async def statement_access(db, owner, actor):
    """Fresh identity, exact provider reports, page mode and financial operation on each check."""
    from operational_balance_access import load_access
    from operational_balance_store import fail
    policy = await load_access(db, {"id": actor}, owner)
    if policy.owner_override:
        return PROVIDERS, PROVIDERS, PROVIDERS
    request = _CURRENT.get()
    modes = request_modes(policy.actor)
    if not modes or not request or not request.path.startswith(API + "/provider-statements"):
        fail("provider_statement_owner_required", "لا توجد صلاحية لكشوف المنصات", 403)
    _allow(modes, "statements", write=request.method != "GET")
    policy.require_enabled()
    readable, uploadable, confirmable = set(), set(), set()
    for provider in PROVIDERS:
        resource = policy.resource("provider", provider)
        if resource["movements"] == "all" and resource["report"] and resource["balance"]:
            readable.add(provider)
            if resource["use"] and modes.get("balances.statements") == "write":
                if policy.can_operation("provider.statement_upload"):
                    uploadable.add(provider)
                if policy.can_operation("provider.statement_confirm"):
                    confirmable.add(provider)
    permitted = readable
    if request.method != "GET":
        permitted = confirmable if request.path.rstrip("/").endswith("/confirm") else uploadable
    if not permitted or (request.provider is not None and request.provider not in permitted):
        _deny()
    return frozenset(readable), frozenset(uploadable), frozenset(confirmable)
