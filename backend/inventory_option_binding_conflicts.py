"""Read-only checks before classifying an option as customer side information."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def informational_option_service_conflicts(
    *,
    informational_option_ids: Iterable[Any],
    bindings: Iterable[Mapping[str, Any]],
    resources_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Find existing option services that contradict a no-supplier-work role.

    The caller supplies owner/product-scoped option-cost bindings and
    owner-scoped resources. Every bound value is checked because classification
    applies to the whole option, not only a customer's current selection.

    Supplier service inheritance currently uses every existing resource link
    whose kind is ``service``. It does not filter inactive resources or require
    a ``requires_preparation`` flag. Keep those links visible as conflicts;
    stopping a resource only prevents new links. Direct costs and component
    links do not create supplier service conflicts and are never changed here.
    """
    option_ids = {_text(value) for value in informational_option_ids if _text(value)}
    conflicts: dict[tuple[str, str, str], dict[str, str]] = {}
    for binding in bindings:
        option_id = _text(binding.get("option_id"))
        if option_id not in option_ids or _text(binding.get("mode")) != "resource":
            continue
        resource_id = _text(binding.get("resource_id"))
        resource = resources_by_id.get(resource_id) or {}
        if _text(resource.get("kind")).casefold() != "service":
            continue
        value_id = _text(binding.get("value_id"))
        key = (option_id, value_id, resource_id)
        conflicts.setdefault(key, {
            "option_id": option_id,
            "value_id": value_id,
            "resource_id": resource_id,
            "resource_name": _text(resource.get("name")) or resource_id,
        })
    return [conflicts[key] for key in sorted(conflicts)]
