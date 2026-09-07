FROM python:3.11-slim

WORKDIR /app

# Копируем полный requirements.txt (для разработки) или requirements.docker.txt (урезанный)
COPY services/root_agent/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Код агента
COPY services/root_agent/src/. ./src

ENV PYTHONPATH=/app \
    PORT=8130 \
    PYTHONUNBUFFERED=1

EXPOSE 8130

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8130/health',timeout=3).status==200 else 1)"

CMD ["python", "-m", "src.web_agent"]
