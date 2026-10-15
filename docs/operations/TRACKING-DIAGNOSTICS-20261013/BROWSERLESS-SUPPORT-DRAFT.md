# Sent technical support inquiry

Recipient: support@browserless.io (official Browserless contact page)
Subject: Hosted Function launch arguments and public-only egress

Hello Browserless support,

We are integrating your hosted Function API for bounded, read-only inspection of public storefront product pages. We send no customer data and return no media, raw HTML, cookies, or recordings.

On https://production-sfo.browserless.io/function, a URL-encoded launch JSON with args ["--host-resolver-rules=MAP * ^NOTFOUND"], record:false and replay:false was accepted. Inside the Function, Browser.getBrowserCommandLine was available, but that exact argument was absent, and an attempt to navigate to our public storefront did not produce ERR_NAME_NOT_RESOLVED. The test blocked all subresources and did not target private networks.

Does this hosted endpoint support custom resolver arguments? What supported configuration enforces public-only egress, including DNS changes, redirects, subresources and WebSockets, for different storefront domains? Does allowedDomains cover all of those cases? Please clarify the existing-plan capabilities; do not change the plan, enable paid features, or provision resources.

Thank you.

No API key, account identifier, campaign metrics or customer data is included. Sent with explicit owner authorization; Gmail SENT verified. See Issue #1006 comment 6153552947. No response verified.
