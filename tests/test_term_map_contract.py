import json
from pathlib import Path
from typing import Any

import pytest

from cueweaver.application.errors import ServiceError
from cueweaver.application.term_maps import (
    MAX_TERM_MAP_BYTES,
    MAX_TERM_MAP_UPLOAD_BYTES,
    canonical_term_map_bytes,
    validate_term_map_entries,
)
from cueweaver.http.term_maps import _decode_upload

CONTRACT = Path(__file__).parents[1] / "contracts" / "term-map-validation.json"
CONTRACT_DATA = json.loads(CONTRACT.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "text",
    [
        '{"Straße":"one","STRASSE":"two"}',
        '{"ſource":"one","source":"two"}',
        '{"ǰ":"one","ǰ":"two"}',
        '{"և":"one","եւ":"two"}',
    ],
)
def test_python_casefold_rejects_unicode_collisions(text: str):
    with pytest.raises(ServiceError, match="unique regardless of case"):
        validate_term_map_entries(_object_pairs(text))


def _case_text(case: dict[str, Any]) -> str:
    generated = case.get("generated")
    if not isinstance(generated, dict):
        return str(case["text"])
    return (
        '{"source":"'
        + "x" * int(generated["targetLength"])
        + '"'
        + " " * int(generated["rawPadding"])
        + "}"
    )


def _object_pairs(text: str) -> list[tuple[object, object]]:
    return json.loads(text, object_pairs_hook=list)


@pytest.mark.parametrize(
    "case",
    CONTRACT_DATA["cases"],
    ids=lambda case: case["name"],
)
def test_python_validator_matches_shared_term_map_contract(case: dict[str, Any]):
    expected = case.get("canonicalValid", case["valid"])
    try:
        content = validate_term_map_entries(_object_pairs(_case_text(case)))
    except (ServiceError, json.JSONDecodeError, TypeError, ValueError):
        assert expected is False
        return

    assert expected is True
    assert len(canonical_term_map_bytes(content)) <= MAX_TERM_MAP_BYTES


@pytest.mark.parametrize(
    "case",
    [case for case in CONTRACT_DATA["cases"] if "generated" in case],
    ids=lambda case: case["name"],
)
def test_raw_content_limit_is_separate_from_canonical_limit(case: dict[str, Any]):
    raw_content = _case_text(case)
    pairs, raw_size = _decode_upload(
        ('{"name":"Terms","content":' + raw_content + "}").encode("utf-8")
    )

    assert isinstance(pairs, list)
    assert (raw_size <= MAX_TERM_MAP_UPLOAD_BYTES) is case.get(
        "rawValid", case["valid"]
    )
