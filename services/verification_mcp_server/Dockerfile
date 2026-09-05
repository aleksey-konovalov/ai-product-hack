FROM python:3.11-slim

WORKDIR /app

COPY services/verification_mcp_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/verification_mcp_server/src/. ./src
# копируем общий пакет из корня проекта
COPY shared ./shared

# чтобы `import shared` работал при запуске из /app/src
ENV PYTHONPATH=/app

WORKDIR /app/src

EXPOSE 3010

CMD ["python", "server.py"]
