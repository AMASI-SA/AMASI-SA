"""Bounded XLSX upload validation shared by all import endpoints.

The validator reads at most max_bytes + 1 and inspects ZIP metadata before
openpyxl sees the archive. It never expands members while applying bomb limits.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import PurePosixPath

from fastapi import HTTPException, UploadFile

DEFAULT_MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_ZIP_MEMBERS = 2_000
MAX_UNCOMPRESSED_BYTES = 80 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
_REQUIRED_XLSX_MEMBERS = {"[Content_Types].xml", "xl/workbook.xml"}


def _reject(status_code: int, code: str, message: str) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def validate_xlsx_archive(
    content: bytes,
    *,
    max_members: int = MAX_ZIP_MEMBERS,
    max_uncompressed_bytes: int = MAX_UNCOMPRESSED_BYTES,
    max_member_bytes: int = MAX_MEMBER_BYTES,
    max_compression_ratio: int = MAX_COMPRESSION_RATIO,
) -> None:
    """Reject malformed or suspicious XLSX ZIP metadata without extraction."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if not members or len(members) > max_members:
                _reject(413, "xlsx_member_limit", "ملف Excel يحتوي على عدد عناصر غير مسموح.")

            names = {item.filename for item in members}
            if not _REQUIRED_XLSX_MEMBERS.issubset(names):
                _reject(400, "invalid_xlsx_structure", "الملف ليس مصنف Excel صالحاً.")

            total_uncompressed = 0
            for item in members:
                path = PurePosixPath(item.filename)
                if item.filename.startswith("/") or ".." in path.parts:
                    _reject(400, "unsafe_xlsx_path", "بنية ملف Excel غير آمنة.")
                if item.file_size < 0 or item.compress_size < 0:
                    _reject(400, "invalid_xlsx_metadata", "بيانات ملف Excel غير صالحة.")
                if item.file_size > max_member_bytes:
                    _reject(413, "xlsx_member_too_large", "أحد أجزاء ملف Excel كبير جداً.")

                total_uncompressed += item.file_size
                if total_uncompressed > max_uncompressed_bytes:
                    _reject(413, "xlsx_uncompressed_too_large", "حجم ملف Excel بعد الفك كبير جداً.")

                if item.file_size >= 1024 * 1024:
                    ratio = item.file_size / max(item.compress_size, 1)
                    if ratio > max_compression_ratio:
                        _reject(413, "xlsx_compression_ratio", "نسبة ضغط ملف Excel غير آمنة.")
    except HTTPException:
        raise
    except (zipfile.BadZipFile, OSError, ValueError):
        _reject(400, "invalid_xlsx_archive", "تعذر قراءة ملف Excel أو أن بنيته غير صالحة.")


async def read_safe_xlsx_upload(
    file: UploadFile,
    *,
    max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> bytes:
    """Read one XLSX with a hard transport bound and ZIP-bomb checks."""
    filename = str(file.filename or "").strip().lower()
    if not filename.endswith(".xlsx"):
        _reject(400, "xlsx_required", "يرجى رفع ملف Excel بصيغة .xlsx فقط.")

    content = await file.read(max_bytes + 1)
    if not content:
        _reject(400, "empty_xlsx", "الملف فارغ.")
    if len(content) > max_bytes:
        _reject(413, "xlsx_upload_too_large", "حجم ملف Excel يتجاوز الحد المسموح.")

    validate_xlsx_archive(content)
    return content


__all__ = [
    "DEFAULT_MAX_UPLOAD_BYTES",
    "read_safe_xlsx_upload",
    "validate_xlsx_archive",
]
