"""
Локальный dev-сервер web_ui: раздаёт статику и проксирует /chat, /health в agent API.

Нужен потому, что фронт ходит на относительный /chat (как через nginx в docker),
а python -m http.server проксировать не умеет. Повторяет поведение nginx.conf.

Запуск (агент должен быть поднят отдельно на :8130):
    python services/web_ui/dev_server.py      # UI: http://localhost:8080

Переменные окружения: PORT (по умолчанию 8080), AGENT_ORIGIN (по умолчанию http://localhost:8130).
"""
import http.server
import json
import logging
import os
import urllib.error
import urllib.request

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
)
log = logging.getLogger("web_ui.dev")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
AGENT_ORIGIN = os.getenv("AGENT_ORIGIN", "http://localhost:8130").rstrip("/")
PORT = int(os.getenv("PORT", "8080"))
PROXY_PREFIXES = ("/chat", "/health")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def _proxy(self):
        body = None
        if self.command == "POST":
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
        req = urllib.request.Request(
            AGENT_ORIGIN + self.path,
            data=body,
            method=self.command,
            headers={"Content-Type": self.headers.get("Content-Type", "application/json")},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                data, status, ctype = resp.read(), resp.status, resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            data, status, ctype = e.read(), e.code, e.headers.get("Content-Type", "application/json")
        except urllib.error.URLError as e:
            # Агент не поднят — фронт покажет «Локальный агент недоступен»
            log.error("proxy %s %s → agent unreachable: %s", self.command, self.path, e.reason)
            data = json.dumps({"ok": False, "error": f"agent unreachable: {e.reason}"}).encode()
            status, ctype = 502, "application/json"
        log.info("proxy %s %s → %s", self.command, self.path, status)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith(PROXY_PREFIXES):
            return self._proxy()
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith(PROXY_PREFIXES):
            return self._proxy()
        self.send_error(404)

    def log_message(self, format, *args):
        # Дефолтный access-лог http.server перенаправляем в logging
        log.info("%s %s", self.address_string(), format % args)


if __name__ == "__main__":
    log.info("web_ui dev server on http://localhost:%s (agent: %s)", PORT, AGENT_ORIGIN)
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
