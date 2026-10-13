# Historical read-only project diagnostic request

Submitted before the owner prohibited paid project chat at 2026-10-14 01:19 Asia/Riyadh. Do not reissue this through project chat. Subsequent support communication is email-only; see SUPPORT-COMMUNICATION.md and STATUS.json.

Retrieve the FULL Cloud Build/CodeBuild transcript for existing project salla-analytics, latest failed builder `f92cb445-09bb-4f90-8e0e-08fb9469f70d`. Return all frontend step `#26` lines after `release identity validation failed (FileNotFoundError)` and the actual terminal error, exception and exit code. The current UI stores only a shortened tail.

Reviewed deployment M is `4d4b218a5ce8343236eb3e81f43813f23d460a05`; source A is `5a333df08d8864d1f8841f59739e12483c614b1b`, intent B is `fe5c89ff42ae1076afbeb10b24a596b9bccacc4c`. The deterministic identity is `rg5-bcc1901d6b4e24097509cafe5e38bbb3e0187551533d5f8b68db64a090447b94`.

Fresh exact-M full no-Git/no-backend-env build PASS on x86_64, host Node24.19.0/Python3.12.14, governed Node22.23.2/Yarn1.22.22. Exact frontend artifact `390e3cda5d1e2e5b5d7580523d2bae0656981cb10a05349a7750465d11af5293` and isolated frontend/backend identity/health proofs PASS. The aarch64 no-Git/no-env/no-generated-identity manifest probe also PASS. Both emit the same nonfatal FileNotFoundError; `read_release_identity` catches it and returns unavailable identity. There is no evidence for changing cleanup/materialization ordering merely to silence that log.

This is read-only retrieval/diagnosis: preserve application source, release intent, environment files, leases, deploy manifests and current deployment. Do not publish, re-publish, run provider writes, read secret values or contact support. Report the actual fatal boundary and sanitized evidence; flag any inaccessible transcript. Existing healthy production remains on source `c8d34947250930c5fa1aacdd160337db107c2e85`, and current guard active=false.
