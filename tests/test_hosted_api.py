import importlib.util
import io
import json
import os
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("hosted_entrypoint", Path(__file__).resolve().parents[1] / "api" / "index.py")
hosted = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hosted)

class HostedApiTests(unittest.TestCase):
    def request(self, path, body, token=""):
        request = object.__new__(hosted.handler)
        request.path = path
        raw = json.dumps(body).encode()
        request.rfile = io.BytesIO(raw)
        request.headers = Message()
        request.headers["Content-Type"] = "application/json"
        request.headers["Content-Length"] = str(len(raw))
        request.headers["Authorization"] = "Bearer " + token
        replies = []
        request.reply = lambda status, value: replies.append((status, value))
        request.do_POST()
        return replies[0]

    def test_rejects_missing_or_wrong_credentials_before_running_providers(self):
        with patch.dict(os.environ, {"GARRA_RESEARCH_TOKEN": "synthetic-test-token"}), patch.object(hosted.service, "search") as search:
            for token in ("", "wrong"):
                self.assertEqual(self.request("/api/research/papers", {"query": "Marfan"}, token)[0], 401)
            search.assert_not_called()

    def test_valid_credential_uses_the_dedicated_paper_pipeline(self):
        with patch.dict(os.environ, {"GARRA_RESEARCH_TOKEN": "synthetic-test-token"}), patch.object(hosted.service, "search", return_value={"pipeline": "repository-literature"}) as search:
            status, result = self.request("/api/research/papers", {"query": "Marfan"}, "synthetic-test-token")
            self.assertEqual(status, 200)
            self.assertEqual(result["pipeline"], "repository-literature")
            search.assert_called_once_with({"query": "Marfan"}, papers=True)

    def test_unconfigured_service_stays_closed(self):
        with patch.dict(os.environ, {"GARRA_RESEARCH_TOKEN": ""}):
            self.assertEqual(self.request("/api/research/papers", {"query": "Marfan"})[0], 401)

    def test_bounded_requests_and_unknown_routes_never_run_providers(self):
        with patch.dict(os.environ, {"GARRA_RESEARCH_TOKEN": "synthetic-test-token"}), patch.object(hosted.service, "search") as search:
            self.assertEqual(self.request("/api/research/papers", {"query": "x" * 5000}, "synthetic-test-token")[0], 413)
            self.assertEqual(self.request("/api/private", {}, "synthetic-test-token")[0], 404)
            search.assert_not_called()
