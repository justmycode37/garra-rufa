"""Public explain API."""

from __future__ import annotations

from garra.actions.journey import build_journey
from garra.sources.envelope import envelope, now_iso

from .context import build_evidence_packet
from .fallback import fallback_narrative
from .openai_client import call_openai


def fallback_explain(journey: dict) -> dict:
    if journey.get("status") != "ok":
        return envelope(
            status=journey.get("status", "rejected"),
            source_id="garra.explain",
            publisher="garra-rufa",
            license="n/a",
            url="local",
            message=journey.get("message") or "Journey did not succeed; nothing to explain.",
        )
    packet = build_evidence_packet(journey)
    narrative = fallback_narrative(packet)
    return _wrap_explanation(journey, packet, narrative)


def explain_journey(
    journey: dict,
    *,
    use_openai: bool = True,
    model: str | None = None,
) -> dict:
    if journey.get("status") != "ok":
        return envelope(
            status=journey.get("status", "rejected"),
            source_id="garra.explain",
            publisher="garra-rufa",
            license="n/a",
            url="local",
            message=journey.get("message") or "Journey did not succeed; nothing to explain.",
        )

    packet = build_evidence_packet(journey)
    if use_openai:
        try:
            narrative = call_openai(packet, model=model)
        except RuntimeError as exc:
            narrative = fallback_narrative(packet)
            narrative["openai_error"] = str(exc)
            narrative["mode"] = "fallback"
    else:
        narrative = fallback_narrative(packet)

    return _wrap_explanation(journey, packet, narrative)


def explain_query(
    query: str,
    *,
    neighbor_limit: int = 5,
    live: bool = True,
    use_openai: bool = True,
    model: str | None = None,
) -> dict:
    journey = build_journey(query, neighbor_limit=neighbor_limit, live=live)
    explanation = explain_journey(journey, use_openai=use_openai, model=model)
    return {
        "status": explanation.get("status", "ok"),
        "query": query,
        "journey": journey,
        "explanation": explanation,
    }


def _wrap_explanation(journey: dict, packet: dict, narrative: dict) -> dict:
    return {
        "status": "ok",
        "source_id": "garra.explain",
        "publisher": "OpenAI" if narrative.get("mode") == "openai" else "garra-rufa (deterministic fallback)",
        "license": "n/a",
        "url": "https://platform.openai.com/docs" if narrative.get("mode") == "openai" else "local",
        "retrieved_at": now_iso(),
        "query": journey.get("query"),
        "narrative": narrative,
        "evidence": packet,
    }
