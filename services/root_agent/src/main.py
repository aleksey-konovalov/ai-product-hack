import sys
import asyncio
import gradio as gr
from pathlib import Path

from langchain_core.messages import HumanMessage, AIMessageChunk, ToolMessage

# Импорт логгера из shared
from shared.utils.logger import get_logger

# Импорт логики агента
from core.agent import root_agent

# Настраиваем логгер один раз при старте
logger = get_logger(name="agent")


def _visible_text(buffer: str) -> str:
    """
    Отрезает блок размышлений <think>...</think>, если модель прислала его
    прямо в content. Пока блок не закрыт — считаем, что ответ ещё не начался.
    """
    if "<think>" in buffer and "</think>" not in buffer:
        return ""  # модель всё ещё «думает» — видимого ответа пока нет
    return buffer.split("</think>")[-1]


async def respond(message_text, history):
    """
    Стриминговый обработчик: отдаёт ответ по мере генерации, чтобы UI не
    выглядел «зависшим» на медленной локальной модели. Пока идут только
    размышления/вызовы инструментов — показываем индикатор.
    """
    buffer = ""            # накопленный content ассистента (может включать <think>)
    tool_running = False   # идёт ли сейчас вызов инструмента
    yielded_any = False

    async for chunk, _meta in root_agent.astream(
        {"messages": [HumanMessage(message_text)]},
        config={"configurable": {"thread_id": 0}, "max_tool_calls": 5},
        stream_mode="messages",
    ):
        # Ассистент печатает токены ответа (или размышлений)
        if isinstance(chunk, AIMessageChunk):
            if chunk.tool_calls or chunk.tool_call_chunks:
                tool_running = True
            content = chunk.content
            if isinstance(content, list):  # некоторые провайдеры отдают блоки
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            if content:
                buffer += content
            visible = _visible_text(buffer).lstrip("\n")
            if visible:
                yielded_any = True
                yield visible
            elif not yielded_any:
                yield "🔎 Проверяю данные…" if tool_running else "💭 Думаю…"
        # Результат работы инструмента — показываем, что что-то происходит
        elif isinstance(chunk, ToolMessage):
            tool_running = True
            if not yielded_any:
                yield "🔎 Собираю данные по контрагенту…"

    final = _visible_text(buffer).strip()
    yield final if final else "Извините, не удалось сформировать ответ. Попробуйте переформулировать запрос."

def main():
    logger.info("Запуск Agent Service UI (Gradio)...")
    
    # Настройка Gradio
    with gr.Blocks() as demo:
        chatbot = gr.ChatInterface(respond, title="Churn Copilot Agent")
        
    # Запуск
    demo.launch(server_name="0.0.0.0", server_port=7860)

if __name__ == "__main__":
    main()

