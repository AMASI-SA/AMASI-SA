"""Salla → "تجهيز المنتجات" PDF pipeline (Iteration 34).

Two responsibilities:

1. **Parse a Salla orders PDF** into structured per-order data:
     - order number, date, total_products
     - product list with: product name, customer name (from "الاسم"
       product option), card-message / note (when present)
     - the embedded product image bytes for each line item
   The parser is conservative — it handles Salla's RTL glyph-encoded PDFs
   where some Arabic ligatures (lam-alef) drop on text extraction. The
   resulting structure is what the frontend renders for preview and what
   the generator uses for output.

2. **Build the printable prep PDF**: A4 portrait, 4×4 = 16 cards per page,
   per-card layout matches the reference template (serial, image, QR with
   order#, ط:, الاسم, ملاحظة, تاريخ, الكمية, "{carrier} - {N}").

The module is import-safe (no FastAPI/Mongo deps); routes wire it up.
"""
from __future__ import annotations

import io
import re
import logging
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from typing import Optional

import fitz  # PyMuPDF
import qrcode
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    _HAS_AR = True
except Exception:  # pragma: no cover
    _HAS_AR = False

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────
# Salla's PDF embeds Arabic with isolated/presentation glyphs that lose
# the lam-alef ligature on extraction (e.g. الاسم → السم, الخبر → الخرب).
# When matching option *keys* we accept both spellings. Values like names
# usually don't contain lam-alef so they extract cleanly.
KEY_NAME_VARIANTS: tuple[str, ...] = ("الاسم", "السم", "الإسم", "الأسم")
NOTE_KEY_PREFIXES: tuple[str, ...] = (
    "ملاحظ",                # ملاحظة / ملاحظات / ملاحظه
    "الكتابه عىل الكرت", "الكتابة على الكرت",
    "العباره عىل الكرت", "العبارة على الكرت",
    "رسالة الكرت", "رساله الكرت",
)
FOOTER_MARKERS: tuple[str, ...] = ("شكر", "نتمن", "نتمنى")
ORDER_NUM_RE = re.compile(r"رقم\s*الطلب[\s\S]{0,40}#\s*(\d+)")
DATE_RE = re.compile(r"(\w+\s+\d{1,2}\s+\w+\s+\d{4})")
PHONE_RE = re.compile(r"^\+?\d{6,}")


@dataclass
class ProductLine:
    order_number: str
    order_date: Optional[str]
    product_name: Optional[str]
    customer_name: Optional[str]
    note: Optional[str]
    quantity: int
    total_products_in_order: int   # used for "iMile - N"
    image_bytes: Optional[bytes] = field(default=None, repr=False)
    image_mime: Optional[str] = None
    shipping_company: Optional[str] = None  # populated from unified_orders later

    def to_dict_preview(self) -> dict:
        d = asdict(self)
        d.pop("image_bytes", None)
        d["has_image"] = bool(self.image_bytes)
        return d


# ── PDF parsing ──────────────────────────────────────────────────────────
def _ar(text: Optional[str]) -> str:
    """Reshape Arabic for proper RTL rendering in reportlab."""
    if not text:
        return ""
    if not _HAS_AR:
        return str(text)
    try:
        return get_display(arabic_reshaper.reshape(str(text)))
    except Exception:
        return str(text)


def _extract_page_product_images(doc: fitz.Document, page: fitz.Page) -> list[tuple[bytes, str]]:
    """Return product-image bytes per page (heuristic filter).

    Salla embeds: tiny header logos (≈ 200×40), the printable product image
    (~200..1200 px each side, aspect 0.5..2.0), and a wide cover background
    (4222×2235 in the sample). We keep only the printable mid-size ones.
    """
    out: list[tuple[bytes, str]] = []
    seen_xrefs: set[int] = set()
    for img in page.get_images(full=True):
        xref = img[0]
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        try:
            info = doc.extract_image(xref)
        except Exception:
            continue
        w, h = info.get("width") or 0, info.get("height") or 0
        if not w or not h:
            continue
        ratio = w / h
        # Reject obvious non-product imagery
        if w < 150 or h < 150:                  # logos / icons
            continue
        if w * h > 1_500_000:                   # 4222×2235 cover
            continue
        if not (0.4 <= ratio <= 2.5):           # banners / strips
            continue
        out.append((info["image"], info.get("ext", "png")))
    return out


