"""
Локальный веб-агент: HTML-страница общается с локально развёрнутой моделью.

Запуск:
    .venv/bin/python services/root_agent/src/web_agent.py
Открыть: http://localhost:8130

Что делает:
  GET  /            → отдаёт web/index.html (фронт на базе reqs/ui/mockup.html)
  GET  /health      → {ok, model, llm}
  POST /chat        → принимает {message, analyze_results, primary_inn, session_len, kind_hint}
                      зовёт LM Studio (SYSTEM_PROMPT + response_format из report_contract),
                      валидирует (G3/G4) и возвращает {ok, turn, validation}.

Данные о контрагентах бэкенд получает из MCP-сервера коллеги
(get_companies_info_by_inn) — по ИНН из сессии, а НЕ из JSON клиента.
Клиент присылает лишь ИНН сессии и флаги (assessment_given, deal_context);
grounding для модели строится из отчётов, полученных через MCP
(enrich_from_mcp + report_to_analyze). Роутинг интентов и отрисовка карточек —
на клиенте. Бэкенд отвечает за «язык» (structured output модели).
"""
import json
import re
import os
import time
import urllib.request
import urllib.error
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.getenv("PORT", "8130"))


def _read_env(path):
    """Простейший парсер .env (KEY=VALUE)."""
    env = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


def _load_profile():
    """Активный профиль LLM: из ACTIVE_LLM_PROFILE (.env / окружение) + llm_config.json."""
    env = _read_env(os.path.join(HERE, "..", ".env"))
    name = os.getenv("ACTIVE_LLM_PROFILE") or env.get("ACTIVE_LLM_PROFILE") or "local_qwen_agent"
    cfg = json.load(open(os.path.join(HERE, "core", "llm_config.json"), encoding="utf-8"))
    p = cfg.get(name) or cfg["local_qwen_agent"]
    key_env = p.get("api_key_env", "")
    api_key = ""
    if key_env and key_env != "any":
        api_key = os.getenv(key_env) or env.get(key_env) or ""
    base = p["base_url"].rstrip("/")
    extra = p.get("extra_body") or {}
    # Модель не «размышляет», если профиль явно выключил thinking (локальный qwen и qwen по API
    # на dslab это уважают) → можно держать маленький max_tokens без переполнения reasoning.
    ctk = extra.get("chat_template_kwargs") or {}
    no_think = ctk.get("enable_thinking") is False
    return {
        "name": name,
        "url": base + "/chat/completions",
        "model": p["model"],
        "api_key": api_key,
        "is_local": p.get("provider") in ("lmstudio", "vllm"),
        "extra_body": extra,
        "no_think": no_think,
        "temperature": float(p.get("temperature", 0.2)),
        "top_p": float(p.get("top_p", 0.9)),
        "key_env": key_env,
    }


PROFILE = _load_profile()
LM_URL = os.getenv("LM_URL", PROFILE["url"])
MODEL = os.getenv("LM_MODEL", PROFILE["model"])

# --- Логи в отдельный файл (для проверки, что запросы через MCP идут корректно) ---
import logging  # noqa: E402
LOG_PATH = os.getenv("WEB_AGENT_LOG", os.path.join(HERE, "..", "logs", "web_agent.log"))
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
log = logging.getLogger("web_agent")
if not log.handlers:
    log.setLevel(logging.INFO)
    _fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    _fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    _fh.setFormatter(_fmt)
    log.addHandler(_fh)
    _sh = logging.StreamHandler()
    _sh.setFormatter(_fmt)
    log.addHandler(_sh)
log.info("web_agent старт · профиль=%s · модель=%s · лог=%s", PROFILE["name"], MODEL, LOG_PATH)


def _llm_headers():
    h = {"Content-Type": "application/json"}
    if PROFILE["api_key"]:
        h["Authorization"] = "Bearer " + PROFILE["api_key"]
    return h

# report_contract без импорта пакета core (там agent.py тянет MCP на импорте)
_spec = importlib.util.spec_from_file_location("report_contract", os.path.join(HERE, "core", "report_contract.py"))
_rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rc)
SYSTEM_PROMPT, response_format = _rc.SYSTEM_PROMPT, _rc.response_format

