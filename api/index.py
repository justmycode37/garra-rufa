"""Hosted entrypoint for the existing repository research pipelines."""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("PYTHONPATH", str(ROOT / "src"))
os.environ.setdefault("GARRA_RESEARCH_CACHE_DIR", "/tmp/garra-research-cache")
from garra.community import CommunityCatalog  # noqa: E402
from garra.discovery.hosted import load_discovery  # noqa: E402
from garra.research.service import ResearchService, ResearchUnavailable  # noqa: E402
from garra.ui.service import InputError  # noqa: E402

service = ResearchService()
community_path = Path(os.environ.get("GARRA_COMMUNITIES", ROOT / "examples/communities.demo.json"))
communities = CommunityCatalog(json.loads(community_path.read_text()) if community_path.exists() else None)


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

    def authorized(self):
        expected = os.environ.get("GARRA_RESEARCH_TOKEN", "")
        provided = self.headers.get("Authorization", "").removeprefix("Bearer ")
        return bool(expected) and hmac.compare_digest(expected.encode(), provided.encode())

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == "/health":
            try:
                engine, _ = load_discovery(ROOT)
                return self.reply(200, {"status": "ok", "service": "garra-research", "regions": True, "discovery": engine.metadata()})
            except Exception:
                return self.reply(503, {"error": "Discovery data is unavailable"})
        if not self.authorized():
            return self.reply(401, {"error": "Unauthorized"})
        known = route in {"/api/entity", "/api/regions", "/api/clusters", "/api/neighbors", "/api/communities"} or route.startswith(("/api/regions/", "/api/clusters/", "/api/communities/"))
        if not known:
            return self.reply(404, {"error": "not_found"})
        try:
            engine, regions = load_discovery(ROOT)
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if any(len(v) != 1 for v in query.values()):
                raise InputError("Use each filter once")
            if route == "/api/neighbors":
                if set(query) != {"entity_id"} or not query["entity_id"][0]:
                    raise InputError("Use one entity_id")
                result = regions.neighbors(query["entity_id"][0])
            elif route == "/api/communities":
                if set(query) - {"kind", "disease_id", "process_id"}:
                    raise InputError("Invalid community filters")
                result = communities.list(**{k: v[0] for k, v in query.items()})
            elif route == "/api/entity":
                if set(query) != {"id"} or not query["id"][0]:
                    raise InputError("Use one entity id")
                result = engine.entity_graph(query["id"][0])
            elif route == "/api/clusters":
                if set(query) - {"kind", "entity_id"}:
                    raise InputError("Invalid cluster filters")
                result = engine.clusters(**{k: v[0] for k, v in query.items()})
            else:
                if query:
                    raise InputError("Unexpected query parameters")
                if route == "/api/regions":
                    result = regions.catalog()
                elif route.startswith("/api/regions/"):
                    result = regions.detail(route.removeprefix("/api/regions/"))
                elif route.startswith("/api/communities/"):
                    result = communities.get(route.removeprefix("/api/communities/"))
                else:
                    result = engine.cluster(route.removeprefix("/api/clusters/"))
            self.reply(200, result) if result is not None else self.reply(404, {"error": "not_found"})
        except InputError as error:
            self.reply(400, {"error": str(error)})
        except Exception:
            self.reply(503, {"error": "Discovery is temporarily unavailable"})

    def do_POST(self):
        if not self.authorized():
            return self.reply(401, {"error": "Unauthorized"})
        route = urlsplit(self.path).path
        if route not in {"/api/search", "/api/research/search", "/api/research/papers", "/api/research/atlas"}:
            return self.reply(404, {"error": "Not found"})
        if self.headers.get_content_type() != "application/json":
            return self.reply(415, {"error": "Use application/json"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 4096:
                return self.reply(413, {"error": "Invalid request size"})
            body = json.loads(self.rfile.read(size))
            if route == "/api/search":
                result = load_discovery(ROOT)[0].search(body)
            elif route.endswith("/atlas"):
                result = load_discovery(ROOT)[1].atlas(body)
            else:
                result = service.search(body, papers=route.endswith("/papers"))
            self.reply(200, result)
        except (ValueError, InputError):
            self.reply(400, {"error": "Use a valid research query"})
        except ResearchUnavailable as error:
            self.reply(503, {"error": str(error)})
        except Exception:
            self.reply(503, {"error": "Research is temporarily unavailable"})
