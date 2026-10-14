"""One disposable Linux parser. No app imports, credentials, paths or network input."""
import json
import sys

MEMORY_BYTES = 512 * 1024 * 1024
CPU_SECONDS = 2
MAX_BYTES = 2 * 1024 * 1024
MAX_PAGES = 8


def apply_limits():
    if sys.platform != "linux":
        raise RuntimeError("resource isolation unavailable")
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))


class PdfRejected(Exception):
    pass


def verify_pdf(data, tracking):
    if not data.startswith(b"%PDF-") or not data.rstrip().endswith(b"%%EOF"):
        raise PdfRejected("shipping_document_not_pdf")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if pdf.is_encrypted or pdf.needs_pass or pdf.is_repaired or not 1 <= pdf.page_count <= MAX_PAGES:
                raise PdfRejected("shipping_document_pdf_rejected")
            # A scanned image or partial/prefixed token is not AWB identity proof.
            match = re.compile(r"(?<![\w-])" + re.escape(tracking) + r"(?![\w-])")
            found = False
            for page in pdf:
                text = page.get_text()
                if len(text) > 1_000_000:
                    raise PdfRejected("shipping_document_pdf_rejected")
                found = bool(match.search(text)) or found
            if not found:
                raise PdfRejected("shipping_document_awb_unproven")
    except PdfRejected:
        raise
    except Exception as exc:
        raise PdfRejected("shipping_document_pdf_rejected") from exc



def main():
    try:
        apply_limits()  # Set hard OS limits BEFORE loading native parser or PDF.
        global pymupdf, re
        import pymupdf
        import re
        header = sys.stdin.buffer.readline(1024)
        request = json.loads(header)
        tracking = request["tracking"]
        if not isinstance(tracking, str) or not 0 < len(tracking) <= 200:
            raise PdfRejected("shipping_document_identity_missing")
        data = sys.stdin.buffer.read(MAX_BYTES + 1)
        if not 0 < len(data) <= MAX_BYTES:
            raise PdfRejected("shipping_document_size_exceeded")
        verify_pdf(data, tracking)
        sys.stdout.write("OK")
    except PdfRejected as exc:
        sys.stdout.write(str(exc))
    except BaseException:
        # No PDF data, AWB, URL, traceback or credentials cross this boundary.
        sys.stdout.write("shipping_document_parser_failed")


if __name__ == "__main__":
    main()
