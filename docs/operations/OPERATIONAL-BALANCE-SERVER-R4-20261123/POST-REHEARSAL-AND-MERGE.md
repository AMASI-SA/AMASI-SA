# PR2082 — post-rehearsal and protected GitHub merge checkpoint

Owner terminal evidence on 2026-11-23 at 12:32:30 UTC:
- Original shared guard inactive before, during and after the isolated adapter.
- Original /app checkout at N `44122bcc986bd1dff1532b9ad62c0268e899a179`.
- Isolated rehearsal receipt: `/tmp/mezan-pr2082-rehearsal-6210m7fq/result.json`; log `/tmp/mezan-pr2082-rehearsal-6210m7fq/adapter.log`.
- `status=isolated_rehearsal_passed`, `adapter_exit=0`, `built=true`, `package_boundaries_verified=true`.
- Source A `aec203382b387c3812509084e77c2922aa860877`; B `0bd6a3fadd33d6354ebdc8dec814bcb80b0d7218`; B tree `4fa54935f420e2a546c2237c0fb7b0ef9ef8367d`.
- Runtime `rg5-12e0f132b3ee688815cd2ec401bd34c43706d193492edacc7be6c5ca6e6ade8a`; Frontend artifact `27b7717df8a2dcc2b46868da4d103b44d33e2ad07c879da6563ecaef7e545b9a`.
- /app free 313737216 bytes; /tmp free 266792726528 bytes; helper changed no shared source/output, created no lease, and did not publish.
- Original environment `GIT_ASKPASS` caused first helper abort; it was removed for only the successful invocation, without exposing its value.

GitHub protected Production from a direct ref update. No forced update occurred. Approved PR2082 was marked ready and merged through the official PR merge API. Final deployment commit D `c2d244a3349013c06f3fb7824070bfa12bd0bfe0` was read back:
- Parent 1: N `44122bcc986bd1dff1532b9ad62c0268e899a179`
- Parent 2: B `0bd6a3fadd33d6354ebdc8dec814bcb80b0d7218`
- Tree: exact B tree `4fa54935f420e2a546c2237c0fb7b0ef9ef8367d`
- Production ref equals D; PR2082 merged and closed.

The exact host-stage helper is `HOST-STAGE-2082.py` in this evidence branch, immutable commit `6a1a805194a13ea4a57ce29c24a397128e84613b`, file SHA256 `9ad89679ffe9bcfd518fe498b1418fc8f4abbdd076c747944cde29e0e23991de`. It rechecks the original guard/refs, original rehearsal receipt, exact generated output hashes, disk and tracked cleanliness, stages D via fast-forward only, preserves backups of the three generated outputs, and uses the original guard prepare/prepublish. The helper was uploaded with exact bytes; its SHA-256 pin was corrected after a first launcher mismatch, and the helper itself remains unchanged; it has **NOT** yet been run on /app. Production cloud publish, verify3 and lease closure remain pending.

Next safe action: owner executes the pinned GitHub helper from Emergent terminal once; only on `PR2082_STAGE status=READY_TO_PUBLISH` press Re-publish once in Emergent, then obtain explicit new Deployment Succeeded and run original `python scripts/production_release_guard.py verify --url https://mezansalla.com` followed by `status` to ensure inactive. Any STOP requires diagnosis; never reset --hard or clear another task's lease. No financial, budget, sync or live business test writes.

GitHub Production changed: YES, merged D. Shared Emergent /app, lease and live site changed by this task so far: NO.
