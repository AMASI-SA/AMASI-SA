# Snapchat media upload vertical — 2026-10-25

Task branch: codex/snap-media-upload-20261025. Authoring baseline:
442bddc40493a05945cd6dd17fb1fe1ef1ea5a20. The parent/root task owns Git,
commands, test execution, reviewed release intent, publication and live checks.
This working checkpoint is not a deployment or live acceptance claim.

## Result implemented in source

- An owner/account-bound metadata preview records the expected SHA-256, received
  length, MIME and AI declaration before provider writes. The upload accepts a
  raw streamed request body; no multipart intake dependency or file URL is used.
- The same owner/account/content digest resolves to one durable Mongo document.
  A revision/claim CAS admits one provider-write lifecycle. One local semaphore
  bounds byte-holding upload lifecycles to one per application worker.
- The server verifies the real received length, digest and modern MP4/MOV
  container signature before creating a Media object. HTTPX uploads the bytes
  to the fixed Snapchat Media endpoint as multipart/form-data. No permanent
  file copy is stored. The provider ID is persisted before the file upload.
- Creation and upload attempts have separate durable states. A created Media
  remains in-flight until upload or an explicit failure; it is not exposed as
  available for a second writer midway through execution.
- Unknown creation is explicitly unresolved and never repeated blindly.
  A known Media upload timeout can recover through provider GET/readback.
  Stale recovery changes the CAS revision/claim so an old worker cannot start
  a later write. Recovery never issues a provider POST.
- A real browser File is hashed with WebCrypto, uploaded only after an explicit
  click, and linked into the creative form only after server-derived READY
  evidence. The upload POST disables the common Axios 401 replay.
- Owner/account/file changes clear previous media proof and invalidate stale
  asynchronous responses. Transfer progress is distinct from provider READY.
- The account-scoped Public Profile dropdown uses a bounded, fixed Business API
  sharing-policy read. A sole available profile is selected automatically.
  Empty, incomplete or unavailable profile evidence is visible as a blocker;
  the UI never grants sharing permissions or substitutes a manually invented ID.
- Creative preview and final execution re-read account/profile membership and
  the exact provider Media ID/account/type/READY state. The final Media GET
  follows the profile listing and precedes the creative provider write; cached
  upload status alone cannot authorize creation. Proof is retained in the
  existing proposal/audit surface.

The profile picker labels are generic IDs when the sharing-policy API supplies
no display name. The code does not add another Public Profile API permission
merely to fetch optional names.

## Acceptance and checks

The root reported a first focused run of the new backend suite: 25 passed,
0.70 seconds. This authoring agent executed no local commands or tests.
Fresh root verification is required on the final complete source.

Mandatory new CI selections include the new backend suite and both frontend
suites. Existing settings, campaign-management, safety and execution-hardening
checks remain selected. The settings scope guard gains exact reviewed paths,
without a wildcard or relaxed rejection of unrelated paths.

Key covered scenarios in source tests: content/size rejection; one-winner
creation and replay; capacity refusal before reading; role and kill-switch
changes after reception; foreign-account responses; mid-flight created Media;
stale CAS recovery; unknown-create refusal; upload timeout/readback; owner
boundaries; multipart transport; fresh creative dependency drift; unavailable
profile membership; profile paging origin escape; raw browser File transport;
WebCrypto; no automatic retry; stale account/profile responses and sole-profile
selection.

## Limits and next actions

- Real provider file upload, profile-list access and creative creation have not
  been exercised by this authoring agent. The root must prove those boundaries
  after normal source/CI/release gates.
- Public Profile/sharing-policy access can return an upstream permission or
  schema blocker. Such a response is not success and must not be bypassed.
- Files larger than 32,000,000 bytes and legacy MOV containers without an ftyp
  signature are outside this first release. Chunked one-gigabyte upload is not
  implemented or claimed.
- Optional provider duration/dimension metadata is checked for contradictions
  when present. Its absence is not described as verified local decoding or a
  complete video-specification check.
- Unknown-create catalogue adoption is not implemented. The explicit blocked
  state prevents duplicate Media creation; a later bounded read-only catalogue
  reconciliation may resolve it.
- This is the upload-to-creative-form vertical. It does not add automatic
  campaign-tree publication, spend activation, or a new autonomous budget policy.
- Production/provider writes by this authoring agent: none.

Next safe root action: inspect the final diff, run the new frontend suites and
broader backend management regressions, review current Production and active
release ownership, then prepare a fresh protocol-v5 source/intent release.
Preserve all newer Google/TikTok releases and unrelated changes.
