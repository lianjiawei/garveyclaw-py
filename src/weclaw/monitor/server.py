from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import urlsplit

from weclaw.core.agent_activity import build_agent_activity_snapshot
from weclaw.monitor.pixel_office_core_adapter import build_pixel_office_core_payload

PIXEL_OFFICE_CORE_DIR = Path(__file__).resolve().parents[3] / "pixel-office-core"
DEFAULT_HOST = os.getenv("WECLAW_DASHBOARD_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("WECLAW_DASHBOARD_PORT", "8765"))
IMMUTABLE_ASSET_EXTENSIONS = {
    ".css",
    ".gif",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".png",
    ".svg",
    ".webp",
}
STATIC_CACHE_CONTROL = "public, max-age=86400"


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    PIXEL_OFFICE_CORE_DIR.mkdir(parents=True, exist_ok=True)

    class CombinedHandler(SimpleHTTPRequestHandler):
        def end_headers(self) -> None:
            request_path = urlsplit(self.path).path
            directory = Path(str(getattr(self, "directory", ""))).resolve()
            if directory == PIXEL_OFFICE_CORE_DIR.resolve() and (
                request_path in {"/weclaw-dashboard.html", "/weclaw-dashboard.js"} or request_path.startswith("/dist/")
            ):
                self.send_header("Cache-Control", "no-store")
            elif Path(request_path).suffix.lower() in IMMUTABLE_ASSET_EXTENSIONS:
                self.send_header("Cache-Control", STATIC_CACHE_CONTROL)
            super().end_headers()

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            request_path = parsed.path
            query_suffix = f"?{parsed.query}" if parsed.query else ""

            if request_path in {"", "/"}:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", f"/core{query_suffix}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return

            if request_path in {"/api/activity", "/api/activity/"}:
                payload = build_agent_activity_snapshot()
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if request_path in {"/api/pixel-office-core/commands", "/api/pixel-office-core/commands/"}:
                payload = build_pixel_office_core_payload()
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if request_path in {"/core", "/core/", "/pixel-office-core"}:
                self.path = f"/weclaw-dashboard.html{query_suffix}"
                self.directory = str(PIXEL_OFFICE_CORE_DIR)
                return super().do_GET()

            if request_path.startswith("/core/"):
                self.path = f"{request_path.removeprefix('/core')}{query_suffix}"
                self.directory = str(PIXEL_OFFICE_CORE_DIR)
                return super().do_GET()

            self.send_error(HTTPStatus.NOT_FOUND, "Only the core dashboard is available at /core.")

        def log_message(self, format: str, *args: object) -> None:
            return

    handler = CombinedHandler
    server = ThreadingHTTPServer((host, port), handler)
    print(f"WeClaw core dashboard running at http://{host}:{port}/core")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    serve()


def dashboard_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    return f"http://{host}:{port}/core"


def start_background_dashboard(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> tuple[threading.Thread | None, str, str | None]:
    url = dashboard_url(host, port)

    def runner() -> None:
        try:
            serve(host=host, port=port)
        except OSError as exc:
            print(f"Dashboard startup failed at {url}: {exc}")

    thread = threading.Thread(target=runner, daemon=True, name="pixel-office-dashboard")
    try:
        thread.start()
    except OSError as exc:
        return None, url, str(exc)
    return thread, url, None


if __name__ == "__main__":
    main()
