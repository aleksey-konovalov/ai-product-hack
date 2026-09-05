"""
Промежуточное программное обеспечение, для оптимизации контекста для LLM.
"""
import os
import sys

from langchain.agents.middleware import ModelRequest, ModelResponse, AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from typing import Callable



class TrimMessagesMiddleware(AgentMiddleware):
    def __init__(self, max_messages: int = 15, max_tool_messages: int = 1):
        self.max_messages = max_messages
        self.max_tool_messages = max_tool_messages

    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        messages = request.state["messages"]

        # Группируем сообщения в чанки (от новых к старым)
        chunks = []
        i = len(messages) - 1
        
        while i >= 0:
            msg = messages[i]
            
            if isinstance(msg, ToolMessage):
                # Собираем подряд идущие ToolMessages
                chunk = [msg]
                while i > 0 and isinstance(messages[i-1], ToolMessage):
                    i -= 1
                    chunk.append(messages[i])
                
                # Забираем родительский AIMessage с tool_calls
                if i > 0 and isinstance(messages[i-1], AIMessage) and messages[i-1].tool_calls:
                    i -= 1
                    chunk.append(messages[i])
                    chunks.append(list(reversed(chunk))) # Восстанавливаем хронологию
            else:
                if isinstance(msg, AIMessage):
                    if msg.tool_calls or not msg.content:
                        pass # Отбрасываем висящие AIMessage и пустые
                    else:
                        chunks.append([msg])
                else:
                    # HumanMessage и другие
                    chunks.append([msg])
            
            i -= 1

        # Собираем итоговый список
        limited_messages = []
        total_count = 0
        tool_count = 0

        for chunk in chunks:
            chunk_tools = sum(1 for m in chunk if isinstance(m, ToolMessage))
            chunk_len = len(chunk)

            # Динамическая проверка:
            # 1. Общий лимит (max_messages) еще не исчерпан
            # 2. Лимит тулов (max_tool_messages) не исчерпан для текущего чанка
            if (total_count + chunk_len) <= self.max_messages and (tool_count + chunk_tools) <= self.max_tool_messages:
                limited_messages = chunk + limited_messages
                total_count += chunk_len
                tool_count += chunk_tools
            # Если условие не выполняется, мы просто идем к следующему чанку 

        # Создаем запрос с отфильтрованными сообщениями
        modified_request = ModelRequest(
            state=request.state,
            model=request.model,
            tools=request.tools,
            runtime=request.runtime,
            system_prompt=request.system_prompt,
            messages=limited_messages,   #  меняем сообщения только для LLM, не меняя состояние
            tool_choice=request.tool_choice,
            response_format=request.response_format
        )
        return await handler(modified_request)
