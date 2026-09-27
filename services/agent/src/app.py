"""
HTTP API сервиса agent: POST /chat и GET /health.

UI раздаётся отдельным сервисом web_ui (nginx проксирует /chat и /health сюда),
поэтому статику агент больше не отдаёт. Контракт /chat сохранён прежним
(README, раздел «API») — фронт не менялся при разделении.
"""
import logging
import os

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .chat_service import agent_initialized, run_chat

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(levelname)-8s %(asctime)s - %(name)s - %(message)s",
)
logger = logging.getLogger("agent")

PORT = int(os.getenv("PORT", "8130"))

app = FastAPI(title="agent", description="API агента проверки контрагентов", version="1.0.0")

# CORS нужен только для сценариев, где фронт ходит напрямую (не через web_ui proxy):
# локальная разработка с другим портом, будущий раздельный домен.
_origins = [o for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def _bad_request(request: Request, exc: RequestValidationError):
    # Прежний контракт фронт ожидал в виде ok:false (было 400 у stdlib-сервера),
    # а не 422 FastAPI по умолчанию.
    return JSONResponse(status_code=400, content={"ok": False, "error": f"bad request: {exc}"})


@app.get("/health")
async def health():
    profile = os.getenv("ACTIVE_LLM_PROFILE")
    model = None
    try:
        from .core.llm_client import CURRENT_PROFILE, load_config
        profile = profile or CURRENT_PROFILE
        model = (load_config().get(CURRENT_PROFILE or "", {}) or {}).get("model")
    except Exception:
        pass  # llm_client падает без ACTIVE_LLM_PROFILE — health должен отвечать всегда
    return {
        "ok": True,
        "agent_initialized": agent_initialized(),
        "profile": profile,
        "model": model,
    }


@app.post("/chat")
async def chat(payload: dict = Body(...)):
    if not isinstance(payload.get("message"), str):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "bad request: 'message' (string) is required"},
        )
    try:
        return await run_chat(payload)
    except Exception as e:
        # Прежний контракт: ошибка агента — 200 + ok:false, фронт показывает сообщение юзеру.
        logger.exception("agent error")
        return {"ok": False, "error": f"agent error: {e}"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
