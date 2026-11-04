"""
Работа с базой контрагентов: загрузка снапшота (с кэшем по mtime), поиск по ИНН,
сборка payload для MCP-тула.
"""
import asyncio
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

Counteragent = dict[str, Any]
InnInput = str | int | Iterable[str] | None
DbSource = list[Counteragent] | str | Path


def _normalize_inns(inns: InnInput) -> list[str]:
    """
    Приводит входные ИНН к списку уникальных непустых строк.
    Поддерживает один ИНН (строкой или числом) или список/кортеж ИНН.
    """
    if inns is None:
        return []

    # Одиночный ИНН может прийти не строкой (например, числом из JSON)
    raw_items = [inns] if isinstance(inns, (str, int)) else list(inns)

    seen: set = set()
    result: list[str] = []
    for item in raw_items:
        inn = str(item).strip()
        if inn and inn not in seen:
            seen.add(inn)
            result.append(inn)
    return result


def _extract_base_inn(record: Counteragent) -> str:
    """
    Достает ИНН контрагента из report.baseInfo.inn.
    Если поля нет или структура битая, возвращает пустую строку.
    """
    if not isinstance(record, dict):
        return ""
    report = record.get("report")
    if not isinstance(report, dict):
        return ""
    base_info = report.get("baseInfo")
    if not isinstance(base_info, dict):
        return ""
    inn = base_info.get("inn")
    return "" if inn is None else str(inn).strip()


def short_company_info(record: Counteragent) -> Counteragent:
    """Компактная сводка по контрагенту (когда полные отчёты не нужны)."""
    report = record.get("report") or {}
    base_info = report.get("baseInfo") or {}
    status = report.get("status") or {}
    fin_reports = report.get("finReports") or []

    latest_fin = fin_reports[0] if fin_reports else {}
    common = latest_fin.get("common") or {}

    return {
        "inn": base_info.get("inn"),
        "ogrn": base_info.get("ogrn"),
        "shortName": base_info.get("shortName"),
        "fullName": base_info.get("fullName"),
        "riskLevel": base_info.get("riskLevel"),
        "status": status.get("status"),
        "companySize": base_info.get("companySize"),
        "latestProceedsYear": common.get("year"),
        "latestProceeds": common.get("proceeds"),
    }


def _ensure_list(data) -> list[Counteragent]:
    """JSON-база: один объект оборачиваем в список, иначе проверяем тип."""
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("Ожидается, что база контрагентов является списком объектов")
    return data


def _build_index(records: list[Counteragent]) -> dict[str, list[Counteragent]]:
    """Индекс ИНН → записи (один ИНН может встречаться несколько раз)."""
    index: dict[str, list[Counteragent]] = {}
    for record in records:
        inn = _extract_base_inn(record)
        if inn:
            index.setdefault(inn, []).append(record)
    return index


class _SnapshotCache:
    """
    Кэш файловой базы: перечитывает файл только при изменении mtime и держит
    индекс ИНН → записи, чтобы каждый вызов тула не парсил весь снапшот заново.
    Одноразовые источники (list, JSON-строка) обрабатываются без кэша.
    """

    def __init__(self):
        self._path: Path | None = None
        self._mtime: float | None = None
        self._index: dict[str, list[Counteragent]] = {}

    def get_index(self, source: DbSource) -> dict[str, list[Counteragent]]:
        if isinstance(source, list):
            return _build_index(source)

        text = str(source).strip()
        # Если передали сам JSON, а не путь к файлу
        if text.startswith(("[", "{")):
            return _build_index(_ensure_list(json.loads(text)))

        path = Path(text)
        if not path.exists():
            raise FileNotFoundError(f"Файл с JSON не найден: {text}")

        mtime = path.stat().st_mtime
        if path != self._path or mtime != self._mtime:
            self._index = _build_index(_ensure_list(json.loads(path.read_text(encoding="utf-8"))))
            self._path, self._mtime = path, mtime
        return self._index


_cache = _SnapshotCache()


def get_counteragents_by_inn(
    db: DbSource,
    inns: InnInput,
) -> tuple[list[Counteragent], list[str]]:
    """
    Возвращает:
    - найденных контрагентов (в порядке запрошенных ИНН);
    - список ИНН, которые не найдены.
    """
    index = _cache.get_index(db)
    wanted = _normalize_inns(inns)

    found: list[Counteragent] = []
    found_inns: set = set()
    for inn in wanted:
        for record in index.get(inn, []):
            found.append(record)
            found_inns.add(inn)

    missing = [inn for inn in wanted if inn not in found_inns]
    return found, missing


def _use_full_payload() -> bool:
    """
    Читает env-переменную COUNTERAGENTS_FULL_PAYLOAD.
    Значения 1/true/yes/on (регистронезависимо) включают полные данные.
    """
    raw = os.getenv("COUNTERAGENTS_FULL_PAYLOAD", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


async def build_counteragents_payload(
    db: DbSource,
    inns: InnInput,
    *,
    raise_on_missing: bool = False,
    full: bool | None = None,
) -> dict[str, Any]:
    """
    Собирает ответ для MCP-тула:
    - found: данные найденных контрагентов;
    - missing: ИНН, которых нет в базе.

    По умолчанию отдаются краткие данные (short_company_info).
    Если env-переменная COUNTERAGENTS_FULL_PAYLOAD=true (или аргумент full=True) —
    возвращаются полные исходные записи контрагентов.
    Аргумент full имеет приоритет над env-переменной.
    """
    # Чтение файла при смене mtime — блокирующий I/O, выносим из event loop
    found, missing = await asyncio.to_thread(get_counteragents_by_inn, db, inns)

    if missing and raise_on_missing:
        raise LookupError(f"Не найдены контрагенты по ИНН: {', '.join(missing)}")

    if full is None:
        full = _use_full_payload()

    found_payload: list[Any] = found if full else [short_company_info(record) for record in found]

    return {
        "found": found_payload,
        "missing": missing,
    }
