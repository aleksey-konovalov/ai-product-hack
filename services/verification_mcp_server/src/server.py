import os
import re
import json
from pathlib import Path
import asyncio

# Совместимость версий mcp: в mcp<2 класс называется FastMCP (API идентичен:
# @mcp.tool(), @mcp.resource(), mcp.streamable_http_app()). Пытаемся импортировать
# «родной» MCPServer, иначе используем FastMCP под тем же именем.
try:
    from mcp.server.mcpserver import MCPServer, Context  # noqa: F401
except ModuleNotFoundError:
    from mcp.server.fastmcp import FastMCP as MCPServer, Context

# Импорт логгера из shared
from shared.utils.logger import app_logger, get_logger

# Импорт вспомогательных функций для работы с контрагентами
from counteragents import build_counteragents_payload



mcp = MCPServer("verification_mcp_server")

# ИНН: 10 цифр (юрлицо) или 12 цифр (ИП/физлицо)
INN_PATTERN = re.compile(r"(?<!\d)\d{10}(?!\d)|(?<!\d)\d{12}(?!\d)")


@mcp.tool()
async def get_companies_info_by_inn(text: str, ctx: Context) -> str:
    """
    Получает информацию по контрагентам по ИНН.
    Если контрагентов несколько, то нужно передать список/кортеж ИНН.
    """
    # Извлекаем ИНН из текста запроса
    inns = INN_PATTERN.findall(text)

    if not inns:
        await ctx.info("ИНН в запросе не найдены")
        app_logger.info("ИНН в запросе не найдены")
        return json.dumps(
            {
                "error": "В запросе не найдено ни одного ИНН.",
                "hint": "ИНН должен состоять из 10 цифр (юрлицо) или 12 цифр (ИП).",
            },
            ensure_ascii=False,
        )

    await ctx.info(f"Извлечённые из запроса ИНН: {', '.join(inns)}")
    app_logger.info(f"Извлечённые из запроса ИНН: {', '.join(inns)}")

    db_source = Path(os.getenv("COUNTERAGENTS_DB_PATH", "/opt/data/contractors_audit.snapshot.json"))

    try:
        payload = await build_counteragents_payload(db_source, inns, raise_on_missing=False)
    except FileNotFoundError as exc:
        await ctx.error(f"База контрагентов не найдена: {exc}")
        app_logger.info(f"База контрагентов не найдена: {exc}")
        return json.dumps({"error": f"База контрагентов не найдена: {exc}"}, ensure_ascii=False)
    except (ValueError, TypeError) as exc:
        await ctx.error(f"Не удалось прочитать базу контрагентов: {exc}")
        app_logger.info(f"Не удалось прочитать базу контрагентов: {exc}")
        return json.dumps({"error": f"Не удалось прочитать базу контрагентов: {exc}"}, ensure_ascii=False)

    found_count = len(payload["found"])
    missing = payload["missing"]

    await ctx.info(
        f"Найдено контрагентов: {found_count}"
        + (f"; не найдены по ИНН: {', '.join(missing)}" if missing else "")
    )
    app_logger.info(
        f"Найдено контрагентов: {found_count}"
        + (f"; не найдены по ИНН: {', '.join(missing)}" if missing else "")
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
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=3010)