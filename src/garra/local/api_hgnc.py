"""HGNC REST (https://rest.genenames.org/fetch/<field>/<value>) from ontology.sqlite."""

from __future__ import annotations

from urllib.parse import unquote

from garra.local import ontology as O
from garra.local.router import Reply, Req, register

FIELDS = {"symbol", "alias_symbol", "prev_symbol", "hgnc_id", "entrez_id", "ensembl_gene_id"}


@register("rest.genenames.org", "/fetch/", "ontology")
def fetch(req: Req) -> Reply | None:
    parts = req.path[len("/fetch/"):].split("/", 1)
    if len(parts) != 2 or parts[0] not in FIELDS:
        return None
    docs = O.hgnc(parts[0], unquote(parts[1]))
    return Reply({"responseHeader": {"status": 0, "QTime": 0},
                  "response": {"numFound": len(docs), "start": 0, "numFoundExact": True,
                               "docs": docs}})