# --- Данные о контрагентах берём из MCP-сервера коллеги (а не с клиента) ---
MCP_URL = os.getenv("MCP_VERIFICATION_URL", "http://localhost:3010/mcp")
_RISK = {"LOW": "низкий", "MEDIUM": "средний", "HIGH": "высокий", "UNKNOWN": "не определён"}
_ZSK = {"GREEN": "зелёный", "YELLOW": "жёлтый", "RED": "красный"}


def _mln(v):
    try:
        v = float(v) / 1e6
    except (TypeError, ValueError):
        return "—"
    return (f"{v:.1f}".replace(".", ",") + " млн ₽") if abs(v) < 1000 else (f"{v/1000:.1f}".replace(".", ",") + " млрд ₽")


def _mcp_call(text):
    """Вызывает MCP-инструмент get_companies_info_by_inn(text). Возвращает ({inn: report}, missing[]).
    ИНН из текста извлекает сам инструмент (его регулярка), решение о вызове принимает модель."""
    import asyncio
    from langchain_mcp_adapters.client import MultiServerMCPClient

    async def _run():
        client = MultiServerMCPClient({"verification": {"transport": "streamable_http", "url": MCP_URL}})
        tools = await client.get_tools()
        tool = next((t for t in tools if "inn" in t.name.lower()), tools[0])
        res = await tool.ainvoke({"text": text})
        if isinstance(res, (list, tuple)):
            res = "".join(x if isinstance(x, str) else (x.get("text", "") if isinstance(x, dict) else getattr(x, "text", "")) for x in res)
        data = json.loads(res)
        out = {}
        for rec in data.get("found", []):
            rep = rec.get("report") if isinstance(rec, dict) and "report" in rec else rec
            bi = (rep or {}).get("baseInfo") or {}
            if bi.get("inn"):
                out[str(bi["inn"])] = rep
        return out, data.get("missing", [])

    log.info("MCP → %s | tool=get_companies_info_by_inn text=%r", MCP_URL, text)
    try:
        out, missing = asyncio.run(_run())
    except Exception as exc:
        log.error("MCP ОШИБКА: %s", exc)
        raise
    log.info("MCP ← найдено=%s не_найдено=%s", list(out.keys()), missing)
    return out, missing


def _mcp_fetch(inns):
    """{inn: report} по списку ИНН (совместимость)."""
    if not inns:
        return {}
    found, _ = _mcp_call("проверь " + " ".join(str(i) for i in inns))
    return found


