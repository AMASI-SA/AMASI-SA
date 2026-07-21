# Mezan MCP Gateway

`Mezan MCP Gateway` is a private, tool-only MCP resource server for interactive
ChatGPT diagnostics. It does not use `OPENAI_API_KEY`, run an autonomous agent,
or change the existing Mezan, Salla, or Qoyod write paths.

## Endpoint and protocol

- Streamable HTTP endpoint: `/api/ai/mcp`
- Production URL: `https://mezansalla.com/api/ai/mcp`
- Protected-resource metadata:
  `/.well-known/oauth-protected-resource`
- Ingress-safe metadata alias:
  `/api/.well-known/oauth-protected-resource`
- Transport methods: `POST` and `OPTIONS` only. `GET` and `DELETE` return 405.
- All phase-one tools declare `readOnlyHint=true`.

## Phase-one tools

- `mezan_health`
- `mezan_get_system_status`
- `mezan_get_order`
- `mezan_compare_order_with_salla`
- `mezan_get_error_trace`
- `mezan_list_recent_failures`
- `mezan_qoyod_reconciliation`
- `mezan_get_database_schema`

## Security boundary

- OAuth 2.1 bearer tokens are validated using an external identity provider.
  Mezan browser-session tokens and static API keys are not accepted.
- The identity provider must support Authorization Code with PKCE S256 and
  issue an audience-restricted access token containing the `mezan:read` scope
  and a tenant claim.
- Mongo access is through an allowlisted read-only facade. Mutation methods
  throw before reaching Motor. Arbitrary SQL/Mongo queries are not exposed.
- Salla comparisons use one direct HTTPS `GET`. The gateway never refreshes or
  persists a Salla token and never calls a Salla mutation endpoint.
- Qoyod reconciliation reads local Mezan collections only. It does not create a
  Qoyod HTTP client or call create, send, retry, replay, update, or delete.
- Customer phone, email, address, coordinates, tokens, credentials, database
  URLs, and sensitive keys are removed from tool output and error text.
- Audit logs contain request id, tool, outcome, duration, and hashed identities;
  they never contain tool arguments, raw tenant ids, or OAuth subjects.
- A process-local rate limiter is enforced. Production must also enforce a
  shared rate limit at the reverse proxy/WAF when running multiple workers.

## Required secret configuration

Configure these values in the deployment secret manager. Never commit them or
paste them into ChatGPT:

```text
MEZAN_MCP_OAUTH_ISSUER
MEZAN_MCP_OAUTH_AUDIENCE
MEZAN_MCP_OAUTH_JWKS_URL
MEZAN_MCP_REQUIRED_SCOPE=mezan:read
MEZAN_MCP_TENANT_CLAIM=mezan_tenant_id
MEZAN_MCP_PUBLIC_BASE_URL
MEZAN_MCP_RESOURCE_URL
MEZAN_MCP_METADATA_URL
MEZAN_MCP_RATE_LIMIT_PER_MINUTE=60
```

Production uses these non-secret endpoint values:

```text
MEZAN_MCP_PUBLIC_BASE_URL=https://mezansalla.com
MEZAN_MCP_RESOURCE_URL=https://mezansalla.com/api/ai/mcp
MEZAN_MCP_OAUTH_AUDIENCE=https://mezansalla.com/api/ai/mcp
MEZAN_MCP_METADATA_URL=https://mezansalla.com/api/.well-known/oauth-protected-resource
```

The audience and resource must be exact. Copy the Auth0 issuer exactly as it
appears in its discovery document, including its trailing slash. The tenant
claim value must identify the same store/tenant used by Mezan's database
filters. A namespaced claim such as `https://mezansalla.com/tenant_id` is
recommended when the identity provider requires it.

## OAuth provider contract

Configure a separate OAuth resource/API for Production. The provider may be
Auth0, Okta, Cognito, or another OAuth 2.1 implementation, but
it must publish its discovery document, support Authorization Code with PKCE
S256, accept the MCP `resource` parameter, and issue an audience-restricted
token with only `mezan:read` plus the configured tenant claim. If the provider
requires namespaced custom claims, set `MEZAN_MCP_TENANT_CLAIM` to that exact
claim name.

The provider/client choice must use one of ChatGPT's supported registration
methods: CIMD, DCR, or a predefined OAuth client. Store any predefined client
secret only in the relevant platform secret manager. Do not place it in Mezan,
GitHub, ChatGPT messages, command arguments, or logs.

Preview can validate transport, metadata JSON, static security tests, and the
unauthenticated 401 challenge without Production data or Production OAuth
credentials. Final OAuth, tenant isolation, and data checks must run against
Production after explicit authorization; never connect Preview to the
Production database merely to complete these checks.

## Release gates

Before enabling the private ChatGPT connection in Production, verify:

1. Protected-resource metadata advertises the correct Production resource and
   authorization server.
2. ChatGPT completes OAuth and discovers exactly eight tools.
3. `mezan_health` succeeds.
4. One Production-safe order view contains no unnecessary customer PII.
5. The same order can be compared with Salla through GET-only access.
6. A sanitized trace and recent failures can be read.
7. Qoyod reconciliation reads local records and performs no Qoyod network call.
8. Security tests prove Mongo/Salla/Qoyod mutation paths are unavailable.
9. Existing Qoyod send behavior and financial data are unchanged.

Run the repository verifier from a trusted Production runner. Put the short-lived
access token in an environment secret; the verifier never prints it or any
tool payload:

```bash
export MEZAN_MCP_BEARER_TOKEN='set-in-the-runner-secret-manager'
python scripts/verify_mezan_mcp_gateway.py \
  https://mezansalla.com/api/ai/mcp \
  --order-number SAFE_TEST_ORDER_NUMBER
```

The verifier checks public protected-resource metadata, the unauthenticated
OAuth challenge, MCP initialization, the exact eight read-only tools,
`mezan_health`, and—when an order number is provided—the order view, Salla
comparison, trace, and local-only Qoyod reconciliation. It reports pass/fail
only and deliberately does not print returned order data.

Enable the ChatGPT connection only after these gates pass. Rollback is the
normal application rollback to the previous build; this change has no database
migration or stored-data rollback.

## ChatGPT connection

Create a private tool-only app using the environment MCP URL. Configure OAuth
with the same external identity provider and request only `mezan:read`. GitHub
access remains a separate ChatGPT connection; no GitHub secret belongs in this
gateway.
