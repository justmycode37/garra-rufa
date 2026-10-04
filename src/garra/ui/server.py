"""Local stdlib HTTP bridge. Run: python -m garra.ui --port 8787."""

import argparse
import json
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from garra.community import CommunityCatalog
from garra.discovery import DiscoveryEngine
from garra.research.service import ResearchService, ResearchUnavailable

from .catalog import get_catalog
from .monarch import UpstreamError
from .service import ConnectionService, InputError


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, service, origins, communities, discovery, research, **kwargs):
        self.service, self.origins = service, origins
        self.communities = communities
        self.discovery = discovery
        self.research = research
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        # Don't persist request details or symptom queries.
        pass

    def _allowed(self):
        origin = self.headers.get("Origin")
        local_origins = {
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }
        return not origin or origin in self.origins or origin in local_origins

    def _reply(self, code, value):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(code)
        origin = self.headers.get("Origin")
        if origin in self.origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._reply(
            200 if self._allowed() else 403,
            {"status": "ok" if self._allowed() else "origin_not_allowed"},
        )

    def do_GET(self):
        if not self._allowed():
            return self._reply(403, {"error": "origin_not_allowed"})
        path = urlsplit(self.path).path
        if path == "/":
            return self._reply(
                200,
                {
                    "status": "ok",
                    "service": "garra-ui-bridge",
                    "message": "This is the backend API, not the body-map frontend.",
                    "routes": {
                        "health": "/health",
                        "symptom_menu": "/api/body-map",
                        "communities": "/api/communities",
                        "research": "/api/research/search",
                        "papers": "/api/research/papers",
                        "atlas": "/api/research/atlas",
                        "unified_search": {"method": "POST", "path": "/api/search"},
                        "clusters": "/api/clusters",
                        "explorer": "/explore",
                        "search": {
                            "method": "POST",
                            "path": "/api/connections/search",
                            "body": {"hpo_ids": ["HP:0001324"], "confirmed": True},
                        },
                    },
                },
            )
        if path == "/explore":
            body = Path(__file__).with_name("explore.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/clusters":
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if set(query) - {"kind", "entity_id"} or any(len(v) != 1 for v in query.values()):
                return self._reply(400, {"error": "invalid_cluster_filters"})
            try:
                return self._reply(
                    200, self.discovery.clusters(**{k: v[0] for k, v in query.items()})
                )
            except InputError as exc:
                return self._reply(400, {"error": "invalid_request", "message": str(exc)})
        if path.startswith("/api/clusters/"):
            result = self.discovery.cluster(path.removeprefix("/api/clusters/"))
            return (
                self._reply(200, result)
                if result is not None
                else self._reply(404, {"error": "not_found"})
            )
        if path == "/health":
            return self._reply(200, {"status": "ok", "service": "garra-ui-bridge", "research": True})
        if path == "/api/body-map":
            return self._reply(200, get_catalog())
        if path == "/api/communities":
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if set(query) - {"kind", "disease_id", "process_id"} or any(
                len(values) != 1 for values in query.values()
            ):
                return self._reply(400, {"error": "invalid_community_filters"})
            try:
                result = self.communities.list(**{k: v[0] for k, v in query.items()})
            except ValueError as exc:
                return self._reply(400, {"error": "invalid_request", "message": str(exc)})
            return self._reply(200, result)
        if path.startswith("/api/communities/"):
            result = self.communities.get(path.removeprefix("/api/communities/"))
            if result is not None:
                return self._reply(200, result)
        self._reply(404, {"error": "not_found"})

    def do_POST(self):
        if not self._allowed():
            return self._reply(403, {"error": "origin_not_allowed"})
        path = urlsplit(self.path).path
        if path not in {
            "/api/connections/search",
            "/api/search",
            "/api/research/search",
            "/api/research/papers",
            "/api/research/atlas",
        }:
            return self._reply(404, {"error": "not_found"})
        if self.headers.get_content_type() != "application/json":
            return self._reply(415, {"error": "content_type_must_be_application_json"})
        try:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise InputError("Content-Length must be an integer") from exc
            if not 0 < length <= 16384 or self.headers.get("Transfer-Encoding"):
                return self._reply(413, {"error": "request_body_must_be_1_to_16384_bytes"})
            self.connection.settimeout(30)
            body = json.loads(self.rfile.read(length))
            if path == "/api/search":
                if isinstance(body, dict) and body.get("mode") == "phenotype":
                    result = self.service.search({k: v for k, v in body.items() if k != "mode"})
                    result["mode"] = "phenotype"
                    result["discovery"] = self.discovery.metadata()
                    for card in result["cards"]:
                        card["cluster_ids"] = self.discovery.memberships.get(card["id"], [])
                else:
                    result = self.discovery.search(body)
            elif path == "/api/connections/search":
                result = self.service.search(body)
            elif path.endswith("/atlas"):
                result = self.research.atlas(body)
            else:
                result = self.research.search(body, papers=path.endswith("/papers"))
        except (InputError, json.JSONDecodeError, UnicodeError) as exc:
            return self._reply(400, {"error": "invalid_request", "message": str(exc)})
        except UpstreamError as exc:
            return self._reply(
                504 if exc.timeout else 502,
                {"error": "similarity_unavailable", "message": str(exc), "retryable": True},
            )
        except ResearchUnavailable as exc:
            return self._reply(503, {"error": "research_unavailable", "message": str(exc), "retryable": True})
        except TimeoutError:
            return self._reply(408, {"error": "request_timeout"})
        except Exception:
            return self._reply(
                500, {"error": "internal_error", "message": "Unable to build connection cards"}
            )
        self._reply(200, result)


def create_server(
    *,
    port=8787,
    service=None,
    communities=None,
    discovery=None,
    research=None,
    origins=(
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ),
):
    handler = partial(
        Handler,
        service=service or ConnectionService(),
        research=research or ResearchService(),
        origins=set(origins),
        communities=communities if communities is not None else CommunityCatalog(),
        discovery=discovery if discovery is not None else DiscoveryEngine(),
    )
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local body-map / Monarch UI bridge")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--enrichment",
        type=Path,
        help="Verified disease research catalog JSON; no fetching performed",
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        help="Exact UI origin; repeat for multiple origins (replaces defaults)",
    )
    parser.add_argument("--communities", type=Path, help="Public community catalog JSON")
    parser.add_argument(
        "--bridge", type=Path, help="Version-1 disease bridge snapshot for search/clustering"
    )
    parser.add_argument(
        "--atlas", type=Path, help="Optional atlas SQLite database for disease/gene aliases"
    )
    parser.add_argument("--cluster-threshold", type=float, default=0.5)
    args = parser.parse_args(argv)
    try:
        enrichment = json.loads(args.enrichment.read_text()) if args.enrichment else None
        service = ConnectionService(enrichment=enrichment)
        communities = CommunityCatalog(
            json.loads(args.communities.read_text()) if args.communities else None
        )
        discovery = DiscoveryEngine(
            json.loads(args.bridge.read_text()) if args.bridge else None,
            atlas=args.atlas,
            phenotype_threshold=args.cluster_threshold,
        )
        options = {
            "port": args.port,
            "service": service,
            "communities": communities,
            "discovery": discovery,
        }
        if args.allow_origin:
            for origin in args.allow_origin:
                parsed = urlsplit(origin)
                if (
                    parsed.scheme not in {"http", "https"}
                    or not parsed.netloc
                    or parsed.path
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError("Use exact HTTP(S) origins without a path")
            options["origins"] = args.allow_origin
        server = create_server(**options)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(f"UI bridge: http://127.0.0.1:{server.server_port} (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
