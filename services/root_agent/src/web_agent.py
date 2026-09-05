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

Данные и роутинг интентов живут на клиенте (как в mockup): страница строит
analyze_results из своей БД и передаёт сюда. Бэкенд отвечает только за «язык»
(structured output модели) — ровно та граница, что в интеграционном доке.
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
        try:
            turn = _call_llm(payload.get("message", ""), analyze,
                             payload.get("primary_inn"), int(payload.get("session_len", 1)))
        except Exception as e:
            return self._send(200, {"ok": False, "error": f"LLM error: {e}"})
        validation = _validate(turn, analyze)
        return self._send(200, {"ok": True, "turn": turn, "validation": validation})


if __name__ == "__main__":
    print(f"web_agent on http://localhost:{PORT}  (model={MODEL})")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
