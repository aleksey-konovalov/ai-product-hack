"""Скачивание данных проекта с Google Drive.

Запускается в служебном контейнере dev-tools (`make download`); локальный
запуск: `python shared/utils/download_project_assets.py` (нужны gdown и
python-dotenv, переменные берутся из корневого .env).

ID файлов задаются в .env (см. .env.example). Уже скачанные файлы не
перезаписываются — чтобы перекачать, удалите файл в data/.
"""

import json
import os
import sys
from pathlib import Path

import gdown

REPO_ROOT = Path(__file__).resolve().parents[2]

# env-ключ -> (путь от корня репозитория, обязателен ли файл для запуска стека)
ASSETS_MAP = {
    "GDRIVE_SNAPSHOT_JSON_ID": ("data/contractors_audit.snapshot.json", True),
}


def validate_json(path: Path) -> None:
    """Проверка, что Drive отдал данные, а не страницу ошибки или чужой файл."""
    try:
        with path.open(encoding="utf-8") as f:
            json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        head = path.read_bytes()[:200].decode("utf-8", errors="replace")
        raise ValueError(
            "скачанный файл не является корректным JSON — проверьте, что ID "
            f"ведёт на нужный файл (начало файла: {head!r})"
        ) from None


# Валидатор по расширению; для остальных файлов — только проверка размера.
VALIDATORS = {".json": validate_json}


def download_assets() -> None:
    failures = []
    for env_key, (rel_path, required) in ASSETS_MAP.items():
        dest = REPO_ROOT / rel_path
        file_id = os.getenv(env_key)

        if not file_id:
            msg = f"{env_key} не задан — добавьте в .env (см. .env.example)"
            if required:
                failures.append(f"{rel_path}: {msg}")
            else:
                print(f"[WARN] {msg}, пропускаю {rel_path}")
            continue

        if dest.is_file() and dest.stat().st_size > 0:
            print(f"[SKIP] уже скачан: {rel_path}")
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        # Качаем во временный файл и переименовываем: прерванная загрузка не
        # оставит обрезанный файл, который следующий запуск примет за готовый.
        tmp = dest.with_suffix(dest.suffix + ".part")
        print(f"[DOWN] {rel_path} (Google Drive id: {file_id})")
        try:
            gdown.download(id=file_id, output=str(tmp), quiet=False, fuzzy=True)
            if not tmp.is_file() or tmp.stat().st_size == 0:
                raise RuntimeError("gdown не вернул файл (проверьте ID и доступ к Drive)")
            validator = VALIDATORS.get(dest.suffix)
            if validator:
                validator(tmp)
            elif tmp.stat().st_size < 1000:
                raise ValueError("файл подозрительно мал — вероятно, страница об ошибке Drive")
            tmp.replace(dest)
        except Exception as e:
            print(f"[ERROR] {rel_path}: {e}")
            tmp.unlink(missing_ok=True)
            failures.append(f"{rel_path}: {e}")

    if failures:
        print("\nНе удалось получить данные:")
        for failure in failures:
            print(f"  - {failure}")
        print("Исправьте и повторите: make download")
        sys.exit(1)

    print("[OK] все данные на месте")


if __name__ == "__main__":
    try:
        # В dev-tools переменные приходят из compose; load_dotenv — для запуска на хосте.
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass
    download_assets()
