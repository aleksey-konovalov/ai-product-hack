"""
Сборка LangChain-агента: MCP-инструменты, системный промпт, strict structured output.

Инициализация ленивая и под блокировкой (параллельные первые запросы не должны
создавать два экземпляра), закрытие MCP-клиента — через aclose() из FastAPI
lifespan при остановке сервиса.
"""
import asyncio
import os

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver

from .llm_client import get_llm
from .prompts import RESPONSE_SCHEMA, SYSTEM_PROMPT


class RootAgent:
    """Ленивая инициализация агента с MCP-инструментами."""

    def __init__(self):
        self._agent = None
        self._client = None
        self._lock = asyncio.Lock()

    @property
    def is_initialized(self) -> bool:
        return self._agent is not None

    async def _ensure_initialized(self):
        if self._agent is not None:
            return self._agent
        async with self._lock:
            # double-checked locking: пока первый запрос грузил инструменты,
            # второй мог дождаться этой же блокировки
            if self._agent is None:
                client = MultiServerMCPClient({
                    "verification": {
                        "transport": "streamable_http",
                        "url": os.getenv("MCP_VERIFICATION_URL", "http://localhost:3010/mcp"),
                    },
                })
                mcp_tools = await client.get_tools()

                self._client = client
                self._agent = create_agent(
                    get_llm(),
                    tools=mcp_tools,
                    system_prompt=SYSTEM_PROMPT,
                    # Строгий structured output под контракт фронта (brief/panel/suggestions).
                    # ToolStrategy совместим с dslab (ProviderStrategy этот json_schema не принимает),
                    # и структурный ответ приходит отдельным tool-call → не смешивается с reasoning/прозой.
                    response_format=ToolStrategy(schema=RESPONSE_SCHEMA),
                    checkpointer=InMemorySaver(),
                )
        return self._agent

    async def aclose(self):
        """Закрывает MCP-клиент. Вызывается из lifespan при остановке сервиса."""
        async with self._lock:
            if self._client is None:
                return
            client, self._client, self._agent = self._client, None, None
            aclose = getattr(client, "aclose", None)
            if aclose is not None:
                await aclose()
            else:
                await client.__aexit__(None, None, None)

    async def ainvoke(self, *args, **kwargs):
        agent = await self._ensure_initialized()
        return await agent.ainvoke(*args, **kwargs)


root_agent = RootAgent()
