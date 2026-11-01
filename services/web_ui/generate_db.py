#!/usr/bin/env python3
"""
Генератор фронтовой базы данных (static/db.js) из data/contractors_audit.snapshot.json.

Зачем: раньше БД (const DB) лежала прямо в index.html и при изменении снапшота
синхронизировалась вручную. Теперь данные живут в отдельном db.js, а числа
обновляются одной командой:

    python services/web_ui/generate_db.py            # обновить механические поля из снапшота
    python services/web_ui/generate_db.py --check    # выйти с ошибкой, если db.js устарел (для CI)

Что откуда берётся:
  - «Механические» поля (inn, ogrn, risk, zsk, reg, age, fin, arb, related, ...)
    вычисляются из снапшота однозначно и перечитываются при каждом запуске;
  - «Курируемые» поля (short, address, report, trend, ep, regNeg, regPos, facts,
    gaps, steps, cmp) — тексты и упрощения, сделанные руками под макет (например,
    число реестров ФНС в снапшоте не совпадает с макетом — это осознанная
    иллюстративность, см. комментарий в index.html). Они переносятся из текущего
    db.js без изменений;
  - компания из снапшота, которой ещё нет в db.js, добавляется с механическими
    полями и автогенерированным повествованием (facts/cmp/gaps/steps) по шаблонам.

db.js хранится как `const DB = {strict JSON}` — валидный JS и одновременно
парсится этим скриптом без внешних зависимостей.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SNAPSHOT_PATH = ROOT / "data" / "contractors_audit.snapshot.json"
DB_JS_PATH = HERE / "static" / "db.js"
INDEX_HTML_PATH = HERE / "static" / "index.html"

# Фиксированная таблица «сигнал → шаг» (переехала из index.html вместе с DB)
STEP = {
    'ep': ['Запросить подтверждение погашения или справку ФССП; учесть сумму в условиях оплаты.', 'активные исполнительные производства'],
    'arb': ['Посмотреть предмет открытых дел в картотеке арбитражных дел; уточнить у контрагента.', 'дела как ответчик'],
    'addr': ['Подтвердить фактический адрес и реквизиты: договор аренды, свежая выписка ЕГРЮЛ.', 'адрес'],
    'block': ['Запросить справку об отсутствии задолженности; не работать по предоплате до снятия блокировки.', 'блокировка счетов ФНС'],
    'auth': ['Запросить документы о полномочиях подписанта.', 'недостоверные данные руководителя'],
    'fin': ['Рассмотреть постоплату или обеспечение; запросить пояснения по отчётности.', 'финансы'],
    'finmiss': ['Запросить бухгалтерскую отчётность за 2–3 года напрямую.', 'отчётности недостаточно'],
    'finip': ['Запросить налоговую декларацию или выписку по счёту за период.', 'ИП не публикует отчётность'],
    'young': ['Запросить рекомендации и портфолио; начать с небольшого объёма.', 'компания моложе 3 лет'],
    'change': ['Уточнить причины смены; проверить полномочия нового руководителя по выписке.', 'смена руководителя / учредителя'],
    'status': ['Уточнить статус в ЕГРЮЛ/ЕФРСБ до любых переговоров.', 'статус в ЕГРЮЛ'],
    'reason': ['Запросить свежую выписку ЕГРЮЛ и уточнить, что за процедура идёт.', 'процедура в ЕГРЮЛ'],
}

STATUS_MAP = {"CURRENT": "действует"}
MSK = timedelta(hours=3)  # даты снапшота в UTC, в макете — по Москве

# Поля, которые генератор НЕ трогает (курируются в db.js руками)
CURATED_FIELDS = (
    "short", "address", "report", "trend", "coef", "ep", "insp",
    "regNeg", "regPos", "headline", "facts", "gaps", "steps", "cmp",
)


# ---------------------------------------------------------------------------
# Толерантный парсер JS-литералов (одинарные кавычки, ключи без кавычек,
# вызовы st(...)) — нужен только для первичного извлечения DB из index.html.
# ---------------------------------------------------------------------------

class _JsParser:
    def __init__(self, text: str):
        self.s, self.i = text, 0

    def _skip_ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
            self.i += 1

    def parse(self):
        self._skip_ws()
        return self._value()

    def _value(self):
        self._skip_ws()
        c = self.s[self.i]
        if c == "{":
            return self._dict()
        if c == "[":
            return self._list()
        if c in "'\"":
            return self._string()
        num = re.match(r"-?\d+(?:\.\d+)?", self.s[self.i:])
        if num:
            self.i += num.end()
            raw = num.group(0)
            return float(raw) if "." in raw else int(raw)
        ident = re.match(r"[A-Za-z_$][\w$]*", self.s[self.i:])
        if not ident:
            raise ValueError(f"Неожиданный символ {c!r} на позиции {self.i}")
        word, self.i = ident.group(0), self.i + ident.end()
        if word == "null":
            return None
        if word == "true":
            return True
        if word == "false":
            return False
        if word == "st":  # вызов st(k, why?) — материализуем на месте вызова
            self._skip_ws()
            if self.s[self.i] != "(":
                raise ValueError(f"Ожидался вызов st(...) на позиции {self.i}")
            self.i += 1
            args = [self._value()]
            self._skip_ws()
            while self.s[self.i] == ",":
                self.i += 1
                args.append(self._value())
                self._skip_ws()
            self.i += 1  # закрывающая скобка
            return {"__st__": args}
        raise ValueError(f"Неизвестный идентификатор {word!r} на позиции {self.i}")

    def _string(self):
        quote = self.s[self.i]
        self.i += 1
        out = []
        while self.s[self.i] != quote:
            if self.s[self.i] == "\\":
                self.i += 1
            out.append(self.s[self.i])
            self.i += 1
        self.i += 1
        return "".join(out)

    def _dict(self):
        self.i += 1
        result = {}
        self._skip_ws()
        while self.s[self.i] != "}":
            self._skip_ws()
            if self.s[self.i] in "'\"":
                key = self._string()
            else:
                m = re.match(r"[A-Za-z_$][\w$]*", self.s[self.i:])
                if not m:
                    raise ValueError(f"Ожидался ключ объекта на позиции {self.i}")
                key = m.group(0)
                self.i += m.end()
            self._skip_ws()
            if self.s[self.i] != ":":
                raise ValueError(f"Ожидался ':' после ключа {key!r} на позиции {self.i}")
            self.i += 1
            result[key] = self._value()
            self._skip_ws()
            if self.s[self.i] == ",":
                self.i += 1
                self._skip_ws()
        self.i += 1
        return result

    def _list(self):
        self.i += 1
        result = []
        self._skip_ws()
        while self.s[self.i] != "]":
            result.append(self._value())
            self._skip_ws()
            if self.s[self.i] == ",":
                self.i += 1
                self._skip_ws()
        self.i += 1
        return result


def _materialize_steps(node, step_table):
    """Заменяет маркеры st(...) на готовые {text, why}."""
    if isinstance(node, dict):
        if set(node) == {"__st__"}:
            k, why = node["__st__"][0], (node["__st__"][1] if len(node["__st__"]) > 1 else None)
            return {"text": step_table[k][0], "why": why or step_table[k][1]}
        return {k: _materialize_steps(v, step_table) for k, v in node.items()}
    if isinstance(node, list):
        return [_materialize_steps(v, step_table) for v in node]
    return node


def extract_db_from_index_html() -> dict:
    """Первичное извлечение DB из index.html (когда db.js ещё не существует)."""
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    m = re.search(r"const DB = \{(.*?)\n\};", html, re.DOTALL)
    if not m:
        sys.exit("Не удалось найти блок `const DB = {...}` в index.html")
    db = _JsParser("{" + m.group(1) + "}").parse()
    return _materialize_steps(db, STEP)


def load_db_js() -> dict:
    """Читает db.js (строгое JSON после `const DB = `)."""
    text = DB_JS_PATH.read_text(encoding="utf-8")
    m = re.search(r"^const DB = (\{.*\});\s*$", text, re.DOTALL | re.MULTILINE)
    if not m:
        sys.exit(f"{DB_JS_PATH}: не найден блок `const DB = {{...}}`")
    return json.loads(m.group(1))


# ---------------------------------------------------------------------------
# Форматтеры (значения снапшота → строки макета)
# ---------------------------------------------------------------------------

def _num(value):
    """Число из значения снапшота: int/float либо Mongo-формат {"$numberLong": "..."}."""
    if isinstance(value, dict):
        value = value.get("$numberLong")
    if value is None:
        return None
    return float(value)


def fmt_money(amount) -> str | None:
    """957189 → '957,2 тыс. ₽'; 806284000 → '806,3 млн ₽'; 116300000000 → '116,3 млрд ₽'."""
    amount = _num(amount)
    if amount is None:
        return None
    for scale, unit in ((1e9, "млрд ₽"), (1e6, "млн ₽"), (1e3, "тыс. ₽")):
        if amount >= scale:
            value = f"{amount / scale:.1f}".replace(".", ",")
            return f"{value} {unit}"
    return f"{int(amount)} ₽"


def fmt_date(d) -> str | None:
    """{'$date': '2022-03-15T21:00:00.000Z'} → '16.03.2022' (дата по Москве)."""
    if not d or not isinstance(d, dict) or "$date" not in d:
        return None
    dt = datetime.fromisoformat(d["$date"].replace("Z", "+00:00")) + MSK
    return dt.strftime("%d.%m.%Y")


def fmt_age(years) -> str | None:
    if years is None:
        return None
    if years == 0:
        return "меньше года"
    mod10, mod100 = years % 10, years % 100
    if mod10 == 1 and mod100 != 11:
        word = "год"
    elif 2 <= mod10 <= 4 and not 12 <= mod100 <= 14:
        word = "года"
    else:
        word = "лет"
    return f"{years} {word}"


def human_case(name: str | None) -> str | None:
    """'ДАУТОВА АНАСТАСИЯ АРТУРОВНА' → 'Даутова Анастасия Артуровна'."""
    return None if not name else " ".join(w.capitalize() for w in name.split())


def quotes_ru(name: str | None) -> str | None:
    """'ООО "ТРАНСЛОРИУМ"' → 'ООО «ТРАНСЛОРИУМ»'."""
    return re.sub(r'"([^"]*)"', r"«\1»", name) if name else name


def short_brand_name(full_name: str | None) -> str | None:
    """'ООО "ТРАНСЛОРИУМ"' → 'Транслориум'; 'ИП ПЕТРОВ А.В.' → 'ИП Петров' (эвристика)."""
    if not full_name:
        return None
    name = re.sub(r'^(ООО|ОАО|ЗАО|ПАО|АО)\s*"([^"]+)"$', r"\2", full_name)
    if name == full_name and full_name.upper().startswith("ИП "):
        name = "ИП " + full_name[3:].split()[0].capitalize()
    elif name != full_name:
        name = name.capitalize()
    return name


# ---------------------------------------------------------------------------
# Механический маппинг: запись снапшота → поля db.js
# ---------------------------------------------------------------------------

def _fin_rows(fin_reports) -> list:
    rows = []
    for fr in fin_reports or []:
        common = fr.get("common") or {}
        assets = fr.get("assets") or {}
        current = assets.get("currentAssets") or {}
        liabilities = fr.get("liabilities") or {}
        stl = liabilities.get("shortTermLiabilities") or {}

        def millions(key, source):
            value = _num(source.get(key))
            return round(value / 1e6, 1) if value is not None else None

        rows.append({
            "y": common.get("year"),
            "pr": millions("proceeds", common),
            "pf": millions("profit", common),
            "cap": millions("capitals", liabilities),
            "stl": millions("total", stl),
            "ca": millions("total", current),
        })
    return rows


def _arb_block(arb) -> dict | None:
    if not arb or arb.get("commonCount") is None:
        return None
    plaintiff = arb.get("plaintiffArbitration") or {}
    defendant = arb.get("defandantArbitration") or {}  # опечатка в источнике — сохраняем как есть
    pfin = plaintiff.get("plaintiffArbitrationFinished") or {}
    dfin = defendant.get("defandantArbitrationFinished") or {}
    return {
        "total": arb.get("commonCount"),
        "sum": fmt_money(arb.get("commonAmount")),
        "pf": pfin.get("pfCount", 0),
        "pfA": fmt_money(pfin.get("pfAmount")),
        "pa": len(plaintiff.get("plaintiffArbitrationAppealed") or {}),
        "pp": len(plaintiff.get("plaintiffArbitrationPending") or {}),
        "df": dfin.get("dfCount", 0),
        "da": len(defendant.get("defandantArbitrationAppealed") or {}),
        "dp": len(defendant.get("defandantArbitrationPending") or {}),
    }


def _people(founders_info, reference_date):
    if not isinstance(founders_info, dict):
        return None, []
    auth = founders_info.get("authPerson") or None
    if auth:
        since = fmt_date(auth.get("positionDate"))
        recent = _is_recent(auth.get("positionDate"), reference_date)
        auth = {"name": human_case(auth.get("name")), "pos": (auth.get("positionName") or "").lower(),
                "since": since, "recent": recent}
    founders = [
        {"name": human_case(c.get("name")), "share": f"{c['share']:g} %" if c.get("share") is not None else None,
         "since": fmt_date(c.get("dateFrom")), "recent": _is_recent(c.get("dateFrom"), reference_date)}
        for c in founders_info.get("cofounders") or []
    ]
    return auth, founders


def _is_recent(date_dict, reference_date) -> bool:
    if not date_dict or not isinstance(date_dict, dict) or "$date" not in date_dict:
        return False
    dt = datetime.fromisoformat(date_dict["$date"].replace("Z", "+00:00"))
    ref = reference_date or datetime.now(dt.tzinfo)
    return (ref - dt) <= timedelta(days=365)


def mechanical_fields(record: dict) -> dict:
    """Все поля db.js, однозначно вычисляемые из записи снапшота."""
    report = record.get("report") or {}
    base = report.get("baseInfo") or {}
    registration = base.get("registrationInfo") or {}
    reg_date = registration.get("registrationDate")
    kinds = report.get("kindsOfActivityInfo") or {}
    main_kind = kinds.get("mainKindOfActivity") or {}
    tax = report.get("taxSystem") or []
    status = report.get("status") or {}
    status_date = status.get("date")
    inn = str(base.get("inn") or "")
    founders_info = report.get("foundersInfo")
    reference_date = (datetime.fromisoformat(status_date["$date"].replace("Z", "+00:00"))
                      if status_date and isinstance(status_date, dict) and "$date" in status_date else None)
    auth, founders = _people(founders_info, reference_date)

    return {
        "inn": inn,
        "isIP": len(inn) == 12,
        "name": quotes_ru(base.get("shortName")),
        "full": base.get("fullName"),
        "ogrn": base.get("ogrn"),
        "kpp": base.get("kpp"),
        "risk": base.get("riskLevel"),
        "zsk": report.get("zskRiskLevel"),
        "status": STATUS_MAP.get(status.get("status"), status.get("status")),
        "statusDate": fmt_date(status_date),
        "reg": fmt_date(reg_date),
        "age": fmt_age(registration.get("yearsFromRegistration")),
        "size": base.get("companySize"),
        "okved": f"{main_kind.get('code')} — {main_kind.get('description')}" if main_kind else None,
        "okvedOther": len(kinds.get("otherKindsOfActivity") or []),
        "email": base.get("email"),
        "tax": (tax[0].get("shortName") if tax else None),
        "fin": _fin_rows(report.get("finReports")),
        "arb": _arb_block(report.get("arbitrationByStatus")),
        "auth": auth,
        "founders": founders,
        "related": len(report.get("relatedCompanies") or []),
        "licenses": [],
    }


# --- Шаблоны повествования для компаний, которых ещё нет в db.js -------------

def _template_narrative(entry: dict, report: dict) -> None:
    """Заполняет facts/gaps/steps/cmp/trend/regNeg новичка по шаблонам."""
    negative = (report.get("reputationalRisks") or {}).get("negative") or []
    positive = (report.get("reputationalRisks") or {}).get("positive") or []
    ep = report.get("executionProceedings") or []
    active_ep = [e for e in ep if e.get("active")]
    is_ip = entry["isIP"]

    facts, cmp_bank = [], []
    facts.append({"slot": "Оценка банка", "pol": "info",
                  "text": f"Оценка банка: {RISK_TEXT.get(entry['risk'], 'риск ' + str(entry['risk'] or 'не определён')).lower()}, ЗСК {ZSK_TEXT.get(entry['zsk'], entry['zsk'] or 'не определён').lower()}.",
                  "refs": ["baseInfo.riskLevel", "zskRiskLevel"]})
    cmp_bank.append(["info", f"{RISK_TEXT.get(entry['risk'], entry['risk'] or '—')}, ЗСК {entry['zsk'] or '—'}"])

    if is_ip:
        facts.append({"slot": "Финансы", "pol": "gap",
                      "text": "ИП не публикует бухгалтерскую отчётность — оценить финансы по отчёту нельзя.",
                      "refs": ["finReports"]})
    elif entry["fin"]:
        first, last = entry["fin"][0], entry["fin"][-1]
        if len(entry["fin"]) >= 2 and first["pr"] and last["pr"] and first["y"] != last["y"]:
            delta = round((last["pr"] / first["pr"] - 1) * 100)
            pol = "positive" if delta >= 0 else "caution"
            facts.append({"slot": "Финансы", "pol": pol,
                          "text": f"Выручка {'растёт' if delta >= 0 else 'снижается'}: {fmt_money(first['pr'] * 1e6)} ({first['y']}) → {fmt_money(last['pr'] * 1e6)} ({last['y']}), {delta:+d} %.",
                          "refs": [f"finReports[year={first['y']}].common.proceeds", f"finReports[year={last['y']}].common.proceeds"]})
        else:
            facts.append({"slot": "Финансы", "pol": "info",
                          "text": f"Выручка за {last['y']}: {fmt_money(last['pr'] * 1e6)}." if last["pr"] else "Данных о выручке в отчёте нет.",
                          "refs": ["finReports[].common.proceeds"]})

    if active_ep:
        facts.append({"slot": "Юридические события", "pol": "negative",
                      "text": f"Активные исполнительные производства: {len(active_ep)}.",
                      "refs": ["executionProceedings"]})
    else:
        facts.append({"slot": "Юридические события", "pol": "positive",
                      "text": "Исполнительные производства не найдены.", "refs": ["executionProceedings"]})
    facts.append({"slot": "Реестры ФНС",
                  "pol": "caution" if negative else "positive",
                  "text": (f"Негативных отметок ФНС: {len(negative)}." if negative
                           else f"Проверено {len(positive)} реестров ФНС, негативных отметок нет."),
                  "refs": ["reputationalRisks"]})

    gaps = [{"text": "сведений о проверках контролирующих органов в отчёте нет", "ref": "inspections"}]
    if is_ip:
        gaps.insert(0, {"text": "ИП не публикует отчётность", "ref": "finReports"})

    codes = []
    if active_ep:
        codes.append("ep")
    if negative:
        codes.append("addr")  # ближайший шаг по реестрам ФНС
    if is_ip:
        codes.append("finip")
    elif not entry["fin"]:
        codes.append("finmiss")
    if entry["age"] in ("меньше года", "1 год", "2 года"):
        codes.append("young")

    entry.update({
        "short": short_brand_name(entry["full"]) or entry["name"],
        "address": None,
        "report": None,
        "trend": None,
        "coef": None,
        "ep": {"active": [], "closed": []},
        "insp": None,
        "regNeg": [{"code": n.get("code"), "pol": "caution", "text": (n.get("name") or "").rstrip(".") + "."}
                   for n in negative],
        "regPos": len(positive),
        "headline": None,
        "facts": facts,
        "gaps": gaps,
        "steps": [{"text": STEP[c][0], "why": STEP[c][1]} for c in codes],
        "cmp": {"bank": cmp_bank, "fin": [], "legal": [], "reg": [], "gapsRow": []},
    })


RISK_TEXT = {"LOW": "Риск низкий", "MEDIUM": "Риск средний", "HIGH": "Риск высокий"}
ZSK_TEXT = {"GREEN": "зелёный", "YELLOW": "жёлтый", "RED": "красный"}


def refresh_from_snapshot(db: dict, snapshot: list, add_missing: bool = False) -> tuple[dict, list]:
    """Обновляет механические поля существующих записей. С add_missing=True
    добавляет компании из снапшота, которых ещё нет в db.js (автогенерированное
    повествование). Возвращает (db, список обновлённых ИНН)."""
    index = {}
    for record in snapshot:
        report = record.get("report") or {}
        base = report.get("baseInfo") or {}
        inn = str(base.get("inn") or "")
        if inn:
            index[inn] = record

    new_db, seen = {}, []
    for inn, curated in db.items():
        record = index.get(inn)
        if record is None:
            print(f"  ! {inn}: нет в снапшоте — запись оставлена без изменений", file=sys.stderr)
            new_db[inn] = curated
            continue
        seen.append(inn)
        entry = mechanical_fields(record)
        if curated.get("isIP"):
            entry["isIP"] = True  # не сбрасывать флаг, если в снапшоне ИНН вдруг 10-значный
        for field in CURATED_FIELDS:
            entry[field] = curated.get(field)
        new_db[inn] = entry

    if add_missing:
        for inn, record in index.items():
            if inn not in new_db:
                print(f"  + {inn}: новая компания из снапшота — запись сгенерирована", file=sys.stderr)
                entry = mechanical_fields(record)
                _template_narrative(entry, record.get("report") or {})
                new_db[inn] = entry

    return new_db, seen


HEADER = """\
// ФРОНТОВАЯ БАЗА ДАННЫХ — сгенерирована services/web_ui/generate_db.py
// из data/contractors_audit.snapshot.json. Числа (механические поля) обновляются
// командой `python services/web_ui/generate_db.py`. Курируемые тексты (short,
// address, report, trend, ep, regNeg, regPos, facts, gaps, steps, cmp) при
// перегенерации сохраняются — править их можно прямо в этом файле.
const DB = """


def write_db_js(db: dict) -> None:
    body = json.dumps(db, ensure_ascii=False, indent=2)
    DB_JS_PATH.write_text(HEADER + body + ";\n", encoding="utf-8")
    print(f"  записано {DB_JS_PATH} ({len(db)} компаний)", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="не писать файл; ошибка, если перегенерация что-то меняет")
    ap.add_argument("--add-missing", action="store_true",
                    help="добавить компании из снапшота, которых ещё нет в db.js "
                         "(по умолчанию набор компаний демо не расширяется)")
    args = ap.parse_args()

    if not DB_JS_PATH.exists():
        print("db.js не найден — первичное извлечение из index.html", file=sys.stderr)
        db = extract_db_from_index_html()
    else:
        db = load_db_js()

    if SNAPSHOT_PATH.exists():
        db, _ = refresh_from_snapshot(db, json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")),
                                      add_missing=args.add_missing)
    else:
        print(f"  ! снапшот не найден: {SNAPSHOT_PATH} — работаем с текущим db.js", file=sys.stderr)

    if args.check:
        current = DB_JS_PATH.read_text(encoding="utf-8")
        if current != HEADER + json.dumps(db, ensure_ascii=False, indent=2) + ";\n":
            sys.exit("db.js устарел: перегенерация меняет файл (запустите generate_db.py без --check)")
        print("db.js актуален", file=sys.stderr)
    else:
        write_db_js(db)


if __name__ == "__main__":
    main()
