# Mezan AI asynchronous analysis

The production UI keeps the existing `POST /api/ai/analyze` contract, while the HTTP transport rewrites that request to a timeout-safe background flow:

1. `POST /api/ai/analyze-async` returns `202` and a `run_id` immediately.
2. The OpenAI call runs after the response is sent.
3. `GET /api/ai/analyze-async/{run_id}` returns `queued`, `running`, `complete`, or `failed`.
4. The frontend polls with short authenticated requests and converts the completed job back to the original analyzer response shape.

Jobs are scoped to the authenticated user, persisted in MongoDB, contain no raw customer payload or credentials, and remain read-only. Repeated clicks reuse an active job instead of creating duplicate OpenAI usage.
