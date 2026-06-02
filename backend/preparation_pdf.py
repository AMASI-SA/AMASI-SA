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
    item_index: int = 0            # 0-based position of this item within its order
    image_bytes: Optional[bytes] = field(default=None, repr=False)
    image_mime: Optional[str] = None
    shipping_company: Optional[str] = None  # populated from unified_orders later

    @property
    def item_key(self) -> str:
        """Composite key identifying a single item within a single order.

        Two items of the same product appearing in the same order are still
        distinct cards (different option values / item_index), so the dedup
        log uses this key — not the order number alone.
        """
        import hashlib
        parts = "|".join([
            str(self.order_number or ""),
            (self.product_name or "").strip().lower(),
            (self.customer_name or "").strip().lower(),
            str(self.item_index),
        ])
        return hashlib.sha1(parts.encode("utf-8")).hexdigest()

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
                    item_index=i,
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


_MONTH_ALIASES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2,
    "march": 3, "mar": 3, "april": 4, "apr": 4,
    "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
    # Arabic month names sometimes appear in Salla PDFs
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "إبريل": 4,
    "مايو": 5, "يونيو": 6, "يوليو": 7, "أغسطس": 8, "أغسطو": 8,
    "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
}


def short_date(raw: Optional[str]) -> Optional[str]:
    """Compress a long date string ("Tuesday 2 June 2026") into MM/DD.

    Supports: "DD Month YYYY", "Month DD, YYYY", and ISO "YYYY-MM-DD".
    Returns None when no parseable date is found — caller decides whether
    to skip the line or show the raw string.
    """
    if not raw:
        return None
    text = str(raw).strip().lower()
    # Pattern 1: "...DD Month YYYY..."
    m = re.search(r"(\d{1,2})[\s\u00A0]+([\u0600-\u06FFA-Za-z]+)[\s\u00A0]+\d{4}", text)
    if m:
        day = int(m.group(1))
        mname = m.group(2)
        if mname in _MONTH_ALIASES:
            return f"{_MONTH_ALIASES[mname]:02d}/{day:02d}"
    # Pattern 2: "Month DD, YYYY"
    m = re.search(r"([\u0600-\u06FFA-Za-z]+)[\s\u00A0]+(\d{1,2}),?[\s\u00A0]+\d{4}", text)
    if m:
        mname = m.group(1)
        day = int(m.group(2))
        if mname in _MONTH_ALIASES:
            return f"{_MONTH_ALIASES[mname]:02d}/{day:02d}"
    # Pattern 3: ISO YYYY-MM-DD
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(2)}/{m.group(3)}"
    return None


def _wrap_arabic_lines(raw_text: str, font_name: str, font_size: float,
                       max_width: float, max_lines: int = 2) -> list[str]:
    """Word-wrap raw Arabic to fit within `max_width` over up to `max_lines`.

    Returns a list of *reshaped+bidi* strings ready to pass to drawRightString.
    Adds an ellipsis on the last line if there's leftover text. Guarantees
    every returned line measures ≤ max_width — the printable card stays
    within its bounds no matter how long the source text is.
    """
    raw = (raw_text or "").strip()
    if not raw:
        return []
    # Fast path: full text fits
    full_visual = _ar(raw)
    if pdfmetrics.stringWidth(full_visual, font_name, font_size) <= max_width:
        return [full_visual]

    words = raw.split()
    out_lines: list[str] = []
    i = 0
    while i < len(words) and len(out_lines) < max_lines:
        # Find the largest prefix words[i:j+1] that fits.
        j = i
        last_fit_visual: Optional[str] = None
        last_fit_j = i - 1
        while j < len(words):
            candidate = " ".join(words[i:j + 1])
            visual = _ar(candidate)
            if pdfmetrics.stringWidth(visual, font_name, font_size) <= max_width:
                last_fit_visual = visual
                last_fit_j = j
                j += 1
            else:
                break
        if last_fit_visual is None:
            # Even the first remaining word doesn't fit — hard truncate it.
            w = words[i]
            while w and pdfmetrics.stringWidth(_ar(w + "…"), font_name, font_size) > max_width:
                w = w[:-1]
            out_lines.append(_ar((w or "") + "…"))
            return out_lines
        out_lines.append(last_fit_visual)
        i = last_fit_j + 1

    # If text remains uncaptured, append ellipsis to the last line.
    if i < len(words) and out_lines:
        last = out_lines[-1]
        # Try simple "…" append
        if pdfmetrics.stringWidth(last + "…", font_name, font_size) <= max_width:
            out_lines[-1] = last + "…"
        else:
            while last and pdfmetrics.stringWidth(last + "…", font_name, font_size) > max_width:
                last = last[:-1]
            out_lines[-1] = (last or "") + "…"
    return out_lines


