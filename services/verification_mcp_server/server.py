import sys
from mcp.server.fastmcp import FastMCP, Context


mcp = FastMCP("verification_mcp_server")

@mcp.tool()
def verify_text(text: str, ctx: Context) -> str:
    """
    Проверяет переданного контагента на надежность.
    """
    # Используем ctx для логирования (обязательно)
    ctx.info(f"Начинаю проверку: {text[:10]}...")
    
    # Основная логика здесь
    result = f"'{text}' успешно проверен."
    
    return result

# Пример ресурса (Resource)
@mcp.resource("config://app")
def get_config() -> str:
    """Возвращает конфигурацию сервера."""
    return "Сервер верификации v2.0 запущен и готов к работе."


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=3010)