def _parse_options_block(block_lines: list[str]) -> dict[str, str]:
    """Walk key/value pairs in a خيارات المنتج block.

    Stops when we encounter a bare-digit line (which is the quantity
    indicator of the *next* product) so each block stays scoped to one
    product.
    """
    opts: dict[str, str] = {}
    k = 0
    while k < len(block_lines):
        key = block_lines[k]
        if re.match(r"^\d+$", key) and k > 0:
            break
        val = block_lines[k + 1] if k + 1 < len(block_lines) else None
        if val is None:
            break
        opts[key] = val
        k += 2
    return opts


def _collect_bottom_product_names(lines: list[str], expected_count: int) -> list[str]:
    """Find product names that appear right before the footer marker."""
    foot_idx = next(
        (i for i, ln in enumerate(lines) if any(m in ln for m in FOOTER_MARKERS)),
        len(lines),
    )
    bottom: list[str] = []
    k = foot_idx - 1
    # Walk upward. Stop on phone-only / known boilerplate / address tokens.
    while k >= 0 and len(bottom) < expected_count + 4:
        ln = lines[k]
        if PHONE_RE.match(ln) and not re.search(r"[\u0600-\u06FF]", ln):
            break
        if ln in ("السعودية", "amasi-sa.com", "متجر أمايس"):
            break
        if len(ln) < 3:
            break
        bottom.insert(0, ln)
        k -= 1
    # Trim or merge so length == expected_count
    if expected_count <= 0:
        # Fallback: when no options block was found, keep ALL collected
        # names as products (one product per name).
        return bottom
    while len(bottom) > expected_count and len(bottom) >= 2:
        bottom[-2] = bottom[-2] + " " + bottom[-1]
        bottom.pop()
    return bottom


def _pick_name_from_options(opts: dict[str, str]) -> Optional[str]:
    for variant in KEY_NAME_VARIANTS:
        if variant in opts:
            v = (opts.get(variant) or "").strip()
            return v or None
    return None


def _pick_note_from_options(opts: dict[str, str]) -> Optional[str]:
    for k, v in opts.items():
        for pref in NOTE_KEY_PREFIXES:
            if k.startswith(pref) or pref in k:
                return (v or "").strip() or None
    return None


