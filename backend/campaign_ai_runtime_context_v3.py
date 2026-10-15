"""Async-task-local runtime context for Decision Intelligence V3.

The established V2 monitor calls the OpenAI boundary with candidates/history but
without passing db/user_id.  ContextVar lets the additive V3 wrapper access the
same tenant database safely without process-global mutable state, including when
multiple tenant monitor tasks coexist in one worker process.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DecisionRuntimeContextV3:
    db: Any
    user_id: str


_CONTEXT: ContextVar[DecisionRuntimeContextV3 | None] = ContextVar(
    "campaign_ai_decision_runtime_v3",
    default=None,
)


def set_runtime_context(db: Any, user_id: str) -> Token:
    return _CONTEXT.set(DecisionRuntimeContextV3(db=db, user_id=str(user_id)))


def reset_runtime_context(token: Token) -> None:
    _CONTEXT.reset(token)


def get_runtime_context() -> DecisionRuntimeContextV3:
    value = _CONTEXT.get()
    if value is None:
        raise RuntimeError("campaign_ai_v3_runtime_context_missing")
    return value


__all__ = [
    "DecisionRuntimeContextV3",
    "get_runtime_context",
    "reset_runtime_context",
    "set_runtime_context",
]
