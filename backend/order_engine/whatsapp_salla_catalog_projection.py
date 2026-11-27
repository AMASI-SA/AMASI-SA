"""Minimal public-product evidence from a trusted, READ-ONLY Salla response.

Mezan internal source projection shared with Amasi chatbot; read-only. A separately approved fixed-host Mezan/Salla adapter must
authenticate the source. This module NEVER performs HTTP, sends WhatsApp,
creates Salla orders, or proves final checkout stock or pricing. Use the
authenticated checkout quote for the actual amount before any order action.
No user/contact/order fields are accepted or exported.
"""
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

MAX_OPTIONS = 32
MAX_VALUES = 80
MAX_IMAGES = 12
ID = re.compile(r"[1-9][0-9]{0,15}\Z", re.ASCII)
TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,200}\Z")
ALLOW_IMAGES = frozenset(("cdn.salla.sa",))
ALLOW_PRODUCT = frozenset(("amasi-sa.com", "www.amasi-sa.com"))
CURRENCIES = frozenset(("SAR",))
VISIBILITY = frozenset(("always", "on_condition"))
KINDS = frozenset(("text", "textarea", "radio", "checkbox", "select", "date", "number", "file", "image"))
PURPOSE = frozenset(("form", "variants"))


class InvalidProduct(ValueError):
    """Invalid source structure; never print untrusted raw values."""


def fail(field):
    raise InvalidProduct("untrusted_product_" + field)


def ident(value):
    if type(value) not in (str, int) or not ID.fullmatch(str(value)):
        fail("identity")
    return str(value)


def label(value, max_len=200):
    if type(value) is not str or not value.strip() or len(value) > max_len:
        fail("label")
    if not TEXT.fullmatch(value.strip()):
        fail("label")
    return value.strip()


def amount(value, currency="SAR"):
    """Keep exact decimal SAR. Reject NaN, booleans, negatives and other FX."""
    if type(value) is dict:
        if value.get("currency","SAR") not in CURRENCIES:
            fail("currency")
        value=value.get("amount")
    elif currency not in CURRENCIES:
        fail("currency")
    if type(value) not in (int, float, str, Decimal):
        fail("amount")
    try:
        d=Decimal(str(value))
    except (InvalidOperation,ValueError):
        fail("amount")
    if not d.is_finite() or d<0 or d>10000000 or d!=d.quantize(Decimal(".01")):
        fail("amount")
    return f"{d:.2f}"


def safe_url(value,hosts):
    if type(value) is not str or len(value)>2048:
        fail("url")
    try:
        parsed=urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in hosts or
                parsed.port not in (None,443) or parsed.username is not None or
                parsed.password is not None or not parsed.path.startswith("/") or
                parsed.query or parsed.fragment or "\\" in value):
            fail("url")
    except ValueError:
        fail("url")
    return value


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]
        self.ignored=0

    def handle_starttag(self,tag,attrs):
        if tag in ("script","style"):
            self.ignored+=1
        elif not self.ignored and tag in ("p","br","li"):
            self.parts.append(" ")

    def handle_endtag(self,tag):
        if tag in ("script","style") and self.ignored:
            self.ignored-=1

    def handle_data(self,data):
        if not self.ignored:
            self.parts.append(data)


def public_description(text):
    if text is None:
        return ""
    if type(text) is not str or len(text)>30000:
        fail("description")
    parser=_PlainText()
    try:
        parser.feed(text)
    except (ValueError,TypeError):
        fail("description")
    return " ".join(" ".join(parser.parts).split())[:1600]


