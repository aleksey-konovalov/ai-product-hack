FROM python:3.11-slim

WORKDIR /app

COPY services/verification_mcp_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/verification_mcp_server/src/. ./src
# общий пакет shared (логгирование) из корня проекта
COPY shared ./shared

# чтобы `import shared` работал при запуске из /app/src
ENV PYTHONPATH=/app \
    PORT=3010 \
    PYTHONUNBUFFERED=1

# Непривилегированный пользователь
RUN useradd --create-home appuser
USER appuser

WORKDIR /app/src

EXPOSE 3010

# Готовность = TCP-порт слушается (HTTP-рукопожатие MCP требует установленной сессии)
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD python -c "import os,socket; socket.create_connection(('127.0.0.1', int(os.environ.get('PORT','3010'))), 3).close()"

CMD ["python", "server.py"]
