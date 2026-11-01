"""
MCP-сервер верификации: тул get_companies_info_by_inn — единственный источник
данных о контрагентах по ИНН.
"""
import json
import logging
import os
import re
from pathlib import Path

# Совместимость версий mcp: в mcp<2 класс называется FastMCP (API идентичен:
# @mcp.tool(), @mcp.resource(), mcp.streamable_http_app()). Пытаемся импортировать
# «родной» MCPServer, иначе используем FastMCP под тем же именем.
try:
    from mcp.server.mcpserver import MCPServer, Context  # noqa: F401
except ModuleNotFoundError:
    from mcp.server.fastmcp import FastMCP as MCPServer, Context

from counteragents import build_counteragents_payload
from shared.logging import setup_logging

setup_logging("verification_mcp_server")

logger = logging.getLogger(__name__)


def _transport_security():
    """Настройка защиты MCP-транспорта (DNS-rebinding) через окружение.

    По умолчанию FastMCP разрешает только Host=localhost, поэтому в docker-сети,
    где agent ходит по имени сервиса (verification_mcp_server:3010), запросы
    отклоняются с HTTP 421. Управляем через env:
      MCP_ALLOW_ALL_HOSTS=1        — отключить проверку (для внутренней docker-сети);
      FASTMCP_ALLOWED_HOSTS=a,b    — явный список разрешённых Host (иначе);
      FASTMCP_ALLOWED_ORIGINS=a,b  — явный список разрешённых Origin.
    Если ничего не задано — поведение FastMCP по умолчанию (только localhost).
    """
    try:
        from mcp.server.transport_security import TransportSecuritySettings
    except Exception:
        return None
    if os.getenv("MCP_ALLOW_ALL_HOSTS", "").lower() in ("1", "true", "yes"):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [h for h in os.getenv("FASTMCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    origins = [o for o in os.getenv("FASTMCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if hosts or origins:
        return TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=origins)
    return None


_sec = _transport_security()
mcp = MCPServer("verification_mcp_server", transport_security=_sec) if _sec else MCPServer("verification_mcp_server")

# ИНН: 10 цифр (юрлицо) или 12 цифр (ИП/физлицо)
INN_PATTERN = re.compile(r"(?<!\d)\d{10}(?!\d)|(?<!\d)\d{12}(?!\d)")


async def _notify(ctx: Context, message: str, *, error: bool = False):
    """Одно сообщение — и в лог сервиса, и в прогресс MCP-клиенту."""
    (logger.error if error else logger.info)(message)
    await (ctx.error(message) if error else ctx.info(message))


@mcp.tool()
async def get_companies_info_by_inn(text: str, ctx: Context) -> str:
    """
    Получает информацию по контрагентам по ИНН.
    Если контрагентов несколько, то нужно передать список/кортеж ИНН.
    """
    # Извлекаем ИНН из текста запроса
    inns = INN_PATTERN.findall(text)

    if not inns:
        await _notify(ctx, "ИНН в запросе не найдены")
        return json.dumps(
            {
                "error": "В запросе не найдено ни одного ИНН.",
                "hint": "ИНН должен состоять из 10 цифр (юрлицо) или 12 цифр (ИП).",
            },
            ensure_ascii=False,
        )

    await _notify(ctx, f"Извлечённые из запроса ИНН: {', '.join(inns)}")

    db_source = Path(os.getenv("COUNTERAGENTS_DB_PATH", "/opt/data/contractors_audit.snapshot.json"))

    try:
        payload = await build_counteragents_payload(db_source, inns, raise_on_missing=False)
    except FileNotFoundError as exc:
        await _notify(ctx, f"База контрагентов не найдена: {exc}", error=True)
        return json.dumps({"error": f"База контрагентов не найдена: {exc}"}, ensure_ascii=False)
    except (ValueError, TypeError) as exc:
        await _notify(ctx, f"Не удалось прочитать базу контрагентов: {exc}", error=True)
        return json.dumps({"error": f"Не удалось прочитать базу контрагентов: {exc}"}, ensure_ascii=False)

    found_count = len(payload["found"])
    missing = payload["missing"]

    await _notify(
        ctx,
        f"Найдено контрагентов: {found_count}"
        + (f"; не найдены по ИНН: {', '.join(missing)}" if missing else ""),
    )

    # Возвращаем агенту компактный JSON-ответ
    return json.dumps(payload, ensure_ascii=False, indent=2)


# Пример ресурса (Resource)
@mcp.resource("config://app")
async def get_config() -> str:
    """Возвращает конфигурацию сервера."""
    return "Сервер верификации v2.0 запущен и готов к работе."


if __name__ == "__main__":
    import uvicorn

    # log_config=None: не перенастраивать logging — уже настроен setup_logging()
    uvicorn.run(
        mcp.streamable_http_app(),
        host="0.0.0.0",
        port=int(os.getenv("PORT", "3010")),
        log_config=None,
    )
