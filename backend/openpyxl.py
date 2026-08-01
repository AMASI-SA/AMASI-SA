"""Temporary CI-only shim for the targeted Ads Manager test rerun.

The historical shared conftest imports openpyxl only to create an unrelated
Excel fixture. The temporary runner snapshot cannot add dependencies after it
has been created, so this file provides exactly the four members conftest uses.
It is deleted before the pull request is made ready for review.
"""

from pathlib import Path


class _Worksheet:
    def __init__(self) -> None:
        self.rows: list[list[object]] = []

    def append(self, row) -> None:
        self.rows.append(list(row))


class Workbook:
    def __init__(self) -> None:
        self.active = _Worksheet()

    def save(self, path) -> None:
        # The Ads Manager tests never read this unrelated fixture. Creating a
        # deterministic placeholder is sufficient for shared conftest setup.
        Path(path).write_bytes(b"temporary-ads-manager-test-fixture\n")
