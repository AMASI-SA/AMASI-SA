# Production release protocol

These rules apply to every agent and every conversation that can deploy this
repository to the shared Emergent production project.

## Single source of truth

- Production branch: `hotfix/prod-snap-meta-final`.
- GitHub `origin/hotfix/prod-snap-meta-final` is the only source of truth.
- Emergent publishes the current `/app` workspace; it does not implicitly pull
  GitHub. Never infer that clicking **Re-publish** includes recent commits.

## Mandatory release gate

Before clicking **Re-publish changes**, run from `/app`:

```bash
python scripts/production_release_guard.py prepare --actor "<conversation>"
```

The command must exit successfully. It enforces all of the following:

1. only one production release lease exists across conversations;
2. the checked-out branch is the production branch;
3. tracked files are clean;
4. local `HEAD` exactly matches `origin/hotfix/prod-snap-meta-final` after a
   fresh fetch;
5. the exact Git SHA is embedded in the package for post-deploy proof.

Immediately before clicking **Re-publish changes**, run this second gate. It
refetches GitHub and refuses if another conversation pushed or changed files
after preparation:

```bash
python scripts/production_release_guard.py prepublish
```

If a lease already exists, do not start another deployment and do not clear
another conversation's lease. Inspect it with:

```bash
python scripts/production_release_guard.py status
```

## Completion proof

`Deployment Started`, `Publishing...`, an enabled publish button, or a new
Emergent publish number is not success. Completion requires both:

1. Emergent visibly reports a newer `Deployment Succeeded`; and
2. this command succeeds three consecutive times internally and reports the
   same boot-time Git SHA and critical-file hashes prepared above:

```bash
python scripts/production_release_guard.py verify --url https://mezansalla.com
```

Do not perform any production financial write, advertising write, bulk send,
or other irreversible action until both checks pass. A failed deployment can
be released explicitly only by the owner of the prepared SHA:

```bash
python scripts/production_release_guard.py abort --expected-sha <full-sha>
```

Never use `git reset --hard` to satisfy this protocol. Preserve unrelated or
untracked Emergent files.
