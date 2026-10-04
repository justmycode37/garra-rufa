"""Hosted entrypoint for the existing repository research pipelines."""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("PYTHONPATH", str(ROOT / "src"))
os.environ.setdefault("GARRA_RESEARCH_CACHE_DIR", "/tmp/garra-research-cache")
from garra.research.service import ResearchService, ResearchUnavailable
from garra.ui.service import InputError

service = ResearchService()


class handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(200, {"status": "ok", "service": "garra-research"})

    def do_POST(self):
        expected = os.environ.get("GARRA_RESEARCH_TOKEN", "")
        provided = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if not expected or not hmac.compare_digest(expected, provided):
            return self.reply(401, {"error": "Unauthorized"})
        route = urlsplit(self.path).path
        if route not in {"/api/research/search", "/api/research/papers", "/api/research/atlas"}:
            return self.reply(404, {"error": "Not found"})
        if self.headers.get_content_type() != "application/json":
            return self.reply(415, {"error": "Use application/json"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 4096:
                return self.reply(413, {"error": "Invalid request size"})
            body = json.loads(self.rfile.read(size))
            result = service.atlas(body) if route.endswith("/atlas") else service.search(body, papers=route.endswith("/papers"))
            self.reply(200, result)
        except (ValueError, InputError):
            self.reply(400, {"error": "Use a valid research query"})
        except ResearchUnavailable as error:
            self.reply(503, {"error": str(error)})
        except Exception:
            self.reply(503, {"error": "Research is temporarily unavailable"})
