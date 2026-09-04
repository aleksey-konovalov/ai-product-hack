#!/bin/bash
set -e

CMD="vllm serve ${MODEL_NAME}"

# Основные параметры
CMD="${CMD} --gpu-memory-utilization ${GPU_MEMORY_UTILIZATION:-0.9}"
CMD="${CMD} --max-model-len ${MAX_MODEL_LEN:-8192}"
CMD="${CMD} --max-num-seqs ${MAX_NUM_SEQS:-1}"
CMD="${CMD} --tensor-parallel-size ${TENSOR_PARALLEL_SIZE:-1}"
CMD="${CMD} --cpu-offload-gb ${CPU_OFFLOAD_GB:-0}"
CMD="${CMD} --port ${PORT:-8000}"
CMD="${CMD} --served-model-name ${SERVED_MODEL_NAME:-churn-model}"
CMD="${CMD} --chat-template-content-format ${CHAT_TEMPLATE_CONTENT_FORMAT:-openai}"
CMD="${CMD} --kv-cache-dtype ${KV_CACHE_DTYPE:-auto}"

# Опциональные флаги (добавляются только если переменная = true)
if [ "${ENABLE_AUTO_TOOL_CHOICE}" = "true" ]; then
    CMD="${CMD} --enable-auto-tool-choice"
fi

if [ -n "${TOOL_CALL_PARSER}" ]; then
    CMD="${CMD} --tool-call-parser ${TOOL_CALL_PARSER}"
fi

if [ "${ENABLE_PREFIX_CACHING}" = "true" ]; then
    CMD="${CMD} --enable-prefix-caching"
fi

echo "Starting vLLM with command:"
echo "$CMD"

# Запуск
exec $CMD

