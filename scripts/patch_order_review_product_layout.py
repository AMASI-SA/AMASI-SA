"""One-shot responsive layout patch for order-review product cards."""
from pathlib import Path


ORDER_REVIEW_PATH = Path("frontend/src/pages/OrderReview.jsx")
CONTRACT_PATH = Path("backend/tests/test_fulfillment_v2_contract.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


source = ORDER_REVIEW_PATH.read_text(encoding="utf-8")
source = replace_once(
    source,
    '<article className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">',
    '<article data-testid="order-review-product-card" className="min-w-0 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">',
    "product card article",
)
source = replace_once(
    source,
    '<div className="grid gap-4 p-4 sm:grid-cols-[150px_minmax(0,1fr)]">',
    '<div className="grid grid-cols-[96px_minmax(0,1fr)] gap-3 p-4 sm:grid-cols-[128px_minmax(0,1fr)]">',
    "product card header grid",
)
source = replace_once(
    source,
    '<h3 className="text-lg font-extrabold text-slate-900">{item.name}</h3>',
    '<h3 className="break-words text-base font-extrabold leading-7 text-slate-900 sm:text-lg">{item.name}</h3>',
    "product title",
)
source = replace_once(
    source,
    'className={`h-20 w-20 shrink-0 overflow-hidden rounded-xl border-2 ${selected ? "border-teal-500 ring-2 ring-teal-100" : "border-slate-200 hover:border-violet-400"}`}',
    'className={`h-16 w-16 shrink-0 overflow-hidden rounded-xl border-2 sm:h-20 sm:w-20 ${selected ? "border-teal-500 ring-2 ring-teal-100" : "border-slate-200 hover:border-violet-400"}`}',
    "gallery thumbnail",
)
source = replace_once(
    source,
    '<div className="grid gap-2 sm:grid-cols-2">\n                        {specs.map((spec) => (',
    '<div data-testid="order-review-product-specs" className="grid gap-2">\n                        {specs.map((spec) => (',
    "product specs grid",
)
source = replace_once(
    source,
    '<div key={spec.key} className="flex min-w-0 items-start gap-2 rounded-xl bg-violet-50 px-3 py-2 text-sm">',
    '<div key={spec.key} className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-start gap-2 rounded-xl bg-violet-50 px-3 py-2 text-sm">',
    "product spec row",
)
source = replace_once(
    source,
    '<span className="min-w-0 break-words font-extrabold text-slate-900">{spec.value}</span>',
    '<span className="min-w-0 whitespace-pre-wrap break-words font-extrabold leading-6 text-slate-900">{spec.value}</span>',
    "product spec value",
)
source = replace_once(
    source,
    '<section className="h-full w-full overflow-y-auto bg-slate-50 shadow-2xl md:max-w-5xl">',
    '<section className="h-full w-full overflow-y-auto bg-slate-50 shadow-2xl md:max-w-7xl">',
    "review drawer width",
)
source = replace_once(
    source,
    '<div className="grid gap-4 xl:grid-cols-3">',
    '<div data-testid="order-review-products-grid" className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">',
    "products grid",
)
ORDER_REVIEW_PATH.write_text(source, encoding="utf-8")

contract = CONTRACT_PATH.read_text(encoding="utf-8")
test_name = "test_order_review_product_cards_keep_titles_and_specs_readable"
if test_name not in contract:
    contract += r'''


def test_order_review_product_cards_keep_titles_and_specs_readable():
    source = (ROOT / "frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")

    assert 'data-testid="order-review-product-card"' in source
    assert 'grid-cols-[96px_minmax(0,1fr)]' in source
    assert 'sm:grid-cols-[128px_minmax(0,1fr)]' in source
    assert 'data-testid="order-review-product-specs" className="grid gap-2"' in source
    assert 'data-testid="order-review-products-grid" className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3"' in source
    assert 'md:max-w-7xl' in source
    assert 'sm:grid-cols-[150px_minmax(0,1fr)]' not in source
    assert 'grid gap-4 xl:grid-cols-3' not in source
'''
    CONTRACT_PATH.write_text(contract, encoding="utf-8")

print("Order review product layout patch applied.")
