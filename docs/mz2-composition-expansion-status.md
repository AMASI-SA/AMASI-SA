# MZ2 composition expansion — work in progress

Scope: continue existing Product V2/Components V2 bundle engine for the owner specification attached 2026-10-17 (e66f4499-5fd6-476c-a15d-aee0759ee5a2). No production publication until fresh owner approval.

Base: origin/hotfix/prod-snap-meta-final at 342bbefab (full SHA recorded in continuation ledger). Existing picker/release work is complete; this is additional composition behavior.

Acceptance groups: direct add and separate product/component cards; stable conditional rules and quantities; inherited components with per-occurrence exclusion and frozen cost basis; current-product units/slots; dynamic sourcing using existing product policies, actual matching inventory and approved supplier relations; cost completeness and fixed purchase prices; immutable order snapshots, safe amendments/cancellation/idempotency; isolated integration/UI evidence and normal-product regressions.

Current audit: existing nested/conditional expansion, current-product slots, snapshots and idempotent fulfillment are implemented. Existing normal-product expansion inherits resource bindings but offers no per-bundle exclusions. Source is explicitly stock/supplier, component costs always added, and provenance is insufficient to distinguish independent from included supplier packaging. UI has separate addition step and exposes sourcing/unit fields for all kinds. These gaps require backend work, not cosmetic hiding.

Work ownership: root backend/integration, composition_ui frontend picker/editor tests, sourcing_audit and inheritance_audit read-only inspection. No shared production edits. Existing untracked frontend/build-bundle-verification preserved. Final acceptance remains NO-GO until new paths and regressions are demonstrated.
