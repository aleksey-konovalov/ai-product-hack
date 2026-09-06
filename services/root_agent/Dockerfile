FROM python:3.11-slim

WORKDIR /app

# Ставим только продовые зависимости web_agent.py (лёгкий образ без gradio/langgraph)
COPY services/root_agent/requirements.docker.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Код агента: src/ содержит web_agent.py и core/ (report_contract.py, llm_config.json)
COPY services/root_agent/src/. ./src

ENV PYTHONPATH=/app \
    PORT=8130 \
    PYTHONUNBUFFERED=1

WORKDIR /app/src

EXPOSE 8130

# Простой healthcheck по /health эндпоинту web_agent
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8130/health',timeout=3).status==200 else 1)"

CMD ["python", "web_agent.py"]