def report_to_analyze(rep):
    """Проекция сырого отчёта банка (из MCP) в компактный analyze для модели."""
    bi = rep.get("baseInfo") or {}
    inn = str(bi.get("inn") or "")
    risk, zsk = bi.get("riskLevel"), rep.get("zskRiskLevel")
    years = (bi.get("registrationInfo") or {}).get("yearsFromRegistration")
    signals, gaps = [], []
    signals.append({"id": "BANK_RISK", "polarity": "info", "slot": "Оценка банка",
                    "message": f"Оценка банка: риск {_RISK.get(risk, '—')}" + (f", ЗСК {_ZSK[zsk]}" if zsk in _ZSK else "") + ".",
                    "fields": ["baseInfo.riskLevel", "zskRiskLevel"]})
    ys = sorted([(c.get("common") or {}) for c in (rep.get("finReports") or []) if isinstance(c, dict)],
                key=lambda c: c.get("year") or 0)
    ys = [c for c in ys if c.get("year") and c.get("proceeds") is not None]
    if len(ys) >= 2:
        a, b = ys[0], ys[-1]
        pct = round((b["proceeds"] - a["proceeds"]) / abs(a["proceeds"]) * 100) if a["proceeds"] else None
        pol = "positive" if (pct or 0) > 5 else "negative" if (pct or 0) < -5 else "info"
        signals.append({"id": "FIN_PROCEEDS", "polarity": pol, "slot": "Финансы",
                        "message": f"Выручка: {_mln(a['proceeds'])} ({a['year']}) → {_mln(b['proceeds'])} ({b['year']})" + (f", {'+' if pct >= 0 else ''}{pct} %" if pct is not None else "") + ".",
                        "fields": ["finReports[].common.proceeds"]})
    elif len(ys) == 1:
        signals.append({"id": "FIN_ONE_YEAR", "polarity": "gap", "slot": "Финансы",
                        "message": f"Выручка есть только за {ys[0]['year']} ({_mln(ys[0]['proceeds'])}); тренд оценить нельзя.", "fields": ["finReports[].common.proceeds"]})
        gaps.append({"text": "данные о выручке только за один год", "ref": "finReports[].common.proceeds"})
    else:
        signals.append({"id": "FIN_MISSING", "polarity": "gap", "slot": "Финансы", "message": "Финансовая отчётность в отчёте отсутствует.", "fields": ["finReports"]})
        gaps.append({"text": "финансовой отчётности нет", "ref": "finReports"})
    ep = rep.get("executionProceedings") or []
    active = [e for e in ep if isinstance(e, dict) and str(e.get("active")).lower() == "true"]
    if active:
        signals.append({"id": "EP_ACTIVE", "polarity": "negative", "slot": "Юридические события",
                        "message": f"Активные исполнительные производства: {len(active)}.", "fields": ["executionProceedings[].active"]})
    arb = rep.get("arbitrationByStatus") or {}
    if arb.get("commonCount"):
        signals.append({"id": "ARB", "polarity": "info", "slot": "Юридические события",
                        "message": f"Арбитраж: всего дел {arb.get('commonCount')}.", "fields": ["arbitrationByStatus.commonCount"]})
    for n in [x for x in ((rep.get("reputationalRisks") or {}).get("negative") or []) if isinstance(x, dict)]:
        signals.append({"id": "REG_" + str(n.get("code")), "polarity": "caution", "slot": "Реестры ФНС",
                        "message": (n.get("name") or n.get("code") or "Отметка реестра ФНС.")[:220],
                        "fields": [f"reputationalRisks.negative[code={n.get('code')}]"]})
    status = (rep.get("status") or {})
    if status.get("reasonName"):
        signals.append({"id": "STATUS_REASON", "polarity": "caution", "slot": "Оценка банка",
                        "message": f"Статус ЕГРЮЛ: {status.get('reasonName')}.", "fields": ["status.reasonName"]})
    profile = {"name": bi.get("fullName") or bi.get("shortName"), "short": bi.get("shortName"), "inn": inn,
               "riskLabel": _RISK.get(risk), "zskLabel": _ZSK.get(zsk), "size": bi.get("companySize"),
               "age": (f"{years} лет" if years else None), "address": bi.get("address")}
    return {"inn": inn, "profile": profile, "signals": signals, "gaps": gaps,
            "summary_slots": [s["id"] for s in signals], "next_steps": []}


def enrich_from_mcp(analyze):
    """Заменяет данные каждого контрагента на полученные из MCP; сохраняет флаги сессии
    (assessment_given, deal_context) с клиента. Возвращает (analyze, source)."""
    inns = [str(k) for k in (analyze or {}).keys()]
    try:
        reports = _mcp_fetch(inns)
    except Exception as exc:
        return analyze, f"client (MCP недоступен: {exc})"
    used_mcp = False
    for inn, a in (analyze or {}).items():
        rep = reports.get(str(inn))
        if not rep:
            continue
        derived = report_to_analyze(rep)
        keep_assessment = a.get("assessment_given")
        keep_deal = a.get("deal_context")
        a["signals"] = derived["signals"]
        a["gaps"] = derived["gaps"]
        a["summary_slots"] = derived["summary_slots"]
        a.setdefault("profile", {}).update(derived["profile"])
        if keep_assessment is not None:
            a["assessment_given"] = keep_assessment
        if keep_deal is not None:
            a["deal_context"] = keep_deal
        used_mcp = True
    return analyze, ("mcp" if used_mcp else "client (в MCP не найдено)")


_LEAK = re.compile(r"finReports|baseInfo|reputationalRisks|executionProceedings|arbitration|foundersInfo|zskRiskLevel|\b(LOW|MEDIUM|HIGH|UNKNOWN|GREEN|YELLOW|RED)\b")


def _extract_json(s: str) -> str:
    s = (s or "").split("</think>")[-1]
    i, j = s.find("{"), s.rfind("}")
    return s[i:j + 1] if i >= 0 and j > i else s


