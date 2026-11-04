# AI-агент проверки контрагентов

Чат-ассистент для проверки контрагентов перед сделкой: пользователь присылает ИНН (один или несколько), агент собирает данные по компаниям, выделяет риски и пробелы в данных, сравнивает кандидатов между собой и подсказывает, что проверить до сделки. Проект выполнен для хакатон-кейса Альфа-Банка.

**Ключевой продуктовый принцип:** LLM ничего не «знает» о компаниях и не считает риск сама. Все факты достаются инструментом (MCP) из детерминированной базы отчётов, модель только формулирует человеческий текст, раскладывает сигналы по слотам и выбирает, какую панель открыть. Карточки, матрицу сравнения и графики рисует фронт по своим правилам.

---

## Возможности

- **Проверка по ИНН** — 10 цифр (юрлицо) или 12 (ИП); поиск по названию сознательно не поддерживается.
- **Risk summary** — краткий вывод по компании: оценка банка словами, на что обратить внимание, пробелы данных.
- **Сравнение контрагентов** — матрица по блокам, алерты по каждой компании (без рейтингов и «победителей» — это запрещено и промптом, и продуктом).
- **Уточняющие вопросы** — финансы, суды и долги, исполнительные производства, реестры ФНС, «что проверить до сделки».
- **Графики** — динамика выручки/прибыли/капитала по годам.
- **Контекст сделки** — «контракт на 100 млн, поставка» учитывается в выводе.
- **Режим без LLM** — тумблер в интерфейсе переводит чат на правило-based ответы клиента (демо-режим без модели).

## Архитектура

UI и агент — **два независимых сервиса**:

```
                          ┌────────────────────────────┐
                          │  Браузер                   │
                          └─────────────┬──────────────┘
                                        │ :8080
                          ┌─────────────▼──────────────┐
                          │       web_ui :8080         │
                          │  nginx:alpine              │
                          │  • / → static/index.html   │
                          │  • /chat,/health → proxy   │
                          └─────────────┬──────────────┘
                                        │ http://agent:8130 (внутренняя сеть compose)
                          ┌─────────────▼──────────────┐
                          │         agent :8130        │
                          │  FastAPI/uvicorn           │
                          │  POST /chat, GET /health   │
                          │  + LangChain/LangGraph     │
                          │  structured output (JSON)  │
                          │  InMemorySaver (thread=sid)│
                          └───────┬──────────────┬─────┘
                    MCP (streamable_http)  │    OpenAI-совместимый API
                          │                │    (dslab / LM Studio / vLLM)
            ┌─────────────▼───────────┐    └────► LLM-профиль из
            │ verification_mcp_server │         llm_config.json
            │         :3010           │
            │ tool: get_companies_    │
            │       info_by_inn       │
            └─────────────┬───────────┘
                          │ read-only
            ┌─────────────▼───────────┐
            │ data/contractors_audit. │
            │ snapshot.json (100      │
            │ отчётов GetFullReport)  │
            └─────────────────────────┘

  Опционально (профиль local_qwen_agent / профиль vllm):
  vllm :3040 → локальный inference с GPU вместо облачного API.
```

### Сервисы

| Сервис | Порт | Стек | Назначение |
|---|---|---|---|
| `web_ui` | `${WEB_UI_PORT:-8080}` → 80 | nginx:alpine | Статика фронта (`static/index.html`, `static/db.js`) + reverse proxy `/chat` и `/health` на агента. Единственный вход для пользователя |
| `agent` | `8130` (внутренний) | Python 3.11, FastAPI/uvicorn, LangChain/LangGraph | Чистый API: `POST /chat`, `GET /health`. Логика агента: MCP-инструменты, structured output, память сессий. Статику не раздаёт |
| `verification_mcp_server` | `3010` (внутренний) | Python, FastMCP, uvicorn | MCP-сервер: единственный источник данных о контрагентах. Тул `get_companies_info_by_inn(text)` ищет ИНН в тексте и возвращает отчёты |
| `vllm` (профиль `vllm`) | `8901` (внутренний) | vLLM, GPU | Опциональный локальный inference (OpenAI-совместимый API) |
| `shared/` | — | Python-пакет | Общий код: единая настройка логирования (`shared/logging.py`: `setup_logging` + `getLogger(__name__)`); используется agent и MCP-сервером |

Конвенция портов: наружу публикуется только вход пользователя (порт — переменная `WEB_UI_PORT` из `.env`, по умолчанию 8080); внутренние порты сервисов (8130, 3010, 8901) наружу не публикуются. Для отладки снаружи есть `docker-compose.debug.yml` — хост-порт = внутренний + 10000 (18130 / 13010 / 18901).

