"""Local stdlib HTTP bridge. Run: python -m garra.ui --port 8787."""

import argparse
import json
import re
import sys
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from garra.community import CommunityCatalog
from garra.datasets import ensure
from garra.discovery import DiscoveryEngine
from garra.discovery.regions import RegionIndex
from garra.research.service import ResearchService, ResearchUnavailable

from .catalog import get_catalog
from .graphs import GraphBuilds
from .monarch import UpstreamError
from .service import ConnectionService, InputError

EVIDENCE_PATH = re.compile(r"^/api/graphs/([a-z0-9-]{1,60}-[0-9a-f]{8})/evidence$")


class Handler(BaseHTTPRequestHandler):
    def __init__(
        self, *args, service, origins, communities, discovery, research, regions, graphs=None,
        **kwargs
    ):
        self.service, self.origins = service, origins
        self.communities = communities
        self.discovery = discovery
        self.research = research
        self.regions = regions
        self.graphs = graphs
        super().__init__(*args, **kwargs)

    def _write(self, body):
        # Write in small pieces: on Windows a single large loopback send can stall after
        # the first 64 KB until the connection resets about 19 s later.
        for start in range(0, len(body), 16384):
            self.wfile.write(body[start:start + 16384])

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
        self._write(body)

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
                        "graphs": "/api/graphs",
                        "graph_build": {"method": "POST", "path": "/api/graphs/build"},
                        "graph_evidence": {"method": "POST", "path": "/api/graphs/{id}/evidence"},
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
            self._write(body)
            return
        if path == "/api/regions" or path.startswith("/api/regions/"):
            if self.regions is None:
                return self._reply(503, {"error": "Regional ontology data is not configured"})
            try:
                result = (
                    self.regions.catalog()
                    if path == "/api/regions"
                    else self.regions.detail(path.removeprefix("/api/regions/"))
                )
                return self._reply(200, result)
            except InputError as exc:
                return self._reply(400, {"error": str(exc)})
        if path == "/api/neighbors":
            if self.regions is None:
                return self._reply(503, {"error": "Ontology data is not configured"})
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if set(query) != {"entity_id"} or len(query["entity_id"]) != 1:
                return self._reply(400, {"error": "Use one entity_id"})
            try:
                return self._reply(200, self.regions.neighbors(query["entity_id"][0]))
            except InputError as exc:
                return self._reply(400, {"error": str(exc)})
        if path == "/api/entity":
            query = parse_qs(urlsplit(self.path).query)
            if set(query) != {"id"} or len(query["id"]) != 1:
                return self._reply(400, {"error": "Use one entity id"})
            result = self.discovery.entity_graph(query["id"][0])
            return self._reply(200, result) if result else self._reply(404, {"error": "not_found"})
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
        if path == "/api/graphs" or path.startswith("/api/graphs/"):
            return self._graphs_get(path)
        if path == "/health":
            return self._reply(
                200,
                {
                    "status": "ok",
                    "service": "garra-ui-bridge",
                    "research": True,
                    "graphs": self.graphs is not None,
                    "discovery": self.discovery.metadata(),
                    "regions": self.regions is not None,
                },
            )
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

    def _graphs_get(self, path):
        if self.graphs is None:
            return self._reply(503, {"error": "Graph builds are not configured"})
        parts = path.removeprefix("/api/graphs").strip("/").split("/")
        if parts == [""]:
            return self._reply(200, self.graphs.list())
        if len(parts) == 1:
            build = self.graphs.get(parts[0])
            return self._reply(200, build) if build else self._reply(404, {"error": "not_found"})
        # /api/graphs/<id>/<view> is the view JSON, /api/graphs/<id>/<view>.md its report
        md = len(parts) == 2 and parts[1].endswith(".md")
        file = None if len(parts) != 2 else (self.graphs.markdown(parts[0], parts[1].removesuffix(".md"))
                                             if md else self.graphs.view(*parts))
        if not file:
            return self._reply(404, {"error": "not_found"})
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8" if md else "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._write(body)

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
            "/api/graphs/build",
        } and not EVIDENCE_PATH.match(path):
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
            if path == "/api/graphs/build":
                if self.graphs is None:
                    return self._reply(503, {"error": "Graph builds are not configured"})
                result = self.graphs.start(body)
            elif EVIDENCE_PATH.match(path):
                if self.graphs is None:
                    return self._reply(503, {"error": "Graph builds are not configured"})
                if body != {}:
                    raise InputError("Send an empty JSON object")
                result = self.graphs.add_evidence(EVIDENCE_PATH.match(path)[1])
                if result is None:
                    return self._reply(404, {"error": "not_found"})
            elif path == "/api/search":
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
                result = (
                    self.regions.atlas(body)
                    if self.regions is not None
                    else self.research.atlas(body)
                )
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
            return self._reply(
                503, {"error": "research_unavailable", "message": str(exc), "retryable": True}
            )
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
    regions=None,
    graphs=None,
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
        regions=regions,
        graphs=graphs,
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
    parser.add_argument(
        "--graph-builds-dir",
        type=Path,
        help="Where graphs built from the webapp are kept (default data/web-graphs)",
    )
    parser.add_argument(
        "--no-graph-builds", action="store_true", help="Disable building graphs from queries"
    )
    parser.add_argument("--ontology", type=Path, help="HPO OBO file for anatomical discovery")
    parser.add_argument(
        "--region-map",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "webapp/src/lib/body-regions.json",
    )
    args = parser.parse_args(argv)
    # Any dataset may be missing: it is restored when possible, otherwise its routes
    # degrade (empty discovery, live HPO atlas, 503 regions) instead of failing startup.
    args.bridge, args.atlas = ensure(args.bridge), ensure(args.atlas)
    args.ontology, args.communities = ensure(args.ontology), ensure(args.communities)
    args.enrichment = ensure(args.enrichment)
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
            "graphs": None if args.no_graph_builds else GraphBuilds(args.graph_builds_dir),
        }
        if args.ontology and not args.bridge:
            print("--ontology needs --bridge; body regions are disabled", file=sys.stderr)
        elif args.ontology and ensure(args.region_map):
            options["regions"] = RegionIndex(
                discovery,
                json.loads(args.bridge.read_text()),
                json.loads(args.region_map.read_text()),
                args.ontology,
            )
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
