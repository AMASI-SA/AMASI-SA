import io
import zipfile

import pytest
from fastapi import HTTPException, UploadFile

from excel_upload_security import read_safe_xlsx_upload, validate_xlsx_archive


class TrackingBytesIO(io.BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self.requested_sizes = []

    def read(self, size=-1):
        self.requested_sizes.append(size)
        return super().read(size)


def _archive(extra_members=None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        for name, value in extra_members or []:
            archive.writestr(name, value)
    return output.getvalue()


def test_valid_xlsx_metadata_is_accepted():
    validate_xlsx_archive(_archive())


def test_missing_workbook_structure_is_rejected():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("random.txt", "not a workbook")
    with pytest.raises(HTTPException) as exc:
        validate_xlsx_archive(output.getvalue())
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "invalid_xlsx_structure"


def test_zip_member_limit_is_enforced_without_extraction():
    content = _archive([("xl/a.xml", "a"), ("xl/b.xml", "b")])
    with pytest.raises(HTTPException) as exc:
        validate_xlsx_archive(content, max_members=3)
    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "xlsx_member_limit"


def test_high_compression_ratio_is_rejected():
    content = _archive([("xl/sharedStrings.xml", b"A" * (2 * 1024 * 1024))])
    with pytest.raises(HTTPException) as exc:
        validate_xlsx_archive(content, max_compression_ratio=20)
    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "xlsx_compression_ratio"


@pytest.mark.asyncio
async def test_transport_read_is_bounded_to_max_plus_one():
    stream = TrackingBytesIO(b"x" * 20)
    upload = UploadFile(filename="oversized.xlsx", file=stream)
    with pytest.raises(HTTPException) as exc:
        await read_safe_xlsx_upload(upload, max_bytes=10)
    assert exc.value.status_code == 413
    assert stream.requested_sizes == [11]


@pytest.mark.asyncio
async def test_only_xlsx_extension_is_accepted():
    upload = UploadFile(filename="legacy.xls", file=io.BytesIO(_archive()))
    with pytest.raises(HTTPException) as exc:
        await read_safe_xlsx_upload(upload)
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "xlsx_required"