### Кто за что отвечает (разделение ответственности)

- **Фронтенд** (`services/web_ui/static/index.html`, один файл без сборки):
  - хранит сессию в памяти браузера: список ИНН, выбранная компания, контекст сделки, `sid`;
  - содержит **встроенный датасет** (~100 компаний) для отрисовки карточек, матрицы и графиков — карточки строятся клиентом по правилам, а не моделью;
  - детерминированные операции («убери компанию», «очисти сессию») и режим «LLM выкл» обрабатывает сам;
  - всё остальное отправляет агенту: `POST /chat {message, session}` на относительный путь `/chat` (через nginx попадает в агент).
- **Агент** (`services/agent/src/`):
  - `app.py` — FastAPI: `POST /chat`, `GET /health` (Pydantic-модели запроса), CORS (`CORS_ALLOW_ORIGINS`, по умолчанию выключен), request-id middleware; жизненный цикл MCP-клиента — через lifespan, статику не раздаёт;
  - `chat_service.py` — сборка ответа: контекст сессии, вызов агента, извлечение `structured_response`, достаёт ИНН, реально полученные через MCP (`validation.fetched`/`missing`), фолбэки, если модель вернула прозу вместо JSON;
  - `core/agent.py` — сборка LangChain-агента: MCP-инструменты, системный промпт, strict structured output (`ToolStrategy`), `InMemorySaver` с `thread_id = sid` (изоляция сессий); инициализация — ленивая, под `asyncio.Lock`;
  - `core/prompts.py` — системный промпт и `RESPONSE_SCHEMA` (JSON Schema ответа);
  - `core/llm_client.py` + `llm_config.json` — выбор LLM по профилю (клиент создаётся лениво, конфиг кэшируется);
- **MCP-сервер** (`services/verification_mcp_server/src/`):
  - `server.py` — тул `get_companies_info_by_inn`: извлекает ИНН из текста запроса регэкспом, возвращает найденное и `missing`;
  - `counteragents.py` — база кэшируется по mtime файла + индекс по ИНН (снапшот не перечитывается на каждый вызов), поиск, компактный или полный payload (`COUNTERAGENTS_FULL_PAYLOAD`).

Поток ответа: `POST /chat` (через web_ui) → агент получает сообщение + контекст сессии → вызывает MCP-тул по ИНН → LLM формирует строгий JSON по схеме (`answer_kind`, `reply_text`/`brief`, `panel`, `suggestions`) → бэкенд добавляет `validation.fetched` → фронт добавляет компании в сессию, рисует карточки/матрицу и открывает нужную панель.

## Структура репозитория

```
├── README.md                       ← этот файл
├── DEPLOY_YC.md                    ← деплой на Yandex Cloud (Docker Compose)
├── docker-compose.yml              ← web_ui + agent + verification_mcp_server (+ профили vllm, assets)
├── docker-compose.debug.yml        ← отладочные публикации портов (host = внутренний + 10000)
├── Makefile                        ← make up/down/logs/download, lint/test (up докачивает данные при необходимости)
├── .env.example                    ← переменные compose (WEB_UI_PORT, GDRIVE_SNAPSHOT_JSON_ID)
├── pytest.ini / requirements-dev.txt / tests/   ← тесты (см. «Разработка»)
├── LICENSE                         (Apache-2.0)
├── data/
│   └── contractors_audit.snapshot.json   # база: 100 отчётов GetFullReportResponse (в git не хранится — make download)
├── shared/                         # общий python-пакет (логгирование)
│   ├── logging.py
│   └── utils/download_project_assets.py  # скачивание данных с Google Drive (вызывается make download)
├── reqs/                           # продуктовые спецификации
│   ├── context_pack.md             # кейс, JTBD, требования кейсоадателя
│   ├── signal_rules.md             # детерминированные правила сигналов (IF/THEN)
│   ├── columns_spec.md             # схема полей отчёта банка
│   └── profile_fields.md           # профиль контрагента и зоны отображения
└── services/
    ├── web_ui/                     # сервис фронтенда
    │   ├── Dockerfile              # nginx:alpine, healthcheck через proxy → agent
    │   ├── nginx.conf              # статика + proxy /chat,/health → agent:8130 (+ X-Request-ID)
    │   ├── dev_server.py           # локальная разработка без docker (static + proxy)
    │   ├── generate_db.py          # генератор static/db.js из data/*.snapshot.json
    │   └── static/
    │       ├── index.html          # весь фронтенд (один файл)
    │       └── db.js               # данные компаний: const DB = {...} (генерируется)
    ├── agent/                      # сервис агента (чистый API)
    │   ├── Dockerfile              # python:3.11-slim + shared, non-root, healthcheck на $PORT
    │   ├── requirements.txt        # прямые зависимости (langchain, langgraph, fastapi, ...)
    │   ├── .env.example            # ACTIVE_LLM_PROFILE, DSLAB_API_KEY, MCP_VERIFICATION_URL, LOG_*
    │   └── src/
    │       ├── app.py              # FastAPI: POST /chat, GET /health, CORS, request-id, lifespan
    │       ├── chat_service.py     # логика ответа: сессия → агент → контракт фронта
    │       └── core/               # агент, LLM-клиент, промпт+схема
    ├── verification_mcp_server/
    │   ├── Dockerfile              # python:3.11-slim + shared, non-root, TCP-healthcheck
    │   ├── requirements.txt
    │   ├── .env.example
    │   └── src/                    # server.py, counteragents.py
    └── vllm/                       # опциональный локальный inference (GPU)
```

