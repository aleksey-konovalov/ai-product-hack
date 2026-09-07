# web_agent.py (упрощённый)
"""
Локальный веб-агент: общается с фронтом, делегирует логику root_agent.
"""
import json
import os
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from langchain_core.messages import HumanMessage

from .core.agent import root_agent

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.getenv("PORT", "8130"))

# Логи (сохраняем те же)
import logging
LOG_PATH = os.getenv("WEB_AGENT_LOG", os.path.join(HERE, "..", "logs", "web_agent.log"))
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
log = logging.getLogger("web_agent")
# ... (настройка логгера как раньше)


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
    result = await root_agent.ainvoke(
        {
            "messages": [HumanMessage(content=user_input)],
        },
        config={"configurable": {"thread_id": thread_id}},  # thread_id уникален для сессии
    )
    return result


def agent_turn(message: str, session: dict):
    """Синхронная обёртка для HTTP-хендлера."""
    log.info("ЗАПРОС: message=%r | сессия: %s", message, _session_ctx(session))
    
    # Генерируем thread_id на основе ИНН или другой информации из сессии
    thread_id = str(session.get("selected") or session.get("user_id") or "anonymous")
    
    result = asyncio.run(_agent_invoke(message, session, thread_id))
        
    # Извлекаем финальный ответ агента
    if isinstance(result, dict):
        msgs = result.get("messages", [])
        content = ""
        for m in reversed(msgs):
            if hasattr(m, "content") and isinstance(m.content, str) and m.content.strip():
                # Ищем последний ответ ассистента (не tool-результат)
                if getattr(m, "type", "") == "ai" or getattr(m, "role", "") == "assistant":
                    content = m.content
                    break
        if not content and msgs:
            content = str(msgs[-1].content if hasattr(msgs[-1], "content") else msgs[-1])
    elif hasattr(result, "content"):
        content = str(result.content)
    else:
        content = str(result)
    
    # Пробуем распарсить как JSON (если агент настроен на structured output)
    turn = None
    try:
        obj = json.loads(content)
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
    
    return turn, {}, "agent"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "json" in ctype or "html" in ctype else ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            path = os.path.join(HERE, "web", "index.html")
            if not os.path.exists(path):
                return self._send(404, {"error": "index.html not built"})
            with open(path, "rb") as f:
                return self._send(200, f.read(), "text/html")
        if self.path.startswith("/health"):
            profile = None
            try:
                # Попытка получить информацию о профиле
                env = {}
                import importlib.util
                spec = importlib.util.spec_from_file_location("_profile_check", os.path.join(HERE, "..", ".env"))
                # not needed — просто вернём базовые данные
            except Exception:
                pass
            return self._send(200, {"ok": True, "agent_initialized": root_agent._agent is not None,
                                    "model": os.getenv("LM_MODEL", "unknown")})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/chat":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"ok": False, "error": f"bad request: {e}"})
        
        session = payload.get("session")
        if not session:
            ar = payload.get("analyze_results") or {}
            session = {"inns": list(ar.keys()), "selected": payload.get("primary_inn"),
                       "assessment": {k: v.get("assessment_given") for k, v in ar.items()},
                       "deal": next((v.get("deal_context") for v in ar.values() if v.get("deal_context")), None)}
        
        try:
            turn, analyze, source = agent_turn(payload.get("message", ""), session)
        except Exception as e:
            return self._send(200, {"ok": False, "error": f"agent error: {e}"})
        
        return self._send(200, {"ok": True, "turn": turn, "validation": {"data_source": source, "issues": []}})


if __name__ == "__main__":
    print(f"web_agent on http://localhost:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()