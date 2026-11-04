"""
База контрагентов: нормализация ИНН, поиск (list/JSON/файл), кэш по mtime,
компактный и полный payload, raise_on_missing.
"""
import asyncio
import json
import os

import pytest
from counteragents import (
    _extract_base_inn,
    _normalize_inns,
    build_counteragents_payload,
    get_counteragents_by_inn,
    short_company_info,
)


def _rec(inn, proceeds=None, year=2025):
    """Минимальная запись в форме снапшота: report.baseInfo.inn + finReports."""
    report = {"baseInfo": {"inn": inn}}
    if proceeds is not None:
        report["finReports"] = [{"common": {"year": year, "proceeds": proceeds}}]
    return {"report": report}


def _run(coro):
    return asyncio.run(coro)


# --- нормализация и извлечение ИНН -------------------------------------------

def test_normalize_none_empty():
    assert _normalize_inns(None) == []
    assert _normalize_inns("") == []


def test_normalize_single_and_dedup():
    assert _normalize_inns("7701") == ["7701"]
    assert _normalize_inns(7701) == ["7701"]
    assert _normalize_inns(["7701", " 7701 ", 7701, "7702"]) == ["7701", "7702"]


def test_extract_base_inn_tolerates_broken_structure():
    assert _extract_base_inn(_rec("123")) == "123"
    assert _extract_base_inn({}) == ""
    assert _extract_base_inn({"report": {"baseInfo": None}}) == ""
    assert _extract_base_inn(None) == ""


# --- поиск --------------------------------------------------------------------

DB = [_rec("111", 100), _rec("222"), _rec("333", 7)]


def test_search_in_list_source():
    found, missing = get_counteragents_by_inn(DB, ["222", "999", "111"])
    assert [r["report"]["baseInfo"]["inn"] for r in found] == ["222", "111"]
    assert missing == ["999"]


def test_search_json_string_source():
    found, missing = get_counteragents_by_inn(json.dumps(DB), "333")
    assert len(found) == 1
    assert missing == []


def test_search_single_object_json_wrapped_in_list():
    found, missing = get_counteragents_by_inn(json.dumps(DB[0]), ["111"])
    assert len(found) == 1 and missing == []


def test_search_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        get_counteragents_by_inn(tmp_path / "nope.json", ["111"])


# --- кэш по mtime -------------------------------------------------------------

def test_file_cache_reloads_on_mtime_change(tmp_path):
    db_file = tmp_path / "db.json"
    db_file.write_text(json.dumps([_rec("111")]), encoding="utf-8")

    found, missing = get_counteragents_by_inn(db_file, ["111"])
    assert len(found) == 1 and missing == []

    # перезапись без смены mtime → кэш не перечитывается: 222 ещё «не существует»
    old_ns = db_file.stat().st_mtime_ns
    db_file.write_text(json.dumps([_rec("222")]), encoding="utf-8")
    os.utime(db_file, ns=(old_ns, old_ns))
    found, missing = get_counteragents_by_inn(db_file, ["111", "222"])
    assert len(found) == 1 and missing == ["222"]

    # смена mtime → кэш перечитывается
    os.utime(db_file, ns=(old_ns + 1_000_000_000, old_ns + 1_000_000_000))
    found, missing = get_counteragents_by_inn(db_file, ["222"])
    assert len(found) == 1 and missing == []


# --- payload ------------------------------------------------------------------

def test_payload_compact_by_default_when_env_unset(monkeypatch):
    # По умолчанию (env не задан, full=None) отдаётся компактная сводка
    monkeypatch.delenv("COUNTERAGENTS_FULL_PAYLOAD", raising=False)
    payload = _run(build_counteragents_payload(DB, ["111"], full=None))
    assert payload["missing"] == []
    compact = payload["found"][0]
    assert compact["inn"] == "111" and "report" not in compact


def test_payload_full_on_demand(monkeypatch):
    monkeypatch.delenv("COUNTERAGENTS_FULL_PAYLOAD", raising=False)
    payload = _run(build_counteragents_payload(DB, ["111"], full=True))
    assert payload["found"][0] == DB[0]  # полная запись


def test_payload_compact(monkeypatch):
    monkeypatch.delenv("COUNTERAGENTS_FULL_PAYLOAD", raising=False)
    payload = _run(build_counteragents_payload(DB, ["111"], full=False))
    compact = payload["found"][0]
    assert compact["inn"] == "111"
    assert compact["latestProceeds"] == 100
    assert compact["latestProceedsYear"] == 2025
    assert "report" not in compact


def test_payload_full_payload_env(monkeypatch):
    monkeypatch.setenv("COUNTERAGENTS_FULL_PAYLOAD", "1")
    payload = _run(build_counteragents_payload(DB, ["111"], full=None))
    assert payload["found"][0] == DB[0]


def test_payload_missing_listed_and_raise_on_missing():
    payload = _run(build_counteragents_payload(DB, ["111", "777"]))
    assert payload["missing"] == ["777"]
    with pytest.raises(LookupError):
        _run(build_counteragents_payload(DB, ["777"], raise_on_missing=True))


def test_short_company_info_empty_report():
    info = short_company_info({"report": {}})
    assert info["inn"] is None and info["latestProceeds"] is None