## API

Вход для пользователя — `http://localhost:8080` (web_ui). Агент отвечает на те же пути напрямую (порт `8130`, внутри compose-сети), контракт одинаковый.

### `POST /chat`

Запрос (фронт → агент):

```json
{
  "message": "проверь 9721159668",
  "session": {
    "inns": ["9721159668"],
    "selected": "9721159668",
    "sid": "s-abc123",
    "assessment": {"9721159668": true},
    "deal": {"goal": "поставка", "amount": 100}
  }
}
```

- `sid` — идентификатор сессии: становится `thread_id` памяти агента (новая/очищенная сессия → новый `sid` → чистая память);
- `assessment` — по каким компаниям общий разбор уже давался (чтобы не повторять его);
- `deal` — контекст сделки (опционально).

Ответ:

```json
{
  "ok": true,
  "turn": {
    "answer_kind": "summary | qa | compare | chart | smalltalk | refusal",
    "reply_text": "",
    "brief": ["абзац 1", "абзац 2"],
    "primary_inn": "9721159668",
    "panel": {"kind": "card|compare|chart|none", "inn": "...", "block": "A|B|C|D|E|null", "metric": "...|null"},
    "suggestions": ["Что с финансами?", "Есть ли долги и суды?"],
    "facts": [], "claims": [], "gaps": [], "next_steps": [], "headline": null
  },
  "validation": {
    "data_source": "agent",
    "issues": [],
    "fetched": ["9721159668"],
    "missing": []
  }
}
```

Полная схема `turn` — в `services/agent/src/core/prompts.py` (`RESPONSE_SCHEMA`). `validation.fetched` — ИНН, которые агент реально вытащил через MCP: по ним фронт добавляет компании в сессию и открывает отчёт. Ошибки агента возвращаются как `{"ok": false, "error": "..."}` — фронт показывает их пользователю.

### `GET /health`

`{"ok": true, "agent_initialized": false, "profile": "api_deepseek_agent", "model": "deepseek-v4-flash-0731"}` — `agent_initialized` становится `true` после первого обращения к агенту (инициализация ленивая).

### MCP-тул `get_companies_info_by_inn`

Аргумент `text` (свободный текст), ИНН извлекаются регэкспом. Ответ: `{"found": [отчёты...], "missing": ["ИНН"]}`. Полный payload отчёта (`baseInfo`, `finReports`, `executionProceedings`, `arbitrationByStatus`, `reputationalRisks`, `procurements`, ...) включается при `COUNTERAGENTS_FULL_PAYLOAD=1`, иначе компактная сводка.

## Конфигурация

Все секреты — в `.env` (в git не коммитятся; в git есть только `.env.example`).

| Переменная | Где | Назначение |
|---|---|---|
| `WEB_UI_PORT` | корневой `.env` | Публикуемый порт web_ui (по умолчанию `8080`; 8080 популярен — при конфликте поменяйте здесь) |
| `ACTIVE_LLM_PROFILE` | agent `.env` | Профиль из `llm_config.json`: `api_deepseek_agent`, `api_qwen_agent`, `api_glm_flash_agent`, `local_qwen_agent` |
| `DSLAB_API_KEY` | agent `.env` | Ключ облачного API (dslab) |
| `MCP_VERIFICATION_URL` | agent | Адрес MCP: `http://localhost:3010/mcp` локально, `http://verification_mcp_server:3010/mcp` в compose |
| `PORT` | agent, MCP | Внутренний порт сервиса (8130 / 3010) — единое место истины для uvicorn и healthcheck |
| `CORS_ALLOW_ORIGINS` | agent | По умолчанию выключен (фронт ходит через nginx same-origin). Задать origin'ы через запятую только для прямых обращений минуя web_ui |
| `COUNTERAGENTS_DB_PATH` | MCP | Путь к базе (`/opt/data/contractors_audit.snapshot.json`) |
| `COUNTERAGENTS_FULL_PAYLOAD` | MCP | `1` — отдавать полные отчёты, `0` — компактную сводку |
| `MCP_ALLOW_ALL_HOSTS` | MCP | Отключает DNS-rebinding защиту (нужно в docker-сети; в проде порт 3010 наружу не публикуется) |
| `LOG_LEVEL` | agent, MCP | Уровень логирования (по умолчанию `INFO`) |
| `LOG_FORMAT` | agent, MCP | `console` (по умолчанию) или `json` — машинный формат для сборщиков логов |
| `PORT`, `AGENT_ORIGIN` | web_ui dev_server | Порт dev-сервера (8080) и адрес агента для локальной разработки |

