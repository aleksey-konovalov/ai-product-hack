# Деплой на Yandex Cloud (Docker Compose)

Сервис состоит из двух контейнеров:

| Контейнер | Порт | Назначение |
|---|---|---|
| `root_agent` | `8130` | Бэкенд-агент + веб-интерфейс (чат, structured output) |
| `verification_mcp_server` | `3010` | MCP-сервер: данные о контрагентах по ИНН |

`root_agent` ходит в MCP по внутренней сети compose (`http://verification_mcp_server:3010/mcp`).
Данные монтируются из `./data` в MCP-контейнер (read-only).

---

## 1. Подготовка ВМ (Yandex Compute Cloud)

- Образ: Ubuntu 22.04 LTS, минимум 2 vCPU / 2 ГБ RAM (LLM выполняется во внешнем API — GPU не нужен).
- Открыть в security group входящий TCP `8130` (и `3010`, только если MCP нужен снаружи — обычно не нужен).

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
cp services/root_agent/.env.example services/root_agent/.env
```

Отредактировать `services/root_agent/.env`:

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
curl -s http://localhost:8130/health
```

`/health` должен вернуть `{"ok": true, "profile": "api_deepseek_agent", ...}`.

Веб-интерфейс: `http://<внешний-IP-ВМ>:8130/`

## 5. Логи и управление

```bash
docker compose logs -f root_agent
docker compose logs -f verification_mcp_server
docker compose restart root_agent      # после смены профиля в .env
docker compose down                    # остановить всё
```

---

## Смена LLM-профиля

Профили описаны в `services/root_agent/src/core/llm_config.json`
(`api_deepseek_agent`, `api_qwen_agent`, `api_glm_flash_agent`, `local_qwen_agent`).
Поменять `ACTIVE_LLM_PROFILE` в `services/root_agent/.env` и
`docker compose up -d root_agent` (пересоздаст контейнер с новым env).

## Замечания по безопасности

- `.env`-файлы и реальный `DSLAB_API_KEY` в git не коммитятся (см. `.gitignore`).
- MCP-порт `3010` можно не публиковать наружу — он нужен только контейнеру `root_agent`.
  Чтобы закрыть, удалите блок `ports` у `verification_mcp_server` в `docker-compose.yml`
  (секция `expose` оставляет его доступным внутри docker-сети).