def _parse_turn(raw: str, latency) -> dict:
    """Устойчивый разбор ответа модели в turn. Берёт ПЕРВЫЙ валидный JSON-объект
    (игнорируя markdown-обёртку и лишний хвост/второй объект). Если JSON не найден —
    оборачивает текст как обычный ответ, чтобы UI не падал."""
    s = (raw or "").split("</think>")[-1].strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    dec = json.JSONDecoder()
    idx = s.find("{")
    while idx != -1:
        try:
            obj, _end = dec.raw_decode(s[idx:])  # парсит первый объект, хвост игнорируется
            if isinstance(obj, dict) and ("reply_text" in obj or "brief" in obj or "answer_kind" in obj):
                obj["_latency_s"] = latency
                return obj
        except ValueError:
            pass
        idx = s.find("{", idx + 1)
    # Фолбэк: не удалось получить JSON — показываем текст как ответ
    text = re.sub(r"\s+", " ", re.sub(r"[{}\[\]\"]", " ", s)).strip()[:800] or "Не удалось разобрать ответ модели."
    return {"reply_text": text, "brief": [], "answer_kind": "qa", "primary_inn": None,
            "facts": [], "claims": [], "gaps": [], "next_steps": [], "headline": None,
            "panel": {"kind": "card", "inn": None, "block": None, "metric": None},
            "suggestions": [], "_latency_s": latency}


def _validate(turn: dict, analyze: dict) -> dict:
    allowed_ids, allowed_fields = set(), set()
    for a in (analyze or {}).values():
        for sg in a.get("signals", []):
            allowed_ids.add(sg.get("id"))
            allowed_fields.update(sg.get("fields", []))
        for st in a.get("next_steps", []):
            if isinstance(st, dict) and st.get("signal_id"):
                allowed_ids.add(st["signal_id"])
    issues = []
    for f in turn.get("facts", []):
        if f.get("signal_id") not in allowed_ids:
            issues.append(f"facts.signal_id вне analyze: {f.get('signal_id')}")
        for r in f.get("refs", []):
            if r not in allowed_fields:
                issues.append(f"facts.ref вне analyze: {r}")
    for c in turn.get("claims", []):
        if c.get("field") not in allowed_fields:
            issues.append(f"claims.field вне analyze: {c.get('field')}")
    rt = turn.get("reply_text", "") or ""
    brief = turn.get("brief", []) or []
    kind = turn.get("answer_kind")
    if kind == "summary":
        if rt.strip():
            issues.append("summary: reply_text должен быть пустым")
        if not brief:
            issues.append("summary: brief пуст (нет «Краткого вывода»)")
    if kind == "qa" and turn.get("facts"):
        issues.append("qa: facts должны быть пустыми")
    m = _LEAK.search(rt + "\n" + "\n".join(brief))
    if m:
        issues.append(f"LEAK в тексте: {m.group(0)}")
    return {"issues": issues, "allowed_signals": len(allowed_ids)}


def _post_llm(body, timeout=900):
    """POST к активному LLM-эндпоинту. Для не-локальных API при отказе от json_schema
    пробуем повтор без response_format (не все провайдеры его поддерживают)."""
    def _do(b):
        req = urllib.request.Request(LM_URL, data=json.dumps(b).encode(), headers=_llm_headers())
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    try:
        return _do(body)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        log.error("LLM HTTP %s: %s", getattr(e, "code", "?"), detail)
        if "response_format" in body:
            log.info("Повтор запроса БЕЗ response_format")
            b2 = {k: v for k, v in body.items() if k != "response_format"}
            return _do(b2)
        raise


def _call_llm(message: str, analyze: dict, primary_inn, session_len: int) -> dict:
    user = (
        "ANALYZE_RESULTS:\n" + json.dumps(analyze, ensure_ascii=False, indent=2) +
        f"\n\nСессия: компаний в сессии {session_len}, текущая — ИНН {primary_inn}.\n"
        f"Вопрос пользователя: \"{message}\""
    )
    local = PROFILE["is_local"]
    sys_content = SYSTEM_PROMPT + ("\n\nНЕ размышляй, сразу верни валидный JSON по схеме." if local else "\n\nВерни строго один валидный JSON-объект по схеме, без markdown и текста вокруг.")
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": user},
        ],
        "temperature": PROFILE["temperature"],
        # qwen по API (dslab) НЕнадёжно уважает enable_thinking — иногда всё равно пишет в
        # reasoning_content. Даём запас, чтобы финальный JSON не обрезался вместе с reasoning.
        "max_tokens": 1600 if local else 3000,
        "response_format": response_format(),
    }
    body.update(PROFILE["extra_body"])  # chat_template_kwargs/reasoning_effort из профиля
    t0 = time.time()
    resp = _post_llm(body)
    dt = round(time.time() - t0, 1)
    ch0 = resp["choices"][0]
    msg = ch0["message"]
    content = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning_content") or msg.get("reasoning") or "")
    log.info("LLM ответ: finish=%s content_len=%d reasoning_len=%d за %ss",
             ch0.get("finish_reason"), len(content), len(reasoning), dt)
    return _parse_turn(content or reasoning, dt)


