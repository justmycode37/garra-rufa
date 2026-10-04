"""Export, per atlas body region, the findings most often annotated in that organ.

Source: the local HPO release (src/query-test/sources/_hpoa.py: hp.obo + phenotype.hpoa in
data/hpo/). For each region of webapp/src/lib/body-regions.json the descendants of its HPO
term are ranked by the number of diseases annotated with them (or a term below them); the
top ones (except those covering most of the organ's diseases) are offered in the atlas as
suggestions to add to an organ query, next to the number of diseases with any finding in
the organ.

Output: webapp/public/organ-findings.json, read by the atlas graph builder
(webapp/src/components/OrganGraphBuilder.tsx). Re-run after changing body-regions.json or
refreshing data/hpo/:  python scripts/export_organ_findings.py
"""
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "query-test"))
from sources import _hpoa  # noqa: E402

REGIONS = ROOT / "webapp/src/lib/body-regions.json"
OUT = ROOT / "webapp/public/organ-findings.json"
TOP = 24
BROAD = 0.5  # max share of the organ's diseases a suggested finding may cover


def main():
    hpo = _hpoa.load()
    if hpo is None:
        sys.exit("HPO data unavailable (data/hpo/)")
    regions = json.loads(REGIONS.read_text(encoding="utf-8"))
    out = {"source": "Human Phenotype Ontology annotations (phenotype.hpoa)",
           "exported": date.today().isoformat(), "regions": {}}
    for region_id, region in regions.items():
        root = hpo.canonical(region["hpo"])
        if root is None:
            print(f"skip {region_id}: unknown term {region['hpo']}", file=sys.stderr)
            continue
        subtree = hpo.descendants(root)
        diseases = sum(1 for terms in hpo.ann.values() if any(t in subtree for t in terms))
        # a finding most of the organ's diseases have narrows nothing ("Abnormal skeletal
        # morphology" under the skeleton): offer the next level instead
        findings = sorted((t for t in subtree if t != root and 0 < hpo.count.get(t, 0) <= BROAD * diseases),
                          key=lambda t: (-hpo.count[t], hpo.name[t]))[:TOP]
        out["regions"][region_id] = {
            "hpo": root, "hpo_label": hpo.name[root], "diseases": diseases,
            "findings": [{"id": t, "label": hpo.name[t], "diseases": hpo.count[t]}
                         for t in findings]}
        print(f"{region_id:16} {diseases:6} diseases  {len(findings):3} findings")
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n",
                   encoding="utf-8")
    print(f"-> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
