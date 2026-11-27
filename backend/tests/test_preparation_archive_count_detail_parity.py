"""Regression: empty archive markers remain visible in the same detail reads counted by the summary."""
from pathlib import Path


def test_progress_details_include_empty_string_archive_marker():
    source = (Path(__file__).resolve().parents[1] / "preparation_section_reads.py").read_text()
    # Supplier progress count-only reads already include both None and "".
    # Preserve that eligibility in both the day directory and all detail selectors,
    # including cross-date supplier file members.
    expected = '"experiment_archived_at": {"$in": [None, ""]}'
    assert source.count(expected) == 3
    assert '"experiment_archived_at": None' not in source


def test_employee_workspace_hydration_accepts_empty_archive_marker():
    source = (Path(__file__).resolve().parents[1] / "preparation_supplier_dispatch.py").read_text()
    assert '"experiment_archived_at": {"$in": [None, ""]}' in source


def test_count_only_directory_accepts_same_archive_markers():
    source = (Path(__file__).resolve().parents[1] / "preparation_progress_counts.py").read_text()
    assert '"experiment_archived_at": {"$in": [None, ""]}' in source
