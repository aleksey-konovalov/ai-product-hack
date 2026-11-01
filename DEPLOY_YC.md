# Деплой на Yandex Cloud (Docker Compose)

Сервис состоит из трёх контейнеров:

| Контейнер | Порт | Назначение |
|---|---|---|
| `web_ui` | `${WEB_UI_PORT:-8080}` → 80 | Веб-интерфейс: статика + reverse proxy `/chat`, `/health` → agent. **Единственный вход для пользователя** |
| `agent` | `8130` (внутренний) | API агента (FastAPI): чат, structured output. Наружу не публикуется |
| `verification_mcp_server` | `3010` (внутренний) | MCP-сервер: данные о контрагентах по ИНН. Наружу не публикуется |

`web_ui` проксирует запросы в `agent` по внутренней сети compose (`http://agent:8130`),
`agent` ходит в MCP (`http://verification_mcp_server:3010/mcp`).
Данные монтируются из `./data` в MCP-контейнер (read-only). Наружу публикуется
только порт web_ui — он задаётся переменной `WEB_UI_PORT` из корневого `.env`
(по умолчанию 8080; при конфликте с другим приложением поменяйте там).

---

## 1. Подготовка ВМ (Yandex Compute Cloud)

- Образ: Ubuntu 22.04 LTS, минимум 2 vCPU / 2 ГБ RAM (LLM выполняется во внешнем API — GPU не нужен).
- Открыть в security group входящий TCP `8080` (порты агента и MCP наружу не нужны).

Установить Docker:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # перелогиниться после этого
```

## 2. Доставить код на сервер

```bash
git clone git@github.com:CyX4Puk/AITHackathon.git
cd AITHackathon
```

Данные (`data/contractors_audit.snapshot.json`) уже в репозитории — отдельно копировать не нужно.

## 3. Создать `.env` с секретами (в git их нет)

```bash
cp services/agent/.env.example services/agent/.env
cp .env.example .env      # переменные compose; нужен порт входа, отличный от 8080 — меняйте WEB_UI_PORT
```

Отредактировать `services/agent/.env`:

```env
ACTIVE_LLM_PROFILE=api_deepseek_agent
DSLAB_API_KEY=<ваш ключ dslab>
```

`MCP_VERIFICATION_URL` внутри compose переопределяется автоматически — трогать не нужно.

## 4. Запуск

```bash
docker compose up -d --build
```

Проверка:

```bash
docker compose ps
curl -s http://localhost:8080/health
```

`/health` (через web_ui → agent) должен вернуть `{"ok": true, "profile": "api_deepseek_agent", ...}`.

Веб-интерфейс: `http://<внешний-IP-ВМ>:8080/`

## 5. Логи и управление

```bash
docker compose logs -f web_ui                  # access/error nginx
docker compose logs -f agent                   # логи агента
docker compose logs -f verification_mcp_server
docker compose up -d agent                     # после смены профиля в .env
docker compose down                            # остановить всё
```

---

## Смена LLM-профиля

Профили описаны в `services/agent/src/core/llm_config.json`
(`api_deepseek_agent`, `api_qwen_agent`, `api_glm_flash_agent`, `local_qwen_agent`).
Поменять `ACTIVE_LLM_PROFILE` в `services/agent/.env` и
`docker compose up -d agent` (пересоздаст контейнер с новым env).

## Отладка API напрямую (минуя web_ui)

Основной compose наружу ничего не публикует, кроме web_ui. Для отладки
подключите файл с отладочными портами (хост = внутренний + 10000):

```bash
docker compose -f docker-compose.yml -f docker-compose.debug.yml up -d
# agent: 18130, MCP: 13010
curl -s -X POST http://localhost:18130/chat -H 'Content-Type: application/json' \
  -d '{"message":"проверь 9721159668","session":{"inns":["9721159668"],"sid":"debug"}}'
```

После отладки перезапустите обычным `docker compose up -d` — публикации закроются.

## Замечания по безопасности

- `.env`-файлы и реальный `DSLAB_API_KEY` в git не коммитятся (см. `.gitignore`).
- Наружу публикуется только web_ui (`${WEB_UI_PORT:-8080}`); agent (`8130`) и MCP
  (`3010`) доступны только внутри docker-сети. Отладочные публикации — только в
  `docker-compose.debug.yml`, на постоянной основе не оставляйте.
- Логи сервисов ротируются docker'ом (`max-size: 10m`, 3 файла) — диск VM не переполняется.