# --- Агент сам решает, звать ли инструмент проверки (без регекса на клиенте) ---
TOOL_DEF = {
    "type": "function",
    "function": {
        "name": "get_companies_info_by_inn",
        "description": "Возвращает данные по контрагенту(ам) по ИНН из отчётов банка. "
                       "Вызывай, когда пользователь просит проверить компанию, указал ИНН, "
                       "или задаёт вопрос о компании из текущей сессии.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Текст с ИНН, например 'проверь 9721159668'. Для вопроса о текущей компании подставь её ИНН из контекста сессии."}},
            "required": ["text"],
        },
    },
}

TOOL_DECISION_PROMPT = (
    "Ты — агент проверки контрагентов Альфа-Банка. Тебе доступен инструмент "
    "get_companies_info_by_inn(text) — поиск ТОЛЬКО по ИНН (10 или 12 цифр).\n"
    "Вызови инструмент, если:\n"
    "— в сообщении есть ИНН (10/12 цифр) — передай его в text;\n"
    "— это вопрос о компании из текущей сессии — подставь ИНН этой компании из контекста.\n"
    "НЕ вызывай инструмент, если:\n"
    "— компания названа только по имени/бренду без ИНН (по названию не ищем);\n"
    "— в сообщении нет ИНН и нет текущей компании в сессии;\n"
    "— сообщение не по теме проверки контрагентов (болтовня, общие вопросы).\n"
    "Не рассуждай и не пиши текст — только реши про вызов инструмента."
)


def _session_ctx(session: dict) -> str:
    inns = [str(i) for i in (session.get("inns") or [])]
    selected = session.get("selected")
    parts = []
    if inns:
        parts.append("В сессии ИНН: " + ", ".join(inns) + ".")
    if selected:
        parts.append(f"Текущая компания сессии — ИНН {selected}.")
    return " ".join(parts) or "Сессия пуста."


