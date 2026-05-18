from __future__ import annotations

import json
import os
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import urlsplit

from weclaw.core.agent_activity import build_agent_activity_snapshot
from weclaw.monitor.pixel_office_core_adapter import build_pixel_office_core_payload

ASSETS_ROOT = Path(__file__).resolve().parent / "assets"
CLASSIC_ASSETS_DIR = ASSETS_ROOT / "pixel-office"
V2_ASSETS_DIR = ASSETS_ROOT / "pixel-office-v2"
PIXEL_OFFICE_CORE_DIR = Path(__file__).resolve().parents[3] / "pixel-office-core"
DEFAULT_HOST = os.getenv("WECLAW_DASHBOARD_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("WECLAW_DASHBOARD_PORT", "8765"))


class PixelOfficeHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in {"/api/activity", "/api/activity/"}:
            payload = build_agent_activity_snapshot()
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in {"/", "/pixel-office", "/classic"}:
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, format: str, *args: object) -> None:
        return


class PixelOfficeV2Handler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in {"/api/activity", "/api/activity/"}:
            payload = build_agent_activity_snapshot()
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in {"/v2", "/v2/", "/pixel-office-v2"}:
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    CLASSIC_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    V2_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    class CombinedHandler(SimpleHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            request_path = parsed.path
            query_suffix = f"?{parsed.query}" if parsed.query else ""

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

            if request_path in {"/", "/pixel-office", "/classic"}:
                self.path = f"/index.html{query_suffix}"
                self.directory = str(CLASSIC_ASSETS_DIR)
                return super().do_GET()

            if request_path in {"/v2", "/v2/", "/pixel-office-v2"}:
                self.path = f"/index.html{query_suffix}"
                self.directory = str(V2_ASSETS_DIR)
                return super().do_GET()

            if request_path.startswith("/v2/"):
                self.path = f"{request_path.removeprefix('/v2')}{query_suffix}"
                self.directory = str(V2_ASSETS_DIR)
                return super().do_GET()

            if request_path in {"/core", "/core/", "/pixel-office-core"}:
                self.path = f"/weclaw-dashboard.html{query_suffix}"
                self.directory = str(PIXEL_OFFICE_CORE_DIR)
                return super().do_GET()

            if request_path.startswith("/core/"):
                self.path = f"{request_path.removeprefix('/core')}{query_suffix}"
                self.directory = str(PIXEL_OFFICE_CORE_DIR)
                return super().do_GET()

            self.directory = str(CLASSIC_ASSETS_DIR)
            return super().do_GET()

        def log_message(self, format: str, *args: object) -> None:
            return

    handler = CombinedHandler
    server = ThreadingHTTPServer((host, port), handler)
    print(
        f"Pixel Office dashboard running at http://{host}:{port} (classic) | "
        f"http://{host}:{port}/v2 | http://{host}:{port}/core"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    serve()


def dashboard_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    return f"http://{host}:{port}"


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
