"""
Описание и параметры агента администратора.
"""
import os
import sys
import asyncio

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.messages import HumanMessage

from .llm_client import llm
from .prompts import root_agent_prompt
from .middleware import TrimMessagesMiddleware

from shared.utils.logger import app_logger, get_logger


async def _load_mcp_tools():
    """Подключается к MCP-серверам и возвращает список инструментов."""
    client = MultiServerMCPClient(
        {
            "verification": {
                "transport": "streamable_http",
                # Внутри docker-compose — имя сервиса, снаружи — localhost.
                "url": os.getenv("MCP_VERIFICATION_URL", "http://localhost:3010/mcp"),
            },
        }
    )
    return await client.get_tools()


mcp_tools = asyncio.run(_load_mcp_tools())
app_logger.info(f"Загружено MCP-инструментов: {len(mcp_tools)}")


root_agent = create_agent(
    llm,
    tools=mcp_tools,
    system_prompt=root_agent_prompt,
    middleware=[TrimMessagesMiddleware(max_messages=15, max_tool_messages=1)],
    checkpointer=InMemorySaver(),
)