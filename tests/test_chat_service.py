"""
Логика ответа агента: основной путь (structured_response), фолбэки
(markdown-фенс, проза), извлечение ИНН из tool-сообщений, изоляция тредов.
root_agent подменяется фейком — LLM и MCP не нужны.
"""
import asyncio
import json
from types import SimpleNamespace

import src.chat_service as cs


class FakeAgent:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def ainvoke(self, payload, config=None):
        self.calls.append({"payload": payload, "config": config})
        return self.result


def _tool_msg(found_inns, missing):
    content = json.dumps({
        "found": [{"report": {"baseInfo": {"inn": inn}}} for inn in found_inns],
        "missing": missing,
    }, ensure_ascii=False)
    return SimpleNamespace(type="tool", content=content)


def _ai_msg(text):
    return SimpleNamespace(type="ai", content=text)


STRUCTURED = {
    "reply_text": "",
    "brief": ["абзац"],
    "answer_kind": "summary",
    "primary_inn": None,
    "panel": {"kind": "card", "inn": None, "block": None, "metric": None},
    "suggestions": [],
}


def test_structured_response_and_fetched(monkeypatch):
    fake = FakeAgent({
        "structured_response": dict(STRUCTURED),
        "messages": [_tool_msg(["111", "222"], ["999"])],
    })
    monkeypatch.setattr(cs, "root_agent", fake)

    turn, source = asyncio.run(cs.agent_turn("проверь 111", {"inns": ["111"], "sid": "s-1"}))

    assert source == "agent"
    assert turn["primary_inn"] == "111"          # подтянут из _fetched
    assert turn["panel"]["inn"] == "111"          # card-панель тоже
    assert turn["_fetched"] == ["111", "222"]
    assert turn["_missing"] == ["999"]
    # thread_id = sid сессии
    assert fake.calls[0]["config"]["configurable"]["thread_id"] == "s-1"
    # контекст сессии и вопрос попали в сообщение
    user_text = fake.calls[0]["payload"]["messages"][0].content
    assert "111" in user_text and "проверь 111" in user_text


def test_fallback_json_in_markdown_fence(monkeypatch):
    payload = {"reply_text": "готово", "brief": [], "answer_kind": "qa"}
    fake = FakeAgent({
        "messages": [_tool_msg(["111"], []), _ai_msg(f"```json\n{json.dumps(payload)}\n```")],
    })
    monkeypatch.setattr(cs, "root_agent", fake)

    turn, _ = asyncio.run(cs.agent_turn("вопрос", {"sid": "s-2"}))
    assert turn["answer_kind"] == "qa"
    assert turn["reply_text"] == "готово"
    assert turn["_fetched"] == ["111"]


def test_fallback_prose_becomes_qa_turn(monkeypatch):
    fake = FakeAgent({"messages": [_ai_msg("Погодите, данных нет.")]})
    monkeypatch.setattr(cs, "root_agent", fake)

    turn, _ = asyncio.run(cs.agent_turn("вопрос", {"sid": "s-3"}))
    assert turn["answer_kind"] == "qa"
    assert turn["reply_text"] == "Погодите, данных нет."
    assert turn["_fetched"] == []


def test_no_sid_gets_unique_thread(monkeypatch):
    fake = FakeAgent({"structured_response": dict(STRUCTURED), "messages": []})
    monkeypatch.setattr(cs, "root_agent", fake)

    asyncio.run(cs.agent_turn("раз", {}))
    asyncio.run(cs.agent_turn("два", {}))
    t1 = fake.calls[0]["config"]["configurable"]["thread_id"]
    t2 = fake.calls[1]["config"]["configurable"]["thread_id"]
    # одноразовые uuid вместо общего "anonymous": чужая память не подмешается
    assert t1 != t2 and t1 != "anonymous"


def test_extract_json_variants():
    assert cs._extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert cs._extract_json('преамбула {"a": 1} хвост') == '{"a": 1}'
    assert cs._extract_json("без объекта") == "без объекта"
