"""Validated, versioned public catalog. No automatic enrollment or medical inference."""

from copy import deepcopy
from urllib.parse import urlsplit

SECTIONS = {
    "disease": "Disease communities",
    "mechanism": "Mechanism groups",
    "project": "Collaboration projects",
}
COMMON = {"id", "kind", "name", "description", "disease_ids", "process_ids", "evidence"}
PROJECT = {
    "community_ids",
    "owner",
    "goal",
    "access_conditions",
    "reuse_checks",
    "next_step",
    "status",
    "asset_urls",
}


def text(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError(f"{field} must be nonempty text of at most 4000 characters")
    return value


def strings(value, field, *, identifiers=False):
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError(f"{field} must be a list of at most 200 strings")
    for item in value:
        text(item, field)
        if identifiers and (":" not in item or any(c.isspace() for c in item)):
            raise ValueError(f"{field} requires namespaced identifiers")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} contains duplicates")
    return value


def url(value):
    parsed = urlsplit(text(value, "url"))
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
        raise ValueError("Links must be public HTTP(S) URLs without credentials")
    return value


class CommunityCatalog:
    """Load curated public records; references do not establish scientific support."""

    def __init__(self, document=None):
        document = deepcopy(
            document
            if document is not None
            else {
                "schema_version": 1,
                "demo": False,
                "records": [],
            }
        )
        if not isinstance(document, dict) or set(document) != {"schema_version", "demo", "records"}:
            raise ValueError("Catalog requires schema_version, demo, and records only")
        if type(document["schema_version"]) is not int or document["schema_version"] != 1:
            raise ValueError("Unsupported community schema version")
        if type(document["demo"]) is not bool or not isinstance(document["records"], list):
            raise ValueError("demo must be boolean and records must be a list")
        self.demo = document["demo"]
        self.records = {}
        for row in document["records"]:
            if not isinstance(row, dict):
                raise ValueError("Each community record must be an object")
            kind = row.get("kind")
            if not isinstance(kind, str) or kind not in SECTIONS:
                raise ValueError("kind must be disease, mechanism, or project")
            if set(row) != COMMON | (PROJECT if kind == "project" else set()):
                raise ValueError(f"Invalid fields for {kind} record")
            for field in ("id", "name", "description"):
                text(row[field], field)
            if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in row["id"]):
                raise ValueError("id must be a lowercase URL-safe slug")
            if row["id"] in self.records:
                raise ValueError("Duplicate community ID")
            for field in ("disease_ids", "process_ids"):
                strings(row[field], field, identifiers=True)
            if kind == "disease" and not row["disease_ids"]:
                raise ValueError("Disease communities require disease_ids")
            if kind == "mechanism" and not row["process_ids"]:
                raise ValueError("Mechanism groups require process_ids")
            evidence = row["evidence"]
            if not isinstance(evidence, list):
                raise ValueError("evidence must be a list")
            for link in evidence:
                if not isinstance(link, dict) or set(link) != {
                    "claim_id",
                    "publication_id",
                    "url",
                    "review_status",
                }:
                    raise ValueError("Evidence needs claim_id, publication_id, url, review_status")
                text(link["claim_id"], "claim_id")
                strings([link["publication_id"]], "publication_id", identifiers=True)
                url(link["url"])
                # Scientific review stays in the evidence system, not a community assertion.
                if link["review_status"] != "unreviewed":
                    raise ValueError("Community evidence links must remain unreviewed")
            if kind == "project":
                for field in ("owner", "goal", "access_conditions", "next_step"):
                    text(row[field], field)
                for field in ("community_ids", "reuse_checks", "asset_urls"):
                    strings(row[field], field)
                    if field != "asset_urls" and not row[field]:
                        raise ValueError(f"Projects require {field}")
                for value in row["asset_urls"]:
                    url(value)
                if row["status"] not in (
                    "proposed",
                    "seeking_collaborators",
                    "active",
                    "completed",
                ):
                    raise ValueError("Invalid project status")
            self.records[row["id"]] = row
        for row in self.records.values():
            if row["kind"] == "project":
                for identifier in row["community_ids"]:
                    target = self.records.get(identifier)
                    if target is None or target["kind"] == "project":
                        raise ValueError(
                            "Projects must link to existing disease or mechanism groups"
                        )

    def list(self, *, kind=None, disease_id=None, process_id=None):
        if kind is not None and kind not in SECTIONS:
            raise ValueError("kind must be disease, mechanism, or project")
        for field, value in (("disease_id", disease_id), ("process_id", process_id)):
            if value is not None:
                strings([value], field, identifiers=True)
        sections = []
        for key, label in SECTIONS.items():
            if kind is not None and key != kind:
                continue
            items = []
            for row in self.records.values():
                if row["kind"] != key:
                    continue
                if disease_id is not None and disease_id not in row["disease_ids"]:
                    continue
                if process_id is not None and process_id not in row["process_ids"]:
                    continue
                item = deepcopy(row)
                item["match_reasons"] = [
                    {"field": field, "id": value}
                    for field, value in (("disease_ids", disease_id), ("process_ids", process_id))
                    if value is not None
                ]
                items.append(item)
            sections.append({"kind": key, "label": label, "items": items, "count": len(items)})
        return {**self.metadata(), "sections": sections}

    def metadata(self):
        return {
            "schema_version": 1,
            "demo": self.demo,
            "capabilities": {"read": True, "join": False, "post": False, "create_project": False},
            "notice": "Topic matches are not proof of shared mechanisms or asset reusability. "
            "Browsing does not enroll anyone or disclose membership.",
        }

    def get(self, identifier):
        row = self.records.get(identifier)
        if row is None:
            return None
        linked = [
            r["id"]
            for r in self.records.values()
            if r["kind"] == "project" and identifier in r["community_ids"]
        ]
        return {**self.metadata(), "record": deepcopy(row), "project_ids": linked}
