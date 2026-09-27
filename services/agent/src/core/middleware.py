"""
Промежуточное программное обеспечение, для оптимизации контекста для LLM.
"""
import os
import sys

from langchain.agents.middleware import ModelRequest, ModelResponse, AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from typing import Callable



class TrimMessagesMiddleware(AgentMiddleware):
    def __init__(self, max_messages: int = 20, max_tool_messages: int = 2):
        self.max_messages = max_messages
        self.max_tool_messages = max_tool_messages

    async def awrap_model_call(self, request: ModelRequest, handler: Callable) -> ModelResponse:
        messages = request.state["messages"]
        
        # Всегда держим последние 2 сообщения (текущий запрос и ответ)
        always_keep = messages[-2:]
        
        # Для остальных сообщений применяем обрезку по тулам
        older_messages = messages[:-2] if len(messages) > 2 else []
        
        # Собираем кандидатов на сокращение
        keep_messages = []
        tool_count = 0
        
        for msg in reversed(older_messages):
            if isinstance(msg, (ToolMessage, AIMessage)):
                if msg.tool_calls or isinstance(msg, ToolMessage):
                    if tool_count < self.max_tool_messages:
                        tool_count += 1
                        keep_messages.append(msg)
                    continue  # Пропускаем старые tool-сообщения
            keep_messages.append(msg)
        
        # Ограничиваем до max_messages
        older_messages = list(reversed(keep_messages[-self.max_messages:]))
        final_messages = older_messages + always_keep

        modified_request = ModelRequest(
            state=request.state,
            model=request.model,
            tools=request.tools,
            runtime=request.runtime,
            system_prompt=request.system_prompt,
            messages=final_messages,
            tool_choice=request.tool_choice,
            response_format=request.response_format
        )
        return await handler(modified_request)