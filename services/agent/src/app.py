"""
HTTP API сервиса agent: POST /chat и GET /health.

UI раздаётся отдельным сервисом web_ui (nginx проксирует /chat и /health сюда),
поэтому статику агент больше не отдаёт. Контракт /chat сохранён прежним
(README, раздел «API») — фронт не менялся при разделении.
"""
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from shared.logging import request_id_var, setup_logging

setup_logging("agent")

from .chat_service import run_chat
from .core.agent import root_agent
from .core.llm_client import active_profile, load_config

logger = logging.getLogger(__name__)


# --- Контракт запроса (README, раздел «API») ---------------------------------
# extra="allow": незнакомые поля (в т.ч. legacy-путь старых клиентов с
# analyze_results) сохраняются и передаются дальше без потерь.
class DealContext(BaseModel):
    """Контекст сделки (опциональное поле сессии)."""

    model_config = ConfigDict(extra="allow")
    goal: str | None = None
    amount: float | None = None


class ChatSession(BaseModel):
    """Состояние сессии с фронта: список ИНН, выбранная компания, идентификатор."""

    model_config = ConfigDict(extra="allow")
    inns: list[str] = []
    selected: str | None = None
    sid: str | None = None
    assessment: dict[str, bool] = {}
    deal: DealContext | None = None


class ChatRequest(BaseModel):
    """Тело POST /chat."""

    model_config = ConfigDict(extra="allow")
    message: str
    session: ChatSession | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    # Корректно закрываем соединение к MCP-серверу при остановке сервиса
    await root_agent.aclose()


app = FastAPI(
    title="agent",
    description="API агента проверки контрагентов",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS нужен только для сценариев, где фронт ходит напрямую (не через web_ui proxy):
# локальная разработка с другим портом, будущий раздельный домен. По умолчанию
# выключен — через nginx-прокси фронт ходит по same-origin и CORS не требуется.
_origins = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Сквозной request_id: приходит от nginx (X-Request-ID) или генерируется здесь.

    Попадает в каждую строку лога запроса (через shared.logging.request_id_var)
    и возвращается клиенту — связывает логи web_ui и agent по одному обращению.
    """
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    return response


@app.exception_handler(RequestValidationError)
async def _bad_request(request: Request, exc: RequestValidationError):
    # Прежний контракт фронт ожидал в виде ok:false (было 400 у stdlib-сервера),
    # а не 422 FastAPI по умолчанию.
    return JSONResponse(status_code=400, content={"ok": False, "error": f"bad request: {exc}"})


@app.get("/health")
async def health():
    profile, model = None, None
    try:
        profile = active_profile()
        model = load_config()[profile].get("model")
    except Exception:
        # Конфигурация LLM может быть не задана — health обязан отвечать всегда
        profile = profile or os.getenv("ACTIVE_LLM_PROFILE")
    return {
        "ok": True,
        "agent_initialized": root_agent.is_initialized,
        "profile": profile,
        "model": model,
    }


@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        return await run_chat(req.model_dump())
    except Exception:
        # Прежний контракт: ошибка агента — 200 + ok:false, фронт показывает сообщение юзеру.
        # Детали исключения — только в лог (с request_id), наружу — общий текст.
        logger.exception("agent error")
        return {"ok": False, "error": "Внутренняя ошибка агента. Попробуйте ещё раз."}


if __name__ == "__main__":
    import uvicorn

    PORT = int(os.getenv("PORT", "8130"))
    uvicorn.run(app, host="0.0.0.0", port=PORT)
