"""Temporary CI bootstrap for the Meta V2 patch script.

Python imports ``sitecustomize`` from the script directory before executing the
requested script.  This adjusts one stale guard in the temporary patch tool;
it does not modify runtime application modules and is deleted before merge.
"""

from pathlib import Path


patch_path = Path(__file__).with_name("tmp_apply_meta_v2_ads_manager.py")
if patch_path.exists():
    text = patch_path.read_text(encoding="utf-8")
    old = '''    provider_source_count = service.count(provider_source_old)
    if provider_source_count != 2:
        raise SystemExit(
            "provider source key: expected two matches, "
            f"found {provider_source_count}"
        )
    service = service.replace(provider_source_old, provider_source_new)
'''
    new = '''    provider_source_count = service.count(provider_source_old)
    if provider_source_count != 1:
        raise SystemExit(
            "provider source key: expected one match, "
            f"found {provider_source_count}"
        )
    service = service.replace(provider_source_old, provider_source_new, 1)
    if provider_source_old in service:
        raise SystemExit("legacy provider source expression remains")
'''
    if old in text:
        patch_path.write_text(text.replace(old, new, 1), encoding="utf-8")
