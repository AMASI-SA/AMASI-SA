# External browser connection increment

Browserless Function API is the candidate for a limited connectivity pilot.
It accepts fixed Puppeteer code remotely and closes the REST session afterward.
This avoids installing Chromium on Mezan. No service account or token is
available to this task, so no live request has been made.

Implemented: `backend/external_browser_connection.py` and the operator command
`scripts/check_external_browser_connection.py`. The command reads only
`BROWSERLESS_API_TOKEN` from secure runtime configuration. Do not pass a token
on the command line or paste it in chat, GitHub, screenshots, or logs.

The probe sends static reviewed code and a random nonce to the fixed official
HTTPS endpoint. It evaluates the blank page without navigating. Authentication
is in the Authorization header, never the URL. A single request has a total
35-second deadline and 8 KiB JSON response limit; redirects and automatic
retries are disabled. Error bodies/exceptions are not printed or returned.
The CLI exits zero only for `connection_verified`, always accompanied by
`storefront_checked: false`. It is not imported by a production route.

After the account token is installed through secure settings, run once:

```bash
python scripts/check_external_browser_connection.py
```

No storefront URL, campaign metrics, customer data, cookies, media, screenshots,
or model request is part of this connection test. Transport fixtures passed;
provider behavior and header authentication have not been tested live.
The documented data/type envelope and direct JSON data form are accepted;
all other shapes fail closed. No unrestricted code/URL execution API is exposed.

## Activation is still blocked

Account access is the next owner action, not approval to buy a paid plan.
Browserless currently documents a free plan; do not upgrade or enable overages
automatically. Provisioning must remain in the owner's account.

A working token or blank-page result does not prove a safe collector. Before
any real page visit, verify hosted private-network/DNS-rebinding protection,
subresource and WebSocket coverage, and recording/retention settings. The
enterprise Docker blocklist documentation does not establish the hosted
service's complete isolation contract. Do not disable it, use stealth/unblock,
solve access challenges, or use a self-hosted root browser as a shortcut.

Still unimplemented: resource-bounded page collector, telemetry suppression,
scoped job routes/persistence/cancellation, live competitor search and offer
collection, native bindings for all platforms, and real recommendation
integration with verified citations. Existing research status stays
`not_connected`. No media is downloaded or stored by the connection probe.
No production release, merge, or live model call in this increment.

## Evidence and sources

- https://docs.browserless.io/rest-apis/function
- https://docs.browserless.io/open-api/chromium-function
- https://docs.browserless.io/rest-apis/scrapers (Bearer header format)
- https://docs.browserless.io/enterprise/docker/config (self-hosted blocklist;
  not proof of hosted isolation)
- https://www.browserless.io/pricing
- https://docs.browserless.io/overview/unit-consumption

Reviewed 2026-10-13. 18 focused transport tests, 93 affected backend tests passed.
The initial test collection failed because the new module did not yet exist.
No frontend files changed; prior frontend results were not rerun or relabeled.

## Live account checkpoint (2026-10-13 after 18:52 Riyadh)

Owner completed signup. Browserless Home and Logout were visible in the cloud
browser. One /function execution in the signed-in REST API Playground returned
`data.protocol=mezan-browser-probe-v1`, `blank_page=true`,
`storefront_checked=false`, with the documented data/type envelope. Static code
only evaluated `location.href` on the initial page; no navigation, model,
customer, or campaign payload. No API key was revealed or copied.

This supersedes the earlier absence of a service account/live request. It does
not verify the backend probe, header authentication, nonce path, or runtime
secret configuration. Public-only egress, recording policy, actual collector and
end-to-end recommendation integration remain unverified/unimplemented as above.
Production did not change.
