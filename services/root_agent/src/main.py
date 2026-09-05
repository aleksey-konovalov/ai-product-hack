import sys
import asyncio
import gradio as gr
from pathlib import Path

from langchain_core.messages import HumanMessage

# Импорт логгера из shared
from shared.utils.logger import get_logger

# Импорт логики агента
from core.agent import root_agent

# Настраиваем логгер один раз при старте
logger = get_logger(name="agent")


async def respond(message_text, history):
    # Вызываем логику из core/agent.py
    agent_response = await root_agent.ainvoke(
            {"messages": [HumanMessage(message_text)]},                             #  + prompt_suffix
            config={"configurable": {"thread_id": 0}, "max_tool_calls": 5}          # thread_id
        )                      
    # Извлечение ответа агента
    agent_answer = agent_response['messages'][-1].content.split('</think>')[-1].strip() 
    return agent_answer

def main():
    logger.info("Запуск Agent Service UI (Gradio)...")
    
    # Настройка Gradio
    with gr.Blocks() as demo:
        chatbot = gr.ChatInterface(respond, title="Verification Agent")
        
    # Запуск
    demo.launch(server_name="0.0.0.0", server_port=7860)

if __name__ == "__main__":
    main()

