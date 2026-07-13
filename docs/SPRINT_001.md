# Sprint 001 — Order Engine Foundation

Status: IN PROGRESS

## Goal

Create the first authoritative Order Engine read contract and connect the new
Orders Workspace to one data source.

## Current Baseline

- Owner-only Orders V2 frontend exists.
- Route: `/orders-v2`
- Detail route: `/orders-v2/:orderNumber`
- Initial frontend commit: `95a9d00`
- Current page still requires a dedicated authoritative backend contract.

## In Scope

- Architecture documents
- Legacy order capability audit
- Orders V2 list API
- Orders V2 detail API
- Owner-only backend authorization
- Read-only service layer
- Salla creation-date ordering
- Normalized DTO
- Full details contract
- Tests
- Frontend connection to Orders V2 API

## Out of Scope

- Supplier assignment
- Preparation state mutation
- Purchase batches
- Operational inventory mutation
- Availability reservations
- PDF generation
- Marketing attribution
- Qoyod writes
- Automatic sending

## Acceptance Criteria

- `GET /api/orders-v2` exists.
- The endpoint returns at most 15 rows by default.
- Results are ordered by Salla order creation date.
- Order updates do not reorder old orders.
- `GET /api/orders-v2/{order_number}` returns one exact order.
- The API is read-only.
- The API performs no Qoyod request.
- Backend enforces owner-only access for the initial release.
- Orders V2 reads only from the new API contract.
- Frontend does not calculate authoritative business values.
- Full Salla raw data remains preserved internally.
- Build succeeds.
- Backend tests succeed.
- Legacy `/orders` remains unchanged until replacement acceptance.
- No Sprint 2 work begins before all criteria pass.

## Legacy Audit Requirement

Before implementing the API, classify existing order capabilities:

- REUSE infrastructure only
- REFACTOR useful logic into the engine
- REPLACE conflicting logic
- DELETE temporary or duplicate screens after replacement

## Completion Gate

Sprint 001 is complete only after production validation confirms:

- First 15 orders load.
- Infinite loading works without duplicates.
- Search finds an exact order.
- Detail view opens.
- Order creation dates are correct.
- The old Orders page remains unaffected.