def _fit_single_line(text: str, font_name: str, font_size: float,
                     max_width: float) -> str:
    """Truncate a single reshaped Arabic line to fit max_width with ellipsis."""
    visual = _ar(text)
    if pdfmetrics.stringWidth(visual, font_name, font_size) <= max_width:
        return visual
    # Trim characters off the trailing (visual-left = logical-end) side
    while visual and pdfmetrics.stringWidth(visual + "…", font_name, font_size) > max_width:
        visual = visual[1:]  # drop one char at the trailing visual edge
    return (visual + "…") if visual else "…"


def generate_preparation_pdf(
    lines: list[ProductLine],
    *,
    serial_start: int = 1,
    title: str = "تجهيز المنتجات",
) -> bytes:
    """Generate the 4×4 prep PDF and return its bytes.

    Every card is rendered inside a *clipping rectangle* so even if our
    wrapping/truncation underestimates a glyph's width, no character is
    allowed to spill into the neighbouring card. Field-by-field rules:

    - Order# (ط:) — fixed 9pt, digits LTR.
    - الاسم — single line, ellipsis if it overflows.
    - ملاحظة — up to 2 lines, ellipsis on overflow.
    - التاريخ — compressed to MM/DD.
    - الكمية — single line.
    - شركة الشحن + N — single line, smaller if needed.

    All text is drawRightString'd from a fixed right edge (RTL). Auto font
    shrinking applies when even the truncated short variant doesn't fit.
    """
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
    pad = 4  # inner padding (pts) — fixed text frame

    accent = HexColor("#0A3622")
    text_color = HexColor("#1A1A1A")
    muted_color = HexColor("#666666")

    def _draw_card(card_idx_on_page: int, line: ProductLine, serial: int) -> None:
        col = card_idx_on_page % cols
        row = card_idx_on_page // cols
        x = margin + col * card_w
        y = page_h - margin - (row + 1) * card_h

        # ── 1. Card border ──────────────────────────────────────────
        c.setStrokeColor(HexColor("#A0A0A0"))
        c.setLineWidth(0.5)
        c.rect(x + 0.5, y + 0.5, card_w - 1, card_h - 1)

        # ── 2. Clip everything else to the card interior ────────────
        c.saveState()
        p = c.beginPath()
        p.rect(x + pad, y + pad, card_w - 2 * pad, card_h - 2 * pad)
        c.clipPath(p, stroke=0)

        right_edge = x + card_w - pad   # text right-align anchor
        left_edge = x + pad
        usable_w = right_edge - left_edge

        # ── 3. Serial (top-right) + QR (top-left) + Image (centered) ──
        from reportlab.lib.utils import ImageReader

        c.setFont(font_name, 7)
        c.setFillColor(muted_color)
        c.drawRightString(right_edge, y + card_h - pad - 8, f"#{serial}")

        qr_size = 36
        qr_buf = io.BytesIO(_qr_png_bytes(line.order_number))
        c.drawImage(ImageReader(qr_buf),
                    left_edge, y + card_h - pad - qr_size,
                    width=qr_size, height=qr_size, mask="auto")

        # Image area: between QR and serial, sits below them; size ~ 55×55
        img_slot = 56
        img_x = x + (card_w - img_slot) / 2
        img_y = y + card_h - pad - img_slot - 2
        pim = _safe_image_or_placeholder(line.image_bytes)
        img_buf = io.BytesIO()
        pim.save(img_buf, format="PNG")
        img_buf.seek(0)
        c.drawImage(ImageReader(img_buf), img_x, img_y,
                    width=img_slot, height=img_slot,
                    preserveAspectRatio=True, anchor="c", mask="auto")

        # ── 4. Text block — anchored to the card's bottom ────────────
        # Compute total required vertical space; if it overflows, shrink.
        order_size = 9.0
        body_size = 8.0
        ship_size = 8.5
        line_gap = 2.0   # gap between lines (added to font size)

        # Pre-wrap dynamic fields with measurements based on body_size.
        def _build_text_lines(base_size: float) -> list[tuple[str, float, HexColor, bool]]:
            """Return [(visual_text, font_size, color, is_bold), ...] for body block."""
            block: list[tuple[str, float, HexColor, bool]] = []
            # Order# (with ط prefix)
            order_visual = _ar("ط") + f" : {line.order_number}"
            block.append((order_visual, order_size, text_color, True))
            if line.customer_name:
                v = _fit_single_line(f"الاسم: {line.customer_name}", font_name, base_size, usable_w)
                block.append((v, base_size, text_color, False))
            if line.note:
                note_lines = _wrap_arabic_lines(
                    f"ملاحظة: {line.note}", font_name, base_size, usable_w, max_lines=2,
                )
                for nl in note_lines:
                    block.append((nl, base_size, muted_color, False))
            # Compact: date + quantity on the same line — saves vertical room
            date_short = short_date(line.order_date)
            qty_str = f"ك: {line.quantity or 1}"
            tail = qty_str
            if date_short:
                tail = f"{date_short}    {qty_str}"  # two-space gap
            block.append((_fit_single_line(tail, font_name, base_size, usable_w),
                          base_size, text_color, False))
            # Shipping line (carrier - N)
            carrier = (line.shipping_company or "").strip() or "—"
            n = max(1, int(line.total_products_in_order or 1))
            ship_line = f"{carrier} - {n}"
            ship_visual = _fit_single_line(ship_line, font_name, ship_size, usable_w)
            block.append((ship_visual, ship_size, accent, True))
            return block

        block = _build_text_lines(body_size)

        # Available vertical room below the image
        text_top = img_y - 4
        text_bottom = y + pad + 1   # leave 1pt above border inside clip

        def _total_height(blk: list[tuple[str, float, HexColor, bool]]) -> float:
            return sum(item[1] + line_gap for item in blk) - line_gap

        # Auto-shrink if it overflows: reduce body_size step by step.
        while _total_height(block) > (text_top - text_bottom) and body_size > 6.0:
            body_size -= 0.5
            ship_size = max(7.0, ship_size - 0.5)
            block = _build_text_lines(body_size)
        # As a last resort, drop the note entirely if still overflowing
        if _total_height(block) > (text_top - text_bottom) and line.note:
            line_note_backup = line.note
            line.note = None
            block = _build_text_lines(body_size)
            line.note = line_note_backup

        # Render top-down from the top of the text block
        cursor = text_top
        for visual, fsize, color, is_bold in block:
            c.setFillColor(color)
            c.setFont(font_name, fsize)
            cursor -= fsize  # move down by ascent
            c.drawRightString(right_edge, cursor, visual)
            cursor -= line_gap

        c.restoreState()

    # Render pages
    for page_idx in range(0, len(lines), per_page):
        page_lines = lines[page_idx:page_idx + per_page]
        for i, ln in enumerate(page_lines):
            _draw_card(i, ln, serial_start + page_idx + i)
        c.showPage()

    c.save()
    return buf.getvalue()