def _tool_decision(message: str, ctx: str):
    """Фаза 1: модель решает, вызывать ли инструмент. Возвращает список text-аргументов вызовов."""
    local = PROFILE["is_local"]
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": TOOL_DECISION_PROMPT},
            {"role": "user", "content": ctx + "\nСообщение пользователя: " + message},
        ],
        "tools": [TOOL_DEF],
        "temperature": 0.1,
        # Запас на возможные размышления облачной модели до tool_call (dslab-qwen может думать).
        "max_tokens": 400 if local else 2500,
    }
    body.update(PROFILE["extra_body"])  # chat_template_kwargs/reasoning_effort из профиля
    req = urllib.request.Request(LM_URL, data=json.dumps(body).encode(), headers=_llm_headers())
    resp = json.loads(urllib.request.urlopen(req, timeout=600).read())
    msg = resp["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    texts = []
    for c in calls:
        try:
            args = json.loads((c.get("function") or {}).get("arguments") or "{}")
            if args.get("text"):
                texts.append(args["text"])
        except (ValueError, TypeError):
            continue
    log.info("РЕШЕНИЕ агента: %s", ("вызвать инструмент " + repr(texts)) if texts else "инструмент НЕ вызывать")
    return texts


def agent_turn(message: str, session: dict):
    """Полный ход агента: сам решает про вызов MCP, тянет данные, формирует структурный ответ.
    Возвращает (turn, analyze, source)."""
    log.info("ЗАПРОС: message=%r | сессия: inns=%s selected=%s", message, session.get("inns"), session.get("selected"))
    ctx = _session_ctx(session)
    tool_texts = _tool_decision(message, ctx)
    analyze, fetched, missing = {}, [], []
    for txt in tool_texts:
        try:
            found, miss = _mcp_call(txt or message)
        except Exception:
            found, miss = {}, []
        for inn, rep in found.items():
            a = report_to_analyze(rep)
            a["assessment_given"] = bool((session.get("assessment") or {}).get(inn))
            if session.get("deal"):
                a["deal_context"] = session["deal"]
            analyze[inn] = a
            if inn not in fetched:
                fetched.append(inn)
        missing += [m for m in miss if m not in missing]
    primary = fetched[0] if fetched else session.get("selected")
    session_len = max(len(analyze), len(session.get("inns") or []), 1)
    turn = _call_llm(message, analyze, primary, session_len)
    # qwen-API иногда выдаёт вырожденный JSON (утечка имён полей схемы в текст или пустой ответ).
    # Это интермиттентно — один ретрай почти всегда даёт чистый ответ. Локальную модель не ретраим.
    if not PROFILE["is_local"]:
        v = _validate(turn, analyze)
        leaked = any("LEAK" in i for i in v["issues"])
        empty = not (turn.get("reply_text") or "").strip() and not [p for p in (turn.get("brief") or []) if (p or "").strip()]
        if leaked or empty:
            log.info("Вырожденный ответ (leak=%s empty=%s) → ретрай _call_llm", leaked, empty)
            turn = _call_llm(message, analyze, primary, session_len)
    # Страховка: данные получены, но модель вернула пустой текст → соберём краткий вывод из сигналов
    if fetched and not (turn.get("reply_text") or "").strip() and not [p for p in (turn.get("brief") or []) if (p or "").strip()]:
        a = analyze.get(primary) or next(iter(analyze.values()), {})
        prof = a.get("profile") or {}
        head = prof.get("short") or prof.get("name") or ("ИНН " + str(primary))
        msgs = [s.get("message") for s in a.get("signals", []) if s.get("message")]
        turn["brief"] = [f"{head}: " + " ".join(msgs[:4])] if msgs else [f"{head}: данные получены, но детали недоступны."]
        turn["answer_kind"] = "summary"
        turn.setdefault("panel", {})["kind"] = turn["panel"].get("kind") or "card"
    turn["_fetched"] = fetched
    turn["_missing"] = missing
    source = "mcp" if fetched else "no-tool"
    log.info("ОТВЕТ: kind=%s source=%s fetched=%s missing=%s latency=%ss",
             turn.get("answer_kind"), source, fetched, missing, turn.get("_latency_s"))
    return turn, analyze, source


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "json" in ctype or "html" in ctype else ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            path = os.path.join(HERE, "web", "index.html")
            if not os.path.exists(path):
                return self._send(404, {"error": "index.html not built"})
            with open(path, "rb") as f:
                return self._send(200, f.read(), "text/html")
        if self.path.startswith("/health"):
            return self._send(200, {"ok": True, "profile": PROFILE["name"], "model": MODEL, "lm_url": LM_URL,
                                    "is_local": PROFILE["is_local"],
                                    "api_key_set": bool(PROFILE["api_key"]) if not PROFILE["is_local"] else True,
                                    "key_env": PROFILE["key_env"]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/chat":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"ok": False, "error": f"bad request: {e}"})
        # Сессия с фронта: агент сам решит, звать ли MCP (регекса на клиенте нет).
        session = payload.get("session")
        if not session:  # обратная совместимость со старым форматом
            ar = payload.get("analyze_results") or {}
            session = {"inns": list(ar.keys()), "selected": payload.get("primary_inn"),
                       "assessment": {k: v.get("assessment_given") for k, v in ar.items()},
                       "deal": next((v.get("deal_context") for v in ar.values() if v.get("deal_context")), None)}
        try:
            turn, analyze, source = agent_turn(payload.get("message", ""), session)
        except Exception as e:
            return self._send(200, {"ok": False, "error": f"agent error: {e}"})
        validation = _validate(turn, analyze)
        validation["data_source"] = source
        validation["fetched"] = turn.get("_fetched", [])
        validation["missing"] = turn.get("_missing", [])
        return self._send(200, {"ok": True, "turn": turn, "validation": validation})


if __name__ == "__main__":
    print(f"web_agent on http://localhost:{PORT}  (model={MODEL})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
