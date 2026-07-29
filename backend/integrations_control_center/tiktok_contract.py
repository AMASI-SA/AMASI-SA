"""Public non-secret contract constants for TikTok V2."""

TIKTOK_NATIVE_V2_CONTRACT = {
    "provider": "tiktok_ads",
    "connection": "oauth",
    "api_family": "marketing_api",
    "api_version": "v1.3",
    "tenant_scoped": True,
    "encrypted_credentials": True,
    "make_data_used": False,
    "legacy_collection_read": False,
    "provider_mutations_enabled": False,
    "approval_lifecycle_required": True,
}
