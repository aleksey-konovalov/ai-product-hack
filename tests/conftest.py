"""
Пути импорта и окружение для тестов.

Тесты запускаются из корня репозитория (см. pytest.ini). Чтобы импортировать
код сервисов, в sys.path добавляются:
  - корень репозитория  → import shared;
  - services/agent      → import src.app / src.chat_service / src.core.*;
  - src MCP-сервера     → import counteragents.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (
    ROOT,
    ROOT / "services" / "agent",
    ROOT / "services" / "verification_mcp_server" / "src",
):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# LLM-клиент ленивый, но /health и сборка агента читают конфиг — подставим валидный профиль
os.environ.setdefault("ACTIVE_LLM_PROFILE", "api_glm_flash_agent")
os.environ.setdefault("DSLAB_API_KEY", "test-key-not-real")
os.environ.setdefault("MCP_VERIFICATION_URL", "http://localhost:3010/mcp")
os.environ.setdefault("COUNTERAGENTS_FULL_PAYLOAD", "")
