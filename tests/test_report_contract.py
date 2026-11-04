"""
Контракт structured output: RESPONSE_SCHEMA валидирует эталонные ответы
(tests/fixtures — бывшие report_output.example.json), перечисления согласованы
с константами, helper response_format() отдаёт правильную обёртку.
"""
import json
from pathlib import Path

import jsonschema
import pytest
from src.core.prompts import (
    ANSWER_KINDS,
    BLOCKS,
    CHART_METRICS,
    PANEL_KINDS,
    POLARITIES,
    RESPONSE_SCHEMA,
    SLOTS,
    response_format,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "name", ["report_output.example.json", "report_output.qa.example.json"]
)
def test_fixtures_match_schema(name):
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    jsonschema.validate(data, RESPONSE_SCHEMA)


def test_schema_enums_built_from_constants():
    props = RESPONSE_SCHEMA["properties"]
    assert props["answer_kind"]["enum"] == ANSWER_KINDS
    assert props["panel"]["properties"]["kind"]["enum"] == PANEL_KINDS
    assert props["panel"]["properties"]["block"]["enum"] == [*BLOCKS, None]
    assert props["panel"]["properties"]["metric"]["enum"] == [*CHART_METRICS, None]
    facts_props = props["facts"]["items"]["properties"]
    assert facts_props["slot"]["enum"] == SLOTS
    assert facts_props["polarity"]["enum"] == POLARITIES


def test_schema_is_strict():
    # strict-совместимость: additionalProperties:false и required на всех уровнях
    assert RESPONSE_SCHEMA["additionalProperties"] is False
    for node in RESPONSE_SCHEMA["properties"].values():
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert "required" in node


def test_response_format_wrapper():
    fmt = response_format()
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] is RESPONSE_SCHEMA
