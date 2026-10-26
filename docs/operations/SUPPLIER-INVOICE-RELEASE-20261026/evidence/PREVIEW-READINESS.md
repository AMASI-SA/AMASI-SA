# Preview supplier update — coordinated read-only checkpoint

Date: 2026-10-26. Owner: task 01a178e8-c96b-7ea2-840d-8b137025159c.

## Verified

- Fresh guard inactive. Fetch succeeded; local HEAD and origin/hotfix/prod-snap-meta-final both 3a23b587a218f2b190cf1913e3b0b1d22c175cbd.
- Approved source: 761a8f330cb3bfcadbd430e6ca9a954008dbe9de. PR1551 excluded; category requirement remains.
- Backend supervisor runs /app/.worktrees/mz2-supplier-editor-fix-20261025/serve.py backend. Frontend runs /app/frontend/scripts/start-governed-runtime.cjs from /app/frontend.
- Backend localhost health 200; unauthenticated auth/me 401. No session/cookie was read or sent.
- Frontend build metadata source is approved source, but environment contains the SHA256 of Production origin, not Preview origin. Compiled JS contains Production origin and not Preview origin. This is configuration evidence, not a captured user-session request trace.
- Old isolated frontend preview-meta source: a3520ad0cb0ddaa39678068f12ef50c3a822d476.
- auth.py, mfa_security.py, email_otp_policy.py, email_otp_security.py, passkey_security.py match approved source byte for byte.
- Bundle manifest: 4535 entries; SHA256 90b21949d5d05ffcde4060e0cdebee10b5b3eb9a8c8cd1f5e070f5086d8d6413.
- /root and /app share /dev/nvme0n14: 9.8G total, 9.5G used, 330M available (97%). Building under /root would still use the constrained Emergent disk.

## Proposed plan, not executed

Build approved source outside Emergent with pinned Node 22.23.2/Yarn 1.22.22, direct Vite preview build and Preview API origin. Do not use the Production build dispatcher or claim Production identity. Assess final transfer size before staging runtime.

Candidate runtime /root/mezan-preview-supplier-20261026 does not currently exist. Preserve old runtime. Copy existing session key and training configuration internally with private permissions; never print or commit values. Change adapter ROOT and serve R for new location, preserve hostname/origin/egress/startup/password policies. Auth hashes need no changes because files are identical. Recompute bundle manifest and adapter digest and create truthful Preview metadata after build. Review server startup/browser middleware compatibility before any restart.

Never call adapter activate: it creates a fresh session key. No supervisor change or restart until owner supplies reviewed instructions. Keep old supervisor sections for rollback; rollback must not modify database or keys.

## Boundaries

No source modifications, build, clone, service restart, database write, financial/material writer change, secret disclosure, Production publish, or APK change performed in this inspection. Only Git fetch updated remote tracking metadata. No deletion. Current Preview update remains incomplete.

## Additional read-only sizing and compatibility

- browser_security.py identical. server.py has 33 diff lines: adds operational push/instruction-comment routers, starts payment inbox and operational push workers in the normal startup routine, and cancels them during shutdown. Existing isolated startup replaces the startup list; import-time effects still need review before switching.
- Old source disk usage 85M, frontend build 7.6M, complete bundle 94M, artifact directory 8K. Overlapping du arguments initially excluded subdirectory counts from the bundle row; separate du confirmed 94M total.
- Approved source has 4685 Git blobs, 80,517,852 content bytes. Approximate tar size 84,127,232 bytes before directory/PAX overhead. Including old frontend build gives 92,047,360 bytes; new build size is not yet known.
- /dev/shm has only 64M free and cannot hold the expanded package. /root free space measured 329M.
- Manifest is 4535 string path to string digest entries (4516 source, 19 frontend), not a metadata schema.
- Preview metadata states environment preview, API origin Preview, automatic workers disabled, external network denied, production release identity false. Old index SHA256 84e64dc5acc459552a1e45e3a808ec4c1d6038a92aad50bf5f1f7595a60ccfca.
- Explorer folder context menu exposes Upload...; menu closed without opening chooser or transferring a file. Documented filechooser API supports absolute local paths, but successful transfer is not yet demonstrated. Wait for owner-provided artifact path/hash and staging instructions.
