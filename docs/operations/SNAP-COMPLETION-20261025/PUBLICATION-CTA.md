# Publication CTA and Public Profile diagnostics

The publication form offers Shop Now (`SHOP_NOW`, compatibility default) and
Order Now (`ORDER_NOW`). The choice belongs to the immutable approved plan and
native creative operation, including exact readback and reconciliation. Changing
it cannot reuse the same request key. Legacy plans retain SHOP_NOW and their
request fingerprints. Direct ACTIVE creation and existing guards are unchanged.

## Public Profile 403: external checks

`public_profile_sharing_read` is Mezan's internal capability label, not a Snap
OAuth scope. A sharing-policy 403 proves denial of that read; it does not alone
identify a missing grant, app allowlisting, or user role. The error now carries
a bounded remediation code without exporting provider bodies or secrets.

Check application Public Profile API access/allowlisting with Snap, the actual
OAuth grant, the invoking user's Public Profile role, and sharing to the selected
ad account. Snap lists `snapchat-profile-api` for Public Profile API reads.
Confirm the endpoint requirement before changing grants. Never request the
internal capability label as a scope. If a new grant is required, complete app
access prerequisites, then obtain owner authorization through the existing
secure OAuth flow and recheck membership. Do not bypass permission denials.

Official references checked 2026-10-27 (Asia/Riyadh):
- https://developers.snap.com/marketing-api/Ads-API/authentication
- https://developers.snap.com/marketing-api/Public-Profile-API/GetStarted
- https://developers.snap.com/marketing-api/Public-Profile-API/ProfileAssetManagement

No live provider writes are used by this increment. Live creation and review
acceptance require an owner-specified campaign and budget.

Focused backend verification: 153 tests passed (1.98s), including both CTAs
through the four-stage native orchestration, stable retries, changed-CTA key
conflicts, legacy saved plans and provider CTA drift during the final ad-token
await. Command from backend: `python -m pytest -p snapchat_v2_test_bootstrap
tests/test_snapchat_campaign_publication.py tests/test_snapchat_media_upload.py
-q --tb=short`. One inherited python_multipart deprecation warning. Frontend
verification and integrated release checks are coordinator-owned; no local
node_modules are present in this isolated worktree. Production unchanged.