def _option(row):
    if type(row) is not dict:
        fail("option")
    if row.get("type") not in KINDS or row.get("purpose") not in PURPOSE:
        fail("option_kind")
    required=row.get("required")
    if type(required) is not bool:
        fail("option_required")
    visibility=row.get("visibility","always")
    if visibility not in VISIBILITY:
        fail("visibility")
    values=row.get("values",[])
    if type(values) is not list or len(values)>MAX_VALUES:
        fail("option_values")
    output_values=[]
    for value in values:
        if type(value) is not dict:
            fail("option_value")
        output_values.append({
            "id":ident(value.get("id")),
            "name":label(value.get("name")),
            "price_delta_sar":amount(value.get("price",{"amount":0,"currency":"SAR"})),
            "out_of_stock":bool(value.get("is_out_of_stock",False))
                if type(value.get("is_out_of_stock",False)) is bool else fail("option_stock"),
            "image_url":safe_url(value["image_url"],ALLOW_IMAGES)
                if value.get("image_url") else None
        })
    condition=None
    if visibility=="on_condition":
        condition={"option_id":ident(row.get("visibility_condition_option")),
                   "value_id":ident(row.get("visibility_condition_value"))}
    return {"id":ident(row.get("id")), "name":label(row.get("name")),
            "type":row["type"],"purpose":row["purpose"],"required":required,
            "visibility":visibility,"condition":condition,"values":output_values}


def project(raw,variants=(),*,fetched_at:int):
    """Read-only projection; all available options preserved incl. dependencies.

    The *caller* supplies a timestamp from its trusted authenticated Salla
    read; no local source authentication is performed by this module.
    """
    if type(raw) is not dict or type(fetched_at) is not int or fetched_at<=0:
        fail("source")
    product_id=ident(raw.get("id"))
    price=amount(raw.get("price"))
    current_sale=raw.get("sale_price")
    if current_sale is not None:
        amt=amount(current_sale)
        if Decimal(amt)>0 and Decimal(amt)>Decimal(price):
            fail("sale_price")
    status=raw.get("status")
    if status not in ("sale","out","hidden"):
        fail("status")
    qty=raw.get("quantity")
    unlimited=raw.get("unlimited_quantity")
    if type(unlimited) is not bool or (qty is not None and (type(qty) is not int or qty<0)):
        fail("quantity")
    inventory=("unlimited" if unlimited else
               "unknown" if qty is None else "available" if qty>0 else "out")
    images=raw.get("images",[])
    if type(images) is not list or len(images)>MAX_IMAGES:
        fail("images")
    photo_urls=[]
    for im in images:
        if type(im) is not dict:
            fail("image")
        url=im.get("url") or im.get("original")
        if url:
            safe=safe_url(url,ALLOW_IMAGES)
            if safe not in photo_urls:
                photo_urls.append(safe)
    first=raw.get("main_image")
    if first:
        safe=safe_url(first,ALLOW_IMAGES)
        if safe not in photo_urls:
            photo_urls.insert(0,safe)
    raw_options=raw.get("options",[])
    if type(raw_options) is not list or len(raw_options)>MAX_OPTIONS:
        fail("options")
    opts=[_option(item) for item in raw_options]
    ids={opt["id"] for opt in opts}
    if len(ids)!=len(opts):
        fail("duplicate_option")
    for opt in opts:
        if opt["condition"] and opt["condition"]["option_id"] not in ids:
            fail("broken_option_dependency")
    if type(variants) not in (list,tuple) or len(variants)>MAX_VALUES:
        fail("variants")
    safe_variants=[]
    for v in variants:
        if type(v) is not dict:
            fail("variant")
        if "product_id" in v and str(v["product_id"])!=product_id:
            fail("wrong_parent_variant")
        q=v.get("quantity")
        if q is not None and (type(q) is not int or q<0):
            fail("variant_stock")
        safe_variants.append({"id":ident(v.get("id")),
                              "sku":label(v["sku"]) if v.get("sku") else None,
                              "price_sar":amount(v.get("price")),
                              "quantity":q})
    return {
        "source":"salla_authenticated_read_required",
        "source_fetched_at":fetched_at,
        "product_id":product_id,
        "name":label(raw.get("name")),
        "url":safe_url(raw.get("url"),ALLOW_PRODUCT),
        "sku":label(raw["sku"]) if raw.get("sku") else None,
        "list_status":status,
        "base_price_sar":price,
        "currency":"SAR",
        "inventory_hint":inventory,
        "final_quote_required":True,
        "description_public":public_description(raw.get("description")),
        "image_urls":photo_urls,
        "options":opts,
        "variants":safe_variants
    }
