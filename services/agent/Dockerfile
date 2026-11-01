FROM python:3.11-slim

WORKDIR /app

COPY services/agent/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Код агента (без статики — фронт живёт в сервисе web_ui) + общий пакет shared (логгирование)
COPY services/agent/src/. ./src
COPY shared ./shared

# Непривилегированный пользователь: приложение ничего не пишет в образ и систему
RUN useradd --create-home appuser
USER appuser

ENV PYTHONPATH=/app \
    PORT=8130 \
    PYTHONUNBUFFERED=1

EXPOSE 8130

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,sys,urllib.request; port=os.environ.get('PORT','8130'); sys.exit(0 if urllib.request.urlopen('http://localhost:'+port+'/health',timeout=3).status==200 else 1)"

# Порт — из PORT (единое место истины: одинаковый для uvicorn и healthcheck)
CMD ["sh", "-c", "python -m uvicorn src.app:app --host 0.0.0.0 --port \"${PORT:-8130}\""]
