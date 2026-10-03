"""Monarch v3 adapter verified against /openapi.json and a live search response."""

import json
import math
import socket
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT = "https://api.monarchinitiative.org/v3/api/semsim/search"
METRICS = {"jaccard_similarity", "ancestor_information_content", "phenodigm_score"}


class UpstreamError(Exception):
    def __init__(self, message, *, timeout=False):
        super().__init__(message)
        self.timeout = timeout


def normalize_results(raw, metric):
    if not isinstance(raw, list):
        raise UpstreamError("Monarch returned an unexpected response shape")
    results = []
    seen = set()
    try:
        for item in raw:
            disease, similarity = item["subject"], item["similarity"]
            key, name, score = disease["id"], disease["name"], item["score"]
            if (
                not isinstance(key, str)
                or not key.startswith("MONDO:")
                or not isinstance(name, str)
                or not name
                or key in seen
                or similarity.get("metric") != metric
                or isinstance(score, bool)
                or not isinstance(score, (float, int))
                or not math.isfinite(score)
                or score < 0
                or (metric == "jaccard_similarity" and score > 1)
            ):
                raise ValueError("Invalid disease, metric, or score")
            seen.add(key)
            # Actual API orientation: subject = matched disease, object = query.
            disease_terms = similarity["subject_termset"]
            query_terms = similarity["object_termset"]
            matches = similarity["object_best_matches"]
            if not all(isinstance(v, dict) for v in (disease_terms, query_terms, matches)):
                raise ValueError("Invalid termsets")
            if not disease_terms or not query_terms or set(matches) != set(query_terms):
                raise ValueError("Incomplete phenotype matches")
            matched = []
            for entry in matches.values():
                source, target = entry["match_source"], entry["match_target"]
                if source not in query_terms or target not in disease_terms:
                    raise ValueError("Unexpected match orientation")
                if (
                    not all(
                        isinstance(entry.get(k), str)
                        for k in ("match_source_label", "match_target_label")
                    )
                    or isinstance(entry.get("score"), bool)
                    or not isinstance(entry.get("score"), (float, int))
                    or not math.isfinite(entry["score"])
                    or entry["score"] < 0
                    or (metric == "jaccard_similarity" and entry["score"] > 1)
                ):
                    raise ValueError("Invalid phenotype match")
                for field in ("match_subsumer", "match_subsumer_label"):
                    if entry.get(field) is not None and not isinstance(entry[field], str):
                        raise ValueError("Invalid shared ancestor")
                matched.append(
                    {
                        "query_id": source,
                        "query_label": entry["match_source_label"],
                        "disease_phenotype_id": target,
                        "disease_phenotype_label": entry["match_target_label"],
                        "score": entry["score"],
                        "shared_ancestor_id": entry.get("match_subsumer"),
                        "shared_ancestor_label": entry.get("match_subsumer_label"),
                        "exact": source == target,
                    }
                )
            results.append(
                {
                    "id": key,
                    "name": name,
                    "value": float(score),
                    "disease_annotation_count": len(disease_terms),
                    "query_term_count": len(query_terms),
                    "matched_phenotypes": matched,
                    "subsets": disease.get("subsets") or [],
                }
            )
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise UpstreamError(
            "Monarch response no longer matches the expected disease/phenotype schema"
        ) from exc
    return results


class MonarchClient:
    """Bounded memory-only cache; no persistent user symptom records."""

    def __init__(self, *, timeout=25, ttl=900, capacity=64, opener=urlopen, clock=time.monotonic):
        self.timeout, self.ttl, self.capacity = timeout, ttl, capacity
        self.opener, self.clock = opener, clock
        self.cache = {}
        self.lock = threading.Lock()

    def search(self, hpo_ids, metric, limit=50):
        key = (tuple(sorted(set(hpo_ids))), metric, limit)
        with self.lock:
            cached = self.cache.get(key)
            if cached and self.clock() - cached[0] < self.ttl:
                out = deepcopy(cached[1])
                out["cache"]["hit"] = True
                return out
        body = {
            "termset": list(key[0]),
            "group": "Human Diseases",
            "metric": metric,
            "directionality": "bidirectional",
            "limit": limit,
        }
        req = Request(
            ENDPOINT,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "garra-rufa-ui/1",
            },
        )
        try:
            with self.opener(req, timeout=self.timeout) as response:
                encoded = response.read(8_000_001)
            if len(encoded) > 8_000_000:
                raise UpstreamError("Monarch response exceeded the size limit")
            raw = json.loads(encoded)
        except (TimeoutError, socket.timeout) as exc:
            raise UpstreamError("Monarch search timed out; retry later", timeout=True) from exc
        except HTTPError as exc:
            raise UpstreamError(f"Monarch search unavailable (HTTP {exc.code})") from exc
        except URLError as exc:
            raise UpstreamError(
                "Monarch could not be reached", timeout=isinstance(exc.reason, TimeoutError)
            ) from exc
        except (OSError, HTTPException) as exc:
            raise UpstreamError("Monarch connection was interrupted; retry later") from exc
        except (ValueError, UnicodeError) as exc:
            raise UpstreamError("Monarch returned invalid JSON") from exc
        results = normalize_results(raw, metric)
        if any(set(row["similarity"]["object_termset"]) != set(key[0]) for row in raw):
            raise UpstreamError("Monarch returned a different query termset")
        now = datetime.now(timezone.utc).isoformat()
        out = {
            "results": results,
            "metric": metric,
            "retrieved_at": now,
            "release": "unknown",
            "source_url": ENDPOINT,
            "request": body,
            "cache": {"hit": False, "ttl_seconds": self.ttl, "storage": "memory_only"},
        }
        with self.lock:
            if len(self.cache) >= self.capacity:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (self.clock(), deepcopy(out))
        return out
