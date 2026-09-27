"""
Описание и параметры агента администратора.
"""
import os
import sys
import asyncio

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.messages import HumanMessage

from .llm_client import llm
from .prompts import SYSTEM_PROMPT, RESPONSE_SCHEMA
from .middleware import TrimMessagesMiddleware

# from shared.utils.logger import app_logger, get_logger



class RootAgent:
    """Ленивая инициализация агента с MCP-инструментами."""
    
    def __init__(self):
        self._agent = None
        self._client = None
    
    async def _ensure_initialized(self):
        if self._agent is None:
            client = MultiServerMCPClient({
                "verification": {
                    "transport": "streamable_http",
                    "url": os.getenv("MCP_VERIFICATION_URL", "http://localhost:3010/mcp"),
                },
            })
            mcp_tools = await client.get_tools()
            # app_logger.info(f"Загружено MCP-инструментов: {len(mcp_tools)}")

            self._client = client
            self._agent = create_agent(
                llm,
                tools=mcp_tools,
                system_prompt=SYSTEM_PROMPT,
                # Строгий structured output под контракт фронта (brief/panel/suggestions).
                # ToolStrategy совместим с dslab (ProviderStrategy этот json_schema не принимает),
                # и структурный ответ приходит отдельным tool-call → не смешивается с reasoning/прозой.
                response_format=ToolStrategy(schema=RESPONSE_SCHEMA),
                # middleware=[TrimMessagesMiddleware(max_messages=20, max_tool_messages=2)],
                checkpointer=InMemorySaver(),
            )
        return self._agent
    
    async def close(self):
        if self._client:
            await self._client.__aexit__(None, None, None)
            self._client = None
            self._agent = None
    
    async def ainvoke(self, *args, **kwargs):
        agent = await self._ensure_initialized()
        return await agent.ainvoke(*args, **kwargs)


root_agent = RootAgent()