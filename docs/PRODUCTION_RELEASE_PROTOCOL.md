# Mezan production release protocol

Production is a shared Emergent workspace. Multiple conversations can safely
prepare code, but only one may own an active production release.

The current guard and embedded identity use protocol v2. If `status` shows a
lease prepared by v1, its owner must abort it with the original v1 guard
before `/app` is updated. After updating, run `prepare` again; a v1 lease or
identity must never be reused by the v2 guard.

## Prepare

After all intended commits are on `origin/hotfix/prod-snap-meta-final`, update
`/app` with a fast-forward pull and run:

```bash
cd /app
python scripts/production_release_guard.py prepare --actor "conversation-name"
```

Do not publish if this refuses. The generated
`backend/release_identity.json` is intentionally untracked and is packaged
with the exact workspace being deployed.

Run the final race check immediately before using Emergent:

```bash
python scripts/production_release_guard.py prepublish
```

## Publish and verify

Use Emergent's **Re-publish changes** once. Wait for a newer explicit
`Deployment Succeeded`, then run:

```bash
cd /app
python scripts/production_release_guard.py verify --url https://mezansalla.com
```

Only `"verified": true` after three consecutive checks proves that production
restarted, is healthy, and is running the prepared Git SHA with matching
critical file hashes. Until then, no financial or other irreversible
production action is allowed.

## Failed release

Inspect the owner and SHA:

```bash
python scripts/production_release_guard.py status
```

The owner may release a failed lease only by providing both the exact full SHA
and release ID returned by `prepare` or `status`:

```bash
python scripts/production_release_guard.py abort \
  --expected-sha <full-sha> \
  --expected-release-id <release-uuid>
```

Never clear the lease merely because the publish button became enabled.
