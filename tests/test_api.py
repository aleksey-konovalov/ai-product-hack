"""
API-контракт: /health, /chat (happy path, legacy-путь, ошибки валидации),
lifespan (закрытие MCP-клиента при остановке) — через TestClient с подменённым
root_agent, без реальных LLM и MCP.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import src.app as app_module
import src.chat_service as cs


class FakeAgent:
    def __init__(self, result):
        self.result = result

    async def ainvoke(self, payload, config=None):
        return self.result


def _structured_result():
    return {
        "structured_response": {
            "reply_text": "",
            "brief": ["абзац"],
            "answer_kind": "summary",
            "primary_inn": None,
            "panel": {"kind": "card", "inn": None, "block": None, "metric": None},
            "suggestions": ["Что с финансами?"],
        },
        "messages": [SimpleNamespace(type="tool", content=json.dumps({
            "found": [{"report": {"baseInfo": {"inn": "111"}}}],
            "missing": [],
        }))],
    }


@pytest.fixture()
def client():
    # with — прогоняет lifespan: проверяем, что shutdown не падает без MCP-клиента
    with TestClient(app_module.app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["agent_initialized"] is False
    assert body["profile"] == "api_glm_flash_agent"
    assert body["model"] == "glm-5.3-flash"


def test_chat_happy_path(client, monkeypatch):
    monkeypatch.setattr(cs, "root_agent", FakeAgent(_structured_result()))
    resp = client.post("/chat", json={
        "message": "проверь 111",
        "session": {"inns": ["111"], "sid": "s-1"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["turn"]["answer_kind"] == "summary"
    assert body["validation"]["fetched"] == ["111"]
    assert body["validation"]["missing"] == []


def test_chat_legacy_payload_without_session(client, monkeypatch):
    # Старый клиент шлёт analyze_results вместо session — extra="allow" сохраняет поля
    monkeypatch.setattr(cs, "root_agent", FakeAgent(_structured_result()))
    resp = client.post("/chat", json={
        "message": "что скажешь",
        "primary_inn": "111",
        "analyze_results": {"111": {"assessment_given": True, "deal_context": {"goal": "поставка"}}},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


@pytest.mark.parametrize("payload", [
    {},                                  # нет message
    {"message": 5},                      # не строка
    {"message": None},
])
def test_chat_validation_errors_are_400_with_ok_false(client, payload):
    resp = client.post("/chat", json=payload)
    assert resp.status_code == 400
    assert resp.json()["ok"] is False


def test_chat_agent_error_masked(client, monkeypatch):
    class Broken:
        async def ainvoke(self, payload, config=None):
            raise RuntimeError("secret internals")

    monkeypatch.setattr(cs, "root_agent", Broken())
    resp = client.post("/chat", json={"message": "x", "session": {"sid": "s"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    # наружу — общий текст, детали только в лог
    assert "secret internals" not in body["error"]
    assert body["error"]


def test_request_id_roundtrip(client):
    resp = client.get("/health", headers={"X-Request-ID": "test-rid-42"})
    assert resp.headers.get("X-Request-ID") == "test-rid-42"

    resp2 = client.get("/health")
    rid = resp2.headers.get("X-Request-ID")
    assert rid and rid != "test-rid-42"   # сгенерирован на сервере
