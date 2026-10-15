"""Authenticated, database-free runtime diagnostics endpoints."""
from __future__ import annotations

import hmac
import os
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request, Response

from runtime_diagnostics import diagnostics


def attach_diagnostics_routes(
    app: FastAPI,
    *,
    mongo_client: Any,
    state: Callable[[], dict[str, str]],
) -> None:
    async def handler(request: Request, response: Response):
        configured = os.environ.get("INTERNAL_DIAGNOSTICS_TOKEN", "")
        supplied = request.headers.get("X-Mezan-Diagnostics-Token", "")
        if not configured or not supplied or not hmac.compare_digest(configured, supplied):
            raise HTTPException(status_code=403, detail="diagnostics_forbidden")
        response.headers["Cache-Control"] = "no-store"
        return {**state(), **diagnostics(mongo_client=mongo_client)}

    app.add_api_route(
        "/health/diagnostics", handler, methods=["GET"], include_in_schema=False,
    )
    app.add_api_route(
        "/api/health/diagnostics", handler, methods=["GET"], include_in_schema=False,
    )


__all__ = ["attach_diagnostics_routes"]