def parse_salla_orders_pdf(pdf_bytes: bytes) -> list[ProductLine]:
    """Parse a Salla-exported orders PDF into a flat list of product lines.

    Each Salla page = one order, but each order can have multiple products,
    so one page can produce 1..N lines. Returns a flat list ready for
    grouping/sorting by the caller.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    lines_out: list[ProductLine] = []
    try:
        for page in doc:
            text = page.get_text() or ""
            text_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if not text_lines:
                continue

            m = ORDER_NUM_RE.search(text)
            if not m:
                continue
            order_num = m.group(1)
            m_date = DATE_RE.search(text)
            date_str = m_date.group(1) if m_date else None

            opt_idx = [i for i, ln in enumerate(text_lines) if ln == "خيارات المنتج"]
            option_blocks: list[dict[str, str]] = []
            for j, start in enumerate(opt_idx):
                end = opt_idx[j + 1] if j + 1 < len(opt_idx) else len(text_lines)
                option_blocks.append(_parse_options_block(text_lines[start + 1:end]))

            bottom_names = _collect_bottom_product_names(text_lines, len(option_blocks))
            total_products = max(len(option_blocks), len(bottom_names))
            if total_products == 0:
                continue

            # Extract embedded product images (heuristic — sized middle-band).
            product_imgs = _extract_page_product_images(doc, page)

            for i in range(total_products):
                opts = option_blocks[i] if i < len(option_blocks) else {}
                pname = bottom_names[i] if i < len(bottom_names) else None
                img_bytes = product_imgs[i][0] if i < len(product_imgs) else None
                img_ext = product_imgs[i][1] if i < len(product_imgs) else None
                lines_out.append(ProductLine(
                    order_number=order_num,
                    order_date=date_str,
                    product_name=pname,
                    customer_name=_pick_name_from_options(opts),
                    note=_pick_note_from_options(opts),
                    quantity=1,
                    total_products_in_order=total_products,
                    image_bytes=img_bytes,
                    image_mime=f"image/{img_ext}" if img_ext else None,
                ))
    finally:
        doc.close()
    return lines_out


# ── Grouping & sorting ───────────────────────────────────────────────────
def group_and_sort_by_product(lines: list[ProductLine]) -> list[dict]:
    """Group lines by product_name and sort groups by line-count desc.

    Returns a list of {"product_name", "count", "lines": [ProductLine, ...]}.
    """
    buckets: "OrderedDict[str, list[ProductLine]]" = OrderedDict()
    for ln in lines:
        key = (ln.product_name or "بدون اسم منتج").strip() or "بدون اسم منتج"
        buckets.setdefault(key, []).append(ln)
    groups = [{"product_name": k, "count": len(v), "lines": v} for k, v in buckets.items()]
    groups.sort(key=lambda g: g["count"], reverse=True)
    return groups


def flatten_sorted(groups: list[dict]) -> list[ProductLine]:
    """Flatten the grouped+sorted structure back into a card-order list."""
    out: list[ProductLine] = []
    for g in groups:
        out.extend(g["lines"])
    return out


# ── PDF generation ───────────────────────────────────────────────────────
_FONT_REGISTERED = False
_FONT_NAME = "Helvetica"


def _register_font() -> str:
    """Register a TTF that supports Arabic. We prefer NotoNaskhArabic;
    DejaVuSans is a fine secondary."""
    global _FONT_REGISTERED, _FONT_NAME
    if _FONT_REGISTERED:
        return _FONT_NAME
    candidates = [
        ("NotoNaskhArabic", "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"),
        ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ("Amiri", "/usr/share/fonts/truetype/amiri/amiri-regular.ttf"),
    ]
    for name, path in candidates:
        try:
            pdfmetrics.registerFont(TTFont(name, path))
            _FONT_NAME = name
            _FONT_REGISTERED = True
            return _FONT_NAME
        except Exception:
            continue
    _FONT_REGISTERED = True
    return _FONT_NAME


def _qr_png_bytes(payload: str) -> bytes:
    qr = qrcode.QRCode(
        version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4, border=1,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _placeholder_png_bytes() -> bytes:
    """Soft grey placeholder when no product image is available."""
    img = Image.new("RGB", (300, 300), (230, 230, 230))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _safe_image_or_placeholder(img_bytes: Optional[bytes]) -> Image.Image:
    """Return a Pillow image — converted to RGB — even if input is bad."""
    if img_bytes:
        try:
            im = Image.open(io.BytesIO(img_bytes))
            if im.mode != "RGB":
                im = im.convert("RGB")
            return im
        except Exception:
            pass
    return Image.open(io.BytesIO(_placeholder_png_bytes()))


def _truncate(s: str, max_chars: int) -> str:
    s = s or ""
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "…"


def generate_preparation_pdf(
    lines: list[ProductLine],
    *,
    serial_start: int = 1,
    title: str = "تجهيز المنتجات",
) -> bytes:
    """Generate the 4×4 prep PDF and return its bytes."""
    if not lines:
        raise ValueError("No product lines to render")

    font_name = _register_font()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4

    margin = 8 * mm
    cols, rows = 4, 4
    per_page = cols * rows
    card_w = (page_w - 2 * margin) / cols
    card_h = (page_h - 2 * margin) / rows

    accent = HexColor("#0A3622")  # match app palette

    def _draw_card(card_idx_on_page: int, line: ProductLine, serial: int) -> None:
        col = card_idx_on_page % cols
        row = card_idx_on_page // cols
        # Cards laid out left-to-right, top-to-bottom (visual print order).
        x = margin + col * card_w
        y = page_h - margin - (row + 1) * card_h

        # Card border (thin)
        c.setStrokeColor(HexColor("#888888"))
        c.setLineWidth(0.4)
        c.rect(x + 1, y + 1, card_w - 2, card_h - 2)

        # ── Serial number (top-right corner; Arabic reads RTL) ──
        c.setFont(font_name, 9)
        c.setFillColor(accent)
        c.drawRightString(x + card_w - 4, y + card_h - 12, str(serial))

        # ── Product image (centered, top half) ──
        pim = _safe_image_or_placeholder(line.image_bytes)
        img_buf = io.BytesIO()
        pim.save(img_buf, format="PNG")
        img_buf.seek(0)
        from reportlab.lib.utils import ImageReader
        ir = ImageReader(img_buf)
        # Image area: centered, fixed slot ~ 32mm × 32mm
        img_slot = min(card_w - 14 * mm, card_h * 0.42)
        img_x = x + (card_w - img_slot) / 2
        img_y = y + card_h - img_slot - 6
        c.drawImage(ir, img_x, img_y, width=img_slot, height=img_slot,
                    preserveAspectRatio=True, anchor="c", mask="auto")

        # ── QR code (top-left, smaller than image) ──
        qr_bytes = _qr_png_bytes(line.order_number)
        qr_buf = io.BytesIO(qr_bytes)
        qr_reader = ImageReader(qr_buf)
        qr_size = 18 * mm
        c.drawImage(qr_reader, x + 4, y + card_h - qr_size - 4,
                    width=qr_size, height=qr_size, mask="auto")

        # ── Text block (bottom half) ──
        text_y = img_y - 8  # baseline of first line
        line_gap = 10
        c.setFillColor(HexColor("#111111"))
        c.setFont(font_name, 8)

        def _line(label_ar: str, value: str, *, bold: bool = False) -> None:
            nonlocal text_y
            # Render full sentence right-aligned; Arabic text reshaped.
            sentence = f"{label_ar}: {value}" if value else label_ar
            c.setFont(font_name, 8.5 if bold else 8)
            c.drawRightString(x + card_w - 4, text_y, _ar(sentence))
            text_y -= line_gap

        # ط (Order#) — keep digits LTR by placing them as ASCII.
        c.setFont(font_name, 9)
        c.drawRightString(
            x + card_w - 4, text_y,
            _ar("ط") + f" : {line.order_number}",
        )
        text_y -= line_gap + 1

        if line.customer_name:
            _line("الاسم", _truncate(line.customer_name, 22))
        if line.note:
            _line("ملاحظة", _truncate(line.note, 32))
        if line.order_date:
            # Render date in ISO-like form if we can parse it; otherwise raw.
            _line("التاريخ", _truncate(line.order_date, 22))
        _line("الكمية", str(line.quantity or 1))

        # Shipping line — always last; bold-ish weight
        carrier = (line.shipping_company or "").strip() or "—"
        n = max(1, int(line.total_products_in_order or 1))
        # Format like "iMile - 3" — carrier may be Arabic.
        ship_line = f"{carrier} - {n}"
        c.setFont(font_name, 9)
        c.setFillColor(accent)
        c.drawRightString(x + card_w - 4, text_y, _ar(ship_line))

    # Render pages
    for page_idx in range(0, len(lines), per_page):
        page_lines = lines[page_idx:page_idx + per_page]
        for i, ln in enumerate(page_lines):
            _draw_card(i, ln, serial_start + page_idx + i)
        c.showPage()

    c.save()
    return buf.getvalue()
