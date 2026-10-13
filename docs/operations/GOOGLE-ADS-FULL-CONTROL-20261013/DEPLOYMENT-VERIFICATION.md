# Google Ads publication verification

Native Google management and shared Mezan intelligence are merged in PR #1355, but the new release has **not been promoted** to production. Daily management is implemented for the officially supported campaign families; provider/account eligibility and the coverage limits in REQUIREMENTS.md still apply.

## Immutable handoff

- Source A: `5a333df08d8864d1f8841f59739e12483c614b1b`
- Intent B: `fe5c89ff42ae1076afbeb10b24a596b9bccacc4c`
- Merge M: `4d4b218a5ce8343236eb3e81f43813f23d460a05`, exactly the B tree
- Runtime identity: `rg5-bcc1901d6b4e24097509cafe5e38bbb3e0187551533d5f8b68db64a090447b94`
- Frontend artifact: `390e3cda5d1e2e5b5d7580523d2bae0656981cb10a05349a7750465d11af5293`

379 backend/shared checks, 74 fresh verifier/client checks and 86 frontend tests passed. All source/intent workflow and check runs completed without failure, including the separate Advanced Security CodeQL result with zero new annotations. The clean-clone and original /app ARM64 adapter rehearsals rebuilt the reviewed artifact and validated isolated packages.

## Actual publication evidence

Two authorized publish requests ran on the original Emergent project. The first failed because the platform frontend build image lacked Python. The controlled retry received platform manifest repairs and reached the app build entry point, but failed before deploy, health check or Mongo migration. The exposed log tail does not contain the actual fatal exception. A static platform RCA attributes the failure to reading a generated identity during cleanup; this is currently a hypothesis, not an established root cause.

A fresh exact-M archive with no Git, no backend environment and no generated identity successfully validates the reviewed intent under `python -I -B` (exit 0). It emits the same `FileNotFoundError` log. This demonstrates that the quoted message is nonfatal; changing cleanup ordering merely to silence it is not an evidence-backed fix. The full exact-M no-Git/no-backend-env adapter reproduction now passes (exit 0) on x86_64 with host Node 24.19.0/Python 3.12.14; it rebuilds the exact reviewed frontend artifact and validates isolated frontend and backend packages, including backend health. A separate aarch64 no-Git/no-env/no-generated-identity manifest probe also passes with the same nonfatal log. The new full ARM reproduction was blocked during dependency copy by local disk ENOSPC; its disposable directory was removed and available space restored. This separate local storage failure does not establish the original cloud failure cause. See NO-GIT-BUILD-EVIDENCE.json.

The public health endpoint was read after the failed retry: old source `c8d34947250930c5fa1aacdd160337db107c2e85`, old release `rg5-e76b78acd6e634167ff53785a53d50c3e3d98a2c348335fdad1dba505f491cbb`, all identity/frontend flags true. The matching failed Google lease was closed, and status confirms `active=false`. No live provider or financial writes were sent.

A separate platform-generated local diagnostic commit `9ba5d3f6de0c5c032fda15801af830ca2ea2c158` appeared in /app after the failed build. Its .gitignore/RCA changes are preserved. It is not the reviewed deployment M and must not be published with M's intent or lease. No raw infrastructure RCA, secrets or customer data are copied to this checkpoint.

## Remaining acceptance

Obtain fresh explicit Deployment Succeeded, then complete the guard's three exact public backend/frontend probes and confirm the matching lease closes. Then inspect native Google forms, validate-only behavior, AI proposal/review and shared dashboard/Ads Manager using the verified production runtime. Workspace/preview DB absence must not be treated as proof of production account disconnection.

## Remaining diagnostic boundary

The complete rendered BUILD row was inspected directly (1758 characters, one line); the platform has already shortened it, and its actual fatal exception is absent. The page provides no WebMCP tool for fuller logs. A read-only request is prepared for the original project diagnostic interface in CLOUD-BUILD-LOG-REQUEST.md; no human support messages or resource purchases have been made. A further publication requires fresh causal evidence or a verified platform repair, plus the existing reviewed-source and guard gates.
