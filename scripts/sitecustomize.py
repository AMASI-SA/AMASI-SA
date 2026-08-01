"""Temporary CI bootstrap for the Meta V2 patch script.

Python imports ``sitecustomize`` from the script directory before executing the
requested script. This updates temporary patch-tool guards only; it does not
modify runtime application modules and is deleted before merge.
"""

from pathlib import Path


patch_path = Path(__file__).with_name("tmp_apply_meta_v2_ads_manager.py")
if patch_path.exists():
    text = patch_path.read_text(encoding="utf-8")

    stale_guard = '''    provider_source_count = service.count(provider_source_old)
    if provider_source_count != 2:
        raise SystemExit(
            "provider source key: expected two matches, "
            f"found {provider_source_count}"
        )
    service = service.replace(provider_source_old, provider_source_new)
'''
    current_guard = '''    provider_source_count = service.count(provider_source_old)
    if provider_source_count != 1:
        raise SystemExit(
            "provider source key: expected one match, "
            f"found {provider_source_count}"
        )
    service = service.replace(provider_source_old, provider_source_new, 1)
    if provider_source_old in service:
        raise SystemExit("legacy provider source expression remains")
'''
    if stale_guard in text:
        text = text.replace(stale_guard, current_guard, 1)

    return_anchor = '''    if provider_source_old in service:
        raise SystemExit("legacy provider source expression remains")
    return service
'''
    daily_patch = '''    if provider_source_old in service:
        raise SystemExit("legacy provider source expression remains")

    daily_provider_source_old = ''' + "'''" + '''                    provider_source_key = (
                        "snapchat_account_daily"
                        if provider_key == "snapchat"
                        else f"{provider_key}_ads_daily"
                    )
''' + "'''" + '''
    daily_provider_source_new = ''' + "'''" + '''                    provider_source_key = (
                        "snapchat_account_daily"
                        if provider_key == "snapchat"
                        else meta_source_key
                        if provider_key == "meta"
                        else "tiktok_ads_daily"
                    )
''' + "'''" + '''
    service = replace_once(
        service,
        daily_provider_source_old,
        daily_provider_source_new,
        "daily chart provider source key",
    )
    return service
'''
    if "daily_provider_source_old" not in text:
        if text.count(return_anchor) != 1:
            raise RuntimeError("Meta V2 patch return anchor is missing or duplicated")
        text = text.replace(return_anchor, daily_patch, 1)

    patch_path.write_text(text, encoding="utf-8")
