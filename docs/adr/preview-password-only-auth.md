# Preview password-only authentication

The design Preview may skip MFA and email OTP only when all runtime guards are
present:

```text
AUTH_PREVIEW_PASSWORD_ONLY=true
MEZAN_ENVIRONMENT=preview
AUTH_PREVIEW_TRUST_PROXY=true  # only when the Preview gateway rewrites Host
Host: salla-analytics.preview.emergentagent.com
```

`AUTH_PREVIEW_ALLOWED_HOSTS` can replace the default exact host allow-list when
the Preview hostname changes. It must contain exact comma-separated hostnames;
wildcards are not supported.

When `AUTH_PREVIEW_TRUST_PROXY` is enabled, the application accepts the exact
allow-listed `X-Forwarded-Host` only if the immediate peer is private or
loopback. A public client cannot enable Preview mode by supplying a forwarded
header.

The canonical login route still validates the password, disabled-account
state, and the existing abuse/rate-limit guards. The Preview session receives
an `mfa=true` claim so normal authorization keeps working, but the user record,
MFA enrollment state, and OTP policy are never changed. Every bypass is written
to the authentication audit log and responses include
`X-Mezan-Auth-Mode: preview-password-only`.

Production must use `MEZAN_ENVIRONMENT=production` and must not set
`AUTH_PREVIEW_PASSWORD_ONLY` or `AUTH_PREVIEW_TRUST_PROXY`. Even if one setting
is copied accidentally, the remaining environment and exact-host guards keep
the bypass closed.
