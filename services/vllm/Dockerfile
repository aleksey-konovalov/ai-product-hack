FROM vllm/vllm-openai:latest

# Убедимся, что скрипт запуска доступен
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

WORKDIR /app

EXPOSE 8901

ENTRYPOINT ["/app/entrypoint.sh"]

