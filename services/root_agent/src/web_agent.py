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
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
LM_URL = os.getenv("LM_URL", "http://localhost:1234/v1/chat/completions")
MODEL = os.getenv("LM_MODEL", "qwen/qwen3.8-27b")
PORT = int(os.getenv("PORT", "8130"))

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


def _mcp_fetch(inns):
    """Тянет полные отчёты по ИНН из MCP-сервера (get_companies_info_by_inn). {inn: report}."""
    if not inns:
        return {}
    import asyncio
    from langchain_mcp_adapters.client import MultiServerMCPClient

    async def _run():
        client = MultiServerMCPClient({"verification": {"transport": "streamable_http", "url": MCP_URL}})
        tools = await client.get_tools()
        tool = next((t for t in tools if "inn" in t.name.lower()), tools[0])
        res = await tool.ainvoke({"text": "проверь " + " ".join(inns)})
        if isinstance(res, (list, tuple)):
            res = "".join(x if isinstance(x, str) else (x.get("text", "") if isinstance(x, dict) else getattr(x, "text", "")) for x in res)
        data = json.loads(res)
        out = {}
        for rec in data.get("found", []):
            rep = rec.get("report") if isinstance(rec, dict) and "report" in rec else rec
            bi = (rep or {}).get("baseInfo") or {}
            if bi.get("inn"):
                out[str(bi["inn"])] = rep
        return out

    return asyncio.run(_run())


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


def _call_llm(message: str, analyze: dict, primary_inn, session_len: int) -> dict:
    user = (
        "ANALYZE_RESULTS:\n" + json.dumps(analyze, ensure_ascii=False, indent=2) +
        f"\n\nСессия: компаний в сессии {session_len}, текущая — ИНН {primary_inn}.\n"
        f"Вопрос пользователя: \"{message}\""
    )
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT + "\n\nНЕ размышляй, сразу верни валидный JSON по схеме."},
            {"role": "user", "content": user + " /no_think"},
        ],
        "temperature": 0.2,
        "max_tokens": 1600,
        "response_format": response_format(),
        "chat_template_kwargs": {"enable_thinking": False},
        "reasoning_effort": "low",
    }
    req = urllib.request.Request(LM_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.time()
    resp = json.loads(urllib.request.urlopen(req, timeout=900).read())
    dt = round(time.time() - t0, 1)
    msg = resp["choices"][0]["message"]
    raw = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or msg.get("reasoning") or "")
    turn = json.loads(_extract_json(raw))
    turn["_latency_s"] = dt
    return turn


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
            return self._send(200, {"ok": True, "model": MODEL, "lm_url": LM_URL})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/chat":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"ok": False, "error": f"bad request: {e}"})
        analyze = payload.get("analyze_results") or {}
        # Данные о контрагентах берём из MCP-сервера (а не с клиента); клиентские
        # флаги сессии (assessment_given, deal_context) сохраняются.
        analyze, source = enrich_from_mcp(analyze)
        try:
            turn = _call_llm(payload.get("message", ""), analyze,
                             payload.get("primary_inn"), int(payload.get("session_len", 1)))
        except Exception as e:
            return self._send(200, {"ok": False, "error": f"LLM error: {e}"})
        validation = _validate(turn, analyze)
        validation["data_source"] = source
        return self._send(200, {"ok": True, "turn": turn, "validation": validation})


if __name__ == "__main__":
    print(f"web_agent on http://localhost:{PORT}  (model={MODEL})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
