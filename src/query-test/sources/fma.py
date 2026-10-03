"""Foundational Model of Anatomy (FMA, human anatomy) via the EBI OLS4 API (no auth).

BioPortal needs an API key, but OLS4 serves FMA 5.1 openly (see _ols.py). OLS uses
non-OBO IRIs (http://purl.org/sig/ont/fma/fma7088, OLS curie "fma7088"); nodes use
FMA:7088. For an FMA:* node this yields subclass_of / has_subclass and every FMA
relation in both directions: regional_part(_of), constitutional_part(_of),
member_of, arterial_supply, venous_drainage, lymphatic_drainage, nerve_supply,
bounded_by, surrounded_by, continuous_with, anterior_to / posterior_to, ... FMA
carries no xrefs; Uberon links to it (UBERON xref FMA:*). Free text: exact label
or synonym match only.
"""
import re

from ._ols import OlsOntologySource

FMA_IRI = "http://purl.org/sig/ont/fma/fma"


class FmaSource(OlsOntologySource):
    name = "fma"
    id_prefixes = frozenset({"FMA"})
    ONTOLOGY = "fma"
    PREFIX = "FMA"

    def iri(self, curie: str) -> str:
        return FMA_IRI + curie.split(":", 1)[1]

    def curie(self, iri: str, ols_curie: str | None = None) -> str | None:
        if iri.startswith(FMA_IRI):
            return "FMA:" + iri[len(FMA_IRI):]
        return super().curie(iri, ols_curie)

    def _norm(self, curie: str) -> str:
        m = re.fullmatch(r"(?i)fma:?(\d+)", curie)
        return f"FMA:{m.group(1)}" if m else curie
