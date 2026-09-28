"""Resource-limited structural inspection of private evidence, not a malware scan.

Executed in a subprocess; no network, user-supplied path, shell, or external PDF
renderer is used. Evidence is never rendered as active same-origin browser HTML.
"""
from __future__ import annotations

import io
import json
import sys
import warnings

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_PAGES = 10


def inspect(data: bytes) -> dict:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("evidence_size_invalid")
    if data.startswith(b"%PDF-"):
        if not data.rstrip().endswith(b"%%EOF"):
            raise ValueError("pdf_trailing_content_or_incomplete")
        from pypdf import PdfReader
        from pypdf.generic import IndirectObject, DictionaryObject, ArrayObject
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted_pdf_not_supported")
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            raise ValueError("pdf_page_limit")
        forbidden = {
            "/OpenAction", "/AA", "/JavaScript", "/JS", "/Launch", "/EmbeddedFiles",
            "/EmbeddedFile", "/Filespec", "/RichMedia", "/XFA", "/AcroForm",
            "/SubmitForm", "/ImportData", "/GoToR", "/URI", "/Movie", "/Sound",
        }
        seen, count = set(), 0
        pending = [(reader.trailer, 0)]
        while pending:
            value, depth = pending.pop()
            count += 1
            if depth > 100 or count > 100_000:
                raise ValueError("pdf_structure_limit")
            if isinstance(value, IndirectObject):
                identity = (value.idnum, value.generation)
                if identity in seen:
                    continue
                seen.add(identity)
                value = value.get_object()
            if isinstance(value, DictionaryObject):
                if forbidden.intersection(map(str, value.keys())):
                    raise ValueError("pdf_active_or_embedded_content")
                if str(value.get("/Type", "")) in forbidden or str(value.get("/S", "")) in forbidden:
                    raise ValueError("pdf_active_or_embedded_content")
                pending.extend((v, depth + 1) for v in value.values())
            elif isinstance(value, ArrayObject):
                pending.extend((v, depth + 1) for v in value)
        for page in reader.pages:
            width, height = float(page.mediabox.width), float(page.mediabox.height)
            if not (1 <= width <= 14400 and 1 <= height <= 14400):
                raise ValueError("pdf_page_size_invalid")
        return {"content_type": "application/pdf", "pages": len(reader.pages),
                "inspection": "structural-limits-v1"}
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            fmt = image.format
            if fmt not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("image_format_not_supported")
            if image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
                raise ValueError("image_resource_limit")
            width, height = image.size
            image.verify()
        # Decode as well as checking the header/checksum, inside the worker limits.
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    return {"content_type": {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt],
            "width": width, "height": height, "inspection": "structural-limits-v1"}


def main():
    if sys.platform == "linux":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    try:
        value = inspect(sys.stdin.buffer.read(MAX_BYTES + 1))
        sys.stdout.write(json.dumps({"ok": True, **value}))
    except Exception:
        # Parser exceptions may include source bytes; never return or log them.
        sys.stdout.write('{"ok":false,"code":"evidence_document_invalid"}')
        raise SystemExit(2)


if __name__ == "__main__":
    main()