Смена модели: поменять `ACTIVE_LLM_PROFILE` в `services/agent/.env` и перезапустить контейнер (`docker compose up -d agent`). Новые профили добавляются в `services/agent/src/core/llm_config.json`.

## Запуск

### Docker Compose (основной способ)

```bash
cp services/agent/.env.example services/agent/.env
# вписать ACTIVE_LLM_PROFILE и DSLAB_API_KEY
cp .env.example .env                     # вписать GDRIVE_SNAPSHOT_JSON_ID — ID файла со снапшотом на Google Drive

make up                                  # проверит наличие данных, докачает при необходимости, поднимет стек
curl http://localhost:8080/health        # {"ok": true, ...} — проверяет связку web_ui → agent
# UI: http://localhost:8080/
```

Отладочные порты сервисов снаружи (agent 18130, MCP 13010, vllm 18901):

```bash
docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d
```

Опционально локальный inference (нужен GPU с NVIDIA):

```bash
cp services/vllm/.env.example services/vllm/.env
docker compose --profile vllm up -d --build
# затем ACTIVE_LLM_PROFILE=local_qwen_agent и base_url профиля → http://vllm:8901/v1
```

### Локально без Docker (разработка)

Три компонента, три терминала (из корня репозитория; `PYTHONPATH=../..` нужен обоим
Python-сервисам для импорта `shared`):

```bash
# 1. MCP-сервер
cd services/verification_mcp_server
pip install -r requirements.txt
COUNTERAGENTS_DB_PATH=../../data/contractors_audit.snapshot.json \
COUNTERAGENTS_FULL_PAYLOAD=1 \
MCP_ALLOW_ALL_HOSTS=1 \
PYTHONPATH=../.. \
python src/server.py                      # :3010

# 2. Агент (API)
cd services/agent
pip install -r requirements.txt
cp .env.example .env                      # вписать ключ; MCP_VERIFICATION_URL=http://localhost:3010/mcp
PYTHONPATH=../.. python -m uvicorn src.app:app --host 0.0.0.0 --port 8130

# 3. UI: статика + прокси /chat,/health → :8130
python services/web_ui/dev_server.py      # UI: http://localhost:8080
```

Для отладки агента без UI: `curl -X POST localhost:8130/chat -H 'Content-Type: application/json' -d '{"message":"проверь 9721159668","session":{"inns":["9721159668"],"sid":"test"}}'`

## Данные

`data/contractors_audit.snapshot.json` — 100 отчётов `GetFullReportResponse` (выгрузка банка), монтируется в MCP-контейнер read-only; база кэшируется по mtime файла, так что обновление снапшота не требует рестарта контейнера. Файл в git не хранится (чтобы не публиковать выгрузку банка) — скачивается с Google Drive по ID из `.env` (`make download`, `make up` делает это автоматически перед стартом; уже скачанные файлы не перезаписываются — чтобы перекачать, удалите файл из `data/`). Поля отчёта и правила нормализации описаны в `reqs/columns_spec.md` и `reqs/signal_rules.md` §1: пустой раздел трактуется как **пробел данных** (`gap`) или как **«не найдено»** (`info`) в зависимости от раздела; ИП (12-значный ИНН) не имеет финансов и учредителей — это норма, а не риск.

Фронтенд использует собственную компактную копию данных (`services/web_ui/static/db.js`, `const DB`) для отрисовки карточек, матрицы и графиков. Она генерируется из снапшота:

```bash
python services/web_ui/generate_db.py                 # обновить числа из снапшота (тексты сохраняются)
python services/web_ui/generate_db.py --check         # проверить, что db.js не разошёлся со снапшотом
python services/web_ui/generate_db.py --add-missing   # добавить компании снапшота, которых ещё нет в db.js
```

