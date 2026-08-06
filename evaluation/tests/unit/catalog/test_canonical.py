from __future__ import annotations

import math

import pytest
from memrelay_eval.catalog.canonical import (
    CanonicalizationError,
    attach_digest,
    canonical_bytes,
    canonical_digest,
    verify_digest,
)


def test_rfc8785_official_number_and_string_vector() -> None:
    value = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27],
        "string": '\u20ac$\u000f\nA\'B"\\"/',
        "literals": [None, True, False],
    }

    assert canonical_bytes(value) == (
        b'{"literals":[null,true,false],"numbers":['
        b"333333333.3333333,1e+30,4.5,0.002,1e-27],"
        b'"string":"\xe2\x82\xac$\\u000f\\nA\'B\\"\\\\\\"/"}'
    )


def test_rfc8785_uses_utf16_property_order_and_ecmascript_number_boundaries() -> None:
    value = {"\ue000": "private-use", "\U0001f600": "astral", "number": 1e20}

    assert canonical_bytes(value) == (
        '{"number":100000000000000000000,"😀":"astral","\ue000":"private-use"}'.encode()
    )


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        -float("inf"),
        math.nan,
        {"not": {"a", "json", "value"}},
        {1: "non-string key"},
    ],
)
def test_canonicalizer_rejects_non_finite_and_unsupported_values(value: object) -> None:
    with pytest.raises(CanonicalizationError):
        canonical_bytes(value)


def test_digest_omits_only_its_declared_field_and_is_lowercase() -> None:
    source = {"payload": {"digest": "nested-value", "value": 1}, "sha256": "must-remain"}
    attached = attach_digest(source)

    assert attached["digest"] == canonical_digest(attached)
    assert attached["payload"] == {"digest": "nested-value", "value": 1}
    assert attached["sha256"] == "must-remain"
    assert verify_digest(attached)
    assert attached["digest"] == attached["digest"].lower()

    changed = {**attached, "digest": "A" * 64}
    assert not verify_digest(changed)


def test_digest_rejects_the_correct_value_when_only_its_case_changes() -> None:
    attached = attach_digest({"payload": "case-sensitive identity"})

    assert any(character.isalpha() for character in attached["digest"])
    uppercase = {
        **attached,
        "digest": "".join(
            character.upper() if character.isalpha() else character
            for character in attached["digest"]
        ),
    }

    assert uppercase["digest"].lower() == attached["digest"]
    assert not verify_digest(uppercase)
