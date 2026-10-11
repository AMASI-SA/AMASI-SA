"""Explicit test-only PDF download transport; real parsing/storage remain enabled."""
from io import BytesIO
from urllib.parse import unquote, urlsplit
from reportlab.pdfgen.canvas import Canvas
import shipping_print_document as documents


def install_pdf_download(monkeypatch, tracking_for_url):
    calls = []
    async def download(url):
        tracking = tracking_for_url(url) if callable(tracking_for_url) else tracking_for_url[url]
        assert tracking, f"Missing synthetic AWB for {url}"
        calls.append(url)
        stream = BytesIO()
        canvas = Canvas(stream)
        canvas.drawString(72, 720, "AWB: " + str(tracking))
        canvas.save()
        return stream.getvalue()
    monkeypatch.setattr(documents, "_download", download)
    return calls


async def assert_verified_document(db, result, *, source_url, tracking=None):
    assert result["ready"] is True
    assert "/carrier-label/document/" in result["label_url"]
    path = urlsplit(result["label_url"]).path
    number = unquote(path.split("/completed/", 1)[1].split("/carrier-label/", 1)[0])
    row = await documents.load_document(db, number, path.rsplit("/", 1)[-1])
    assert row is not None and row["source_url"] == source_url
    assert row["document_sha256"] == result["document_sha256"]
    assert row["tracking_number"] == (tracking or result["tracking_number"])
    assert row["bytes"].startswith(b"%PDF-")
