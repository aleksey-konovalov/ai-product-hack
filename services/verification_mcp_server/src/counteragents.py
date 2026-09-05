import os
import json
import asyncio
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple, Union

Counteragent = Dict[str, Any]
InnInput = Optional[Union[str, Iterable[str]]]
AgentCallback = Callable[[Any], Any]
PayloadBuilder = Callable[[Counteragent], Any]


async def _normalize_inns(inns: InnInput) -> List[str]:
    """
    Приводит входные ИНН к списку строк.
    Поддерживает один ИНН (строкой или числом) или список/кортеж ИНН.
    """
    if inns is None:
        return []

    # Одиночный ИНН может прийти не строкой (например, числом из JSON)
    if isinstance(inns, (str, int)):
        raw_items = [inns]
    else:
        raw_items = list(inns)

    seen: Set[str] = set()
    result: List[str] = []

    for item in raw_items:
        inn = str(item).strip()
        if inn and inn not in seen:
            seen.add(inn)
            result.append(inn)

    return result


async def _extract_base_inn(record: Counteragent) -> str:
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


async def load_db(source: Union[List[Counteragent], str, Path]) -> List[Counteragent]:
    """
    Загружает базу контрагентов.
    Поддерживает:
    - уже готовый list;
    - JSON-строку;
    - путь к JSON-файлу.
    """
    if isinstance(source, list):
        return source

    if isinstance(source, Path):
        data = json.loads(await asyncio.to_thread(source.read_text, encoding="utf-8"))

    elif isinstance(source, str):
        text = source.strip()

        # Если передали сам JSON, а не путь к файлу
        if text.startswith(("[", "{")):
            data = json.loads(text)
        else:
            path = Path(text)
            if not path.exists():
                raise FileNotFoundError(f"Файл с JSON не найден: {text}")

            data = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise TypeError("db должен быть списком объектов, JSON-строкой или Path")

    # Если JSON представлен одним объектом, оборачиваем его в список
    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        raise ValueError("Ожидается, что база контрагентов является списком объектов")

    return data


async def get_counteragents_by_inn(
    db: Union[List[Counteragent], str, Path],
    inns: InnInput,
) -> Tuple[List[Counteragent], List[str]]:
    """
    Возвращает:
    - найденных контрагентов;
    - список ИНН, которые не найдены.
    """
    records = await load_db(db)

    wanted = await _normalize_inns(inns)
    wanted_set = set(wanted)

    found: List[Counteragent] = []
    found_inns: Set[str] = set()

    for record in records:
        inn = await _extract_base_inn(record)

        if inn in wanted_set:
            found.append(record)
            found_inns.add(inn)

    missing = [inn for inn in wanted if inn not in found_inns]

    return found, missing


async def short_company_info(record: Counteragent) -> Dict[str, Any]:
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


async def fetch_counteragents_for_agent(
    db: Union[List[Counteragent], str, Path],
    inns: InnInput,
    agent: AgentCallback,
    *,
    per_record: bool = False,
    raise_on_missing: bool = False,
    payload_builder: Optional[PayloadBuilder] = None,
) -> Dict[str, Any]:
    """
    Достает одного или нескольких контрагентов по ИНН и передает их данные агенту.

    Параметры:
    - db: список контрагентов, JSON-строка или путь к JSON-файлу;
    - inns: один ИНН или список ИНН;
    - agent: функция/обработчик, который получит данные;
    - per_record:
        False: агент получит список найденных контрагентов;
        True: агент будет вызван отдельно для каждого контрагента;
    - raise_on_missing:
        False: если часть ИНН не найдена, функция просто укажет их в missing;
        True: если есть ненайденные ИНН, будет выброшена ошибка;
    - payload_builder:
        функция, которая может преобразовать исходную запись контрагента
        в более компактный объект перед отправкой агенту.

    Возвращает:
    {
        "found": [...],              # исходные найденные записи
        "missing": [...],            # ИНН, которые не найдены
        "payload_sent_to_agent": ... # то, что реально передали агенту
    }
    """
    found, missing = await get_counteragents_by_inn(db, inns)

    if missing and raise_on_missing:
        raise LookupError(f"Не найдены контрагенты по ИНН: {', '.join(missing)}")

    if payload_builder is not None:
        payload = [payload_builder(record) for record in found]
    else:
        payload = found

    if per_record:
        for item in payload:
            agent(item)
    else:
        agent(payload)

    return {
        "found": found,
        "missing": missing,
        "payload_sent_to_agent": payload,
    }


def _use_full_payload() -> bool:
    """
    Читает env-переменную COUNTERAGENTS_FULL_PAYLOAD.
    Значения 1/true/yes/on (регистронезависимо) включают полные данные.
    """
    raw = os.getenv("COUNTERAGENTS_FULL_PAYLOAD", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


async def build_counteragents_payload(
    db: Union[List[Counteragent], str, Path],
    inns: InnInput,
    *,
    raise_on_missing: bool = False,
    full: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Собирает ответ для MCP-тула (без колбэков):
    - found: данные найденных контрагентов;
    - missing: ИНН, которых нет в базе.

    По умолчанию отдаются краткие данные (short_company_info).
    Если env-переменная COUNTERAGENTS_FULL_PAYLOAD=true (или аргумент full=True) —
    возвращаются полные исходные записи контрагентов.
    Аргумент full имеет приоритет над env-переменной.
    """
    found, missing = await get_counteragents_by_inn(db, inns)

    if missing and raise_on_missing:
        raise LookupError(f"Не найдены контрагенты по ИНН: {', '.join(missing)}")

    if full is None:
        full = _use_full_payload()

    if full:
        found_payload: List[Any] = found
    else:
        found_payload = [await short_company_info(record) for record in found]

    return {
        "found": found_payload,
        "missing": missing,
    }