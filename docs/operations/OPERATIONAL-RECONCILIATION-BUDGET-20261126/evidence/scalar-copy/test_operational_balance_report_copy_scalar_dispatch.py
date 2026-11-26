"""Atomic leaves need no recursive dispatch; copy semantics stay unchanged."""
import cProfile
from copy import deepcopy
import pstats

import pytest

import operational_report_copy as copying


@pytest.mark.parametrize("container", [list, dict])
def test_flat_atomic_financial_values_are_copied_without_leaf_recursion(container):
    leaves = ["100.01", 7, 0.25, True, b"retained", None] * 200
    original = leaves if container is list else {str(i): value for i, value in enumerate(leaves)}
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        result = copying.copy_report_value(original)
    finally:
        profiler.disable()
    assert result == deepcopy(original) and result is not original
    recursive_calls = sum(total for (filename, _line, name), (_primitive, total, *_rest)
                          in pstats.Stats(profiler).stats.items()
                          if filename == copying.__file__ and name == "clone")
    # A complete mutable root still needs one independent copy. Immutable
    # leaves have no hooks or aliases requiring a second recursive dispatch.
    assert recursive_calls == 1
    if container is list:
        result.append("changed output")
        assert len(original) == 1200
    else:
        result["0"] = "changed output"
        assert original["0"] == "100.01"


def test_key_hook_cannot_retroactively_change_already_evaluated_atomic_value():
    atom = "original-financial-leaf"

    class Key:
        def __deepcopy__(self, memo):
            memo[id(atom)] = "later-copy-leaf"
            return "copied-key"

    source = {Key(): atom, "later": [atom]}
    expected = {"copied-key": atom, "later": ["later-copy-leaf"]}
    assert copying.copy_report_value(source) == deepcopy(source) == expected
    assert list(source.values()) == [atom, [atom]]


def test_value_hook_is_observed_before_its_atomic_dictionary_key():
    key = "original-financial-key"

    class Value:
        def __deepcopy__(self, memo):
            memo[id(key)] = "copied-financial-key"
            return "copied-value"

    source = {key: Value(), "later": {key: key}}
    expected = {"copied-financial-key": "copied-value",
                "later": {"copied-financial-key": "copied-financial-key"}}
    assert copying.copy_report_value(source) == deepcopy(source) == expected
    assert source["later"] == {key: key}
