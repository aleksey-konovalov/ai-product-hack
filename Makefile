# UID/GID хост-пользователя: контейнер dev-tools раскладывает файлы в data/
# с правами хоста, а не root.
UID := $(shell id -u)
GID := $(shell id -g)
export UID GID

# Единственный обязательный артефакт: MCP-сервер читает снапшот из ./data.
# Если файла нет на make up — запускается скачивание (make download).
SNAPSHOT_PATH := data/contractors_audit.snapshot.json

.PHONY: up down logs download check-data

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
