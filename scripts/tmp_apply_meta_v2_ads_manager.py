from __future__ import annotations

import subprocess


BASE_PATCH_COMMIT = "312db7bfb60ff25bff3934f03fc783d608573b1e"
PATCH_PATH = "scripts/tmp_apply_meta_v2_ads_manager.py"


def load_base_patch() -> str:
    return subprocess.check_output(
        ["git", "show", f"{BASE_PATCH_COMMIT}:{PATCH_PATH}"],
        text=True,
    )


def add_daily_source_fix(source: str) -> str:
    anchor = '''    if provider_source_old in service:
        raise SystemExit("legacy provider source expression remains")
    return service
'''
    replacement = '''    if provider_source_old in service:
        raise SystemExit("legacy provider source expression remains")

    daily_provider_source_old = \'\'\'                    provider_source_key = (
                        "snapchat_account_daily"
                        if provider_key == "snapchat"
                        else f"{provider_key}_ads_daily"
                    )
\'\'\'
    daily_provider_source_new = \'\'\'                    provider_source_key = (
                        "snapchat_account_daily"
                        if provider_key == "snapchat"
                        else meta_source_key
                        if provider_key == "meta"
                        else "tiktok_ads_daily"
                    )
\'\'\'
    service = replace_once(
        service,
        daily_provider_source_old,
        daily_provider_source_new,
        "daily chart provider source key",
    )
    return service
'''
    if source.count(anchor) != 1:
        raise SystemExit("Meta V2 base patch return anchor is missing or duplicated")
    return source.replace(anchor, replacement, 1)


patched_source = add_daily_source_fix(load_base_patch())
exec(compile(patched_source, "<meta-v2-ads-manager-patch>", "exec"), {"__name__": "__main__"})
