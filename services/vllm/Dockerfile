# Версия запинена: v0.24.0 проверена вручную с флагами из entrypoint.sh.
# При обновлении сверять флаги с `vllm serve --help` новой версии
# (см. REFACTOR_PLAN.md 5.5).
FROM vllm/vllm-openai:v0.24.0

# Убедимся, что скрипт запуска доступен
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

WORKDIR /app

EXPOSE 8901

ENTRYPOINT ["/app/entrypoint.sh"]