Механические поля (финансы, даты, арбитраж, руководитель) пересчитываются из снапшота однозначно; тексты фактов и упрощения макета (`facts`, `cmp`, `ep`, `regNeg`, …) — курируемые, при перегенерации сохраняются.

## Разработка

```bash
make logs                                       # все логи (или docker compose logs -f <сервис>)
docker compose logs -f agent                    # логи агента
docker compose logs -f verification_mcp_server  # логи MCP
docker compose logs -f web_ui                   # access/error nginx
docker compose up -d agent                      # после смены .env (пересоздаст контейнер)
make download                                   # докачать отсутствующие данные с Google Drive
make down                                       # остановить всё (= docker compose down)
make lint                                       # линт Python-кода (ruff, конфиг ruff.toml)
make test                                       # тесты в собранном образе agent (то же, что в CI)
```

Логи всех сервисов пишутся в stdout и собираются docker'ом с ротацией (`max-size: 10m`, 3 файла — в compose). Формат единый (`shared/logging.py`); каждая строка запроса содержит `[request_id]`, который генерирует nginx или агент и возвращает клиенту в заголовке `X-Request-ID`.

### Тесты

Тесты (`tests/`) гоняются в собранном образе agent — там уже все пиненые зависимости, это же проверяет сборку:

```bash
make test
```

Или вручную (то же самое; тег `agent-tests` — чтобы не зависеть от имени compose-проекта):

```bash
docker build -t agent-tests -f services/agent/Dockerfile .
docker run --rm -v "$PWD":/work -w /work --entrypoint bash agent-tests -c \
  "python -m pip install -q -r requirements-dev.txt && PYTHONPATH=/work python -m pytest"
```

Или локально (Python 3.11+, установленные `services/agent/requirements.txt` + `requirements-dev.txt`): `PYTHONPATH=. pytest`.

### Линт и CI

`make lint` — ruff по конфигу `ruff.toml` (версия пинится в Makefile и в CI, проверки совпадают). CI — `.github/workflows/ci.yml`: на каждый push в `main` и на PR два job — `lint` (ruff) и `test` (сборка всех образов + тесты в образе agent, слои кэшируются через GHA-cache). Тестам снапшот не нужен, поэтому данные с Google Drive в CI не скачиваются.

Покрытие: контракт `RESPONSE_SCHEMA` (включая эталонные ответы в `tests/fixtures/`), база контрагентов MCP (нормализация, поиск, кэш по mtime, payload), фолбэки `chat_service` (prose/markdown → JSON), API-контракт `/chat` и `/health` (через TestClient с подменённым агентом).

Полезно знать:

- агент не раздаёт статику: `GET /` у `agent` → 404; фронт живёт только в `web_ui`;
- память агента (`InMemorySaver`) живёт в процессе агента — рестарт контейнера сбрасывает историю сессий;
- `mcp` запинен на `1.29.1` (клиент `langchain-mcp-adapters` несовместим с mcp 2.x);
- structured output работает через `ToolStrategy` (не `ProviderStrategy`) — dslab принимает json_schema только как tool-call, так ответ не смешивается с reasoning;
- изоляция сессий держится на `sid`: без него пустые сессии делили один тред и агент «вспоминал» чужой разбор; запрос без `sid` получает одноразовый тред.

## Безопасность

- `.env`-файлы и `DSLAB_API_KEY` в git не коммитятся (`.gitignore`).
- Наружу публикуется только `web_ui` (порт — `WEB_UI_PORT`, по умолчанию 8080). Агент (`8130`) и MCP (`3010`) доступны только внутри docker-сети; для отладки — `docker-compose.debug.yml`.
- CORS агента по умолчанию выключен; включается через `CORS_ALLOW_ORIGINS` только для разработки минуя nginx-прокси.
- `MCP_ALLOW_ALL_HOSTS=1` отключает защиту от DNS-rebinding — допустимо только внутри изолированной docker-сети.
- Контейнеры agent и MCP работают от непривилегированного пользователя; данные монтируются read-only.
- Данные `data/` — предоставленная выгрузка кейса (предагрегированная база банка, см. `reqs/context_pack.md` §4.1). Если репозиторий становится публичным, убедитесь, что условия хакатона допускают её публикацию.

## Документация

- `DEPLOY_YC.md` — пошаговый деплой на Yandex Cloud VM.
- `reqs/` — продуктовые спеки: правила сигналов, схема отчёта, профиль контрагента, контекст кейса.

## Лицензия

Apache-2.0 — см. [LICENSE](LICENSE).
