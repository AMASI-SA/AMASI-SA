# Shipping display checkpoint — 2026-10-03

Starting Backend c9b75346faafde7cb8b720618f43106f4e6fd090, Mobile
313e1841575c376f74ca7b3d34d4280e3ed7ba5e. Existing Supplier fixes preserved.
This checkpoint is source/test work only, not a prepared release or deployment.

## Proven isolated defect

The completed-order response exposed tracking from the printing workflow only,
even when Order Engine's canonical current shipping contained the current AWB.
The clients displayed an old persisted shipping_snapshot_changed error as a
current display failure. The actual Production order's failing guard comparison
is still unproven; no Production requests or mutations were made.

Add current_shipment from canonical Order Engine shipping to the read response.
Its source is the latest accepted Salla synchronization, not a fresh remote Salla
request on every screen open. Explicit null cannot revive an old workflow AWB.
Availability is not proof of print confirmation and does not authorize opening
an old artifact. Existing printing metadata/guards remain intact.

Both clients display current facts independently. Opening/printing validates
through the existing guarded refresh and uses only its fresh artifact. An actual
guard rejection stays visible and opens nothing. Supplier, accounting,
write-control, synchronization and stale/CAS enforcement source are unchanged.

## Verification

- Backend focused display, current shipping, stale guard, fulfillment and Order
  Engine suites: 139 PASS locally, isolated mongomock, no Production.
- Mobile new display/guard tests: 9 PASS; existing print contract PASS.
- Supplier history isolation: 8 PASS; product selection: 9 PASS.
- Browser/device physical printing and actual order UAT: NOT RUN.
- Exact-commit remote CI must be recorded after checkpoint push; prior green
  checks do not establish this new commit's readiness.
- Pre-existing Security braces blocker remains; no waiver/dependency/policy edit.

No Prepare, Prepublish, lease, merge, deployment, rollback, APK or OTA.
This work's Production financial writes = 0. Historical recovery exception B
remains owner-reviewed and historical zero financial writes is NOT PROVEN.
