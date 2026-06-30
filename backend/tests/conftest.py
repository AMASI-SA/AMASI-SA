"""Shared fixtures - creates Salla test Excel fixture once per session."""
import os
import openpyxl

SAMPLE_XLSX = "/tmp/salla_test.xlsx"


def _make_xlsx(path: str) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    # Build header that excel_parser recognizes
    headers = ["رقم الطلب", "تاريخ الطلب", "حالة الطلب", "إجمالي الطلب", "طريقة الدفع", "شركة الشحن"]
    # Pad to 53+ cols so col BA (source) exists
    while len(headers) < 60:
        headers.append(f"col_{len(headers)}")
    ws.append(headers)
    rows = [
        [1001, "2026-01-05", "completed", 250.00, "مدى", "سمسا"],
        [1002, "2026-01-05", "completed", 175.50, "Apple Pay", "سمسا"],
        [1003, "2026-01-06", "completed", 320.00, "تمارا", "أرامكس"],
        [1004, "2026-01-06", "completed", 99.00, "تابي", "سمسا"],
        [1005, "2026-01-07", "completed", 410.00, "مدى", "جندل"],
        [1006, "2026-01-07", "completed", 89.50, "Apple Pay", "سمسا"],
        [1007, "2026-01-08", "completed", 1250.00, "بطاقة ائتمانية", "أرامكس"],
        [1008, "2026-01-08", "completed", 65.00, "الدفع عند الاستلام", "سمسا"],
        [1009, "2026-01-09", "completed", 540.00, "مدى", "جندل"],
        [1010, "2026-01-09", "completed", 220.00, "تمارا", "سمسا"],
    ]
    for r in rows:
        padded = list(r) + [None] * (60 - len(r))
        # put source at column BA (index 52)
        padded[52] = "تطبيق سلة"
        ws.append(padded)
    wb.save(path)


def pytest_configure(config):
    # Iter-292 — load backend/.env into pytest's os.environ so tests
    # that touch encryption keys, Mongo URL, or webhook secrets work
    # without needing the operator to export them manually.
    try:
        with open("/app/backend/.env") as _envf:
            for _line in _envf:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _v = _line.split("=", 1)
                _v = _v.strip().strip('"').strip("'")
                # Don't clobber explicit env (CI might set its own values).
                os.environ.setdefault(_k.strip(), _v)
    except FileNotFoundError:
        pass

    if not os.path.exists(SAMPLE_XLSX):
        _make_xlsx(SAMPLE_XLSX)
