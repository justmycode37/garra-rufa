"""LitVar2 (https://www.ncbi.nlm.nih.gov/research/litvar2/), no key: papers that mention a
genetic variant, text-mined from PubMed and PMC full text (normalised, so "p.C1039Y",
"Cys1039Tyr" and rs137854461 find the same papers).

  /variant/autocomplete/?query=rs137854461 | FBN1 p.Cys1039Tyr   -> litvar ids
  /variant/get/<litvar id>/publications                         -> pmids, pmcids

  /variant/search/gene/<symbol>                                 every variant of a gene with
                                                                its paper count (one Python
                                                                dict literal per line)

Queries (plan.py):
  variant        ClinVar / GTEx / OpenTargets variant nodes: litvar@<rsid>## directly, else
                 looked up by "<gene> <protein change>" from the ClinVar title
                 ("NM_000138.5(FBN1):c.3116G>A (p.Cys1039Tyr)")
  gene_variants  the most published variants of a disease gene (the graph only holds a few
                 variants per gene, often recent ClinVar submissions nobody has cited yet)
"""
import ast
from urllib.parse import quote

import requests

from . import ncbi
from .base import Paper, Provider, Query

API = "https://www.ncbi.nlm.nih.gov/research/litvar2-api"
TOP_VARIANTS = 10  # gene_variants: variants per gene
PER_VARIANT = 30  # gene_variants: papers per variant


class LitVarProvider(Provider):
    name = "litvar"
    throttle = ncbi.RESEARCH
    relations = frozenset({"variant", "gene_variants"})

    def _litvar_id(self, q: Query) -> tuple[str, str] | None:
        v = q.other
        if v.rsid:
            return f"litvar@{v.rsid}##", v.rsid
        if v.gene and v.hgvs:
            text = f"{v.gene} {v.hgvs}"
            for r in self.fetch_json(f"{API}/variant/autocomplete/", {"query": text}) or []:
                if v.gene in (r.get("gene") or []):
                    return r["_id"], text
        return None

    def _publications(self, lid: str) -> list[str]:
        try:
            d = self.fetch_json(f"{API}/variant/get/{quote(lid, safe='@')}/publications")
        except requests.HTTPError as e:  # 400: a variant LitVar has no papers for
            if e.response is not None and e.response.status_code == 400:
                return []
            raise
        return [str(x) for x in d.get("pmids") or []]

    def _gene_variants(self, q: Query) -> tuple[str, list[Paper]]:
        sym = q.other.symbol
        if not sym:
            return "", []
        text = f"{sym} top {TOP_VARIANTS} variants"
        try:
            body = self.fetch(f"{API}/variant/search/gene/{quote(sym)}")
            rows = [ast.literal_eval(x) for x in body.splitlines() if x.strip()]
        except Exception as e:
            self.fail(f"gene variants {sym}", e)
            return "", []
        rows.sort(key=lambda r: -(r.get("pmids_count") or 0))
        out: list[Paper] = []
        for r in rows[:TOP_VARIANTS]:
            try:
                pmids = self._publications(r["_id"])
            except Exception as e:
                self.fail(f"variant {r['_id']}", e)
                continue
            out += [Paper(pmid=p, hits=[self.hit(f"{text}: {r['_id']}", q, i)])
                    for i, p in enumerate(pmids[:PER_VARIANT])]
        return text, out[:q.cap]

    def search(self, q: Query) -> tuple[str, list[Paper]]:
        if q.relation == "gene_variants":
            return self._gene_variants(q)
        try:
            found = self._litvar_id(q)
            if not found:
                return "", []
            lid, text = found
            pmids = self._publications(lid)
        except Exception as e:
            self.fail(f"variant {q.other.label[:60]}", e)
            return "", []
        return text, [Paper(pmid=p, hits=[self.hit(f"{text} ({lid})", q, r)])
                      for r, p in enumerate(pmids[:q.cap])]
