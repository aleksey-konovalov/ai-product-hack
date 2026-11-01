"""
Логика ответа агента: сборка контекста сессии, вызов root_agent,
извлечение structured output и фолбэки, когда модель вернула прозу вместо JSON.

Выделено из web_agent.py при разделении UI и агента: транспорт живёт в app.py,
здесь только серверная логика. Алгоритм перенесён без изменений, кроме
async-сигнатур (раньше каждый запрос крутил свой event loop через asyncio.run).
"""
import json
import logging
import re
import uuid

from langchain_core.messages import HumanMessage

from .core.agent import root_agent

log = logging.getLogger("agent.chat")


def _extract_json(s: str) -> str:
    """Вытаскивает первый JSON-объект из текста (срезает markdown-фенс и прозу вокруг)."""
    s = (s or "").strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.DOTALL)
        if m:
            return m.group(1)
    i, j = s.find("{"), s.rfind("}")
    return s[i:j + 1] if i >= 0 and j > i else s


def _extracted_inns(result):
    """ИНН, реально полученные агентом через MCP (из tool-сообщений). Нужны фронту:
    по ним он добавляет компании в сессию и открывает подробный отчёт (validation.fetched)."""
    fetched, missing = [], []
    msgs = result.get("messages", []) if isinstance(result, dict) else []
    for m in msgs:
        if getattr(m, "type", "") != "tool":
            continue
        content = getattr(m, "content", None)
        if not isinstance(content, str):
            content = "".join(x.get("text", "") for x in content if isinstance(x, dict)) if isinstance(content, list) else ""
        try:
            data = json.loads(content)
        except (ValueError, TypeError):
            continue
        for rec in (data.get("found") or []):
            rep = rec.get("report") if isinstance(rec, dict) and "report" in rec else rec
            inn = str(((rep or {}).get("baseInfo") or {}).get("inn") or "")
            if inn and inn not in fetched:
                fetched.append(inn)
        for mi in (data.get("missing") or []):
            if str(mi) not in missing:
                missing.append(str(mi))
    return fetched, missing


def _session_ctx(session: dict) -> str:
    """Формируем контекст сессии для агента."""
    inns = [str(i) for i in (session.get("inns") or [])]
    selected = session.get("selected")
    parts = []
    if inns:
        parts.append("В сессии ИНН: " + ", ".join(inns) + ".")
    if selected:
        parts.append(f"Текущая компания сессии — ИНН {selected}.")
    return " ".join(parts) or "Сессия пуста."


async def _agent_invoke(message: str, session: dict, thread_id: str = "main"):
    """Вызываем root_agent с контекстом сессии."""
    ctx = _session_ctx(session)
    user_input = (
        f"Сессия пользователя: {ctx}\n\n"
        f"Вопрос пользователя: {message}"
    )
    return await root_agent.ainvoke(
        {
            "messages": [HumanMessage(content=user_input)],
        },
        config={"configurable": {"thread_id": thread_id}},  # thread_id уникален для сессии
    )


async def agent_turn(message: str, session: dict):
    """Полный ход агента: invoke → structured output → фолбэки."""
    log.info("ЗАПРОС: message=%r | сессия: %s", message, _session_ctx(session))

    # thread_id = идентификатор сессии с фронта (sid). Новая/очищенная сессия → новый sid →
    # свежая память агента. Иначе пустые сессии делили общий тред "anonymous" и агент
    # «вспоминал» чужой прошлый разбор вместо свежего анализа. Запрос без sid получает
    # одноразовый uuid: чужая память не подмешается, а накапливать ей нечего.
    sid = session.get("sid")
    thread_id = str(sid) if sid else uuid.uuid4().hex

    result = await _agent_invoke(message, session, thread_id)

    # Основной путь: строгий structured output от агента (response_format=ToolStrategy).
    turn = None
    content = ""
    sr = result.get("structured_response") if isinstance(result, dict) else None
    if isinstance(sr, dict):
        turn = sr
    elif sr is not None and hasattr(sr, "model_dump"):
        turn = sr.model_dump()

    # Фолбэк: structured_response нет — берём текст последнего ответа ассистента
    # и пытаемся извлечь JSON (модель могла обернуть его в прозу/markdown-фенс).
    if turn is None:
        if isinstance(result, dict):
            msgs = result.get("messages", [])
            for m in reversed(msgs):
                if hasattr(m, "content") and isinstance(m.content, str) and m.content.strip():
                    if getattr(m, "type", "") == "ai" or getattr(m, "role", "") == "assistant":
                        content = m.content
                        break
            if not content and msgs:
                content = str(msgs[-1].content if hasattr(msgs[-1], "content") else msgs[-1])
        elif hasattr(result, "content"):
            content = str(result.content)
        else:
            content = str(result)
        try:
            obj = json.loads(_extract_json(content))
            if isinstance(obj, dict) and ("reply_text" in obj or "brief" in obj or "answer_kind" in obj):
                turn = obj
        except (json.JSONDecodeError, TypeError):
            pass

    if turn is None:
        # Фолбэк: агент вернул текст — делаем из него "qa" turn
        turn = {
            "reply_text": content.strip() or "Агент не дал ответа.",
            "brief": [],
            "answer_kind": "qa",
            "primary_inn": session.get("selected"),
            "facts": [],
            "claims": [],
            "gaps": [],
            "next_steps": [],
            "headline": None,
            "panel": {"kind": "card", "inn": None, "block": None, "metric": None},
            "suggestions": [],
            "_source": "agent",
        }

    # ИНН, полученные через MCP — фронт по ним добавляет компании в сессию и открывает отчёт.
    fetched, missing = _extracted_inns(result)
    # Подстрахуем primary_inn/panel.inn, если модель их не проставила, но данные получены.
    if fetched:
        if not turn.get("primary_inn"):
            turn["primary_inn"] = fetched[0]
        panel = turn.get("panel") or {}
        if panel.get("kind") in ("card", "chart") and not panel.get("inn"):
            panel["inn"] = fetched[0]
            turn["panel"] = panel
    turn["_fetched"] = fetched
    turn["_missing"] = missing
    return turn, {}, "agent"


def _legacy_session(payload: dict) -> dict:
    """Совместимость со старым клиентом: сессия собирается из analyze_results."""
    ar = payload.get("analyze_results") or {}
    return {
        "inns": list(ar.keys()),
        "selected": payload.get("primary_inn"),
        "assessment": {k: v.get("assessment_given") for k, v in ar.items()},
        "deal": next((v.get("deal_context") for v in ar.values() if v.get("deal_context")), None),
    }


async def run_chat(payload: dict) -> dict:
    """Точка входа POST /chat: сессия → ход агента → контракт ответа фронта."""
    session = payload.get("session")
    if not session:
        session = _legacy_session(payload)

    turn, analyze, source = await agent_turn(payload.get("message", ""), session)

    validation = {"data_source": source, "issues": [],
                  "fetched": turn.pop("_fetched", []), "missing": turn.pop("_missing", [])}
    return {"ok": True, "turn": turn, "validation": validation}


