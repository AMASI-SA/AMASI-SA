# MZ2 composition expansion — work in progress

Scope: continue existing Product V2/Components V2 bundle engine for the owner specification attached 2026-10-17 (e66f4499-5fd6-476c-a15d-aee0759ee5a2). No production publication until fresh owner approval.

Base: origin/hotfix/prod-snap-meta-final at 342bbefab (full SHA recorded in continuation ledger). Existing picker/release work is complete; this is additional composition behavior.

Acceptance groups: direct add and separate product/component cards; stable conditional rules and quantities; inherited components with per-occurrence exclusion and frozen cost basis; current-product units/slots; dynamic sourcing using existing product policies, actual matching inventory and approved supplier relations; cost completeness and fixed purchase prices; immutable order snapshots, safe amendments/cancellation/idempotency; isolated integration/UI evidence and normal-product regressions.

Implemented WIP: opt-in schema2 composition with stable rules, per-occurrence inherited component decisions and frozen cost provenance; direct product addition and separate product/resource cards; existing product profile policy editor; mapped child variants/options/text; automatic exact-stock-first sourcing with approved supplier fallback and explicit split policy. Source-context changes cannot reuse stale receipt claims. Cost permission redaction includes sourcing segments.

Verification checkpoint: catalog60 + policy28 tests pass (5 real Mongo transaction cases); fulfillment35 pass; composition lifecycle3 pass; editor30 + policy5 pass. Ordinary component regression27 pass +7 subtests,1 skip. Isolated hoodie50+necklace30+box3+wrap5 estimate88; two bundles consume176 once, original bindings and excluded bags unchanged, policy edits do not rewrite frozen snapshots; cancellation and exact silver/name matching demonstrated. Final combined UI/regression/visual checks pending. Test evidence is local synthetic data only.

Policy data uses existing cost_profiles.composition_components and operation_profiles.bundle_sourcing/revision, not a parallel inventory/supplier system. Unknown provenance, missing supplier policy and unsupported independently billed supplier assembly remain explicit unresolved states. Fixed purchased-complete price is preserved when packaging is excluded.

Work ownership: root integration/evidence, composition_ui editor/policy UI, sourcing_audit fulfillment and isolated lifecycle/regressions, inheritance_audit catalog and profile transaction validation. No shared production edits. Existing untracked frontend/build-bundle-verification preserved. Final acceptance remains NO-GO pending complete acceptance mapping and visual/regression evidence. No merge, release lease or deployment for this extension; fresh owner approval required.
