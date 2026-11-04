# UID/GID хост-пользователя: контейнер dev-tools раскладывает файлы в data/
# с правами хоста, а не root.
UID := $(shell id -u)
GID := $(shell id -g)
export UID GID

# Единственный обязательный артефакт: MCP-сервер читает снапшот из ./data.
# Если файла нет на make up — запускается скачивание (make download).
SNAPSHOT_PATH := data/contractors_audit.snapshot.json

# Ruff пинится здесь и в .github/workflows/ci.yml — одинаковые проверки локально и в CI.
RUFF_VERSION := 0.17.3

.PHONY: up down logs download check-data lint test

# Поднять весь стек; перед стартом докачивает данные, если их нет.
up: check-data
	docker compose up -d --build

# Скачать данные с Google Drive (уже скачанные файлы не трогает).
download:
	docker compose run --rm dev-tools \
		bash -c "pip install --target=/tmp/packages --quiet 'gdown>=5.1.0,<6' python-dotenv \
			&& python shared/utils/download_project_assets.py"

# Внутренняя цель: проверка данных перед up.
check-data:
	@test -s $(SNAPSHOT_PATH) || { \
		echo "==> $(SNAPSHOT_PATH) отсутствует — запускаю скачивание"; \
		$(MAKE) download; \
	}

down:
	docker compose down

logs:
	docker compose logs -f

# Линт Python-кода (конфиг — ruff.toml). Кэш ruff — в /tmp, чтобы не сорить в репозитории.
lint:
	docker run --rm -v "$(CURDIR)":/work -w /work \
		-e RUFF_CACHE_DIR=/tmp/ruff-cache \
		ghcr.io/astral-sh/ruff:$(RUFF_VERSION) check .

# Тесты в собранном образе agent (README, раздел «Тесты») — заодно проверяет сборку.
# Явный тег вместо имени compose-проекта; без env_file — тесты идут в герметичном
# окружении из tests/conftest.py, а не с реальными настройками сервиса.
test:
	docker build -t agent-tests -f services/agent/Dockerfile .
	docker run --rm -v "$(CURDIR)":/work -w /work --entrypoint bash agent-tests -c \
		"python -m pip install -q -r requirements-dev.txt && PYTHONPATH=/work python -m pytest"
